"""THE BOX'S DEVICES: a demand asks ``gpu``/``npu``, ``vram_gb``, a precision, ``exclusive``; probes say what is free.

A GPU is shared by memory -- several claims while their declarations and whatever else is resident fit -- or taken
whole when the demand says ``exclusive``, and only when idle: a job the box never admitted takes the device it runs
on. A device lacking a demanded precision is never taken. A box whose devices cannot be read is BLIND and lends none.
Each test drives the decision with a reading it builds; the admitted claim names its devices and the boxed child is
told them once (``CUDA_VISIBLE_DEVICES``, the OpenVINO names).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from _box_fake import quiet

from lab_commons.box import GB, Capacity, Holding, NotAdmitted, ProcessEnd, Refused, hold, run
from lab_commons.box import devices as box_devices
from lab_commons.box.__main__ import main
from lab_commons.box.demand import checked
from lab_commons.box.devices import (
    BUSY_UTIL_PCT,
    DEVICES_ENV,
    EXCLUSIVE,
    GPU,
    NPU,
    OPENVINO_ENV,
    ORDER_ENV,
    VISIBLE_ENV,
    VRAM_GB,
    Device,
    Devices,
    assign,
    binding,
    oversized,
    parse_intel_windows,
    parse_nvidia,
    read_devices,
    sizes,
)
from lab_commons.box.run import _ENFORCERS
from lab_commons.box.supply import Reading, room
from lab_commons.compute.model import Demand
from lab_commons.dimensions import DIMENSIONS, PRECISIONS
from lab_commons.dimensions import EXCLUSIVE as EXCLUSIVE_NAME
from lab_commons.dimensions import GPU as GPU_DIMENSION
from lab_commons.dimensions import NPU as NPU_DIMENSION
from lab_commons.dimensions import VRAM as VRAM_DIMENSION

#: What WS5's RTX PRO 4000 printed on 2026-11-08 under a foreign job: 100 %, and the app's memory is [N/A] (WDDM).
_WS5_GPUS = '0, GPU-c904dcf7-e9c1-342f-d143-9bbb6c01b3d3, 100, 9570, 24467\n'
_WS5_APPS = 'GPU-c904dcf7-e9c1-342f-d143-9bbb6c01b3d3, 49244, [N/A]\n'
#: WS5's Intel inventory as the Windows probe's PowerShell prints it.
_WS5_INTEL = '{"gpu":[{"name":"Intel(R) Graphics","bytes":2147479552}],"npu":[{"name":"Intel(R) AI Boost","bytes":0}]}'

_GIB = 1024


def _nvidia(index: int, *, util: int | None = 0, used_mib: int = 0, pids: tuple[int, ...] = ()) -> Device:
    return Device(GPU, 'nvidia', index, f'GPU-{index}', util, used_mib, 24 * _GIB, pids)


def _npu(*, util: int | None = 0) -> Device:
    return Device(NPU, 'intel', 0, 'AI Boost', util, 0, 0)


def _igpu(*, util: int | None = 0) -> Device:
    return Device(GPU, 'intel', 0, 'Intel(R) Graphics', util, 0, 2 * _GIB)


def _ws5() -> Devices:
    return Devices((*parse_nvidia(_WS5_GPUS, _WS5_APPS), *parse_intel_windows(_WS5_INTEL)))


def _cap(devices: Devices) -> Capacity:
    seats, blind = sizes(devices)
    return Capacity(cpu=4.0, mem_bytes=8 * GB, seats=seats, blind=blind)


def _reader(capacity: Capacity, devices: Devices) -> Callable[[Iterable[int]], Reading]:
    """:func:`_box_fake.quiet` with *devices* as its device look."""
    base = quiet(capacity)
    return lambda pids: replace(base(pids), devices=devices)


def _held(devices: tuple[str, ...], **seats: int) -> Holding:
    return Holding('held', 1, 0, 0, seats, 0.0, devices=devices)


def _quiet() -> Reading:
    return Reading(cores=4.0, idle_cores=4.0, total_bytes=8 * GB, available_bytes=8 * GB)


# ---- one vocabulary ----


def test_the_box_and_the_cluster_speak_one_device_vocabulary() -> None:
    """Single source: the box's names ARE the dimension table's, which the hpc plan reads too."""
    assert (
        GPU_DIMENSION.name,
        NPU_DIMENSION.name,
        VRAM_DIMENSION.name,
        EXCLUSIVE_NAME,
    ) == (GPU, NPU, VRAM_GB, EXCLUSIVE)
    assert {GPU, NPU, VRAM_GB} <= set(DIMENSIONS)
    assert PRECISIONS == ('f64', 'f32', 'f16', 'int8')


# ---- the probes ----


def test_nvidia_smi_is_parsed_per_device_and_its_apps_land_on_their_device_by_uuid() -> None:
    (device,) = parse_nvidia(_WS5_GPUS, _WS5_APPS)
    assert device.id == 'gpu.nvidia.0'
    assert (device.utilization_pct, device.used_mib, device.total_mib, device.pids) == (100, 9570, 24467, (49244,))
    two = parse_nvidia('0, GPU-a, 0, 10, 1000\n1, GPU-b, [N/A], [N/A], 2000\n', 'GPU-b, 7, 300\n')
    assert [d.pids for d in two] == [(), (7,)]
    assert two[1].utilization_pct is None, 'an unreadable utilisation is unknown, never 0'


def test_malformed_nvidia_output_is_refused_by_line() -> None:
    with pytest.raises(ValueError, match='nvidia-smi'):
        parse_nvidia('0, GPU-a, 0\n', '')


def test_the_intel_inventory_finds_its_devices_and_does_not_know_their_use() -> None:
    igpu, npu = parse_intel_windows(_WS5_INTEL)
    assert (igpu.id, npu.id) == ('gpu.intel.0', 'npu.intel.0')
    assert igpu.total_mib == 2047
    assert igpu.utilization_pct is None
    assert npu.utilization_pct is None


def test_the_linux_intel_reader_reads_sysfs_vendor_0x8086(tmp_path: Path) -> None:
    for parent, name, vendor in [('drm', 'card0', '0x8086'), ('drm', 'card1', '0x10de'), ('accel', 'accel0', '0x8086')]:
        device = tmp_path / parent / name / 'device'
        device.mkdir(parents=True)
        (device / 'vendor').write_text(vendor + '\n', encoding='ascii')
    (tmp_path / 'drm' / 'card0-DP-1').mkdir()  # a connector, not a card: PLANTED, never counted
    found = box_devices._intel_linux(tmp_path)
    assert [d.id for d in found] == ['gpu.intel.0', 'npu.intel.0']


def test_a_probe_that_cannot_say_is_blind_and_names_why() -> None:
    def missing() -> tuple[Device, ...]:
        msg = 'nvidia-smi is not on PATH'
        raise RuntimeError(msg)

    read = read_devices({'nvidia': missing, 'intel': lambda: (_npu(),)})
    assert read.blind == {'nvidia': 'nvidia-smi is not on PATH'}
    assert read.unread() == {GPU: 'nvidia: nvidia-smi is not on PATH'}, 'a found kind is not blind'


def test_a_failing_nvidia_smi_is_blind_and_a_working_one_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(box_devices.shutil, 'which', lambda _name: 'nvidia-smi')

    def answered(code: int) -> Callable[..., subprocess.CompletedProcess[str]]:
        def fake(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
            out = _WS5_GPUS if any('query-gpu' in a for a in argv) else _WS5_APPS
            return subprocess.CompletedProcess(argv, code, out, 'NVIDIA-SMI has failed')

        return fake

    monkeypatch.setattr(box_devices._door, 'run', answered(9))
    assert 'nvidia' in read_devices({'nvidia': box_devices._nvidia}).blind
    monkeypatch.setattr(box_devices._door, 'run', answered(0))
    assert [d.id for d in read_devices({'nvidia': box_devices._nvidia}).devices] == ['gpu.nvidia.0']


def test_sizes_count_kinds_and_precisions_and_a_blind_kind_sizes_nothing() -> None:
    seats, blind = sizes(_ws5())
    assert seats == {GPU: 2, NPU: 1, VRAM_GB: 23, 'f64': 1, 'f32': 2, 'f16': 3, 'int8': 3}
    assert blind == {}
    seats, blind = sizes(Devices(blind={'nvidia': 'nvidia-smi is not on PATH'}))
    assert seats == {}
    assert blind[GPU] == blind[VRAM_GB] == 'nvidia: nvidia-smi is not on PATH'


# ---- the decision ----


def test_an_idle_box_serves_whole_shared_and_precise_demands() -> None:
    """PLANTED CONTROL for every refusal below: on idle devices each demand is served."""
    idle = Devices((_nvidia(0), _igpu(), _npu()))
    assert assign({GPU: 1, EXCLUSIVE: 1, 'f64': 1}, idle, ()) == ('gpu.nvidia.0',)
    assert assign({GPU: 1, VRAM_GB: 1}, idle, ()) == ('gpu.intel.0',), 'id order: intel sorts before nvidia'
    assert assign({NPU: 1, 'int8': 1}, idle, ()) == ('npu.intel.0',)


def test_a_foreign_job_at_full_utilisation_refuses_an_exclusive_gpu() -> None:
    """THE CHECK, on WS5's own reading: 100 % on a job the box never admitted -- the GPU cannot be taken whole."""
    verdict = assign({GPU: 1, EXCLUSIVE: 1, 'f64': 1}, _ws5(), ())
    assert isinstance(verdict, str)
    assert 'gpu.nvidia.0 is in use' in verdict


