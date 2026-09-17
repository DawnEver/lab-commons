"""``lab_commons.exceptions`` — generic exception classes + decorators.

Ported from motronics-studio's ``core/utils/exceptions.py`` behavior, renamed to this
package's import path. Only the project-agnostic subset lives here: ``FEMMException``
(vendor-solver domain) stays in motronics and is not tested here.
"""

from typing import Never

import pytest

from lab_commons.exceptions import (
    ParameterException,
    QuantityException,
    deprecated,
    not_implemented,
)


class TestParameterException:
    def test_str_includes_message(self) -> None:
        exc = ParameterException('bad value')
        assert str(exc) == 'ParameterException: bad value'

    def test_raises_and_is_caught_as_exception(self) -> Never:
        msg = 'bad value'
        with pytest.raises(ParameterException):
            raise ParameterException(msg)


class TestQuantityException:
    def test_str_includes_message(self) -> None:
        exc = QuantityException('bad unit')
        assert str(exc) == 'QuantityException: bad unit'

    def test_raises_and_is_caught_as_exception(self) -> Never:
        msg = 'bad unit'
        with pytest.raises(QuantityException):
            raise QuantityException(msg)


class TestNotImplementedDecorator:
    def test_wrapper_raises_not_implemented_error(self) -> None:
        @not_implemented
        def func() -> str:
            return 'never reached'

        with pytest.raises(NotImplementedError, match='func is not implemented yet'):
            func()


class TestDeprecatedDecorator:
    def test_wrapper_raises_deprecation_warning(self) -> None:
        @deprecated
        def func() -> str:
            return 'never reached'

        with pytest.raises(DeprecationWarning, match='func has been deprecated'):
            func()


if __name__ == '__main__':
    pytest.main([__file__])
