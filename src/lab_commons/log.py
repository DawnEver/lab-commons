"""App-agnostic logging primitives.

Provenance: extracted from motronics-studio's ``core/utils/logger.py`` free functions
(the strongest of six near-identical copies across the motronics/optimi-lab/wdg-lab
family; see ``finding-shared-lab-infra-extraction.md``), plus a named-logger factory.
The domain-specific wiring (which config object supplies the formats/level, which path
the file handler writes to) stays in each consumer; here we keep only the generic
mechanics: a named ``propagate=False`` logger, the console+file handler pair, and the
``log`` / ``log_decorator`` / ``timer`` helpers.

v2 (``structured.py``) layers a structlog/JSON sink, secret redaction, and a two-phase
bootstrap -> bind_run_dir transport on top of the SAME named logger this module produces.
"""

import functools
import logging
import os
import sys
import time
from collections.abc import Callable

__all__ = [
    'add_handle',
    'emit',
    'get_logger',
    'log',
    'log_decorator',
    'prettier_dict',
    'set_active_logger',
    'timer',
]

# Third-party loggers we never want at their default chatty level.
for _pack in ['matplotlib']:
    logging.getLogger(_pack).setLevel(logging.ERROR)


def get_logger(app_name: str) -> logging.Logger:
    """Return the app's dedicated named logger, isolated from the root logger.

    ``propagate=False`` so handlers never collide with another library (or the root
    logger) — this is what lets several apps share this module without cross-talk. The
    pre-config default level accepts everything; :func:`add_handle` tightens it to the
    configured level once the consumer's runtime is initialized.
    """
    logger = logging.getLogger(app_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


# The logger targeted by the module-level free functions (:func:`log`, :func:`timer`,
# :func:`log_decorator`). A consumer designates its own via :func:`set_active_logger`;
# the private default keeps the functions callable before any consumer binds one.
_active_logger: logging.Logger = get_logger('lab_commons')


def set_active_logger(logger: logging.Logger) -> None:
    """Bind ``logger`` as the sink for the module-level :func:`log` / :func:`timer` helpers."""
    global _active_logger  # noqa: PLW0603
    _active_logger = logger


def _log_level_from_str(level: str) -> int:
    """Map a config-level string to a logging constant, defaulting to INFO."""
    return getattr(logging, level.upper(), logging.INFO)


def emit(text: object = '', *, err: bool = False, flush: bool = False) -> None:
    """Write ONE line to a real stream.

    The shared ``print`` replacement for CLI tools whose stdout IS the product (gate verdicts,
    bootstrap diagnoses, lane reports).

    Why this exists: the family never waives ruff's T201, so every CLI script needs the
    same one-line idiom; keeping it here makes the idiom single, tested, and visible to
    consumers that otherwise have no reason to import ``sys``. Non-strings are formatted
    exactly like ``print`` (``format(text, '')`` falls back to ``str``). The stream is
    read at call time, so pytest's capsys captures it.
    """
    stream = sys.stderr if err else sys.stdout
    stream.write(f'{text}\n')
    if flush:
        stream.flush()


def add_handle(
    logger: logging.Logger,
    *,
    log_file: str | os.PathLike[str],
    level: int,
    file_format: str,
    console_format: str,
    date_format: str,
) -> None:
    """Attach a file + console handler pair to ``logger`` (idempotent).

    Generic mechanics lifted from the motronics ``add_handle``; the consumer supplies the
    resolved log-file path, level, and formats (which config object those come from is a
    domain concern). Returns early if handlers are already attached, so repeated runtime
    initialization never duplicates output.
    """
    if logger.handlers:
        return

    logger.setLevel(level)

    logging_file_handler = logging.FileHandler(log_file, encoding='utf-8')
    logging_file_handler.setLevel(level)
    logging_file_handler.setFormatter(logging.Formatter(fmt=file_format, datefmt=date_format))
    logger.addHandler(logging_file_handler)

    logging_console_handler = logging.StreamHandler()
    logging_console_handler.setLevel(level)
    logging_console_handler.setFormatter(logging.Formatter(fmt=console_format, datefmt=date_format))
    logger.addHandler(logging_console_handler)


def log(msg: str, level: str = 'INFO') -> None:
    """Logging output function.

    Args:
    ----
        msg (str): Log message
        level (str): Log level -- DEBUG, INFO, WARNING, ERROR or CRITICAL

    Returns:
        None

    Examples:
    --------
        ```python
        log('hello world', level='INFO')
        ```

    Available levels: ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        - DEBUG | Most detailed messages, typically for troubleshooting;
        - INFO | Less detailed than DEBUG, usually record key milestones to confirm things are working as expected;
        - WARNING | Recorded when something unexpected happens (e.g., low disk space),
          but the application is still running;
        - ERROR | Recorded when a more serious problem causes some functionality to fail;
        - CRITICAL | Recorded when a severe error causes the application to stop running.

    """
    level = level.upper()
    log_method = getattr(_active_logger, level.lower(), None)
    if log_method is not None:
        log_method(msg)
    else:
        error_msg = f'Log level error! Unknown log level {level}! {msg}'
        _active_logger.error(error_msg)
        raise ValueError(error_msg)


def log_decorator(msg: str, level: str = 'INFO') -> Callable[[Callable], Callable]:
    """Logging decorator.

    Args:
        msg (str): Log message
        level (str): Log level
    Returns:
        callable

    Examples:
        ```python
        @log_decorator('hello world', level='INFO')
        def func(): ...
        ```

    """

    def wrapper(func: Callable) -> Callable:
        def exc(*args: object, **kwargs: object) -> object:
            result = func(*args, **kwargs)
            log(msg, level)
            return result

        return exc

    return wrapper


def timer(func: Callable) -> Callable:
    """Timer decorator to record function execution time.

    Args:
        func: The function to be decorated
    Returns:
        wrapper: The decorated function

    Example:
        ```python
        @timer
        def func(): ...
        ```

    """

    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> object:
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        elapsed_sec = end - start
        log(
            f'Function {func.__name__} took {elapsed_sec:.6f} seconds',
            level='INFO',
        )
        # Decorator arguments are only provided once at initialization
        return result

    return wrapper


def prettier_dict(d: dict, indent: int = 0, text: str = '') -> str:
    """Render a nested mapping as indented ``key:`` / value lines."""
    for key, value in d.items():
        text += ' ' * indent + f'{key}:\n'
        if isinstance(value, dict):
            return prettier_dict(value, indent + 4, text=text)
        text += ' ' * (indent + 4) + str(value) + '\n'
    return text
