"""THE PART SPLIT -- which part runs a test, declared ONCE per marker on two INDEPENDENT axes in the pyproject.

::

    [tool.lab_commons.platforms]               # the OS axis: a real OS constraint only
    com = ["windows"]                          # a marker -> the platforms that can run a test carrying it

    [tool.lab_commons.capabilities]            # the capability axis: what a test needs beyond the OS
    jmag = { engine = true }                   # an ENGINE: its ids form their own part, ``tests/jmag``
    dev_checkout = { on = ["windows", "macos"] }   # a HOST capability: the OSes its serving hosts run

A marker neither table lists runs everywhere. A test carrying several OS-axis markers runs only where
ALL of them can. Each id is ASSIGNED to exactly one part by :func:`assign`:

* an id carrying ONE engine marker goes to that engine's part, whatever else it carries; an id
  carrying two engines goes nowhere (no host is promised to provide both);
* any other id goes by THE OS RULE: the FIRST platform in :data:`PLATFORMS` order that can run it
  (linux, else windows, else macos). A host capability's ``on`` list constrains it like an OS row, so
  assignment stays a pure function of the table and the id's markers -- and a ``dev_checkout`` id never
  lands on the cluster's linux part, which a host lacking ``dev_checkout`` must stay able to claim.

A host capability creates no part; whether a host may claim a part is its card's ``provides``
(:mod:`lab_commons.dev.forgehosts`). A capability row left in ``platforms`` is REFUSED naming the fix.

The table is read AT THE COMMIT being judged (``git show <sha>:pyproject.toml``), never from the
working tree, so a part is selected by the declaration the commit itself carries. Standard library
only, like the rest of :mod:`lab_commons.hpc`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

from lab_commons.hpc.declared import kit_tables, pyproject_at

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

__all__ = [
    'CAPABILITY_ROWS',
    'LINUX',
    'PART_NAME',
    'PLATFORMS',
    'Table',
    'assign',
    'cannot_run',
    'host_platform',
    'read_table',
    'runnable',
    'table_at',
]

#: The platform names a part is named after -- what it ran ON. THE ORDER IS THE ASSIGNMENT RULE (:func:`assign`).
PLATFORMS: Final = ('linux', 'windows', 'macos')
LINUX: Final = PLATFORMS[0]

#: Rows that say what a HOST has, not what an OS is: refused on the OS axis (``platforms``).
CAPABILITY_ROWS: Final = frozenset({'dev_checkout', 'node', 'jmag'})

#: The spelling of a part (a platform or an engine) -- also what a status context and a description carry.
PART_NAME: Final = re.compile(r'^[a-z][a-z0-9_]*$')

_BY_SYS: Final = {'linux': 'linux', 'win32': 'windows', 'darwin': 'macos'}

_OS_AXIS: Final = '[tool.lab_commons.platforms]'
_CAP_AXIS: Final = '[tool.lab_commons.capabilities]'


@dataclass(frozen=True)
class Table:
    """Both axes as one commit declares them.

    ``rows`` is every constraint the OS rule reads (OS rows and host capabilities' ``on`` lists);
    ``engines`` and ``hosts`` are the capability names, sorted.
    """

    rows: dict[str, tuple[str, ...]] = field(default_factory=dict)
    engines: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()

    @property
    def parts(self) -> tuple[str, ...]:
        """Every part an id can be assigned to: the platforms, then the engines."""
        return (*PLATFORMS, *self.engines)


def host_platform(sys_platform: str) -> str:
    """The :data:`PLATFORMS` name of a ``sys.platform`` value; an unknown one is refused, never guessed."""
    try:
        return _BY_SYS[sys_platform]
    except KeyError:
        msg = f'no platform part is named for sys.platform={sys_platform!r}; known: {sorted(_BY_SYS)}'
        raise ValueError(msg) from None


def _os_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or set(value) - set(PLATFORMS):
        msg = f'{where} = {value!r}: platforms are a list drawn from {PLATFORMS}'
        raise ValueError(msg)
    return tuple(value)


def _capability(name: str, value: object) -> tuple[bool, tuple[str, ...]]:
    """``(is_engine, on)`` of one capability row."""
    where = f'{_CAP_AXIS} {name}'
    if name in PLATFORMS or not PART_NAME.match(name):
        msg = f'{where} names an OS or is not a part name ({PART_NAME.pattern}); rename the marker'
        raise ValueError(msg)
    if not isinstance(value, dict) or len(value) != 1 or not ({'engine', 'on'} & set(value)):
        msg = f'{where} = {value!r}: a row is exactly one of {{ engine = true }} or {{ on = [<platforms>] }}'
        raise ValueError(msg)
    if 'engine' in value:
        if value['engine'] is not True:
            msg = f'{where}: engine must be true'
            raise ValueError(msg)
        return True, ()
    return False, _os_list(value['on'], f'{where}.on')


def read_table(text: str) -> Table:
    """Both axes from pyproject *text*; absent is empty, a malformed or misplaced row is refused naming the fix."""
    declared = kit_tables(text)
    rows: dict[str, tuple[str, ...]] = {}
    for marker, value in declared.get('platforms', {}).items():
        if marker in CAPABILITY_ROWS:
            msg = (
                f'{_OS_AXIS} {marker} says what a HOST has, not what an OS is: move it to {_CAP_AXIS} as '
                f'{marker} = {{ on = [...] }} (or {{ engine = true }} for an engine)'
            )
            raise ValueError(msg)
        rows[marker] = _os_list(value, f'{_OS_AXIS} {marker}')
    engines: list[str] = []
    hosts: list[str] = []
    for name, value in declared.get('capabilities', {}).items():
        if name in rows:
            msg = f'{name} is declared on both axes; keep the {_CAP_AXIS} row'
            raise ValueError(msg)
        engine, on = _capability(name, value)
        if engine:
            engines.append(name)
        else:
            hosts.append(name)
            rows[name] = on
    return Table(rows=rows, engines=tuple(sorted(engines)), hosts=tuple(sorted(hosts)))


def table_at(repo: Path, sha: str) -> Table:
    """The table as commit *sha* of *repo* declares it; a commit carrying no pyproject declares none."""
    return read_table(pyproject_at(repo, sha))


def cannot_run(table: Table, part: str) -> str:
    """The marker expression selecting every test *part* cannot run; ``''`` when it can run all."""
    if part in table.engines:
        return f'not {part}'
    if part not in PLATFORMS:
        msg = f'unknown part {part!r}; known: {table.parts}'
        raise ValueError(msg)
    markers = {marker for marker, can in table.rows.items() if part not in can} | set(table.engines)
    return ' or '.join(sorted(markers))


def assign(can: Sequence[str]) -> str | None:
    """THE ASSIGNMENT RULE: the first of :data:`PLATFORMS` in *can*, else its one engine; ``None`` when none."""
    first = next((platform for platform in PLATFORMS if platform in can), None)
    if first is None and len(can) == 1:
        return can[0]
    return first


def runnable(ids: Iterable[str], cannot: Mapping[str, Collection[str]]) -> dict[str, list[str]]:
    """``{id: the parts that can run it}`` from *cannot* (part -> the ids collected by :func:`cannot_run`).

    An engine part is any key of *cannot* that is no platform; an id it can run carries that engine
    and is runnable there alone -- or nowhere, when it carries two.
    """
    engines = [part for part in cannot if part not in PLATFORMS]
    result: dict[str, list[str]] = {}
    for node in ids:
        carried = [engine for engine in engines if node not in cannot[engine]]
        if carried:
            result[node] = carried if len(carried) == 1 else []
        else:
            result[node] = [p for p in PLATFORMS if node not in cannot.get(p, ())]
    return result
