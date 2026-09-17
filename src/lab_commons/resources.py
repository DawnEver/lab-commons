"""Tier 1 -- what this box rations, declared as data and enforced ACROSS PROCESSES.

The GENERIC half of the design in motronics-studio's ``docs-src/dev/compute-resources.md``.
Nothing here knows what a vendor tool is: a consumer declares the POOLS and the VALUES, and this
module owns the dimensions, the bookkeeping, the admission and the running ceiling.

WHY IT IS HERE AND NOT IN A CONSUMER. Seven mechanisms in that repo enforced something
independently -- six in Python, one in shell -- sharing a discipline and almost no code, and three
of them were mutually unaware while reading the SAME syscall through two duplicated structs. The
reason they could not be unified in place is a layering rule: the dev tree may not import
production code, so a seventh mechanism in either tree is unreachable from the other. An INSTALLED
package is importable from both, and from every worktree on the box regardless of which revision
each has checked out -- which structurally closes the "a stale checkout silently weakens the
box-global ceiling" hazard instead of closing it by policy.

TIER 1, and the test is the package's own contract: this module imports ``lab_commons.proc`` and
stdlib, and nothing else. It needs BYTES and SECONDS, not ``em``'s ``LengthType``/``TimeType``, so
unlike ``multiprocess.py`` it does not pull the EM vocabulary in and is safe to re-export from the
top level. Plain ``int`` bytes are also what the OS hands back and what a ``ctypes`` reader
returns; wrapping them in pint here would put a conversion between a syscall and a comparison, on
the one path whose job is to be cheap enough to poll.

THE FOUR AXES IT IMPLEMENTS
    1. Resource DIMENSIONS are registry data, not branches on a kind (:data:`DIMENSIONS`) -- and
       each names its SCOPE, the pool's own stock or one the whole BOX contends for
       (:class:`Scope`). A pool is a key and a key is a string, so "one at a time, everywhere" is
       not a fact a shared pool NAME can carry; a box-scoped dimension is how it becomes one.
    2. A job DECLARES its cost; it does not request a slot (:meth:`Broker.admit` takes *demands*).
       Admission is a per-dimension headroom check against capacity minus what live peers claim,
       which is what lets four small jobs and two large ones be told apart -- a count never can.
    3. Exhaustion has THREE outcomes: a BOUNDED queue, a readable refusal naming the holder, and a
       CEILING ON THE RUNNING JOB (:meth:`Grant.enforce_ceiling`).
    4. A per-box MEASUREMENT and an everywhere-identical STRUCTURAL CONSTANT are different KINDS
       (:class:`Basis`). The registry models both; declaring a structural fact per-box is what
       makes it RAISE on every machine nobody remembered to declare.

A SEAT FILE IS CREATED WITH ITS RECORD OR NOT AT ALL (:meth:`Broker._take_indexed`). The indexed
file IS the exclusion, so a peer reads it to learn who is there -- which means a seat that exists
and cannot be read yet is a HOLDER, never a free index. Measured 2026-09-15: with the record
written after the exclusive create, the seat was empty for that instant, `_holder_of` read the
empty file as absent, and a peer DELETED a live seat and took its index -- two holders on a lock
that admits one.

THE UNMEASURED BOX GETS A CONSERVATIVE DEFAULT PLUS FORCED VISIBILITY (user decision 2026-09-13),
re-arguing the three positions the origin tree held at once -- one mechanism REFUSED, one defaulted
to a proven width, one defaulted to cpu-only with its own docstring admitting "the memory
constraint disappears".

    - CONSERVATIVE means the direction that CANNOT crash the box: serialise, minimum width, one
      job, a floor that is a share of TOTAL. Never the permissive direction, and never dropping a
      dimension entirely -- an unmeasured ceiling is a ceiling nobody measured, not an absent one.
    - VISIBLE cannot be a log warning. ``taste.md``: "a warning on an unchanged success return is
      the forbidden shape -- the test is whether the CALLER can tell." So the un-measured fact
      travels in the RETURN VALUE: :attr:`Grant.basis` says per dimension what the ceiling rested
      on, and :attr:`Grant.conservative` / :attr:`Grant.is_fully_declared` let a caller, a gate and
      a report all tell without parsing prose.
    - A CEILING HELD AT THE CONSERVATIVE VALUE AND A CEILING NOBODY APPLIED ARE DIFFERENT FACTS,
      and :attr:`Grant.unbounded` is the second one. Collapsing them made ``conservative`` itself a
      declaration that lies (see :class:`Enforcement`), which is the defect this module spends
      most of its length refusing elsewhere.

WHAT IT DOES NOT DECIDE. Cross-MACHINE arbitration is deliberately out of scope; every mechanism
here is per-box, and the box is the thing that crashes.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final

from lab_commons._records import publish as _publish
from lab_commons._records import record as _record
from lab_commons._records import stage as _stage
from lab_commons._records import unpublish as _unpublish
from lab_commons.liveness import CreationClock, creation_stamp, still_the_same_process
from lab_commons.proc import SystemMemory, kill_process_tree, pid_alive, system_memory, working_set_bytes

__all__ = [
    'BOX_SEATS',
    'CONSERVATIVE_MEMORY_SHARE',
    'CPU',
    'DEFAULT_POLL_S',
    'DIMENSIONS',
    'DISK',
    'GPU',
    'MEMORY',
    'RESOURCE_DIR_ENV',
    'SEATS',
    'WALLCLOCK',
    'Accounting',
    'Basis',
    'Broker',
    'Capacity',
    'CapacityRegistry',
    'CeilingExceeded',
    'Dimension',
    'Enforcement',
    'Exhausted',
    'Grant',
    'Holder',
    'JobHandle',
    'JobObservation',
    'JobWatch',
    'MemoryUnreadable',
    'Scope',
    'SystemMemory',
]


# --------------------------------------------------------------------------------------------
# Axis 1 -- the dimensions, as data
# --------------------------------------------------------------------------------------------


class Accounting(Enum):
    """HOW a dimension is depleted, which is what decides how it is enforced.

    The three kinds behave differently enough that branching on the dimension NAME would be the
    vendor-branch defect one level up: ask which registry owns the decision.
    """

    #: Depleted by holders we count ourselves -- seats, cores. Enforced by the record files.
    COUNTED = 'counted'
    #: Depleted by every process on the box, ours or not, and read from the OS -- memory. The OS
    #: reading ALREADY subtracts every live process, so this is cross-process by construction and
    #: sees a job this broker never admitted.
    BOX_GLOBAL = 'box_global'
    #: Not a stock at all but a duration -- wall-clock. Recorded, never summed across holders.
    ELAPSED = 'elapsed'


class Scope(Enum):
    """WHOSE contention a dimension rations: one pool's, or every pool on this box.

    A POOL IS A KEY AND A KEY IS A STRING, which is the whole reason this exists. The broker
    rations per pool, so two consumers that name different pools never meet -- and the broker
    cannot tell them so, because nothing in a name says whether the author MEANT a smaller
    namespace or was opting out of the bigger one. A dimension that declares its scope as BOX is
    the mechanism that does not depend on anyone's spelling: its records are one set per box, and
    every pool demanding it lands on them.
    """

    #: Rationed per pool: two pools do not contend, which is what a pool is FOR. The pool's name is
    #: the namespace its record files live in.
    POOL = 'pool'
    #: Rationed for the whole box: the record files are named by the DIMENSION, so every pool that
    #: demands it contends with every other, whatever string each one calls itself.
    BOX = 'box'


class Enforcement(Enum):
    """WHAT actually bounds a dimension at admission.

    Not the same question as how it depletes, and conflating the two is how a dimension came to be
    declared ``COUNTED`` while nothing counted it.

    :data:`NONE` is the load-bearing member. A dimension can be perfectly well DEFINED and still
    have nothing able to check it, and saying so is the whole difference between a ceiling held at
    a conservative value and a ceiling nobody looked at. Both used to surface identically in
    :attr:`Grant.conservative`, which made that attribute a declaration that lies: measured
    2026-09-13, a demand of ninety-nine CORES was admitted on a box whose conservative cpu ceiling
    is one, and the grant reported cpu as "conservative".
    """

    #: Bounded by summing the LIVE reservation records on this box -- how cores are counted.
    RECORDS = 'records'
    #: Bounded by an EXCLUSIVE record file per unit in an indexed range -- seats, box seats. The OS
    #: arbitrates the race (a create that fails while the name is taken), so the count is EXACT
    #: rather than checked and then acted on, and the file the OS refuses for is the holder's own
    #: record -- which is what makes "unreadable" a state this module can refuse to resolve.
    INDEXED = 'indexed'
    #: Bounded by a reading of the box itself, which already counts processes we never admitted.
    OS_READING = 'os_reading'
    #: NOTHING bounds it here. The demand is recorded and reported in :attr:`Grant.unbounded`, and
    #: the caller owns the fact. Never silently believed.
    NONE = 'none'


@dataclass(frozen=True, slots=True)
class Dimension:
    """One thing a box can run out of. DATA, so a consumer adds a value without a code change."""

    name: str
    unit: str
    accounting: Accounting
    enforcement: Enforcement
    #: The value an UNMEASURED box gets: the direction that cannot crash it. ``None`` exactly when
    #: :attr:`enforcement` is :data:`Enforcement.NONE` -- there is no conservative value to hold a
    #: dimension at when nothing can check it. That correspondence is not a convention here: it is
    #: asserted at import, below, so the narrowings that depend on it cannot silently stop holding.
    conservative: int | None
    note: str = ''
    #: WHOSE contention this rations, and therefore where its record files live. See :class:`Scope`.
    scope: Scope = Scope.POOL


SEATS: Final = Dimension(
    'seats',
    'count',
    Accounting.COUNTED,
    Enforcement.INDEXED,
    conservative=1,
    note='concurrent jobs, or licence seats. An unmeasured box SERIALISES: one at a time.',
)
#: The BOX's seat: one CPU-saturating run at a time, everywhere on this machine.
#:
#: WHY A DIMENSION AND NOT A SHARED POOL NAME. "Box-wide" was previously a property of every caller
#: passing the SAME string -- so a consumer that named its own pool silently opted out of
#: contending, and nothing in the broker could tell it so. A pool-scoped dimension cannot fix that
#: (its records are the pool's own by construction); this one can, because it is not the pool's:
#: its record files are named by the dimension, one set per box, and any pool that demands it meets
#: every other. The scope is the mechanism; the pool name stops being one.
BOX_SEATS: Final = Dimension(
    'box_seats',
    'count',
    Accounting.COUNTED,
    Enforcement.INDEXED,
    conservative=1,
    note='the whole box, one at a time, for every pool that demands it.',
    scope=Scope.BOX,
)
MEMORY: Final = Dimension(
    'memory',
    'bytes',
    Accounting.BOX_GLOBAL,
    Enforcement.OS_READING,
    conservative=1,
    note='resident bytes. An unmeasured box demands CONSERVATIVE_MEMORY_SHARE of TOTAL be free.',
)
CPU: Final = Dimension(
    'cpu',
    'cores',
    Accounting.COUNTED,
    Enforcement.RECORDS,
    conservative=1,
    note='cores a job expects to saturate. An unmeasured box grants the minimum width, never a guess.',
)
#: WALLCLOCK is ENFORCEMENT.NONE and that is not an omission. It is a DURATION, not a stock: it is
#: never summed across holders, so there is nothing for admission to compare a demand against. It
#: is recorded -- a consumer can hand it to a timeout -- and reported in :attr:`Grant.unbounded`
#: so nobody reads its presence in the registry as a bound this module is applying.
WALLCLOCK: Final = Dimension(
    'wallclock',
    'seconds',
    Accounting.ELAPSED,
    Enforcement.NONE,
    conservative=None,
    note='how long the job expects to run. RECORDED, never enforced here: a duration is not a stock.',
)
#: DISK and GPU have the SHAPE and no values, deliberately. There is no portable stdlib reader for
#: either that would answer on every box in this family, and a dimension with a reader that works
#: on SOME machines is the silent-degradation defect this module exists to refuse. Declaring them
#: means a consumer can demand them and SEE, in :attr:`Grant.unbounded`, that nothing bounded them
#: -- which is strictly better than the demand being a ``KeyError`` or, worse, silently believed.
#: DECLARING A VALUE FOR THEM DOES NOT MAKE THEM BOUNDED: a number nobody can check is not a
#: ceiling, and letting a declaration flip the flag would move the same lie one level up.
DISK: Final = Dimension(
    'disk', 'bytes', Accounting.BOX_GLOBAL, Enforcement.NONE, conservative=None, note='shape only; no reader'
)
GPU: Final = Dimension(
    'gpu', 'count', Accounting.COUNTED, Enforcement.NONE, conservative=None, note='shape only; no reader'
)

DIMENSIONS: Final[Mapping[str, Dimension]] = {
    dimension.name: dimension for dimension in (SEATS, BOX_SEATS, MEMORY, CPU, WALLCLOCK, DISK, GPU)
}


def _conservative_floor(dimension: Dimension) -> int:
    """The conservative value of an ENFORCED dimension, as a plain ``int``.

    THIS IS THE NARROWING, AND IT IS NOT A FALLBACK. ``Dimension.conservative`` is ``int | None``
    because the shape-only dimensions genuinely have no value, which is what made a type checker
    flag every site that reached ``int(...)`` or ``min(key=)`` through it. Rather than suppressing
    the checker or defaulting the miss away, the correspondence it was complaining about --
    enforced implies valued -- is CHECKED HERE, at import, for every dimension at once.

    So a future dimension added with :data:`Enforcement.RECORDS` and no conservative value fails
    at import rather than at the first admission on an unmeasured box, and the call sites below get
    an ``int`` the type system agrees is an ``int``. ``.claude/rules/taste.md``: if a miss falls
    back, remove the FALLBACK.
    """
    value = dimension.conservative
    if value is None:
        msg = (
            f'{dimension.name} declares enforcement={dimension.enforcement.value} but no conservative '
            f'value, so an unmeasured box would have nothing to hold it at. An enforced dimension '
            f'MUST name the value that cannot crash the box; a dimension with no such value is '
            f'Enforcement.NONE and is reported in Grant.unbounded instead.'
        )
        raise ValueError(msg)
    return value


def _check_dimension_table(dimensions: Iterable[Dimension]) -> None:
    """Assert enforced-implies-valued, BOTH WAYS, over the whole table. Run at import.

    A RATCHET HAS TWO SIDES. Forwards: a dimension enforced with no conservative value would leave
    an unmeasured box nothing to hold it at, and is what makes the narrowings below unreachable
    rather than merely unlikely. Backwards: a dimension nothing enforces must NOT carry a
    conservative value, because a value in the table reads as a ceiling this module applies -- and
    that reading is exactly the defect :class:`Enforcement` was added to end.

    Taking an argument rather than closing over :data:`DIMENSIONS` is what lets the guard itself be
    tested on a planted table, instead of being a loop nobody can drive.
    """
    for dimension in dimensions:
        if dimension.enforcement is not Enforcement.NONE:
            _conservative_floor(dimension)
        elif dimension.conservative is not None:
            msg = (
                f'{dimension.name} is enforced by nothing yet declares conservative='
                f'{dimension.conservative}, which reads as a ceiling this module applies and does not.'
            )
            raise ValueError(msg)


_check_dimension_table(DIMENSIONS.values())


def _namespaced(pool: str, dimension: Dimension) -> str:
    """The name an INDEXED dimension's record files are built from -- the pool, or the dimension.

    The record directory IS the box (see :meth:`Broker.resource_dir`), so a box-scoped dimension
    needs no box in its name and a pool-scoped one needs no dimension in it:

    | scope | a seat file | whose contention |
    |---|---|---|
    | ``POOL`` | ``{pool}.{dimension}.{index}.slot`` | that pool's alone |
    | ``BOX`` | ``{dimension}.{index}.slot`` | every pool's |

    THE TWO SHAPES CANNOT COLLIDE, and that is by construction rather than by luck: a pool-scoped
    name has four dot-separated parts and a box-scoped one has three, so no pool name -- not even
    one spelled like a dimension -- can produce another's file. That is what makes the box namespace
    safe to allocate without a registry of reserved words.
    """
    return f'{pool}.{dimension.name}' if dimension.scope is Scope.POOL else dimension.name


#: What share of TOTAL physical RAM must be free before an UNMEASURED pool may start.
#:
#: HALF, and it is a ceiling on ignorance rather than a prediction of any job. The thing being
#: refused is the opposite default -- falling back to a cpu-only pool size so that, in the origin
#: tree's own docstring, "the memory constraint disappears". A box that has never been measured
#: cannot say a job is small, so the only safe reading of an unmeasured job is "it could take
#: half this machine", and two of them at once is the crash. Paired with a conservative seat count
#: of 1, this also means an unmeasured pool never has a peer to contend with.
CONSERVATIVE_MEMORY_SHARE: Final = 0.5

#: Seconds between readings while queueing. A job releases its memory in one step when it exits,
#: so polling faster buys nothing and polling slower delays every admission by half a period.
DEFAULT_POLL_S: Final = 5.0

#: Relocates the record root. Two callers need it and neither is a workaround. TESTS: the gate is
#: deliberately BOX-GLOBAL, so a suite exercising it contends with the real records and, under
#: xdist, with its own other workers; pointing each worker at its own root is the only isolation
#: available, because "global" is the property under test and cannot be mocked away without
#: testing nothing. CONTAINERS: a box whose temp directory is not shared between the processes
#: that must agree needs to say where the shared one is.
RESOURCE_DIR_ENV: Final = 'LAB_COMMONS_RESOURCE_DIR'

_BYTES_PER_MB = 1024 * 1024


# --------------------------------------------------------------------------------------------
# Axis 4 -- a measurement and a structural constant are different kinds
# --------------------------------------------------------------------------------------------


class Basis(Enum):
    """WHAT a ceiling rests on. The value that makes an unmeasured box VISIBLE to its caller."""

    #: Measured on ONE box and true of that box only. Carries ``measured_on``; a copy found on a
    #: different machine is refused rather than believed.
    MEASURED = 'measured'
    #: Identical on EVERY box -- a property of the tool, not of the machine. Carries no hostname,
    #: because declaring it per-box is what makes it raise on a machine nobody declared.
    STRUCTURAL = 'structural'
    #: Nobody measured this box. The grant still happened, at the value that cannot crash it.
    CONSERVATIVE_DEFAULT = 'conservative_default'


@dataclass(frozen=True, slots=True)
class Capacity:
    """How much of one dimension a pool may have on a box, AND on what that number rests.

    The two halves are inseparable on purpose. A bare integer cannot say whether it was measured
    here, inherited from another machine, or invented -- and the honest-looking repair when it
    disagrees with reality is to edit the digit.
    """

    dimension: str
    value: int | None
    basis: Basis
    measured_on: str | None = None
    note: str = ''

    def __post_init__(self) -> None:
        """Refuse an undeclared dimension, at construction."""
        if self.dimension not in DIMENSIONS:
            msg = f'{self.dimension!r} is not a declared dimension; known: {sorted(DIMENSIONS)}'
            raise KeyError(msg)
        if self.basis is Basis.MEASURED and not self.measured_on:
            msg = (
                f'a MEASURED capacity must name the box it was measured on (measured_on=), because '
                f"a measurement copied to another machine is a guess wearing a measurement's clothes. "
                f'If {self.dimension!r} is identical on every box, it is Basis.STRUCTURAL.'
            )
            raise ValueError(msg)
        if self.basis is Basis.STRUCTURAL and self.measured_on:
            msg = (
                f'a STRUCTURAL capacity holds on every box, so it must NOT name one '
                f'(measured_on={self.measured_on!r}). Declaring an everywhere-identical fact per-box '
                f'is what makes it unavailable on every machine nobody remembered to declare.'
            )
            raise ValueError(msg)

    @classmethod
    def measured(cls, dimension: str, value: int, *, on: str, note: str = '') -> Capacity:
        """A per-box measurement. *on* is the hostname it was measured on."""
        return cls(dimension=dimension, value=int(value), basis=Basis.MEASURED, measured_on=on, note=note)

    @classmethod
    def structural(cls, dimension: str, value: int, *, note: str = '') -> Capacity:
        """A fact identical on every box -- a property of the tool, not of the machine."""
        return cls(dimension=dimension, value=int(value), basis=Basis.STRUCTURAL, note=note)


class CapacityRegistry:
    """What each pool may have, per dimension. The declaration the broker MUST consult.

    A DECLARATION WITH NO CONSUMER IS THE SAME DEFECT CLASS AS A DOCSTRING THAT LIES, which is why
    the broker reads this and there is no path around it.

    It holds NO values of its own: a consumer declares them, from wherever it keeps per-box
    measurements -- the origin tree's discipline is a git-ignored local file carrying
    ``measured_on``, with no value and no hostname committed to the repo, and that discipline is
    preserved by :class:`Capacity` rather than re-invented here.
    """

    def __init__(self, declarations: Iterable[tuple[str, Capacity]] = ()) -> None:
        """Build the registry, declaring each ``(pool, capacity)`` pair given."""
        self._table: dict[tuple[str, str], list[Capacity]] = {}
        for pool, capacity in declarations:
            self.declare(pool, capacity)

    def declare(self, pool: str, capacity: Capacity) -> None:
        """Add a ceiling for *pool*. Several may be declared for one dimension; see :meth:`capacity`."""
        self._table.setdefault((pool, capacity.dimension), []).append(capacity)

    def capacity(self, pool: str, dimension: str, *, hostname: str | None = None) -> Capacity:
        """The BINDING ceiling for *pool* in *dimension* on *hostname*. NEVER raises for a missing one.

        SEVERAL DECLARATIONS COMPOSE BY TAKING THE SMALLEST, because every one of them is a
        ceiling and a job must satisfy all of them. That is what lets a structural constant
        override a larger per-box measurement: a box measured at four seats for a tool whose
        sessions ATTACH rather than start clean still gets one, and neither declaration has to
        know about the other.

        A MEASUREMENT FROM ANOTHER BOX IS REFUSED, NOT BELIEVED -- and refusing it means falling
        to the conservative default, not raising, so the machine still runs and the caller can see
        in :attr:`Basis` that nobody measured it.

        A BOX-SCOPED DIMENSION IS DECLARED FOR THE BOX, so the *pool* argument does not narrow it:
        every pool's declarations for it compose, and a pool that declares it cannot declare its
        way out of what another pool declared. Keying a box-scoped declaration by the pool would
        put the whole mechanism back on the string it exists to replace -- the running job would
        contend box-wide while the ceiling it was judged by came from one pool's name.
        """
        if dimension not in DIMENSIONS:
            msg = f'{dimension!r} is not a declared dimension; known: {sorted(DIMENSIONS)}'
            raise KeyError(msg)
        here = hostname or _hostname()
        declared = self._declared(pool, dimension)
        # PAIRED WITH THE NARROWED VALUE rather than filtered and then re-read. `min(key=lambda c:
        # c.value)` over a `Capacity` whose `value` is `int | None` is a TypeError waiting for the
        # first valueless declaration, and a filter two lines earlier does not tell a type checker
        # -- or the next reader -- that it cannot happen. Carrying the narrowed `int` INTO the
        # comparison makes the impossibility structural rather than incidental.
        applicable: list[tuple[int, Capacity]] = [
            (candidate.value, candidate)
            for candidate in declared
            if candidate.value is not None and (candidate.basis is Basis.STRUCTURAL or candidate.measured_on == here)
        ]
        if applicable:
            return min(applicable, key=lambda pair: pair[0])[1]
        return _conservative(dimension, pool=pool, hostname=here, rejected=declared)

    def _declared(self, pool: str, dimension: str) -> list[Capacity]:
        """Every declaration that can bind *pool* in *dimension*.

        Pool-scoped is the ordinary case: exactly one key. Box-scoped gathers the dimension's
        declarations from EVERY pool, in a deterministic order so that two equally-small ceilings
        resolve to the same one on every box.
        """
        if DIMENSIONS[dimension].scope is Scope.BOX:
            found: list[Capacity] = []
            for key in sorted(self._table):
                if key[1] == dimension:
                    found.extend(self._table[key])
            return found
        return self._table.get((pool, dimension), [])


def _conservative(dimension: str, *, pool: str, hostname: str, rejected: Iterable[Capacity]) -> Capacity:
    """The ceiling an UNMEASURED box gets, saying in its own note why it is the one in force."""
    subject = _namespaced(pool, DIMENSIONS[dimension])
    elsewhere = sorted({candidate.measured_on for candidate in rejected if candidate.measured_on})
    if elsewhere:
        why = (
            f'{subject} was measured on {", ".join(elsewhere)} and NOT on {hostname}. A '
            f'measurement is a statement about one machine, so the value is not carried across; '
            f'measure this box to lift the conservative default.'
        )
    else:
        why = f'{subject} has never been measured on {hostname}; running at the value that cannot crash it.'
    return Capacity(
        dimension=dimension, value=DIMENSIONS[dimension].conservative, basis=Basis.CONSERVATIVE_DEFAULT, note=why
    )


def _hostname() -> str:
    return platform.node() or 'unknown-host'


# --------------------------------------------------------------------------------------------
# Defect 1b -- the reservation tracks the JOB, not the client
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JobHandle:
    """WHAT is holding the resource. The whole point is that it need not be this interpreter.

    THE DEFECT THIS EXISTS TO FIX, measured 2026-09-03 on the origin box: the reservation recorded
    ``os.getpid()``, the PYTHON CLIENT's process id. A client gave up, its ``finally`` released the
    seat, and the vendor executable ran on for another hour with six live processes still in the
    census. The gate under-counted exactly the case it exists for -- a job outliving its driver
    held ZERO seats, so nothing believed it was still eating the box.

    | the bookkeeping | what it tracked | what it should track |
    |---|---|---|
    | the seat | our client's lifetime | the JOB |
    | "the box is free" | our client exited | the PROCESS TABLE |

    A proxy that is usually right is the hardest kind of wrong, because it earns trust before it
    fails.

    *kind* is open on purpose. ``'pid'`` and ``'client'`` are resolved here against the process
    table; anything else -- a scheduler ticket, a queue id, a licence checkout -- is resolved by
    the CONSUMER's ``liveness`` hook, because ``lab_commons`` must not learn what a scheduler is.
    It learns how to ASK.
    """

    kind: str
    ident: str

    @classmethod
    def for_pid(cls, pid: int) -> JobHandle:
        """The job IS this operating-system process (and, for the ceiling, its whole tree)."""
        return cls('pid', str(int(pid)))

    @classmethod
    def for_client(cls, pid: int | None = None) -> JobHandle:
        """A handle on THIS interpreter.

        The HONEST default before a job exists -- and the thing every grant must be moved off, via
        :meth:`Grant.track`, the moment the real job has an identity.
        """
        return cls('client', str(os.getpid() if pid is None else pid))

    @property
    def pid(self) -> int | None:
        """The OS pid, when this handle names one. ``None`` for an opaque handle."""
        return int(self.ident) if self.kind in ('pid', 'client') and self.ident.lstrip('-').isdigit() else None


@dataclass(frozen=True, slots=True)
class Holder:
    """A live claim on a pool, as read back off the box. What a refusal NAMES."""

    pool: str
    job: JobHandle
    what: str
    demands: Mapping[str, int]
    since: str = ''
    #: The last working set SAMPLED for this job, when anyone has sampled it.
    observed_bytes: int | None = None

    @property
    def outstanding_bytes(self) -> int:
        """Declared memory this holder has NOT yet been observed to have allocated.

        WHY THE SUBTRACTION, and why it is not a clock. The OS's ``available_bytes`` already counts
        every byte a peer has actually taken, so adding that peer's full DECLARATION on top charges
        the box twice for the same gigabytes; but a peer admitted one second ago has allocated
        almost none of what it is about to, and ignoring its declaration is check-then-start -- N
        processes read the same free bytes, all decide there is room, and all start.

        So the honest outstanding claim is what it declared MINUS what it has been seen to hold.
        The origin tree approximated this with a 180-second "ramp window", which is a clock, and a
        clock cannot tell a job that is still growing from one that finished growing. An
        UNOBSERVED peer counts for its whole declaration -- the conservative direction.
        """
        declared = int(self.demands.get(MEMORY.name, 0))
        return declared if self.observed_bytes is None else max(0, declared - self.observed_bytes)

    def describe(self) -> str:
        """The holder as one greppable line: what, which job, and since when."""
        return (
            f'{self.what or "unnamed"} [{self.job.kind}:{self.job.ident}]{f" since {self.since}" if self.since else ""}'
        )


#: What a holder whose record cannot be read is CALLED in a refusal. A refusal that said "unnamed"
#: would send its reader looking for a name that is not in the file; this one says where to look.
_UNREADABLE_WHAT: Final = 'a record that exists and cannot be read'


def _unreadable(path: Path) -> Holder:
    """A record that EXISTS and cannot be read, as a holder this broker can only say is there.

    It declares nothing, so it subtracts nothing from a memory or a core sum -- what it does is
    refuse a seat, which is the conservative direction and the only one available. Nobody can say
    whether the writer is gone, and unlinking the file is the theft that put two holders on a
    one-seat lock in the first place. The holder is returned WITHOUT asking the liveness hook,
    deliberately: a hook answers about handles of a kind its owner knows, and this is not one.
    """
    return Holder(
        pool=path.name.split('.')[0],
        job=JobHandle('unreadable', path.name),
        what=_UNREADABLE_WHAT,
        demands={},
    )


# --------------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------------


class Exhausted(RuntimeError):
    """A dimension of this box is full, and the message says WHICH and WHO.

    "BUSY" WITH NO NAMES CANNOT BE ACTED ON. The reader has to choose between waiting longer,
    stopping a holder, and re-measuring the ceiling, and only the numbers and the names separate
    those three. A raised limit is almost never the remedy: on a MEASURED box the ceiling is a
    property of the machine, and on an unmeasured one the remedy is to measure it.
    """

    def __init__(
        self,
        pool: str,
        dimension: str,
        *,
        needed: int,
        available: int,
        basis: Basis,
        holders: Iterable[Holder] = (),
        what: str = '',
        waited_s: float = 0.0,
    ) -> None:
        """Keep the pool, dimension and reading this refusal is about."""
        self.pool = pool
        self.dimension = dimension
        self.needed = needed
        self.available = available
        self.basis = basis
        self.holders = list(holders)
        self.what = what
        self.waited_s = waited_s
        unit = DIMENSIONS[dimension].unit
        scale = (lambda value: f'{value // _BYTES_PER_MB} MB') if unit == 'bytes' else (lambda value: f'{value} {unit}')
        listed = '; '.join(holder.describe() for holder in self.holders) or 'nothing this broker recorded'
        subject = what or f'a {pool} job'
        hint = (
            'this ceiling is a CONSERVATIVE DEFAULT because nobody measured this box for it -- '
            'measuring the box is the remedy, raising a guess is not'
            if basis is Basis.CONSERVATIVE_DEFAULT
            else 'this ceiling is a MEASUREMENT of this machine, so raising it is not the remedy'
        )
        super().__init__(
            f'{subject}: {pool}.{dimension} needs {scale(needed)} and {scale(max(0, available))} is free'
            f'{f" after {waited_s:.0f}s of waiting" if waited_s else ""}. Holders: {listed}. {hint} -- '
            f'wait, or stop a holder. Starting anyway is what fills a workstation and takes every '
            f'other lane on it down.'
        )


class MemoryUnreadable(RuntimeError):
    """The machine's memory cannot be read, so the floor cannot be respected.

    NOT TREATED AS "NO CONSTRAINT", which is the whole reason the class exists. A caller that reads
    ``None`` and proceeds has silently dropped the ceiling it was written to honour, and the drop
    is invisible on exactly the machines where the reading failed.
    """


class CeilingExceeded(RuntimeError):
    """A job WE started went over the ceiling IT declared.

    Reported rather than only acted on, because the kill is the safety valve and the DIAGNOSIS is
    what stops it recurring: a job that reproducibly wants more than it declared needs its
    declaration re-measured, not a retry.
    """

    def __init__(self, pool: str, job: JobHandle, ceiling_bytes: int, held_bytes: int) -> None:
        """Keep the pool, job, ceiling and the reading that breached it."""
        self.pool = pool
        self.job = job
        self.ceiling_bytes = ceiling_bytes
        self.held_bytes = held_bytes
        super().__init__(
            f'{pool} job {job.kind}:{job.ident} holds {held_bytes // _BYTES_PER_MB} MB, over the '
            f'{ceiling_bytes // _BYTES_PER_MB} MB it declared. It was ended by its ROOT pid to keep '
            f'the workstation alive -- a runaway job does not fail as itself, it crashes the box and '
            f'takes every other lane down with it. The declaration is the suspect, not the ceiling.'
        )


# --------------------------------------------------------------------------------------------
# Defect 1 -- observing a RUNNING job
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JobObservation:
    """One sample of a live job. THE THING NOTHING IN THE ORIGIN TREE DID.

    Admission reads free memory once, at the start. A job admitted with headroom and then growing
    to fill the box was unobserved for its entire life, which is why an admission gate only
    POSTPONES a crash rather than preventing one.
    """

    job: JobHandle
    working_set_bytes: int
    at: float


class JobWatch:
    """A running job's ceiling, enforced on a daemon thread.

    IT EXITS ON ITS OWN when the job does, so nothing has to remember to stop it -- a watchdog that
    needs a matching ``close()`` is a watchdog that stops running the first time a caller raises.
    """

    def __init__(self, thread: threading.Thread, stopping: threading.Event, state: dict) -> None:
        """Keep the watching thread and the event that stops it."""
        self.thread = thread
        self._stopping = stopping
        self._state = state

    @property
    def breach(self) -> CeilingExceeded | None:
        """The breach this watch acted on, or ``None``. The record a report reads."""
        return self._state.get('breach')

    def stop(self) -> None:
        """Stop watching. Does NOT stop the job -- only a breach ever does that."""
        self._stopping.set()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the watching thread, bounded by *timeout*."""
        self.thread.join(timeout)


