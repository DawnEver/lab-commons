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

from lab_commons.exceptions import (
    ParameterException,
    QuantityException,
    deprecated,
    not_implemented,
)
from lab_commons.file_io import (
    check_path,
    list_files_in_dir,
    read_toml,
    save_toml,
)
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
    'ParameterException',
    'QuantityException',
    'SecretHashingFormatter',
    'add_handle',
    'bind_run_dir',
    'bootstrap',
    'check_path',
    'config_root',
    'deprecated',
    'get_logger',
    'get_structured_logger',
    'hash_secret',
    'list_files_in_dir',
    'log',
    'log_decorator',
    'not_implemented',
    'output_root',
    'read_toml',
    'resolve_home',
    'run_date',
    'run_output_dir',
    'run_stamp',
    'save_toml',
    'timer',
    'unique_run_dir',
]
