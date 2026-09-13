"""lab-commons — shared, project-agnostic infrastructure surface.

Tier 1 (re-exported here at top level): infrastructure that carries NO concept from any
single lab's domain -- logging (stdlib + structlog/JSON), secret redaction,
platformdirs-backed path/run-directory resolution (all parameterized by ``app_name``),
TOML file I/O, generic exceptions, the pint<->pydantic ``Annotated`` quantity machinery
(``lab_commons.units``), and -- since v0.2 -- reading what a BOX has and rationing it
(``lab_commons.proc``, ``lab_commons.resources``). Any lab building something unrelated (chemistry,
finance, ...) could use this package unchanged; a symbol naming a specific solver,
winding, optimizer, or vendor tool does not belong here (see the two-tier rule in this
repo's README).

Tier 2 (NOT imported here -- opt in explicitly): ``lab_commons.em`` holds the EM/physical
quantity vocabulary (``LengthType``, ``TorqueType``, ``Q_0Nm``, ...) shared across the
motronics/optimi-lab/wdg-lab family. Import it directly:
``from lab_commons.em import TorqueType``. Importing this package, or ``lab_commons.units``
alone, never pulls ``em`` in.

Provenance: extracted from motronics-studio's ``mylab_logging`` package (logging/paths)
and ``core/units.py`` (the maintainer-designated canonical pint design) -- the strongest
of near-identical copies duplicated across motronics-studio / optimi-lab / wdg-lab
(+3 forks).
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
from lab_commons.proc import (
    SystemMemory,
    kill_pid,
    kill_process_tree,
    pid_alive,
    process_tree,
    system_memory,
    working_set_bytes,
)
from lab_commons.resources import (
    CPU,
    DIMENSIONS,
    DISK,
    GPU,
    MEMORY,
    SEATS,
    WALLCLOCK,
    Basis,
    Broker,
    Capacity,
    CapacityRegistry,
    CeilingExceeded,
    Dimension,
    Exhausted,
    Grant,
    Holder,
    JobHandle,
    JobObservation,
    JobWatch,
    MemoryUnreadable,
)
from lab_commons.structured import (
    SecretHashingFormatter,
    bind_run_dir,
    bootstrap,
    get_structured_logger,
    hash_secret,
)
from lab_commons.units import (
    Q_,
    BaseModel_with_q,
    PydanticQuantity,
    get_quantity_type,
    pydantic_config_dict_with_q,
    ureg,
)

__all__ = [
    'CPU',
    'DIMENSIONS',
    'DISK',
    'GPU',
    'MEMORY',
    'Q_',
    'SEATS',
    'WALLCLOCK',
    'BaseModel_with_q',
    'Basis',
    'Broker',
    'Capacity',
    'CapacityRegistry',
    'CeilingExceeded',
    'Dimension',
    'Exhausted',
    'Grant',
    'Holder',
    'JobHandle',
    'JobObservation',
    'JobWatch',
    'MemoryUnreadable',
    'ParameterException',
    'PydanticQuantity',
    'QuantityException',
    'SecretHashingFormatter',
    'SystemMemory',
    'add_handle',
    'bind_run_dir',
    'bootstrap',
    'check_path',
    'config_root',
    'deprecated',
    'get_logger',
    'get_quantity_type',
    'get_structured_logger',
    'hash_secret',
    'kill_pid',
    'kill_process_tree',
    'list_files_in_dir',
    'log',
    'log_decorator',
    'not_implemented',
    'output_root',
    'pid_alive',
    'process_tree',
    'pydantic_config_dict_with_q',
    'read_toml',
    'resolve_home',
    'run_date',
    'run_output_dir',
    'run_stamp',
    'save_toml',
    'system_memory',
    'timer',
    'unique_run_dir',
    'ureg',
    'working_set_bytes',
]