# --------------------------------------------------------------------------------------------
# The grant
# --------------------------------------------------------------------------------------------


@dataclass
class Grant:
    """An admitted job's ticket: what it may use, what that rested on, and what it actually did.

    It is the RETURN VALUE that carries the un-measured fact, which is the whole of decision 2: a
    caller, a gate and a report can all tell a measured grant from a conservative one without
    reading a log line.
    """

    pool: str
    what: str
    demands: Mapping[str, int]
    basis: Mapping[str, Basis]
    capacity: Mapping[str, Capacity]
    waited_s: float
    job: JobHandle
    _broker: Broker
    #: Every record file this grant holds -- one per INDEXED dimension it demanded, which is more
    #: than one as soon as a box-scoped seat is in play. Each carries this holder's record, so a
    #: peer refusing on ANY of them can name who is in the way.
    _paths: list[Path] = field(default_factory=list)
    _read_working_set: Callable[[int], int | None] = working_set_bytes
    _peak_bytes: int | None = None
    _watches: list[JobWatch] = field(default_factory=list)

    # -- axis 2 / decision 2: what the caller can TELL ------------------------------------------

    @property
    def is_fully_declared(self) -> bool:
        """Whether EVERY dimension this job was judged on rested on a ceiling that was DECLARED.

        TWO KINDS OF DECLARED CEILING COUNT, and neither is a guess: a MEASUREMENT of this box, and
        a STRUCTURAL constant that holds on every box. A structural constant is not a measurement of
        this machine -- there is nothing here to measure, and measuring would not move it -- so this
        property does not claim one; what it claims is that no dimension fell back to
        :data:`Basis.CONSERVATIVE_DEFAULT`, which is the state a caller can act on by measuring the
        box. Reading STRUCTURAL as "unmeasured" would collapse it into the fallback, which is the
        distinction :class:`Enforcement` was added to stop collapsing.
        """
        return not self.conservative

    @property
    def conservative(self) -> tuple[str, ...]:
        """The dimensions BOUNDED at a conservative default because nobody measured this box.

        Non-empty is not an error -- the job ran, under a real ceiling -- but it IS a fact the
        caller now owns, and the remedy it names is to measure the box.

        DISJOINT FROM :attr:`unbounded` by construction. The two used to be the same attribute,
        and that made it a declaration that lies: a dimension nothing checked reported identically
        to one held at the value that cannot crash the box.
        """
        return tuple(
            sorted(
                name
                for name, basis in self.basis.items()
                if basis is Basis.CONSERVATIVE_DEFAULT and name not in self.unbounded
            )
        )

    @property
    def unbounded(self) -> tuple[str, ...]:
        """The demanded dimensions NOTHING checked -- admission applied no ceiling to these at all.

        Not a warning and not an error: it is the honest statement of what this broker can and
        cannot do, handed to the caller in the return value so a gate or a report can act on it.
        A consumer that needs one of these bounded owns that enforcement itself; what it must not
        do is believe this broker bounded it.

        IT IS A PROPERTY OF THE DIMENSION, NEVER OF THE DECLARATION. Declaring a value for `disk`
        does not make disk bounded, because no reader exists to compare a demand against -- and a
        number nobody can check is not a ceiling.
        """
        return tuple(sorted(name for name in self.demands if DIMENSIONS[name].enforcement is Enforcement.NONE))

    def explain(self) -> str:
        """One line a report or a gate can print. Never the only place the fact is available."""
        parts = ', '.join(f'{name}={self.basis[name].value}' for name in sorted(self.basis))
        tail = (
            f' CONSERVATIVE on {", ".join(self.conservative)} -- this box was never measured for them.'
            if self.conservative
            # NOT "every ceiling was measured on this box": a STRUCTURAL ceiling was not measured
            # here and measuring would not move it. The line a report prints must not claim the one
            # kind of declared ceiling it is most likely to be looking at.
            else ' Every ceiling here was DECLARED: measured on this box, or structural on every box.'
        )
        loose = (
            f' UNBOUNDED: {", ".join(self.unbounded)} -- nothing here checked those, and a declared '
            f'value for them would not change it.'
            if self.unbounded
            else ''
        )
        return f'{self.pool}: {self.what or "a job"} admitted on [{parts}].{tail}{loose}'

    # -- defect 1b: track the JOB ---------------------------------------------------------------

    def track(self, job: JobHandle) -> None:
        """Re-point this reservation at the JOB, so it outlives this interpreter if the job does.

        Call it the moment the real job has an identity. Until then the grant is held by the
        client, which is the honest statement of what is known -- and the exact proxy that, left
        in place, under-counts a vendor process outliving its driver.
        """
        self.job = job
        self._write_record()

    # -- defect 1: observe the running job ------------------------------------------------------

    def observe(self) -> JobObservation | None:
        """Sample the tracked job's working set now, or ``None`` when it cannot be sampled.

        ``None`` covers both "this handle names no pid" and "the process is gone or denied", and
        the caller must not read either as "under the ceiling".
        """
        pid = self.job.pid
        if pid is None:
            return None
        held = self._read_working_set(pid)
        if held is None:
            return None
        self._peak_bytes = held if self._peak_bytes is None else max(self._peak_bytes, held)
        self._write_record()
        return JobObservation(job=self.job, working_set_bytes=held, at=time.time())

    @property
    def peak_bytes(self) -> int | None:
        """The largest working set SEEN for this job, or ``None`` if it was never sampled."""
        return self._peak_bytes

    def declared_ratio(self, dimension: str = MEMORY.name) -> float | None:
        """Observed peak over what the job DECLARED, or ``None`` when either is missing.

        A DECLARATION THAT LIES IS THE DOMINANT DEFECT, so the broker measures the actual peak and
        compares it to the estimate. Systematic under-declaration then becomes a recorded RATIO --
        something a consumer can accumulate and act on -- rather than a box crash.

        ``None`` rather than ``1.0`` when unobserved: a flattering default is how a lie stops being
        visible, and finding NOTHING must not read as finding agreement.
        """
        declared = self.demands.get(dimension)
        if not declared or self._peak_bytes is None:
            return None
        return self._peak_bytes / declared

    # -- axis 3: the running ceiling ------------------------------------------------------------

    def enforce_ceiling(
        self,
        *,
        job: JobHandle | None = None,
        ceiling_bytes: int | None = None,
        on_breach: Callable[[int], object] = kill_process_tree,
        interval_s: float = DEFAULT_POLL_S,
    ) -> JobWatch:
        """Bound the job THIS grant tracks, ending it if it exceeds what IT declared.

        THE SECOND HALF OF THE MECHANISM, and without it the first is only a postponement.

        NEVER EVICT ANOTHER PARTY'S RUN. A verdict in progress is someone's evidence. That is not
        left to a docstring: the only route to a watch is through a grant, and a grant refuses any
        job but the one it tracks -- so "we only bound our own" is a property the CODE enforces.
        A job exceeding its OWN declared ceiling is the exception, and it is the safety valve that
        keeps the box alive.

        THE KILL IS BY ROOT PID AND TAKES THE TREE. Stopping a wrapper leaves its children running,
        and a solver's children are where the memory actually is.

        AN UNREADABLE WORKING SET IS NOT A BREACH -- but it is not compliance either. A process that
        has EXITED reads identically to one we cannot query, so the watch simply ENDS, which is the
        correct reading of both and never kills something that already finished.

        Args:
            job: must be the tracked job, if given. Present so the refusal is reachable and tested.
            ceiling_bytes: defaults to what this job DECLARED for memory. There is no "no ceiling".
            on_breach: what to do about a breach. Injected so a test can assert the guard FIRES
                without ending anything.
            interval_s: seconds between samples.

        """
        if job is not None and job != self.job:
            msg = (
                f'this grant tracks {self.job.kind}:{self.job.ident} and may never evict '
                f"{job.kind}:{job.ident} -- a run in progress is someone else's evidence. A job may "
                f'only be bounded by the grant that admitted it.'
            )
            raise PermissionError(msg)
        pid = self.job.pid
        if pid is None:
            msg = (
                f'{self.pool} job {self.job.kind}:{self.job.ident} names no pid, so its memory cannot '
                f'be sampled and no ceiling can be enforced on it. Call track(JobHandle.for_pid(...)) '
                f'once the job has an OS identity.'
            )
            raise ValueError(msg)
        ceiling = int(ceiling_bytes) if ceiling_bytes is not None else self.demands.get(MEMORY.name, 0)
        if ceiling <= 0:
            msg = (
                f'{self.pool}: no ceiling to enforce -- the job declared no {MEMORY.name} cost and none '
                f'was passed. An unbounded running job is the shape that crashed the box; declare the '
                f'cost or pass ceiling_bytes.'
            )
            raise ValueError(msg)
        stopping = threading.Event()
        state: dict = {}
        job_handle = self.job

        def _watch() -> None:
            while not stopping.is_set():
                held = self._read_working_set(pid)
                if held is None:
                    return  # gone, or unqueryable -- either way there is nothing left to bound
                self._peak_bytes = held if self._peak_bytes is None else max(self._peak_bytes, held)
                if held > ceiling:
                    state['breach'] = CeilingExceeded(self.pool, job_handle, ceiling, held)
                    on_breach(pid)
                    return
                stopping.wait(interval_s)

        thread = threading.Thread(target=_watch, name=f'{self.pool}-ceiling-{pid}', daemon=True)
        watch = JobWatch(thread, stopping, state)
        self._watches.append(watch)
        thread.start()
        return watch

    # -- bookkeeping ----------------------------------------------------------------------------

    def _write_record(self) -> None:
        for path in self._paths:
            with contextlib.suppress(OSError):
                _publish(
                    path,
                    _record(
                        self.pool,
                        self.what,
                        self.job,
                        self.demands,
                        self._peak_bytes,
                        clock=self._broker._creation_clock,  # noqa: SLF001 -- one module, one owner
                    ),
                )

    def _release(self) -> None:
        for watch in self._watches:
            watch.stop()
        for path in self._paths:
            _unpublish(path)


