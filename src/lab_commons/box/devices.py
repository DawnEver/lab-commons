"""THE BOX'S ACCELERATOR DEVICES -- GPUs and NPUs of any vendor: read, lent whole, and named to the run that holds them.

ONE VOCABULARY. A demand asks :data:`GPU` or :data:`NPU` (whole devices), :data:`VRAM_GB` (whole GiB free on the
GPUs it takes) and any of :data:`PRECISIONS` (``{'gpu': 1, 'f64': 1}``: every device it takes must compute f64
natively). The names are the one dimension table's (:mod:`lab_commons.dimensions`), which a Slurm plan reads too.
A GPU is SHARED BY MEMORY: a claim states ``vram_gb`` (per GPU it takes) and several claims share one GPU while
their declarations, and whatever else is resident on it, fit -- k-fold fits side by side. :data:`EXCLUSIVE` takes
a GPU whole: no claim beside it, and only an idle device. An NPU has no memory to share and is always whole. No
kernel holds a claim to its share: the declaration is the claim's word, and the observed memory is what checks it.

A DEVICE is ``<kind>.<vendor>.<index>`` (``gpu.nvidia.0``, ``gpu.intel.0``, ``npu.intel.0``) with its DECLARED
native precisions (:data:`NATIVE`, by kind and vendor: fp64 on an NVIDIA GPU is native however slow; an Intel iGPU
has none; an NPU does f16 and int8 only), its memory, and its OBSERVED use.

SUPPLY IS MEASURED, like cores (:mod:`lab_commons.box.supply`). A GPU's share is its memory less the larger of what
is in use now -- every process, admitted or not -- and what its claims declared (:func:`free_gib`). A device is
taken WHOLE only when no claim is on it, no process runs on it and its utilisation is KNOWN and below
:data:`BUSY_UTIL_PCT`: a job the box never admitted takes the device it runs on, listed by the driver or not.
Each vendor is one probe (:data:`PROBES`):

* ``nvidia`` -- two read-only ``nvidia-smi`` queries (Linux and Windows): utilisation, memory, compute processes.
* ``intel`` -- the OS inventory (Windows: CIM ``Win32_VideoController`` and PnP class ``ComputeAccelerator``;
  Linux: ``/sys/class/drm`` and ``/sys/class/accel``, vendor ``0x8086``). It FINDS the devices but no portable
  reader says how busy they are, so their utilisation is unknown and they are inventoried, never lent, until one does.

A probe that is missing, fails or times out is BLIND (:attr:`Devices.blind`, naming why); a kind none of whose
devices was found while a probe was blind is blind in :class:`~lab_commons.box.Capacity` and a demand for it is
refused at once, naming why -- never "none in use".

ASSIGNMENT IS STABLE: usable devices are taken in id order, never by a reading's momentary free memory, so equal
claims on an equal box bind equally, and a run that holds one NVIDIA GPU always sees it as CUDA device 0.

THE RUN IS TOLD ITS DEVICES ONCE (:func:`binding`): :data:`DEVICES_ENV` lists every device it holds;
``CUDA_VISIBLE_DEVICES`` its NVIDIA indices in ``nvidia-smi``'s order (``CUDA_DEVICE_ORDER=PCI_BUS_ID``);
:data:`OPENVINO_ENV` its Intel devices as OpenVINO names (``GPU.0``, ``NPU``). A run that claimed none sees none.
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

import lab_commons.launch as _door
from lab_commons.dimensions import EXCLUSIVE as _EXCLUSIVE
from lab_commons.dimensions import GPU as _GPU
from lab_commons.dimensions import NPU as _NPU
from lab_commons.dimensions import PRECISIONS
from lab_commons.dimensions import VRAM as _VRAM

if TYPE_CHECKING:
    from lab_commons.box.demand import Holding

__all__ = [
    'BUSY_UTIL_PCT', 'DEVICES_ENV', 'EXCLUSIVE', 'GPU', 'KINDS', 'NAMES', 'NATIVE', 'NPU', 'OPENVINO_ENV',
    'ORDER_ENV', 'PRECISIONS', 'PROBES', 'PROBE_S', 'QUERY_APPS', 'QUERY_GPUS', 'VISIBLE_ENV', 'VRAM_GB',
    'Device', 'Devices', 'assign',
    'binding', 'free', 'free_gib', 'oversized', 'parse_intel_windows', 'parse_nvidia', 'read_devices', 'sizes',
]  # fmt: skip

#: The demand's names, from the one dimension table.
GPU: Final = _GPU.name
NPU: Final = _NPU.name
VRAM_GB: Final = _VRAM.name
#: The demand flag that takes each GPU WHOLE; without it a GPU claim is a ``vram_gb`` share (module docstring).
EXCLUSIVE: Final = _EXCLUSIVE
#: The device kinds a demand counts.
KINDS: Final = (GPU, NPU)
#: Every demand name this module owns: the kinds, the GPU memory and the precisions.
NAMES: Final = frozenset({*KINDS, VRAM_GB, EXCLUSIVE, *PRECISIONS})

#: Native precisions, declared per (kind, vendor). An unknown pair does none: it is lent for a count, never a precision.
NATIVE: Final[Mapping[tuple[str, str], frozenset[str]]] = {
    (GPU, 'nvidia'): frozenset({'f64', 'f32', 'f16', 'int8'}),
    (GPU, 'intel'): frozenset({'f32', 'f16', 'int8'}),
    (NPU, 'intel'): frozenset({'f16', 'int8'}),
}

#: Utilisation (percent) at or above which a device no claim holds is in use by someone else.
BUSY_UTIL_PCT: Final = 5

#: Seconds one probe may take before it is blind.
PROBE_S: Final = 10.0

#: The two read-only NVIDIA queries; ``uuid`` is what ties a compute process to its device.
QUERY_GPUS: Final = (
    '--query-gpu=index,uuid,utilization.gpu,memory.used,memory.total',
    '--format=csv,noheader,nounits',
)
QUERY_APPS: Final = ('--query-compute-apps=gpu_uuid,pid,used_memory', '--format=csv,noheader,nounits')

#: The environment a boxed run reads its devices from (:func:`binding`).
DEVICES_ENV: Final = 'LAB_COMMONS_BOX_DEVICES'
VISIBLE_ENV: Final = 'CUDA_VISIBLE_DEVICES'
ORDER_ENV: Final = 'CUDA_DEVICE_ORDER'
OPENVINO_ENV: Final = 'LAB_COMMONS_BOX_OPENVINO'

_MIB_PER_GIB: Final = 1024
_INTEL_PCI: Final = '0x8086'


@dataclass(frozen=True)
class Device:
    """One device as its probe read it; ``utilization_pct`` is ``None`` when nothing could say how busy it is."""

    kind: str
    vendor: str
    index: int
    name: str
    utilization_pct: int | None
    used_mib: int
    total_mib: int
    pids: tuple[int, ...] = ()

    @property
    def id(self) -> str:
        """``<kind>.<vendor>.<index>``: the name the ledger holds it by."""
        return f'{self.kind}.{self.vendor}.{self.index}'

    @property
    def precisions(self) -> frozenset[str]:
        """What it computes natively (:data:`NATIVE`)."""
        return NATIVE.get((self.kind, self.vendor), frozenset())

    @property
    def idle(self) -> bool:
        """No process on it and its utilisation known and below :data:`BUSY_UTIL_PCT`."""
        return not self.pids and self.utilization_pct is not None and self.utilization_pct < BUSY_UTIL_PCT

    @property
    def free_mib(self) -> int:
        """Its memory not in use now."""
        return max(0, self.total_mib - self.used_mib)


@dataclass(frozen=True)
class Devices:
    """The box's devices now; ``blind`` names each probe that could not say, and why."""

    devices: tuple[Device, ...] = ()
    blind: Mapping[str, str] = field(default_factory=dict)

    def unread(self) -> dict[str, str]:
        """Each kind none of whose devices was found while a probe was blind, and why: it lends nothing."""
        why = '; '.join(f'{probe}: {reason}' for probe, reason in sorted(self.blind.items()))
        found = {d.kind for d in self.devices}
        return {kind: why for kind in KINDS if why and kind not in found}


