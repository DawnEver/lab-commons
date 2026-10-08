"""What the cluster has free right now, and what the caller may use of it -- read from Slurm's own text.

ONE PROBE, ONE ROUND TRIP. :data:`PROBE_COMMAND` asks every question in a single shell invocation and
fences each answer with a ``@@@ <name>`` line, because over ``ssh`` each round trip costs a login and
the answers should describe the same instant. :func:`parse_snapshot` is pure over that text, so every
rule below is tested against output recorded on a real cluster rather than against a restatement.

THE SOURCES, AND WHY THESE. ``scontrol show node -o`` and ``scontrol show partition -o`` print one
``key=value`` record per line and carry the ALLOCATED amounts (``CPUAlloc``, ``AllocMem``,
``AllocTRES``) the scheduler itself plans with -- ``sinfo``'s CPU column folds idle and allocated into
one string, and ``FreeMem`` is the kernel's page cache view, not what a job may still ask for. The
quota is the association's ``GrpTRES``/``MaxTRES`` capped by the QOS's per-user ceiling, minus what the
caller's running jobs already hold; ``sacctmgr`` is where Slurm enforces it, so that is where it is read.

``sbatch --test-only`` IS NOT CONSULTED. Measured on a real cluster (2026-10-08): with ~1600 cores idle in
``defq`` it predicted an 8-core job would start in April 2028. Its estimate is a backfill horizon,
not a forecast, and a plan sized from it would be sized from noise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

__all__ = [
    'PROBE_COMMAND',
    'Node',
    'Partition',
    'Quota',
    'Snapshot',
    'parse_minutes',
    'parse_records',
    'parse_snapshot',
    'parse_tres',
]

#: Every question a plan needs, asked at once. ``--me``/``$USER`` keep it about the caller.
PROBE_COMMAND: Final = (
    'echo "@@@ nodes"; scontrol show node -o; '
    'echo "@@@ partitions"; scontrol show partition -o; '
    'echo "@@@ assoc"; sacctmgr -nP show assoc user=$USER '
    'format=account,partition,qos,defaultqos,grptres,maxtres,maxwall; '
    'echo "@@@ qos"; sacctmgr -nP show qos format=name,priority,maxwall,maxtrespu,maxjobspu,maxsubmitpu,grptres; '
    'echo "@@@ account"; sacctmgr -nP show user $USER format=defaultaccount; '
    'echo "@@@ running"; squeue -h --me -t RUNNING -o "%a|%C|%D|%m|%b"; '
    'echo "@@@ queued"; squeue -h --me -r -o "%q"'
)

#: A node state is schedulable only if its base is one of these and it carries no flag below.
_SCHEDULABLE: Final = frozenset({'IDLE', 'MIXED'})
_UNSCHEDULABLE_FLAGS: Final = frozenset({'DRAIN', 'DRAINING', 'DOWN', 'MAINT', 'RESERVED', 'NOT_RESPONDING', 'FAIL'})

#: ``key=`` at the start of a field. A value may itself contain spaces (``OS=Linux 4.18 #1 SMP ...``),
#: so a record is cut at keys, never at whitespace.
_KEY: Final = re.compile(r'(?:^|\s)([A-Za-z][\w:/.-]*)=')
_MEM_UNIT: Final = {'K': 1 / 1024, 'M': 1, 'G': 1024, 'T': 1024 * 1024}
_MEM: Final = re.compile(r'^(\d+(?:\.\d+)?)([KMGT]?)$')
_GRES_COUNT: Final = re.compile(r'^gpu(?::[^:(]+)*:(\d+)')


def parse_records(text: str) -> list[dict[str, str]]:
    """``scontrol -o`` lines into dicts. A value runs to the next ``key=``, spaces included."""
    records = []
    for line in text.splitlines():
        keys = list(_KEY.finditer(line))
        if not keys:
            continue
        record = {}
        for here, after in zip(keys, [*keys[1:], None], strict=True):
            end = after.start() if after else len(line)
            record[here.group(1)] = line[here.end() : end].strip()
        records.append(record)
    return records


def parse_minutes(text: str) -> float | None:
    """A Slurm duration (``D-HH:MM:SS``, ``HH:MM:SS``, ``MM:SS``, ``MM``) in minutes; unlimited is ``None``."""
    text = text.strip()
    if text in {'', 'UNLIMITED', 'INFINITE', 'NONE', 'n/a'}:
        return None
    days, _, clock = text.rpartition('-')
    parts = [float(p) for p in clock.split(':')]
    hours, minutes, seconds = {1: (0, parts[0], 0), 2: (0, *parts), 3: tuple(parts)}[len(parts)]
    return (float(days or 0) * 24 + hours) * 60 + minutes + seconds / 60


def _megabytes(text: str) -> float:
    """``360G``/``773700M``/``773700`` in MB -- Slurm's unit-less memory is megabytes."""
    match = _MEM.match(text.strip())
    if not match:
        msg = f'not a Slurm memory amount: {text!r}'
        raise ValueError(msg)
    return float(match.group(1)) * _MEM_UNIT[match.group(2) or 'M']