def test_foreign_memory_counts_against_a_shared_gpu() -> None:
    """Shared by memory: 9.3 of 23.9 GiB foreign leaves 14 GiB -- a 14 GiB share fits, a 15 GiB one does not."""
    assert assign({GPU: 1, VRAM_GB: 14, 'f64': 1}, _ws5(), ()) == ('gpu.nvidia.0',)
    verdict = assign({GPU: 1, VRAM_GB: 15, 'f64': 1}, _ws5(), ())
    assert isinstance(verdict, str)
    assert 'gpu.nvidia.0 has 14 GiB free' in verdict


def test_shares_sum_on_one_gpu_until_its_memory_is_spent() -> None:
    one = Devices((_nvidia(0),))
    held = [_held(('gpu.nvidia.0',), **{GPU: 1, VRAM_GB: 10}), _held(('gpu.nvidia.0',), **{GPU: 1, VRAM_GB: 10})]
    assert assign({GPU: 1, VRAM_GB: 4}, one, held) == ('gpu.nvidia.0',)
    assert isinstance(assign({GPU: 1, VRAM_GB: 5}, one, held), str), '10 + 10 + 5 > 24'
    assert isinstance(assign({GPU: 1, EXCLUSIVE: 1}, one, held[:1]), str), 'a shared GPU is never taken whole'
    whole = [_held(('gpu.nvidia.0',), **{GPU: 1, EXCLUSIVE: 1})]
    assert isinstance(assign({GPU: 1, VRAM_GB: 1}, one, whole), str), 'a GPU taken whole shares with nobody'


