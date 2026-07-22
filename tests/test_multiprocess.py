"""``lab_commons.multiprocess`` — tier-2 (optional import) process-pool runner.

Ported from motronics-studio's behavior; a real cross-process run rather than a mock, plus
the parameter model's ``TimeType`` validation. Workers must be module-level functions
(never closures/lambdas/nested defs) so Windows' ``spawn`` start method can pickle and
re-import them in the child process -- see ``_worker_ok`` / ``_worker_boom`` below.
"""

import pytest

from lab_commons.multiprocess import (
    MultiProcessing,
    MultiProcessingParameters,
    global_mp_params,
    machine_cores,
)
from lab_commons.units import Q_


def _worker_ok(x, y):
    return {'solution_type': 'OK', 'value': x + y}


def _worker_boom(x):
    msg = f'boom {x}'
    raise ValueError(msg)


def test_machine_cores_is_a_positive_int():
    assert isinstance(machine_cores, int)
    assert machine_cores >= 1


def test_default_params_timeout_is_60s():
    assert global_mp_params.timeout.to('s').magnitude == pytest.approx(60.0)


def test_params_accept_a_time_quantity():
    params = MultiProcessingParameters(timeout=Q_(5, 'min'))
    assert params.timeout.to('s').magnitude == pytest.approx(300.0)


def test_params_field_itself_is_permissive_but_m_as_s_enforces_the_dimension():
    """`get_quantity_type`'s field validator only requires *a* pint Quantity (see
    ``test_units.py::test_pydantic_quantity_rejects_a_raw_non_quantity_at_the_class_level``
    for the documented permissiveness) -- a wrong-dimension value is accepted at
    construction and only surfaces when something actually converts it, which is exactly
    what `MultiProcessing.__init__` does via `base_mp_params.timeout.m_as('s')`.
    """
    params = MultiProcessingParameters(timeout=Q_(5, 'm'))
    with pytest.raises(Exception, match='Cannot convert'):
        params.timeout.m_as('s')


def test_run_maps_task_over_args_list_across_processes():
    """A real pool run: two tasks, two processes, each getting distinct args."""
    mp_run = MultiProcessing(
        _worker_ok,
        args_list=[[1, 2], [10, 20]],
        num_process=2,
    )
    results = mp_run.run()
    assert [r['value'] for r in results] == [3, 30]
    assert all(r['solution_type'] == 'OK' for r in results)


def test_run_accepts_kwds_list_alone():
    mp_run = MultiProcessing(
        _worker_ok,
        kwds_list=[{'x': 1, 'y': 1}, {'x': 2, 'y': 2}],
        num_process=2,
    )
    results = mp_run.run()
    assert [r['value'] for r in results] == [2, 4]


def test_run_num_process_zero_returns_empty_without_spawning():
    mp_run = MultiProcessing(_worker_ok, args_list=[[1, 2]], num_process=0)
    assert mp_run.run() == []


def test_init_raises_without_args_or_kwds():
    from lab_commons.exceptions import ParameterException

    with pytest.raises(ParameterException):
        MultiProcessing(_worker_ok)


def test_init_num_process_capped_by_task_count():
    mp_run = MultiProcessing(_worker_ok, args_list=[[1, 2]], num_process=machine_cores)
    assert mp_run._num_process == 1


def test_run_propagates_a_worker_exception_in_debug_mode():
    """`_try_get_result`'s `debug=True` branch does not catch worker errors -- documented
    behavior (not the TIMEOUT/ERROR-tagging retry path), pinned here so a future change to
    that flag is a deliberate one.
    """
    mp_run = MultiProcessing(_worker_boom, args_list=[[1]], num_process=1)
    with pytest.raises(ValueError, match='boom 1'):
        mp_run.run()
