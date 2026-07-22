"""Tier 2 (optional import) -- timeout/retry-aware process-pool runner + its parameter model.

Not project-specific itself, but tier 2 rather than tier 1 because its timeout field is a
``TimeType`` (``lab_commons.em``): importing this module pulls in the EM vocabulary module,
so it is deliberately NOT re-exported from ``lab_commons``'s top-level ``__init__`` --
that top level's whole contract is "never pulls ``em`` in". Import it explicitly:
``from lab_commons.multiprocess import MultiProcessing``.

Provenance: extracted from motronics-studio's ``core/utils/multiprocess.py`` (the
maintainer-designated canonical copy; optimi-lab's ``utils/multiprocessing.py`` is a
near-identical fork, already English-commented, used as a cross-check here). The last
plan-lab-commons-standalone.md candidate, blocked until units/exceptions/logging had
lab_commons homes -- now unblocked: every motronics dependency below resolves inside this
package (``lab_commons.units``, ``lab_commons.em``, ``lab_commons.exceptions``,
``lab_commons.log``), so the extraction is a clean re-point with no residual motronics
coupling.
"""

import multiprocessing as mp
import os
from collections.abc import Callable

from lab_commons.em import TimeType
from lab_commons.exceptions import ParameterException
from lab_commons.log import log, timer
from lab_commons.units import Q_, BaseModel_with_q

__all__ = ['MultiProcessing', 'MultiProcessingParameters', 'global_mp_params', 'machine_cores']

machine_cores = int(mp.cpu_count())


class MultiProcessingParameters(BaseModel_with_q):
    """Multiprocessing parameters.

    Args:
        num_cores(int): Number of CPU cores to use.
        num_process(int): Number of processes to create.
        timeout(TimeType): Timeout duration.
        retry_timeout(bool): Whether to retry timed-out tasks.
        retry_error(bool): Whether to retry failed tasks.
    """

    num_cores: int = machine_cores
    num_process: int = machine_cores
    timeout: TimeType = Q_(60, 's')
    retry_timeout: bool = True
    retry_error: bool = False


# Global multiprocessing parameters, used as the default `base_mp_params` for `MultiProcessing`.
global_mp_params = MultiProcessingParameters()


def default_init_worker():
    """Initialize a worker process: give it the same pint application registry as the parent.

    A pint ``Quantity`` unpickles against whatever registry is active in the process that
    unpickles it; without this, a worker's registry is a fresh default one, and any pint
    ``Quantity`` crossing the process boundary would fail to compare/operate against values
    built in the parent.
    """
    from pint import set_application_registry

    from lab_commons.units import ureg

    set_application_registry(ureg)