@pytest.mark.parametrize(
    ('demand', 'lacking'),
    [({NPU: 1, 'f64': 1}, 'npu.intel.0 lacks f64'), ({GPU: 1, VRAM_GB: 1, 'f64': 1}, 'gpu.intel.0 lacks f64')],
    ids=['f64-on-the-npu', 'f64-on-the-igpu'],
)
def test_a_device_lacking_the_demanded_precision_is_never_taken(demand: Mapping[str, int], lacking: str) -> None:
    verdict = assign(demand, Devices((_igpu(), _npu())), ())
    assert isinstance(verdict, str)
    assert lacking in verdict


@pytest.mark.parametrize(
    'busy',
    [_nvidia(0, util=BUSY_UTIL_PCT), _nvidia(0, pids=(4242,)), _nvidia(0, util=None)],
    ids=['utilised', 'a-process-runs-on-it', 'utilisation-unknown'],
)
def test_a_device_in_use_outside_the_ledger_is_not_taken_whole(busy: Device) -> None:
    assert assign({GPU: 1, EXCLUSIVE: 1}, Devices((busy, _nvidia(1))), ()) == ('gpu.nvidia.1',)


def test_a_device_whose_use_is_unobserved_is_not_lent_at_all() -> None:
    """The Intel inventory finds devices it cannot watch: neither a share nor the whole of one is lent."""
    unwatched = Devices(parse_intel_windows(_WS5_INTEL))
    assert isinstance(assign({GPU: 1, VRAM_GB: 1}, unwatched, ()), str)
    assert isinstance(assign({NPU: 1}, unwatched, ()), str)


