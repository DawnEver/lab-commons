"""``lab_commons.exceptions`` — generic exception classes + decorators.

Ported from motronics-studio's ``core/utils/exceptions.py`` behavior, renamed to this
package's import path. Only the project-agnostic subset lives here: ``FEMMException``
(vendor-solver domain) stays in motronics and is not tested here.
"""

import pytest

from lab_commons.exceptions import (
    ParameterException,
    QuantityException,
    deprecated,
    not_implemented,
)


class TestParameterException:
    def test_str_includes_message(self):
        exc = ParameterException('bad value')
        assert str(exc) == 'ParameterException: bad value'

    def test_raises_and_is_caught_as_exception(self):
        with pytest.raises(ParameterException):
            raise ParameterException('bad value')


class TestQuantityException:
    def test_str_includes_message(self):
        exc = QuantityException('bad unit')
        assert str(exc) == 'QuantityException: bad unit'

    def test_raises_and_is_caught_as_exception(self):
        with pytest.raises(QuantityException):
            raise QuantityException('bad unit')


class TestNotImplementedDecorator:
    def test_wrapper_raises_not_implemented_error(self):
        @not_implemented
        def func():
            return 'never reached'

        with pytest.raises(NotImplementedError, match='func is not implemented yet'):
            func()


class TestDeprecatedDecorator:
    def test_wrapper_raises_deprecation_warning(self):
        @deprecated
        def func():
            return 'never reached'

        with pytest.raises(DeprecationWarning, match='func has been deprecated'):
            func()


if __name__ == '__main__':
    pytest.main([__file__])
