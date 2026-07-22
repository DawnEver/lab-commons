"""App-agnostic exception classes + decorators.

Provenance: extracted from motronics-studio's ``core/utils/exceptions.py`` (byte-identical
across the motronics/optimi-lab/wdg-lab family; see ``finding-shared-lab-infra-extraction.md``).
Only the GENERIC subset is extracted here — ``FEMMException`` (a vendor-solver name) is
project-specific and stays in motronics; wdg-lab's ``ErrorCode`` catalog / ``WdgError`` base
class is its own project-specific extension and is likewise out of scope for this module.
"""

from lab_commons.log import log

__all__ = ['ParameterException', 'QuantityException', 'deprecated', 'not_implemented']


class QuantityException(Exception):
    """Exception for unexpected parameters, read quantity in pint."""

    def __init__(self, message='') -> None:
        self.message = message

    def __str__(self) -> str:
        msg = f'QuantityException: {self.message}'
        log(msg, level='ERROR')
        return msg


class ParameterException(Exception):
    """Exception for unexpected parameters."""

    def __init__(self, message='') -> None:
        self.message = message

    def __str__(self) -> str:
        msg = f'ParameterException: {self.message}'
        log(msg, level='ERROR')
        return msg


def not_implemented(func):
    def wrapper(*_, **__):
        msg = f'{func.__name__} is not implemented yet.'
        raise NotImplementedError(msg)

    return wrapper


def deprecated(func):
    def wrapper(*_, **__):
        msg = f'{func.__name__} has been deprecated.'
        raise DeprecationWarning(msg)

    return wrapper