def parse_tres(text: str) -> dict[str, float]:
    """``cpu=96,mem=360G,gres/gpu=2`` into ``{'cpu': 96, 'mem': 368640, 'gpu': 2}``; memory in MB.

    Only the three resources a plan sizes by are kept. A typed GPU (``gres/gpu:a100-full=1``) is a
    narrower ceiling than the untyped one and is not what a plain ``--gres=gpu:N`` request counts against.
    """
    tres: dict[str, float] = {}
    for item in filter(None, text.split(',')):
        key, _, value = item.partition('=')
        if key == 'cpu':
            tres['cpu'] = float(value)
        elif key == 'mem':
            tres['mem'] = _megabytes(value)
        elif key == 'gres/gpu':
            tres['gpu'] = float(value)
    return tres


@dataclass(frozen=True)
class Node:
    """One node's FREE resources -- configured minus allocated -- and whether it can take a job at all."""

    name: str
    partitions: tuple[str, ...]
    cpus: int
    mem_mb: float
    gpus: int
    schedulable: bool


@dataclass(frozen=True)
class Partition:
    """A partition's time ceiling (``None`` = unlimited) and whether it is accepting jobs."""

    name: str
    max_minutes: float | None
    up: bool
    allow_qos: tuple[str, ...] = ()


@dataclass(frozen=True)
class Quota:
    """The caller's CEILING (``None`` = none) and what their running jobs already hold of it.

    Both are kept because they answer different questions. The ceiling is what Slurm enforces, so it
    bounds how many tasks may ever run at once; what is held only delays them -- Slurm pends a task over
    the limit (``QOSMaxCpuPerUserLimit``) and starts it when the caller's earlier jobs end.
    """

    account: str
    cpus: float | None
    mem_mb: float | None
    gpus: float | None
    default_qos: str = ''
    qos_minutes: dict[str, float | None] = field(default_factory=dict)
    used: dict[str, float] = field(default_factory=dict)
    qos_max_submit: dict[str, int | None] = field(default_factory=dict)
    qos_max_jobs: dict[str, int | None] = field(default_factory=dict)
    queued: dict[str, int] = field(default_factory=dict)

    def submit_room(self, qos: str) -> int | None:
        """How many more jobs -- each ARRAY TASK counts as one -- *qos* accepts from the caller now.

        Measured on a real cluster (2026-10-08): the ``dev`` QOS caps a user at 4 submitted jobs, and a 10-task array
        was refused whole with ``QOSMaxSubmitJobPerUserLimit``. ``None`` is no cap.
        """
        cap = self.qos_max_submit.get(qos)
        return None if cap is None else max(0, cap - self.queued.get(qos, 0))

    def remaining(self) -> dict[str, float | None]:
        """Ceiling minus held, per dimension name of :func:`parse_tres` (``cpu``, ``mem`` in MB, ``gpu``)."""
        ceilings = {'cpu': self.cpus, 'mem': self.mem_mb, 'gpu': self.gpus}
        return {k: None if v is None else max(0.0, v - self.used.get(k, 0.0)) for k, v in ceilings.items()}


@dataclass(frozen=True)
class Snapshot:
    """The cluster at one instant, as far as a plan needs it."""

    nodes: tuple[Node, ...]
    partitions: dict[str, Partition]
    quota: Quota


