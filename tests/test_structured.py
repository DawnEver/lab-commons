"""``lab_commons.structured`` — structlog/JSON output, secret redaction, two-phase bootstrap.

Ported from motronics-studio's ``tests/unit/mylab_logging/test_structured.py``, renamed to
this package's import path. Proves (a) the structured sink emits valid JSONL with the
expected fields, (b) a record carrying a sensitive key (license key / token / fingerprint /
secret / password) never leaks the raw value — only a stable ``sha256:`` hash — through
EITHER the structlog processor chain or the stdlib ``SecretHashingFormatter``, (c)
``bootstrap()`` then ``bind_run_dir()`` attaches exactly one file handler and re-binding adds
none, and (d) the v1 stdlib path (``log.py``) is untouched by the v2 additions.
"""

import io
import json
import logging

import pytest

from lab_commons import structured
from lab_commons.log import get_logger, log, timer


@pytest.fixture(autouse=True)
def _reset_structured_state():
    """Each test gets a clean base logger + configured-apps set (module state is global)."""
    structured._configured_apps.clear()
    logger = logging.getLogger('test_structured_app')
    logger.handlers.clear()
    yield
    logger.handlers.clear()
    structured._configured_apps.clear()


class TestSecretHashing:
    def test_hash_secret_is_stable_sha256_prefixed(self):
        h1 = structured.hash_secret('super-secret-value')
        h2 = structured.hash_secret('super-secret-value')
        assert h1 == h2
        assert h1.startswith('sha256:')
        assert 'super-secret-value' not in h1

    def test_two_handlers_do_not_duplicate_extras_on_shared_record(self):
        """logging.Handler.emit() calls formatter.format() on the SAME record object for
        every handler on a logger. SecretHashingFormatter must not mutate record.msg/
        record.args, or the second handler (bind_run_dir's file sink after bootstrap's
        console sink) re-appends the extras and corrupts the line.
        """
        logger = logging.getLogger('test_structured_app')
        logger.setLevel(logging.INFO)
        buf_console, buf_file = io.StringIO(), io.StringIO()
        for buf in (buf_console, buf_file):
            handler = logging.StreamHandler(buf)
            handler.setFormatter(structured.SecretHashingFormatter('%(message)s'))
            logger.addHandler(handler)

        logger.info('event', extra={'token': 'RAWSECRET', 'user': 'alice'})

        out_console, out_file = buf_console.getvalue(), buf_file.getvalue()
        # Each sink carries the extras EXACTLY once -- not duplicated by the shared-record mutation.
        assert out_console.count("'user'") == 1
        assert out_file.count("'user'") == 1
        # And redaction still holds on both sinks.
        assert 'RAWSECRET' not in out_console and 'RAWSECRET' not in out_file
        assert 'sha256:' in out_console and 'sha256:' in out_file

    def test_extras_plus_exc_info_traceback_in_both_handlers_and_cached_once(self):
        """With BOTH extra= fields and exc_info, the traceback must appear in every
        handler's output (not dropped by the copy) and stdlib's exc_text cache must be
        preserved on the shared record so the second handler does not reformat it.
        """
        logger = logging.getLogger('test_structured_app')
        logger.setLevel(logging.INFO)
        buf_console, buf_file = io.StringIO(), io.StringIO()
        for buf in (buf_console, buf_file):
            handler = logging.StreamHandler(buf)
            handler.setFormatter(structured.SecretHashingFormatter('%(message)s'))
            logger.addHandler(handler)

        boom = 'boom-marker'
        try:
            raise ValueError(boom)  # noqa: TRY301 -- intentional raise to populate exc_info
        except ValueError:
            logger.exception('failure', extra={'token': 'RAWSECRET'})

        out_console, out_file = buf_console.getvalue(), buf_file.getvalue()
        # Traceback present in BOTH sinks, redaction intact, extras not duplicated.
        for out in (out_console, out_file):
            assert 'Traceback' in out and 'boom-marker' in out
            assert 'RAWSECRET' not in out and 'sha256:' in out
            assert out.count("'token'") == 1
        # exc_text was cached back onto the shared record (so the 2nd handler reused it).
        assert out_console.count('Traceback') == 1 and out_file.count('Traceback') == 1

    @pytest.mark.parametrize(
        'key', ['license_key', 'LICENSE_KEY', 'token', 'api_token', 'fingerprint', 'secret', 'password']
    )
    def test_redact_processor_hashes_sensitive_keys(self, key):
        event_dict = {'event': 'auth', key: 'raw-secret-value'}
        out = structured.redact_secrets_processor(None, 'info', event_dict)
        assert out[key].startswith('sha256:')
        assert 'raw-secret-value' not in out[key]

    def test_redact_processor_leaves_ordinary_fields_alone(self):
        event_dict = {'event': 'auth', 'user': 'alice'}
        out = structured.redact_secrets_processor(None, 'info', event_dict)
        assert out['user'] == 'alice'

    def test_stdlib_formatter_redacts_extra_fields(self):
        logger = get_logger('test_secret_fmt')
        logger.handlers.clear()

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(structured.SecretHashingFormatter('%(message)s'))
        logger.addHandler(handler)

        logger.info('login attempt', extra={'license_key': 'raw-secret-value'})

        output = stream.getvalue()
        assert 'raw-secret-value' not in output
        assert 'sha256:' in output


