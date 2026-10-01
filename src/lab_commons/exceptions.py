"""App-agnostic exception classes + decorators.

Provenance: extracted from consumer-a's ``core/utils/exceptions.py`` (byte-identical
across the consumer-a/consumer-c/consumer-b family; see ``finding-shared-lab-infra-extraction.md``).
Only the GENERIC subset is extracted here — ``FEMMException`` (a vendor-solver name) is
project-specific and stays in consumer-a; consumer-b's ``ErrorCode`` catalog / ``ConsumerBError`` base
class is its own project-specific extension and is likewise out of scope for this module.
"""

from collections.abc import Callable
from typing import Never

from lab_commons.log import log

__all__ = ['ParameterException', 'QuantityException', 'deprecated', 'not_implemented']


class QuantityException(Exception):
    """Exception for unexpected parameters, read quantity in pint."""

    def __init__(self, message: str = '') -> None:
        """Keep *message* as the refusal text this exception renders."""
        self.message = message

    def __str__(self) -> str:
        """Render the refusal AND log it, so a caught-and-printed error still reaches the log."""
        msg = f'QuantityException: {self.message}'
        log(msg, level='ERROR')
        return msg


class ParameterException(Exception):
    """Exception for unexpected parameters."""

    def __init__(self, message: str = '') -> None:
        """Keep *message* as the refusal text this exception renders."""
        self.message = message

    def __str__(self) -> str:
        """Render the refusal AND log it, so a caught-and-printed error still reaches the log."""
        msg = f'ParameterException: {self.message}'
        log(msg, level='ERROR')
        return msg


def not_implemented(func: Callable[..., object]) -> Callable[..., Never]:
    """Replace *func* with one that raises ``NotImplementedError`` naming it."""

    def wrapper(*_: object, **__: object) -> Never:
        msg = f'{func.__name__} is not implemented yet.'
        raise NotImplementedError(msg)

    return wrapper


def deprecated(func: Callable[..., object]) -> Callable[..., Never]:
    """Replace *func* with one that raises ``DeprecationWarning`` naming it."""

    def wrapper(*_: object, **__: object) -> Never:
        msg = f'{func.__name__} has been deprecated.'
        raise DeprecationWarning(msg)

    return wrapper