def _node(record: dict[str, str]) -> Node:
    """Free = configured - allocated - the memory Slurm reserves for the node's own daemons."""
    total_cpus = int(record.get('CPUEfctv') or record['CPUTot'])
    alloc = parse_tres(record.get('AllocTRES', ''))
    spec = float(record.get('MemSpecLimit', '0') or 0)
    mem = float(record['RealMemory']) - spec - float(record.get('AllocMem', '0'))
    gres = (record.get('Gres') or '').split(',')
    gpu_total = sum(int(m.group(1)) for g in gres if (m := _GRES_COUNT.match(g)))
    state = set(re.split(r'[+]', record.get('State', '').rstrip('*').upper()))
    schedulable = (
        bool(state & _SCHEDULABLE) and not state & _UNSCHEDULABLE_FLAGS and not record.get('State', '').endswith('*')
    )
    return Node(
        name=record['NodeName'],
        partitions=tuple(filter(None, record.get('Partitions', '').split(','))),
        cpus=max(0, total_cpus - int(record.get('CPUAlloc', '0'))),
        mem_mb=max(0.0, mem),
        gpus=max(0, gpu_total - int(alloc.get('gpu', 0))),
        schedulable=schedulable,
    )


def _partition(record: dict[str, str]) -> Partition:
    allow = record.get('AllowQos', 'ALL')
    return Partition(
        name=record['PartitionName'],
        max_minutes=parse_minutes(record.get('MaxTime', '')),
        up=record.get('State', 'UP') == 'UP',
        allow_qos=() if allow == 'ALL' else tuple(allow.split(',')),
    )


def _sections(text: str) -> dict[str, str]:
    """Cut :data:`PROBE_COMMAND`'s output at its ``@@@ <name>`` fences."""
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith('@@@ '):
            current = sections.setdefault(line[4:].strip(), [])
        elif current is not None:
            current.append(line)
    return {name: '\n'.join(lines) for name, lines in sections.items()}


def _rows(text: str) -> list[list[str]]:
    return [line.split('|') for line in text.splitlines() if line.strip()]


def _min(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def _counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in filter(None, (raw.strip() for raw in text.splitlines())):
        counts[line] = counts.get(line, 0) + 1
    return counts


def _quota(sections: dict[str, str], account: str) -> Quota:
    """The association ceiling, narrowed by its default QOS's per-user ceiling, minus running jobs."""
    assoc = {row[0]: row for row in _rows(sections.get('assoc', ''))}
    account = account or next(iter(_rows(sections.get('account', ''))), [''])[0] or next(iter(assoc), '')
    row = assoc.get(account, [account, '', '', '', '', '', ''])
    grp, cap = parse_tres(row[4]), parse_tres(row[5])
    qos_rows = {r[0]: r for r in _rows(sections.get('qos', ''))}
    default_qos = row[3]
    per_user = parse_tres(qos_rows[default_qos][3]) if default_qos in qos_rows else {}
    used = {'cpu': 0.0, 'mem': 0.0, 'gpu': 0.0}
    for job in _rows(sections.get('running', '')):
        if job[0] != account:
            continue
        used['cpu'] += float(job[1])
        used['mem'] += _megabytes(job[3]) * float(job[2]) if job[3] not in {'', '0'} else 0.0
        gres = _GRES_COUNT.match(job[4].removeprefix('gres:').removeprefix('gres/'))
        used['gpu'] += float(gres.group(1)) if gres else 0.0

    def ceiling(key: str) -> float | None:
        return _min(grp.get(key), cap.get(key), per_user.get(key))

    return Quota(
        account=account,
        cpus=ceiling('cpu'),
        mem_mb=ceiling('mem'),
        gpus=ceiling('gpu'),
        default_qos=default_qos,
        qos_minutes={name: _min(parse_minutes(r[2]), parse_minutes(row[6])) for name, r in qos_rows.items()},
        used=used,
        qos_max_submit={name: int(r[5]) if r[5] else None for name, r in qos_rows.items()},
        qos_max_jobs={name: int(r[4]) if r[4] else None for name, r in qos_rows.items()},
        queued=_counts(sections.get('queued', '')),
    )


def parse_snapshot(text: str, account: str = '') -> Snapshot:
    """The output of :data:`PROBE_COMMAND` as a :class:`Snapshot`. *account* overrides the default one."""
    sections = _sections(text)
    for required in ('nodes', 'partitions', 'assoc'):
        if not sections.get(required, '').strip():
            msg = f'the probe returned no {required!r} section -- an unread cluster is not an empty one'
            raise ValueError(msg)
    partitions = {p.name: p for p in map(_partition, parse_records(sections['partitions']))}
    return Snapshot(
        nodes=tuple(map(_node, parse_records(sections['nodes']))),
        partitions=partitions,
        quota=_quota(sections, account),
    )