# --------------------------------------------------------------------------------------------
# The probes, one per vendor
# --------------------------------------------------------------------------------------------


def _number(text: str) -> int | None:
    """One ``nounits`` field as an int; ``[N/A]``, ``[Not Supported]`` and the like are ``None``."""
    text = text.strip()
    return int(float(text)) if text.replace('.', '', 1).isdecimal() else None


def _rows(text: str, width: int) -> list[list[str]]:
    rows = [[cell.strip() for cell in line.split(',')] for line in text.splitlines() if line.strip()]
    bad = [row for row in rows if len(row) != width]
    if bad:
        msg = f'nvidia-smi printed {bad[0]!r}, not {width} comma-separated fields'
        raise ValueError(msg)
    return rows


def parse_nvidia(gpus: str, apps: str) -> tuple[Device, ...]:
    """The GPUs of the :data:`QUERY_GPUS` output *gpus*, each with the pids :data:`QUERY_APPS` printed in *apps*.

    Raises:
        ValueError: a line has the wrong number of fields or a device has no index.

    """
    on: dict[str, list[int]] = {}
    for uuid, pid, _used in _rows(apps, 3):
        if (number := _number(pid)) is not None:
            on.setdefault(uuid, []).append(number)
    devices = []
    for index, uuid, util, used, total in _rows(gpus, 5):
        position = _number(index)
        if position is None:
            msg = f'nvidia-smi printed a device without an index: {index!r}'
            raise ValueError(msg)
        devices.append(
            Device(
                GPU,
                'nvidia',
                position,
                uuid,
                _number(util),
                _number(used) or 0,
                _number(total) or 0,
                tuple(on.get(uuid, ())),
            )
        )
    return tuple(devices)


