"""structlog/JSON structured output + secret redaction + two-phase bootstrap (v2).

Purely additive over :mod:`lab_commons.log` — the v1 stdlib ``propagate=False`` logger +
handler pair keeps working unchanged; this module layers a structured JSONL sink and
secret redaction on top of the SAME named logger so both paths share one set of handlers
(no cross-talk, no duplicate output).

Two-phase bootstrap: :func:`bootstrap` configures console-only output before the run
directory is known (log level, process/thread info not yet resolvable to a file);
:func:`bind_run_dir` attaches the file sink once :func:`lab_commons.paths.run_output_dir`
resolves. Both are idempotent, mirroring v1 :func:`log.add_handle`'s "return early if
already attached" guard.
"""

import copy
import logging
import re
from hashlib import sha256
from pathlib import Path

import structlog

from lab_commons.log import get_logger

__all__ = [
    'SecretHashingFormatter',
    'bind_run_dir',
    'bootstrap',
    'get_structured_logger',
    'hash_secret',
    'redact_secrets_processor',
]

# Field KEYS (not values) that must never reach a log sink in the clear. Substring,
# case-insensitive match on the key -- per invariant.md: "Never log license
# keys/tokens/fingerprints -- hashes only."
SECRET_KEY_PATTERN = re.compile(r'(license[_-]?key|token|fingerprint|secret|password)', re.IGNORECASE)

# Hash-prefix length is a display truncation only; the full digest is never needed for a
# log line to prove "this secret changed" or "these two lines share a secret".
_HASH_DISPLAY_CHARS = 16


def hash_secret(value: object) -> str:
    """Return a stable ``sha256:<prefix>`` label for ``value`` -- never the raw value."""
    digest = sha256(str(value).encode('utf-8')).hexdigest()
    return f'sha256:{digest[:_HASH_DISPLAY_CHARS]}'


def redact_secrets_processor(logger, method_name, event_dict):  # noqa: ARG001
    """Structlog processor: replace any sensitive-KEY value with its hash label."""
    for key, value in event_dict.items():
        if SECRET_KEY_PATTERN.search(key):
            event_dict[key] = hash_secret(value)
    return event_dict


# Attributes every stdlib LogRecord carries -- anything else came in via ``extra=`` and
# is a caller-supplied structured field. ``message`` is NOT in a fresh record; a prior
# handler's ``Formatter.format()`` sets it as a side effect (``record.message =
# record.getMessage()``), and the SAME record object is shared across every handler on a
# logger -- excluding it explicitly keeps a second handler from treating the first
# handler's rendering as extra caller data.
_STD_RECORD_ATTRS = frozenset({*vars(logging.makeLogRecord({})), 'message'})


class SecretHashingFormatter(logging.Formatter):
    """``logging.Formatter`` counterpart to :func:`redact_secrets_processor`.

    Protects the v1 stdlib path the same way the structlog chain protects the v2 path:
    any ``extra=`` field whose KEY matches :data:`SECRET_KEY_PATTERN` is replaced with its
    hash label before formatting. Non-sensitive extras are rendered too (not silently
    dropped), which is also what lets a plain ``%(message)s`` format double as the JSONL
    file sink under :func:`bind_run_dir`.
    """

    def format(self, record: logging.LogRecord) -> str:
        extras = {key: value for key, value in vars(record).items() if key not in _STD_RECORD_ATTRS}
        if not extras:
            return super().format(record)
        redacted = {
            key: (hash_secret(value) if SECRET_KEY_PATTERN.search(key) else value) for key, value in extras.items()
        }
        # emit() calls format() on the SAME record for EVERY handler on the logger (console from
        # bootstrap(), file from bind_run_dir()). Mutating record.msg/args here would make the
        # second handler's getMessage() see the first handler's output and re-append the extras,
        # duplicating them in every multi-handler line. Format a shallow COPY -- the shared record
        # is never touched, so each handler renders from the original message.
        record_copy = copy.copy(record)
        record_copy.msg = f'{record.getMessage()} {redacted}'
        record_copy.args = ()
        result = super().format(record_copy)
        # Preserve stdlib's exc_text caching contract (SR-20260720-004): Formatter.format() caches
        # the formatted traceback onto the record it is given. Since we format a COPY, that cache
        # landed on the throwaway -- copy it back so a later handler on the SAME shared record reuses
        # the traceback instead of calling formatException() again.
        if record.exc_text is None and record_copy.exc_text is not None:
            record.exc_text = record_copy.exc_text
        return result


# Apps whose structlog global config + base console handler have already been attached
# -- bootstrap() is idempotent per the SAME reason v1's add_handle() is: repeated
# runtime initialization (re-entrant CLI stages, tests) must never duplicate output.
_configured_apps: set[str] = set()

# structlog.configure() is process-global (not per-app); guard it separately so the
# FIRST bootstrap() call wins and later calls for other app names don't clobber it.
_structlog_configured = False


def bootstrap(app_name: str, *, level: int = logging.INFO) -> structlog.stdlib.BoundLogger:
    """Configure console-only structured logging before the run dir is known (idempotent).

    Wires the SAME named stdlib logger :func:`log.get_logger` returns, so
    :func:`bind_run_dir` and the v1 handler pair all attach to one logger with no
    cross-talk. Returns the bound structured logger (also obtainable via
    :func:`get_structured_logger`).
    """
    global _structlog_configured  # noqa: PLW0603
    if not _structlog_configured:
        structlog.configure(
            processors=[
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt='iso'),
                redact_secrets_processor,
                structlog.processors.JSONRenderer(),
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=False,
        )
        _structlog_configured = True

    if app_name not in _configured_apps:
        base_logger = get_logger(app_name)
        base_logger.setLevel(level)
        if not base_logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(level)
            # The processor chain already rendered JSON; the stdlib formatter just
            # passes it through (and re-protects any raw stdlib extra= call site).
            console_handler.setFormatter(SecretHashingFormatter('%(message)s'))
            base_logger.addHandler(console_handler)
        _configured_apps.add(app_name)

    return get_structured_logger(app_name)


def get_structured_logger(app_name: str) -> structlog.stdlib.BoundLogger:
    """Return ``app_name``'s bound structured logger (call :func:`bootstrap` first)."""
    return structlog.get_logger(app_name)


class _StructuredSinkHandler(logging.FileHandler):
    """Marker subclass, so ``bind_run_dir`` can recognize its own sink.

    A dynamic attribute bolted onto a plain ``FileHandler`` instance (the previous
    approach) is a property ``FileHandler`` never declares -- pyright correctly rejects
    the assignment. Subclassing makes the marker part of the type instead of an
    unenforced runtime graft.
    """


def bind_run_dir(
    app_name: str,
    run_dir: Path,
    *,
    filename: str = 'structured.jsonl',
    level: int = logging.INFO,
) -> None:
    """Attach the JSONL file sink once ``run_dir`` is known (idempotent).

    Mirrors v1 :func:`log.add_handle`'s "return early if already attached" guard: the
    ``_StructuredSinkHandler`` marker subclass (not just "any FileHandler exists") lets
    this coexist with a v1 file handler on the same logger without either re-binding
    the other.
    """
    base_logger = get_logger(app_name)
    if any(isinstance(handler, _StructuredSinkHandler) for handler in base_logger.handlers):
        return

    file_handler = _StructuredSinkHandler(Path(run_dir) / filename, encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(SecretHashingFormatter('%(message)s'))
    base_logger.addHandler(file_handler)
