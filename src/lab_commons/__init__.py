"""lab-commons — shared, project-agnostic logging + paths surface.

Tier 1 (this package's core): infrastructure that carries NO concept from any single
lab's domain -- logging (stdlib + structlog/JSON), secret redaction, and
platformdirs-backed path/run-directory resolution, all parameterized by ``app_name``.
Any lab building something unrelated (chemistry, finance, ...) could use this package
unchanged; a symbol naming a specific solver, winding, optimizer, or vendor tool does
not belong here (see the two-tier rule in this repo's README).

Provenance: extracted from motronics-studio's ``mylab_logging`` package (itself already
``app_name``-parameterized, not motronics-specific) -- the strongest of six near-identical
copies duplicated across motronics-studio / optimi-lab / wdg-lab (+3 forks). v1
(``log.py`` / ``paths.py``) is a faithful, renamed-only extraction; v2 (``structured.py``)
adds an opt-in structlog/JSON sink and secret redaction on top of the SAME named logger
v1 attaches to -- purely additive, the v1 stdlib path is unchanged.
"""

from lab_commons.log import (
    add_handle,
    get_logger,
    log,
    log_decorator,
    timer,
)
from lab_commons.paths import (
    config_root,
    output_root,
    resolve_home,
    run_date,
    run_output_dir,
    run_stamp,
    unique_run_dir,
)
from lab_commons.structured import (
    SecretHashingFormatter,
    bind_run_dir,
    bootstrap,
    get_structured_logger,
    hash_secret,
)

__all__ = [
    'SecretHashingFormatter',
    'add_handle',
    'bind_run_dir',
    'bootstrap',
    'config_root',
    'get_logger',
    'get_structured_logger',
    'hash_secret',
    'log',
    'log_decorator',
    'output_root',
    'resolve_home',
    'run_date',
    'run_output_dir',
    'run_stamp',
    'timer',
    'unique_run_dir',
]