def _ran(argv: Sequence[str]) -> str:
    done = _door.run(
        list(argv), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=PROBE_S, check=False
    )
    if done.returncode != 0:
        msg = f'{Path(argv[0]).name} exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}'
        raise RuntimeError(msg)
    return done.stdout


_PROBE_FAILED: Final = (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError)


def _nvidia() -> tuple[Device, ...]:
    executable = shutil.which('nvidia-smi')
    if executable is None:
        msg = 'nvidia-smi is not on PATH'
        raise RuntimeError(msg)
    return parse_nvidia(_ran([executable, *QUERY_GPUS]), _ran([executable, *QUERY_APPS]))


#: One PowerShell look at the Intel display adapters and the present compute accelerators, as JSON.
_WINDOWS_INVENTORY: Final = (
    "$g=@(Get-CimInstance Win32_VideoController | Where-Object AdapterCompatibility -like 'Intel*' | "
    'ForEach-Object { @{ name = $_.Name; bytes = [int64]$_.AdapterRAM } }); '
    '$n=@(Get-PnpDevice -Class ComputeAccelerator -PresentOnly -ErrorAction SilentlyContinue | '
    "Where-Object { $_.Status -eq 'OK' -and $_.InstanceId -like '*VEN_8086*' } | "
    'ForEach-Object { @{ name = $_.FriendlyName; bytes = 0 } }); '
    '@{ gpu = $g; npu = $n } | ConvertTo-Json -Compress -Depth 3'
)