def test_a_box_too_small_or_blind_is_refused_at_once() -> None:
    seats, blind = sizes(_ws5())
    assert oversized({GPU: 1, VRAM_GB: 8}, seats, blind) is None
    assert 'largest GPU' in (oversized({GPU: 1, VRAM_GB: 64}, seats, blind) or '')
    assert 'f64' in (oversized({GPU: 2, VRAM_GB: 1, 'f64': 1}, seats, blind) or '')
    no_nvidia = sizes(Devices(blind={'nvidia': 'nvidia-smi is not on PATH'}))
    assert 'nvidia-smi is not on PATH' in (oversized({GPU: 1, VRAM_GB: 1}, *no_nvidia) or '')


def test_the_demand_is_checked_for_its_device_names() -> None:
    with pytest.raises(ValueError, match='vram_gb needs gpu'):
        checked({'cpu': 1, 'mem_gb': 1, VRAM_GB: 8}, 60)
    with pytest.raises(ValueError, match='states its vram_gb'):
        checked({'cpu': 1, 'mem_gb': 1, GPU: 1}, 60)
    with pytest.raises(ValueError, match='gpu'):
        checked({'cpu': 1, 'mem_gb': 1, GPU: 0.5, VRAM_GB: 1}, 60)
    assert checked({'cpu': 1, 'mem_gb': 1, GPU: 1, EXCLUSIVE: 1}, 60)


def test_room_reports_the_device_view() -> None:
    box = Devices((_nvidia(0, used_mib=4 * _GIB), _nvidia(1)))
    free = room(_cap(box), [_held(('gpu.nvidia.1',), **{GPU: 1, VRAM_GB: 20})], replace(_quiet(), devices=box))
    assert free[GPU] == 1, 'usable whole: device 0 is idle, device 1 has a claim on it'
    assert free[VRAM_GB] == 20, 'the largest share free: device 0, 24 - 4 GiB'
    assert room(_cap(box), [], _quiet())[GPU] == 0, 'a reading that took no device look lends none'


# ---- the ledger, the binding, the view ----


def test_claims_share_one_gpu_by_memory_and_the_next_is_refused_naming_why() -> None:
    box = Devices((_nvidia(0),))
    size = _cap(box)
    read = _reader(size, box)
    share = Demand(cpu=1, mem_gb=1, gpu=1, vram_gb=10)
    with (
        hold(share, 60, 'fold 1', 0, capacity=size, read=read, key='a') as first,
        hold(share, 60, 'fold 2', 0, capacity=size, read=read, key='b') as second,
    ):
        assert first.devices == second.devices == ('gpu.nvidia.0',)
        with (
            pytest.raises(NotAdmitted, match=r'gpu\.nvidia\.0 has 4 GiB free'),
            hold(share, 60, 'fold 3', 0, capacity=size, read=read, key='c'),
        ):
            pass


def test_a_foreign_busy_gpu_refuses_an_exclusive_hold() -> None:
    box = _ws5()
    size = _cap(box)
    exclusive = Demand(cpu=1, mem_gb=1, gpu=1, exclusive=1, f64=1)
    with (
        pytest.raises(NotAdmitted, match=r'gpu\.nvidia\.0 is in use'),
        hold(exclusive, 60, 'ws5', 0, capacity=size, read=_reader(size, box), key='k'),
    ):
        pass