class MultiProcessing:
    def __init__(
        self,
        task_func: Callable,
        args_list: list[list] | None = None,
        kwds_list: list[dict] | None = None,
        base_mp_params: MultiProcessingParameters | dict | None = None,
        init_worker: Callable = default_init_worker,
        num_cores: int | None = None,
        num_process: int | None = None,
        timeout: TimeType | None = None,
        retry_timeout: bool | None = None,
        retry_error: bool | None = None,
    ) -> None:
        """Build a pool run plan for `task_func` over `args_list` / `kwds_list`.

        Args:
            task_func(Callable): The task function; accepts `*args_x` as input.
            args_list(list[list]): List of positional-args lists.
                `args_list=[[*args_1],[*args_2],...]`; each `*args_x` is passed to `task_func`.
            kwds_list(list[dict]): Optional list of keyword-arg dicts.
                `kwds_list=[{**kwds_1},{**kwds_2},...]`; each `**kwds_x` is passed to `task_func`.
            base_mp_params(MultiProcessingParameters | dict): Base multiprocessing parameters.
                Any parameter below left unset falls back to `base_mp_params`.
            init_worker(Callable): Worker initializer function.
            num_cores(int): Number of CPU cores to use.
            num_process(int): Number of processes to create.
            timeout(TimeType): Timeout duration.
            retry_timeout(bool): Retry timed-out tasks.
            retry_error(bool): Retry failed tasks.
        """
        self._task_func = task_func
        if args_list is not None:
            if kwds_list is not None:
                assert len(kwds_list) == len(args_list), 'args_list and kwds_list lengths do not match'
                args_list = [args + list(kwds.values()) for args, kwds in zip(args_list, kwds_list, strict=False)]
        elif kwds_list is not None:
            args_list = [list(kwds.values()) for kwds in kwds_list]
        else:
            msg = 'args_list and kwds_list cannot both be None'
            raise ParameterException(msg)

        self._args_list = args_list

        if base_mp_params is None:
            base_mp_params = global_mp_params
        elif isinstance(base_mp_params, dict):
            base_mp_params = MultiProcessingParameters.model_validate(base_mp_params)
        self._init_worker = init_worker

        if timeout is None:
            timeout = base_mp_params.timeout.m_as('s')
        self._timeout = timeout

        if retry_timeout is None:
            retry_timeout = base_mp_params.retry_timeout
        self._retry_timeout = retry_timeout
        if retry_error is None:
            retry_error = base_mp_params.retry_error
        self._retry_error = retry_error

        self._num_tasks = num_tasks = len(self._args_list)
        assert num_tasks > 0, 'args_list cannot be empty'
        if num_cores is None:
            num_cores = base_mp_params.num_cores
        self._num_cores = min(num_cores, machine_cores)

        if num_process is None:
            num_process = base_mp_params.num_process
        self._num_process = min(num_cores, num_process, self._num_tasks)

    def _try_get_result(self, p, *args, **kwargs):
        debug = True  # Whether to run in debug mode (surface exceptions instead of tagging them)
        timeout = self._timeout
        if debug:
            result = p.get(timeout=timeout)
            # "solution_type" is defined by task_func
        else:
            try:
                result = p.get(timeout=timeout)
                # "solution_type" is defined by task_func
            except mp.TimeoutError:
                log(msg='Task timed out.', level='WARNING')
                return {
                    'solution_type': 'TIMEOUT',
                }
            except Exception as e:
                log(msg=f'Task failed: {e}.', level='ERROR')
                return {
                    'solution_type': 'ERROR',
                }
        return result

    @timer
    def run(self) -> list[dict]:
        """Run the pool.

        Returns:
            results(list[dict]): one result dict per task, in `args_list` order
                `[{"solution_type":"ERROR",...}, {"solution_type":"ERROR",...}, ...]`.
        """
        if self._num_process == 0:
            log(msg='num_process is 0, exiting.', level='WARNING')
            return []
        with mp.Pool(processes=self._num_process, initializer=self._init_worker) as pool:
            msg = (
                f'Start multiprocessing, num_tasks: {self._num_tasks}, '
                f'num_process: {self._num_process}, num_cores: {self._num_cores}.'
            )
            log(msg, level='INFO')
            # pool.map() cannot express per-task timeout/error handling, so use apply_async
            async_processes = [pool.apply_async(self._task_func, args) for args in self._args_list]
            results = []
            for process in async_processes:
                result = self._try_get_result(process)
                if isinstance(result, dict) and 'solution_type' in result:
                    results.append(result)
                else:
                    results.append({'solution_type': 'ERROR'})

            if self._retry_timeout:
                retry_args_list = [
                    self._args_list[i] for i in range(self._num_tasks) if results[i]['solution_type'] == 'TIMEOUT'
                ]
                if retry_args_list:
                    log(msg='Retrying timed-out tasks.', level='INFO')
                    async_processes = [pool.apply_async(self._task_func, args) for args in retry_args_list]
                    retry_results = [self._try_get_result(process) for process in async_processes]
                    timeout_indices = [i for i in range(self._num_tasks) if results[i]['solution_type'] == 'TIMEOUT']
                    for idx, result in zip(timeout_indices, retry_results, strict=False):
                        if isinstance(result, dict) and 'solution_type' in result:
                            results[idx] = result
                        else:
                            results[idx] = {
                                'solution_type': 'ERROR',
                            }

            if self._retry_error:
                retry_args_list = [
                    self._args_list[i] for i in range(self._num_tasks) if results[i]['solution_type'] == 'ERROR'
                ]
                if retry_args_list:
                    log(msg='Retrying failed tasks.', level='INFO')
                    async_processes = [pool.apply_async(self._task_func, args) for args in retry_args_list]
                    retry_results = [self._try_get_result(process) for process in async_processes]
                    error_indices = [i for i in range(self._num_tasks) if results[i]['solution_type'] == 'ERROR']
                    for idx, result in zip(error_indices, retry_results, strict=False):
                        if isinstance(result, dict) and 'solution_type' in result:
                            results[idx] = result
                        else:
                            results[idx] = {
                                'solution_type': 'ERROR',
                            }

        log(msg='Multiprocessing finished.', level='DEBUG')
        return results


os.environ['NUMEXPR_MAX_THREADS'] = str(machine_cores)