def parse_intel_windows(text: str) -> tuple[Device, ...]:
    """The Intel GPUs and NPUs of :data:`_WINDOWS_INVENTORY`'s JSON; their use is unknown (module docstring)."""
    found = json.loads(text)
    return tuple(
        Device(kind, 'intel', index, str(one['name']), None, 0, int(one.get('bytes') or 0) // 2**20)
        for kind in KINDS
        for index, one in enumerate(found.get(kind) or [])
    )


def _intel_linux(root: Path = Path('/sys/class')) -> tuple[Device, ...]:
    """The Intel GPUs (``drm/card<N>``) and NPUs (``accel/accel<N>``) under *root*; their use is unknown."""

    def intel(paths: Iterable[Path]) -> list[Path]:
        return [p for p in sorted(paths) if (p / 'device' / 'vendor').is_file() and _vendor(p) == _INTEL_PCI]

    gpus = intel(p for p in (root / 'drm').glob('card*') if p.name[4:].isdecimal())
    npus = intel((root / 'accel').glob('accel*'))
    return tuple(
        Device(kind, 'intel', index, path.name, None, 0, 0)
        for kind, paths in ((GPU, gpus), (NPU, npus))
        for index, path in enumerate(paths)
    )


def _vendor(path: Path) -> str:
    return (path / 'device' / 'vendor').read_text(encoding='ascii').strip().lower()


@functools.cache
def _intel() -> tuple[Device, ...]:
    """The Intel inventory, read once per process: hardware does not come and go between two polls."""
    if sys.platform == 'win32':
        powershell = shutil.which('powershell') or shutil.which('pwsh')
        if powershell is None:
            msg = 'no PowerShell to read the device inventory'
            raise RuntimeError(msg)
        return parse_intel_windows(_ran([powershell, '-NoProfile', '-NonInteractive', '-Command', _WINDOWS_INVENTORY]))
    if sys.platform.startswith('linux'):
        return _intel_linux()
    msg = f'no Intel device reader on {sys.platform}'
    raise RuntimeError(msg)


#: Each vendor's probe: devices, or an exception saying why it cannot say.
PROBES: Final[Mapping[str, Callable[[], tuple[Device, ...]]]] = {'nvidia': _nvidia, 'intel': _intel}


def read_devices(probes: Mapping[str, Callable[[], tuple[Device, ...]]] = PROBES) -> Devices:
    """The box's devices now: every probe's, and why each that could not say is blind."""
    devices: list[Device] = []
    blind: dict[str, str] = {}
    for vendor, probe in probes.items():
        try:
            devices.extend(probe())
        except _PROBE_FAILED as why:
            blind[vendor] = str(why) or type(why).__name__
    return Devices(tuple(devices), blind)


# --------------------------------------------------------------------------------------------
# Size, room, assignment, binding
# --------------------------------------------------------------------------------------------


def sizes(devices: Devices) -> tuple[dict[str, int], dict[str, str]]:
    """``(sizes, blind)`` for :class:`~lab_commons.box.Capacity`, what a status view shows.

    Each kind's count, the largest GPU's GiB and each precision's count; admission decides on the devices
    themselves (:func:`assign`). A name no device supplies is absent (it sizes 0); a kind
    :meth:`Devices.unread` names is blind.
    """
    found = devices.devices
    counts = {
        **{kind: sum(d.kind == kind for d in found) for kind in KINDS},
        VRAM_GB: max((d.total_mib // _MIB_PER_GIB for d in found if d.kind == GPU), default=0),
        **{p: sum(p in d.precisions for d in found) for p in PRECISIONS},
    }
    blind = devices.unread()
    if GPU in blind:
        blind[VRAM_GB] = blind[GPU]
    return {name: count for name, count in counts.items() if count}, blind


def _held_gib(device: Device, held: Sequence[Holding]) -> int:
    return sum(int(h.seats.get(VRAM_GB, 0)) for h in held if device.id in h.devices)


def _exclusively_held(device: Device, held: Sequence[Holding]) -> bool:
    return any(device.id in h.devices and h.seats.get(EXCLUSIVE, 0) for h in held)


def free_gib(device: Device, held: Sequence[Holding]) -> int:
    """The whole GiB a new shared claim may take on *device*.

    Its memory less the larger of what is in use now (every process, admitted or not) and what its claims
    declared: the seat formula's MAX, per device.
    """
    return max(0, device.total_mib - max(device.used_mib, _held_gib(device, held) * _MIB_PER_GIB)) // _MIB_PER_GIB


def _usable(device: Device, held: Sequence[Holding], *, exclusive: bool, vram_gb: int) -> bool:
    """Whether a new claim may take *device*: whole (idle, no claim on it) or a memory share of an OBSERVED one."""
    if exclusive or device.kind != GPU:
        return device.idle and not any(device.id in h.devices for h in held)
    observed = device.utilization_pct is not None
    return observed and not _exclusively_held(device, held) and free_gib(device, held) >= vram_gb


def free(
    devices: Devices,
    held: Sequence[Holding],
    *,
    kind: str | None = None,
    needs: Iterable[str] = (),
    exclusive: bool = True,
    vram_gb: int = 0,
) -> list[Device]:
    """Devices a new claim may take, in id order, so equal claims bind equally.

    Of *kind*, doing every precision in *needs*, and usable whole (*exclusive*) or by a *vram_gb* share.
    """
    wanted = set(needs)
    usable = [
        d
        for d in devices.devices
        if kind in {None, d.kind} and wanted <= d.precisions and _usable(d, held, exclusive=exclusive, vram_gb=vram_gb)
    ]
    return sorted(usable, key=lambda d: (d.kind, d.vendor, d.index))


def assign(demand: Mapping[str, float], devices: Devices, held: Sequence[Holding]) -> tuple[str, ...] | str:
    """The device ids *demand* takes, or why the free devices cannot serve it, naming each one lacking.

    Every taken device does every precision the demand names. A GPU is taken WHOLE when the demand says
    :data:`EXCLUSIVE`, else as a ``vram_gb`` share of EACH GPU taken; an NPU is always whole.
    """
    needs = sorted(p for p in PRECISIONS if demand.get(p, 0))
    exclusive = bool(demand.get(EXCLUSIVE, 0))
    vram = int(demand.get(VRAM_GB, 0))
    chosen: list[str] = []
    for kind in KINDS:
        count = int(demand.get(kind, 0))
        if not count:
            continue
        fit = free(devices, held, kind=kind, needs=needs, exclusive=exclusive, vram_gb=vram)[:count]
        if len(fit) < count:
            return _why_not(kind, count, needs, devices, held, exclusive=exclusive, vram=vram, usable=len(fit))
        chosen.extend(d.id for d in fit)
    return tuple(chosen)


def _why_not(
    kind: str,
    count: int,
    needs: Sequence[str],
    devices: Devices,
    held: Sequence[Holding],
    *,
    exclusive: bool,
    vram: int,
    usable: int,
) -> str:
    mine = [d for d in devices.devices if d.kind == kind]
    whole = exclusive or kind != GPU
    lacking = [
        f'{d.id} lacks {"+".join(sorted(set(needs) - d.precisions))}' for d in mine if not set(needs) <= d.precisions
    ]
    busy = [
        f'{d.id} is in use' if whole else f'{d.id} has {free_gib(d, held)} GiB free'
        for d in mine
        if set(needs) <= d.precisions
    ]
    why = ', '.join([*lacking, *busy]) or 'none is on this box'
    asked = (
        f'{count} asked' + (f' doing {"+".join(needs)}' if needs else '') + ('' if whole else f' at {vram} GiB each')
    )
    return f'{kind}: {asked}, {usable} usable -- {why}'


def oversized(demand: Mapping[str, float], seats: Mapping[str, int], blind: Mapping[str, str]) -> str | None:
    """Why *demand* can NEVER be served by a box of these device *seats* (:func:`sizes`) however idle, else ``None``.

    A blind kind, more devices than the box has, more ``vram_gb`` than its largest GPU, a precision too few do.
    """
    asked = {kind: int(demand.get(kind, 0)) for kind in KINDS if demand.get(kind, 0)}
    for kind, count in asked.items():
        if kind in blind:
            return f'{kind} is not measured: {blind[kind]}'
        if count > seats.get(kind, 0):
            return f'{kind}: {count} asked, this box has {seats.get(kind, 0)}'
    if demand.get(VRAM_GB, 0) > seats.get(VRAM_GB, 0):
        return f'{VRAM_GB}: {demand[VRAM_GB]:g} asked, the largest GPU here has {seats.get(VRAM_GB, 0)}'
    needs = [p for p in PRECISIONS if demand.get(p, 0)]
    short = [p for p in needs if seats.get(p, 0) < sum(asked.values())]
    return f'{"+".join(short)}: fewer devices here do it than are asked' if short else None


def binding(held: Sequence[str]) -> dict[str, str]:
    """The environment that tells a boxed run the device ids it *held*: every variable empty when it holds none.

    OpenVINO numbers Intel GPUs ``GPU.<n>`` in the order the OS enumerates them, which is the order the probe
    indexes them in; an NPU is ``NPU``.
    """
    parts = [one.split('.') for one in held]
    nvidia = [index for kind, vendor, index in parts if (kind, vendor) == (GPU, 'nvidia')]
    intel = [f'GPU.{index}' if kind == GPU else 'NPU' for kind, vendor, index in parts if vendor == 'intel']
    return {
        DEVICES_ENV: ','.join(held),
        VISIBLE_ENV: ','.join(nvidia),
        ORDER_ENV: 'PCI_BUS_ID',
        OPENVINO_ENV: ','.join(intel),
    }