class TestStructuredJSONOutput:
    # Neither `capsys` nor `caplog` (needs propagation; our logger is deliberately
    # `propagate=False`, see log.py) reliably observes the bootstrap()-attached console
    # handler across every test runner. Attaching our OWN StringIO handler is
    # deterministic and exercises the exact processor chain bootstrap() wires up.
    def _capture_one_line(self, app_name: str, emit) -> str:
        log_ = structured.bootstrap(app_name)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(structured.SecretHashingFormatter('%(message)s'))
        get_logger(app_name).addHandler(handler)
        emit(log_)
        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        assert lines, 'expected at least one JSONL line'
        return lines[-1]

    def test_bootstrap_emits_valid_jsonl_with_expected_fields(self):
        line = self._capture_one_line('test_structured_app', lambda log_: log_.info('motor_solved', torque_nm=12.5))
        record = json.loads(line)
        assert record['event'] == 'motor_solved'
        assert record['torque_nm'] == 12.5
        assert 'level' in record
        assert 'timestamp' in record

    def test_bootstrap_redacts_secret_through_structlog_chain(self):
        line = self._capture_one_line(
            'test_structured_app', lambda log_: log_.info('license_check', license_key='raw-secret-value')
        )
        assert 'raw-secret-value' not in line
        assert 'sha256:' in line


class TestTwoPhaseBootstrap:
    def test_bind_run_dir_attaches_one_file_handler(self, tmp_path):
        structured.bootstrap('test_structured_app')
        base_logger = get_logger('test_structured_app')
        console_handlers = len(base_logger.handlers)

        structured.bind_run_dir('test_structured_app', tmp_path)
        assert len(base_logger.handlers) == console_handlers + 1

        # Re-binding (same or a different run dir) is idempotent -- no duplicate file handler.
        structured.bind_run_dir('test_structured_app', tmp_path)
        assert len(base_logger.handlers) == console_handlers + 1

    def test_bind_run_dir_writes_jsonl_file(self, tmp_path):
        log_ = structured.bootstrap('test_structured_app')
        structured.bind_run_dir('test_structured_app', tmp_path)
        log_.info('field_solved', flux_wb=0.003)

        files = list(tmp_path.glob('*.jsonl'))
        assert len(files) == 1
        lines = [line for line in files[0].read_text(encoding='utf-8').splitlines() if line.strip()]
        assert lines
        record = json.loads(lines[-1])
        assert record['event'] == 'field_solved'


class TestV1Unchanged:
    """The v1 stdlib path (log.py) is untouched by the v2 additions."""

    def test_v1_get_logger_and_log_still_work(self):
        lg = get_logger('some_v1_app')
        assert lg.propagate is False
        log('still works', level='INFO')

    def test_v1_timer_decorator_still_works(self):
        @timer
        def _fn():
            return 42

        assert _fn() == 42
