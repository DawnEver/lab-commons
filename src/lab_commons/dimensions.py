"""Tier 1 -- the resource DIMENSIONS, declared as data: their names, units, scope and how each is enforced.

What once lived beside this table -- a per-box broker of pools, seats and memory -- is folded into the ONE
box ledger (:mod:`lab_commons.box`, D2 2026-10-16); what remains is the vocabulary a Slurm plan
(:mod:`lab_commons.hpc.plan`) and :func:`lab_commons.width.fits` read: a demand keyed by these names, in
these units (memory in bytes), refused by name for a dimension the table does not declare -- and the box
(:mod:`lab_commons.box.gpu`) takes its GPU names from. A LEAF: it imports nothing of this package, so every tier
below the resource engine may read it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = [
    'BOX_SEATS',
    'CPU',
    'DIMENSIONS',
    'DISK',
    'EXCLUSIVE',
    'GPU',
    'MEMORY',
    'NPU',
    'PRECISIONS',
    'SEATS',
    'VRAM',
    'WALLCLOCK',
    'Accounting',
    'Dimension',
    'Enforcement',
    'Scope',
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
#: DISK has the SHAPE and no values, deliberately. There is no portable stdlib reader for it that would
#: answer on every box in this family, and a dimension with a reader that works on SOME machines is the
#: silent-degradation defect this module exists to refuse. Declaring it means a consumer can demand it and SEE
#: that nothing bounded it -- strictly better than a ``KeyError`` or, worse, a demand silently believed.
#: DECLARING A VALUE FOR IT DOES NOT MAKE IT BOUNDED: a number nobody can check is not a ceiling.
DISK: Final = Dimension(
    'disk', 'bytes', Accounting.BOX_GLOBAL, Enforcement.NONE, conservative=None, note='shape only; no reader'
)
#: THE ACCELERATOR DEVICES -- GPU, NPU -- and the GPU's memory are READ: on a box by its device probes
#: (:mod:`lab_commons.box.devices`), which see every process on a device, admitted or not; on a cluster the GPU by
#: Slurm's ``gres/gpu`` (:mod:`lab_commons.hpc.plan`, ``--gres=gpu:N``). A GPU may be shared by memory
#: (:data:`VRAM`, per GPU taken); see :mod:`lab_commons.box.devices`. A box that cannot read
#: them is blind and lends none -- the conservative value is 0, not 1: not every box has one.
GPU: Final = Dimension(
    'gpu',
    'devices',
    Accounting.COUNTED,
    Enforcement.OS_READING,
    conservative=0,
    note='GPUs, each shared by memory or taken whole. An unread box lends none.',
)
NPU: Final = Dimension(
    'npu',
    'devices',
    Accounting.COUNTED,
    Enforcement.OS_READING,
    conservative=0,
    note='whole NPUs (inference accelerators), each lent to one claim. An unread box lends none.',
)
VRAM: Final = Dimension(
    'vram_gb',
    'GiB',
    Accounting.BOX_GLOBAL,
    Enforcement.OS_READING,
    conservative=0,
    note='GPU memory a claim takes on EACH GPU it holds, whole GiB. An unread box lends none.',
)

#: The NATIVE PRECISIONS a device may be required to compute in. A demand names one with a count of 1
#: (``{'gpu': 1, 'f64': 1}``): every device the claim takes must do it natively. A REQUIREMENT, not a stock -- so
#: it is not a :class:`Dimension` and :func:`lab_commons.width.fits` never sizes by it.
PRECISIONS: Final = ('f64', 'f32', 'f16', 'int8')
#: The requirement that takes each GPU of a claim WHOLE (``{'gpu': 1, 'exclusive': 1}``); without it a GPU claim
#: is a share of its memory. A requirement like :data:`PRECISIONS`, not a stock.
EXCLUSIVE: Final = 'exclusive'

DIMENSIONS: Final[Mapping[str, Dimension]] = {
    dimension.name: dimension for dimension in (SEATS, BOX_SEATS, MEMORY, CPU, WALLCLOCK, DISK, GPU, NPU, VRAM)
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