# --------------------------------------------------------------------------------------------
# The broker
# --------------------------------------------------------------------------------------------


class Broker:
    """Admits jobs against this box's declared capacities, and bounds the ones it admitted.

    WHY FILES AND NOT A ``threading.Semaphore``. The competing jobs are separate PROCESSES -- an
    agent, a CLI run, a background solve, a worktree on a different revision -- that never share an
    interpreter. An in-process semaphore would count one of them and permit N times the limit,
    which is the exact failure it would be added to prevent.

    THE RECORDS LIVE OUTSIDE ANY REPOSITORY, in the OS temp directory. They are per-box runtime
    state naming a pid, and a tree is not where machine state goes -- which is also what makes
    every worktree on the box contend through ONE set of records regardless of the revision each
    has checked out.

    STALENESS IS ASKED OF THE OS, NEVER OF A CLOCK. A clock cannot tell a long solve from a dead
    one, and a killed job leaves its record behind but not its pid. So the worst outcome of any
    crash is a file nobody counts, never a box that refuses everything until a human cleans up.
    """

    def __init__(
        self,
        registry: CapacityRegistry | None = None,
        *,
        hostname: str | None = None,
        read_memory: Callable[[], SystemMemory | None] = system_memory,
        liveness: Callable[[JobHandle], bool | None] | None = None,
        resource_dir: Path | None = None,
        creation_clock: CreationClock = creation_stamp,
    ) -> None:
        """Build a broker over *registry*.

        Args:
        registry: the declared capacities. An empty one is legitimate and means every pool is
            conservative-and-visible, which is a working box rather than a refusing one.
        hostname: override the detected hostname (tests and cross-box diagnosis only).
        read_memory: the reading backend. Injected so the low-memory condition can be PLANTED
            and the REAL guard called.
        liveness: resolves a job handle whose *kind* this module does not own -- a scheduler
            ticket, a queue id, a licence checkout. Returning ``None`` means "cannot tell",
            which is read as STILL HELD: freeing a seat wrongly is the over-subscription this
            whole mechanism exists to prevent, so "I do not know" takes the conservative side.
        resource_dir: override the record root; ``$LAB_COMMONS_RESOURCE_DIR`` otherwise.
        creation_clock: reads a pid's creation stamp, the discriminator that makes a recorded
            pid an IDENTITY rather than a number. Injected for the reason *read_memory* is: pid
            REUSE cannot be planted by asking this box to recycle a number to order, so the
            condition is planted through this hook and the REAL guard is called.

        """
        self.registry = registry if registry is not None else CapacityRegistry()
        self.hostname = hostname or _hostname()
        self._read_memory = read_memory
        self._liveness = liveness
        self._resource_dir = resource_dir
        self._creation_clock = creation_clock

    # -- where the records live -----------------------------------------------------------------

    def resource_dir(self) -> Path:
        """Where this box keeps its reservation records -- OS temp, never a repository."""
        override = self._resource_dir or os.environ.get(RESOURCE_DIR_ENV)
        path = Path(override) if override else Path(tempfile.gettempdir()) / 'lab-commons-resources'
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- reading the box ------------------------------------------------------------------------

    def holders(self, pool: str) -> list[Holder]:
        """Every LIVE holder a *pool* query must be told about, for a refusal, a report or a diagnosis.

        THE POOL'S OWN, PLUS EVERY BOX-SCOPED ONE. A box-scoped dimension is held on the BOX, so a
        reader asking who is in the way is owed that answer whatever pool the holder named -- the
        alternative is a refusal that prints "nothing this broker recorded" while a peer sits on the
        machine. Pool-scoped records stay exactly as they were: those are the pool's own business.

        ONE LINE PER RUN ACROSS NAMESPACES, and one line per SEAT within one. A job holds its pool's
        seat and may also hold a box-scoped one; that is one job to act on and one line to read, so
        a holder already named for the pool is not named again for the box. Two seats of the SAME
        dimension are not that case -- they are two holdings of one stock, which is the number a
        count reports, and collapsing them would under-report a pool that is genuinely full.
        """
        found = self._live(f'{pool}.*')
        named = {(holder.pool, holder.job) for holder in found}
        for dimension in DIMENSIONS.values():
            if dimension.scope is Scope.BOX:
                # The rule above, applied to ONE namespace at a time rather than to the whole
                # listing: seen-in-another-namespace is the only thing that folds.
                box_scoped = self._live(f'{_namespaced(pool, dimension)}.*')
                fresh = [holder for holder in box_scoped if (holder.pool, holder.job) not in named]
                named.update((holder.pool, holder.job) for holder in fresh)
                found.extend(fresh)
        return found

    def _live(self, pattern: str) -> list[Holder]:
        """The live holders among the records matching *pattern*, in a stable order."""
        root = self.resource_dir()
        return [
            holder for holder in (self._holder_of(path) for path in sorted(root.glob(pattern))) if holder is not None
        ]

    def _holder_of(self, path: Path) -> Holder | None:
        """The LIVE holder recorded in *path*, or ``None`` when the path holds no live job.

        AN UNREADABLE RECORD IS A HOLDER, and it is the rule this module already applies to an
        opaque job handle: "cannot tell" is HELD, never free. Reading it as ABSENT was measured on
        2026-09-15 to admit TWO holders to a one-seat pool -- a record is written a moment after the
        seat file appears, that instant is unparseable, and the peer that read it as nobody deleted
        a live seat and took its index. A record that EXISTS and cannot be read says one thing: a
        holder is there, and no name for it. The only safe responses are to wait and to refuse; the
        unsafe one is the one this used to take.

        A MISSING file is a different fact and keeps its old meaning. It is also what a peer that
        finished between the glob and the read leaves behind, so treating it as held would make a
        released seat un-takeable.
        """
        try:
            text = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return None
        except OSError:
            return _unreadable(path)
        try:
            record = json.loads(text)
        except ValueError:
            return _unreadable(path)
        if not isinstance(record, dict):
            return _unreadable(path)
        # Read by KEY and never by shape: a record written by a NEWER broker carries fields this
        # one does not know, and refusing to read it would make an older worktree see a free seat
        # where a live job is. Unknown keys are ignored; the known ones are enough to count.
        job = JobHandle(str(record.get('job_kind', 'client')), str(record.get('job_ident', '')))
        created = record.get('created')
        if not self._job_alive(job, created if isinstance(created, int) else None):
            return None
        demands = record.get('demands') or {}
        return Holder(
            pool=str(record.get('pool', path.name.split('.')[0])),
            job=job,
            what=str(record.get('what', '')),
            demands={str(key): int(value) for key, value in demands.items() if isinstance(value, int | float)},
            since=str(record.get('since', '')),
            observed_bytes=record.get('observed_bytes'),
        )

    def _job_alive(self, job: JobHandle, created: int | None = None) -> bool:
        """Whether the recorded JOB is still running. The one staleness test.

        A PID-KIND HANDLE IS TESTED AGAINST ITS RECORDED CREATION STAMP and not against the number
        alone; :func:`lab_commons.liveness.still_the_same_process` holds that comparison and the
        two incidents that decide its direction. *created* defaults to ``None`` -- "no
        discriminator recorded" -- which is exactly how a record written by an older broker on this
        box must read, and which keeps the old behaviour for it rather than freeing its seat.
        """
        pid = job.pid
        if pid is not None:
            return still_the_same_process(pid, created, alive=pid_alive, clock=self._creation_clock)
        if self._liveness is None:
            return True  # an opaque handle nobody can resolve is HELD, never free
        verdict = self._liveness(job)
        return True if verdict is None else bool(verdict)

    # -- admission ------------------------------------------------------------------------------

    @contextlib.contextmanager
    def admit(
        self,
        pool: str,
        demands: Mapping[str, int] | None = None,
        *,
        what: str = '',
        wait_s: float = 0.0,
        poll_s: float = DEFAULT_POLL_S,
        read_working_set: Callable[[int], int | None] = working_set_bytes,
    ) -> Generator[Grant]:
        """Admit a job that DECLARES its cost, or refuse saying which dimension is full and who holds it.

        AXIS 2: the call says what it expects to CONSUME, not "give me a slot". Admission is then a
        per-dimension headroom check against capacity minus what live peers claim, which is what
        lets four small jobs and two large ones be told apart -- a count never can.

        THE ORDER IS MEMORY THEN SEATS, and it is not cosmetic: waiting for RAM while holding a seat
        blocks a peer that only needed the seat, and the count is the cheap check that should only
        be paid once the expensive one is satisfied.

        EVERY INDEXED DIMENSION THE JOB DEMANDED IS TAKEN, not only ``seats``. That is how a
        box-scoped seat is enforced by the same code path as a pool's: the dimension says whether
        its record files are the pool's or the box's, and this call does not care which.

        THE WAIT IS BOUNDED. Queueing forever converts a crash into a hang, which is not an
        improvement -- and an unbounded wait outlives the thing it waits for.

        Args:
            pool: what is being rationed. A consumer's vocabulary; this module stays ignorant of it.
            demands: dimension name -> amount. Every key must be a declared dimension.
            what: what is being run, recorded in the reservation so a holder can be identified.
            wait_s: ceiling on the WAIT. ``0.0`` means check once and refuse.
            poll_s: seconds between readings while queueing.
            read_working_set: the sampler for :meth:`Grant.observe` and the running ceiling.

        Yields:
            The :class:`Grant`, carrying per-dimension :class:`Basis` so the caller can tell a
            measured admission from a conservative one.

        Raises:
            KeyError: a demand names a dimension nobody declared.
            MemoryUnreadable: this machine's memory cannot be read at all.
            Exhausted: a dimension was still full when the wait ran out.

        """
        wanted = {str(name): int(amount) for name, amount in (demands or {}).items()}
        for name in wanted:
            if name not in DIMENSIONS:
                msg = f'{name!r} is not a declared dimension; known: {sorted(DIMENSIONS)}'
                raise KeyError(msg)
        wanted.setdefault(SEATS.name, 1)
        ceilings = {name: self.registry.capacity(pool, name, hostname=self.hostname) for name in wanted}

        claim = self._write_claim(pool, wanted, what)
        started = time.monotonic()
        taken: list[Path] = []
        try:
            self._await_memory(pool, wanted, ceilings, what=what, wait_s=wait_s, poll_s=poll_s, started=started)
            self._await_cores(pool, wanted, ceilings, what=what, wait_s=wait_s, poll_s=poll_s, started=started)
            for name in sorted(wanted):
                dimension = DIMENSIONS[name]
                if dimension.enforcement is Enforcement.INDEXED:
                    taken.append(
                        self._take_indexed(
                            pool, dimension, wanted, ceilings, what=what, wait_s=wait_s, poll_s=poll_s, started=started
                        )
                    )
        except BaseException:
            # A seat already taken by a dimension that is not the one that failed is RELEASED here:
            # there is no grant yet, so nothing else would ever unlink it and the box would lose that
            # seat to a job that never started. IT INSISTS as `Grant._release` does: a suppressed
            # refusal leaks a record naming a LIVE pid, which is a permanent phantom holder.
            for path in taken:
                _unpublish(path)
            raise
        finally:
            _unpublish(claim)

        grant = Grant(
            pool=pool,
            what=what,
            demands=wanted,
            basis={name: ceiling.basis for name, ceiling in ceilings.items()},
            capacity=ceilings,
            waited_s=time.monotonic() - started,
            job=JobHandle.for_client(),
            _broker=self,
            _paths=taken,
            _read_working_set=read_working_set,
        )
        try:
            yield grant
        finally:
            grant._release()  # noqa: SLF001 -- one module, one owner

    def _write_claim(self, pool: str, wanted: Mapping[str, int], what: str) -> Path:
        """Record the INTENTION before waiting, so a peer's headroom check can see it.

        Without this the memory check is check-then-start: two waiters both read the same free bytes
        and both pass. A claim is counted for memory and NOT for seats, so nobody holds a seat while
        queueing for RAM.

        PUBLISHED ATOMICALLY: a half-written claim reads as nothing. The name repeats per
        (pid, thread), so take two renames onto take one's file -- the one a polling peer has open.
        `_records.publish` insists, and argues why its `False` is deliberately not a refusal here.
        """
        path = self.resource_dir() / f'{pool}.claim.{os.getpid()}.{threading.get_ident()}.json'
        _publish(
            path, _record(pool, what or sys.argv[0], JobHandle.for_client(), wanted, None, clock=self._creation_clock)
        )
        return path

    def _await_memory(
        self,
        pool: str,
        wanted: Mapping[str, int],
        ceilings: Mapping[str, Capacity],
        *,
        what: str,
        wait_s: float,
        poll_s: float,
        started: float,
    ) -> None:
        """Block until this box has room for the declared memory cost, or refuse saying why.

        WHY NO RECORD FILE IS NEEDED FOR THIS HALF's reading. Available physical RAM is a
        BOX-GLOBAL figure and every live process's working set is already subtracted from it by the
        OS. So this guard sees the job that is actually eating the box even when this broker never
        admitted it -- unlike a counter, which only knows what it let in.
        """
        needed_declared = wanted.get(MEMORY.name, 0)
        ceiling = ceilings.get(MEMORY.name)
        if ceiling is None or (needed_declared <= 0 and ceiling.basis is not Basis.CONSERVATIVE_DEFAULT):
            return
        if needed_declared <= 0 and MEMORY.name not in wanted:
            return
        deadline = started + max(0.0, wait_s)
        while True:
            reading = self._read_memory()
            if reading is None:
                msg = (
                    f"cannot read this machine's memory, so the {pool} memory floor cannot be "
                    f'respected. AN UNREADABLE BOX IS NOT AN UNCONSTRAINED ONE: proceeding here is '
                    f'how a ceiling disappears on exactly the machines that could not be checked.'
                )
                raise MemoryUnreadable(msg)
            needed = self._memory_needed(needed_declared, ceiling, reading)
            claimed = sum(holder.outstanding_bytes for holder in self._peers(pool))
            free = reading.available_bytes - claimed
            if free >= needed:
                return
            if time.monotonic() >= deadline:
                raise Exhausted(
                    pool,
                    MEMORY.name,
                    needed=needed,
                    available=free,
                    basis=ceiling.basis,
                    holders=self._peers(pool),
                    what=what,
                    waited_s=time.monotonic() - started,
                )
            time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))

    @staticmethod
    def _memory_needed(declared: int, ceiling: Capacity, reading: SystemMemory) -> int:
        """How much must be FREE before this job may start.

        On a MEASURED box that is what the job declared. On an UNMEASURED one it is the larger of
        the declaration and :data:`CONSERVATIVE_MEMORY_SHARE` of TOTAL -- because an unmeasured box
        cannot say a job is small, and the alternative default in the wild drops the memory
        constraint entirely.
        """
        if ceiling.basis is Basis.CONSERVATIVE_DEFAULT:
            return max(declared, int(CONSERVATIVE_MEMORY_SHARE * reading.total_bytes))
        return declared

    def _await_cores(
        self,
        pool: str,
        wanted: Mapping[str, int],
        ceilings: Mapping[str, Capacity],
        *,
        what: str,
        wait_s: float,
        poll_s: float,
        started: float,
    ) -> None:
        """Bound the cores in flight for *pool* by SUMMING what live holders declared.

        WHY THIS EXISTS AS ITS OWN STEP. ``cpu`` was declared an :data:`Accounting.COUNTED`
        dimension from the start and nothing counted it: only seats were ever enforced. So a
        demand of ninety-nine cores was ADMITTED on a box whose conservative cpu ceiling is one,
        and -- worse than the admission -- the grant reported cpu among its "conservative"
        dimensions, which reads as a ceiling that was applied. Found 2026-09-13 by driving the
        shape-only dimensions through the real ``admit()`` after a type checker pointed at them.

        A COUNT OF SEATS CANNOT SUBSTITUTE. Two jobs is not a width: one may want a single core
        and the next may want every core the box has, which is exactly the distinction axis 2
        exists to make. Cores are summed across live holders; seats are counted.

        The sum is over the same records seats use, so it is cross-process for the same reason,
        and a dead holder contributes nothing for the same reason.
        """
        ceiling = ceilings.get(CPU.name)
        if ceiling is None:
            return
        limit = ceiling.value if ceiling.value is not None else _conservative_floor(CPU)
        needed = wanted.get(CPU.name, 0)
        deadline = started + max(0.0, wait_s)
        while True:
            peers = self._peers(pool)
            in_flight = sum(int(holder.demands.get(CPU.name, 0)) for holder in peers)
            if in_flight + needed <= limit:
                return
            if time.monotonic() >= deadline:
                raise Exhausted(
                    pool,
                    CPU.name,
                    needed=needed,
                    available=limit - in_flight,
                    basis=ceiling.basis,
                    holders=peers,
                    what=what,
                    waited_s=time.monotonic() - started,
                )
            time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))

    def _peers(self, pool: str) -> list[Holder]:
        """Live holders and claims OTHER than this call's own claim file.

        POOL-SCOPED, deliberately, and not the wider reading :meth:`holders` takes: this is the
        arithmetic behind a memory and a core comparison, and those sums are over what the CALLER's
        pool declared. Widening it would charge one pool for another's declaration -- a different
        question with a different answer, not a better one.
        """
        mine = f'{pool}.claim.{os.getpid()}.{threading.get_ident()}.json'
        found = []
        for path in sorted(self.resource_dir().glob(f'{pool}.*')):
            if path.name == mine:
                continue
            holder = self._holder_of(path)
            if holder is not None:
                found.append(holder)
        return found

    def _take_indexed(
        self,
        pool: str,
        dimension: Dimension,
        wanted: Mapping[str, int],
        ceilings: Mapping[str, Capacity],
        *,
        what: str,
        wait_s: float,
        poll_s: float,
        started: float,
    ) -> Path:
        """Claim one unit of an INDEXED dimension by CREATING its record, or refuse naming the holders.

        THE RACE THIS CLOSES, measured 2026-09-15. The record used to be written AFTER
        ``os.open(O_CREAT | O_EXCL)`` returned, so for that instant the seat EXISTED and was EMPTY
        -- a state ``_holder_of`` read as nobody, and the allocator then DELETED before taking the
        index. A peer could therefore remove a seat another process had just taken and take it, and
        two holders ran on a lock that admits one. It is closed at the cause here: the record is
        written to a staging file first and the seat is created by ``os.link``, which makes the name
        only if it is free and only WITH the record already in it. There is no instant at which the
        seat exists and cannot be read, so the unreadable-is-held rule below is a backstop against a
        mixed-version box rather than the mechanism -- and an unreadable seat is never deleted.

        WHY ``os.link`` AND NOT A STAGED RECORD PLUS ``os.rename``. ``os.rename`` REPLACES the
        destination silently on POSIX (``os.replace`` is the same call with the intent spelled out),
        so as an acquisition it admits every racer: two processes rename their records onto one
        index and both believe they won. Measured on this box the same call refuses on Windows --
        so the exclusion would depend on the platform, and a lock that silently admits two holders
        on Linux is worse than the window it was meant to close. ``os.link`` is the one stdlib call
        that fails with ``FileExistsError`` on EVERY platform: the property an index needs.

        The indexed-file technique itself is carried over unchanged from the origin tree because it
        is already cross-process and already correct -- the OS arbitrates the race, and losing it is
        a ``continue`` rather than a failure because the next index may still be free.
        """
        namespace = _namespaced(pool, dimension)
        ceiling = ceilings[dimension.name]
        # NARROWED, NOT DEFAULTED. `ceiling.value` is `int | None` because the shape-only
        # dimensions have no value; an INDEXED dimension always does, and `_conservative_floor` is
        # the assertion of that -- checked at import for every enforced dimension, so this branch is
        # unreachable rather than merely unlikely. Written as an explicit `is None` and never
        # `value or FLOOR` because ZERO IS A MEANINGFUL DECLARATION -- "this box may not run this at
        # all" -- and `or` would silently promote it to one, which is the permissive direction.
        limit = ceiling.value if ceiling.value is not None else _conservative_floor(dimension)
        deadline = started + max(0.0, wait_s)
        while True:
            # Rebuilt per poll rather than once before it: the record's `since` is what a refusal
            # prints, and a job that queued for five minutes would otherwise announce itself as
            # having held the seat since a time it was still waiting for it.
            record = _record(pool, what, JobHandle.for_client(), wanted, None, clock=self._creation_clock)
            for index in range(limit):
                path = self.resource_dir() / f'{namespace}.{index}.slot'
                if self._create_seat(path, record):
                    return path
                if self._holder_of(path) is not None:
                    # A LIVE holder, or a record that cannot be read -- which is a holder this
                    # broker cannot name. Either way the index is somebody's already.
                    continue
                # A dead job's record is not a holder, so removing it is not stealing -- and it
                # INSISTS, so a reader's handle cannot make a reclaimable index permanently held.
                _unpublish(path)
                if self._create_seat(path, record):
                    return path
                # LOST the reclaim to a peer that took the index between the removal and the
                # create: not an error, the next index may still be free.
            if time.monotonic() >= deadline:
                holders = self._live(f'{namespace}.*')
                raise Exhausted(
                    pool,
                    dimension.name,
                    needed=wanted.get(dimension.name, 1),
                    available=limit - len(holders),
                    basis=ceiling.basis,
                    holders=holders,
                    what=what,
                    waited_s=time.monotonic() - started,
                )
            time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))

    @staticmethod
    def _create_seat(path: Path, record: Mapping[str, object]) -> bool:
        """Create *path* holding *record*, atomically and exclusively. ``False`` if it is taken.

        THE WHOLE FIX IN ONE CALL, and the reason the seat is never observable in a state that
        reads as free: ``os.link`` publishes a name for an ALREADY-WRITTEN file, so ``FileExistsError``
        is the OS saying "somebody else's name is on this index" -- the same arbitration ``O_EXCL``
        gave, with the record no longer arriving later.

        IT NEEDS A FILESYSTEM WITH HARD LINKS -- measured on this box's NTFS, and ``link(2)`` is
        fundamental on every POSIX one this family runs on (ext4, APFS, tmpfs). THERE IS NO FALLBACK
        to create-then-write when a directory cannot: that is the code this fix removed, and a lock
        that quietly weakens itself on an unusual mount is worse than one that reports it cannot run
        there. A record root that cannot be linked is a root this broker cannot ration on.
        """
        staged = _stage(path, record)
        try:
            os.link(staged, path)
        except FileExistsError:
            return False
        finally:
            with contextlib.suppress(OSError):
                staged.unlink()
        return True
