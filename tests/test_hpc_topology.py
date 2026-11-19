"""The cluster catalog: each architecture's node classes, read from the per-user file, cut into lanes.

The catalogs here are SYNTHETIC. A real cluster's entry lives in the user's catalog file, never in the repo
(:mod:`lab_commons.hpc.topology`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons.hpc.topology import Lane, catalog_path, cpu_ids, cpulist, load_catalog

#: Shaped like a 2-socket part with L3-as-NUMA: 16 domains of 6 CPUs, domain k = CPUs 6k..6k+5 = NUMA node k.
L3_NUMA = (
    '[arch-a.class.standard]\npartitions = ["defq", "shortq"]\nsockets = 2\ndomains = [\n'
    + ''.join(f'  {{ cpus = "{6 * k}-{6 * k + 5}", numa = {k} }},\n' for k in range(16))
    + ']\n'
)
#: A different architecture: 2 sockets, 4 L3 domains of 8 CPUs interleaved across sockets, one NUMA node per socket.
INTERLEAVED = """
[arch-b.class.thin]
partitions = ["batch"]
sockets = 2
domains = [
  { cpus = "0,2,4,6,8,10,12,14", numa = 0 },
  { cpus = "16,18,20,22,24,26,28,30", numa = 0 },
  { cpus = "1,3,5,7,9,11,13,15", numa = 1 },
  { cpus = "17,19,21,23,25,27,29,31", numa = 1 },
]
"""


def _catalog(tmp_path: Path, text: str = L3_NUMA + INTERLEAVED) -> Path:
    path = tmp_path / 'clusters.toml'
    path.write_text(text, encoding='utf-8')
    return path


def test_the_catalog_sits_beside_the_machine_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('LAB_COMMONS_CONFIG', str(tmp_path / 'config.toml'))
    assert catalog_path() == tmp_path / 'hpc' / 'clusters.toml'


def test_cpulists_read_and_write_the_linux_format() -> None:
    assert cpu_ids('0-2,7,9-10') == (0, 1, 2, 7, 9, 10)
    assert cpulist((0, 1, 2, 7, 9, 10)) == '0-2,7,9-10'


def test_an_l3_numa_architecture_cuts_two_domain_lanes_twelve_by_eight(tmp_path: Path) -> None:
    standard = load_catalog(_catalog(tmp_path))['arch-a'].node_class(('shortq',))
    assert standard is not None
    assert standard.cpus == 96
    lanes = standard.lanes(2)
    assert len(lanes) == 8
    assert lanes[0] == Lane(cpus='0-11', mems='0-1')
    assert lanes[7] == Lane(cpus='84-95', mems='14-15')
    assert len(standard.lanes(1)) == 16
    assert standard.lanes(16) == (Lane(cpus='0-95', mems='0-15'),)


def test_a_second_architecture_derives_its_own_lanes(tmp_path: Path) -> None:
    thin = load_catalog(_catalog(tmp_path))['arch-b'].node_class(('batch',))
    assert thin is not None
    assert thin.cpus == 32
    assert thin.lanes(2) == (
        Lane(cpus='0,2,4,6,8,10,12,14,16,18,20,22,24,26,28,30', mems='0'),
        Lane(cpus='1,3,5,7,9,11,13,15,17,19,21,23,25,27,29,31', mems='1'),
    )


def test_a_partition_no_class_describes_has_no_class(tmp_path: Path) -> None:
    assert load_catalog(_catalog(tmp_path))['arch-a'].node_class(('hmemq',)) is None


def test_a_lane_that_does_not_tile_the_sockets_is_refused(tmp_path: Path) -> None:
    standard = load_catalog(_catalog(tmp_path))['arch-a'].node_class(('defq',))
    assert standard is not None
    for bad in (3, 12, 0):
        with pytest.raises(ValueError, match='lane_domains'):
            standard.lanes(bad)


@pytest.mark.parametrize(
    ('text', 'why'),
    [
        ('[x.class.c]\npartitions = ["p"]\nsockets = 2\ndomains = [{ cpus = "0-5", numa = 0 }]\n', 'sockets'),
        (
            (
                '[x.class.c]\npartitions = ["p"]\nsockets = 1\n'
                'domains = [{ cpus = "0-5", numa = 0 }, { cpus = "5-9", numa = 1 }]\n'
            ),
            'twice',
        ),
        ('[x.class.c]\npartitions = ["p"]\nsockets = 1\ndomains = [{ cpus = "0-5" }]\n', 'numa'),
        ('[x.class.c]\npartitions = ["p"]\nsockets = 1\ncores = 4\ndomains = [{ cpus = "0", numa = 0 }]\n', 'cores'),
        (
            (
                '[x.class.c]\npartitions = ["p"]\nsockets = 1\ndomains = [{ cpus = "0", numa = 0 }]\n'
                '[x.class.d]\npartitions = ["p"]\nsockets = 1\ndomains = [{ cpus = "0", numa = 0 }]\n'
            ),
            'two classes',
        ),
    ],
)
def test_a_malformed_catalog_is_refused_naming_the_file(tmp_path: Path, text: str, why: str) -> None:
    path = _catalog(tmp_path, text)
    with pytest.raises(ValueError, match=why) as refused:
        load_catalog(path)
    assert str(path) in str(refused.value)


def test_a_missing_catalog_names_the_path_and_the_link(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match='junction') as refused:
        load_catalog(tmp_path / 'hpc' / 'clusters.toml')
    assert str(tmp_path / 'hpc' / 'clusters.toml') in str(refused.value)