def test_a_blind_box_refuses_a_gpu_hold_at_once_naming_why() -> None:
    blind = Devices(blind={'nvidia': 'nvidia-smi is not on PATH'})
    size = _cap(blind)
    demand = Demand(cpu=1, mem_gb=1, gpu=1, vram_gb=1)
    with (
        pytest.raises(Refused, match='nvidia-smi is not on PATH'),
        hold(demand, 60, 'b', 30, capacity=size, read=_reader(size, blind), key='k'),
    ):
        pass


def test_the_binding_is_one_export_per_vendor_and_empty_without_a_device() -> None:
    assert binding(('gpu.intel.0', 'gpu.nvidia.1', 'npu.intel.0')) == {
        DEVICES_ENV: 'gpu.intel.0,gpu.nvidia.1,npu.intel.0',
        VISIBLE_ENV: '1',
        ORDER_ENV: 'PCI_BUS_ID',
        OPENVINO_ENV: 'GPU.0,NPU',
    }
    assert binding(()) == {DEVICES_ENV: '', VISIBLE_ENV: '', ORDER_ENV: 'PCI_BUS_ID', OPENVINO_ENV: ''}


def test_equal_claims_bind_equally_and_a_claims_device_is_stable() -> None:
    """Determinism: a seeded run sees the same binding for the same claim, whatever the momentary free memory.

    PLANTED CONTROL: device 1 has MORE free memory in the second reading; a most-free-first order would move the
    claim to it and change the binding.
    """
    first = Devices((_nvidia(0, used_mib=8 * _GIB), _nvidia(1, used_mib=12 * _GIB)))
    second = Devices((_nvidia(0, used_mib=8 * _GIB), _nvidia(1, used_mib=0)))
    demand = {GPU: 1, VRAM_GB: 4}
    taken = [assign(demand, first, ()), assign(demand, second, ())]
    assert taken == [('gpu.nvidia.0',)] * 2
    assert [binding(t) for t in taken if not isinstance(t, str)] == [binding(('gpu.nvidia.0',))] * 2
    assert binding(('gpu.nvidia.0',))[VISIBLE_ENV] == '0'


def test_the_child_is_told_its_devices_and_a_run_without_one_sees_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Declared == used: the child's environment carries the claim's binding, overriding the caller's own."""
    seen: list[Mapping[str, str]] = []

    def fake(_argv: object, _demand: object, _wall_s: object, *, env: Mapping[str, str], **_kw: object) -> ProcessEnd:
        seen.append(env)
        return ProcessEnd(0, 'exit', 0, 0.0, 0.0, 'fake', 'k', None)

    monkeypatch.setitem(_ENFORCERS, sys.platform, fake)
    box = Devices((_nvidia(0), _nvidia(1)))
    size = _cap(box)
    read = _reader(size, box)
    run(['x'], Demand(cpu=1, mem_gb=1, gpu=2, exclusive=1), 60, 'two', 0, key='k', capacity=size, read=read)
    run(['x'], Demand(cpu=1, mem_gb=1), 60, 'none', 0, key='k', capacity=size, read=read, env={VISIBLE_ENV: '0'})
    assert [e[VISIBLE_ENV] for e in seen] == ['0,1', '']
    assert {e[ORDER_ENV] for e in seen} == {'PCI_BUS_ID'}


def test_the_claims_view_shows_the_devices_a_claim_holds(capsys: pytest.CaptureFixture[str]) -> None:
    box = Devices((_nvidia(0),))
    size = _cap(box)
    demand = Demand(cpu=1, mem_gb=1, gpu=1, vram_gb=2)
    with hold(demand, 60, 'shown', 0, capacity=size, read=_reader(size, box), key='k'):
        assert main(['claims']) == 0
    assert "devices=['gpu.nvidia.0']" in capsys.readouterr().out
