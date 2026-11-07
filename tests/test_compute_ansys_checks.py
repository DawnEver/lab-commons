"""The Ansys engine checks: found on PATH, in an AWP_ROOT or a standard release; the release read off the path."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_commons.compute import toolchecks
from lab_commons.compute.toolchecks import ANSYS_ENGINES, CHECKS, ansys_find, ansys_release

ADA = 'ANSYS/2025R1/v251'

#: (engine, the file a Linux cluster module puts on PATH, relative to its release root).
LINUX_MODULE = [
    ('mapdl', 'ansys/bin/mapdl'),
    ('mapdl', 'ansys/bin/ansys251'),
    ('fluent', 'fluent/bin/fluent'),
    ('lsdyna', 'ansys/bin/lsdyna'),
]

#: (engine, the file under a Windows release root).
WINDOWS_INSTALL = [
    ('mapdl', 'ansys/bin/winx64/ANSYS251.exe'),
    ('fluent', 'fluent/ntbin/win64/fluent.exe'),
    ('fluent', 'AnsysEM/fluent/ntbin/win64/fluent.exe'),
    ('lsdyna', 'ansys/bin/winx64/lsdyna_dp.exe'),
    ('aedt', 'AnsysEM/ansysedt.exe'),
]


def _plant(root: Path, rel: str) -> Path:
    exe = root / rel
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text('', encoding='utf-8')
    return exe


@pytest.fixture(autouse=True)
def _no_standard_installs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(toolchecks, 'ANSYS_INSTALLS', {'linux': (str(tmp_path / 'none'),), 'windows': ()})


def test_each_engine_is_checked_under_the_serving_repos_resource_name() -> None:
    assert {'aedt', 'mapdl', 'fluent', 'lsdyna'} <= set(CHECKS)
    assert set(ANSYS_ENGINES) == {'aedt', 'mapdl', 'fluent', 'lsdyna'}
    assert all(CHECKS[engine].heavy for engine in ANSYS_ENGINES)


@pytest.mark.parametrize(('engine', 'rel'), LINUX_MODULE)
def test_a_cluster_module_puts_it_on_path_at_its_release(tmp_path: Path, engine: str, rel: str) -> None:
    exe = _plant(tmp_path / ADA, rel)
    found = ansys_find(engine, platform='linux', environ={'PATH': str(exe.parent)})()
    assert found == ('PATH', exe)
    assert ansys_release(exe) == ('install path', '2025 R1')


@pytest.mark.parametrize(('engine', 'rel'), [*LINUX_MODULE, ('aedt', 'AnsysEM/ansysedt')])
def test_linux_finds_it_under_awp_root(tmp_path: Path, engine: str, rel: str) -> None:
    exe = _plant(tmp_path / 'v251', rel)
    environ = {'PATH': '', 'AWP_ROOT251': str(tmp_path / 'v251')}
    assert ansys_find(engine, platform='linux', environ=environ)() == ('AWP_ROOT251', exe)


def test_the_newest_awp_root_wins(tmp_path: Path) -> None:
    _plant(tmp_path / 'v242', 'fluent/bin/fluent')
    new = _plant(tmp_path / 'v251', 'fluent/bin/fluent')
    environ = {'AWP_ROOT242': str(tmp_path / 'v242'), 'AWP_ROOT251': str(tmp_path / 'v251')}
    assert ansys_find('fluent', platform='linux', environ=environ)() == ('AWP_ROOT251', new)


@pytest.mark.parametrize(('engine', 'rel'), WINDOWS_INSTALL)
def test_windows_finds_it_in_the_newest_standard_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, engine: str, rel: str
) -> None:
    monkeypatch.setattr(toolchecks, 'ANSYS_INSTALLS', {'windows': (str(tmp_path),)})
    _plant(tmp_path / 'v242', rel)
    exe = _plant(tmp_path / 'v251', rel)
    assert ansys_find(engine, platform='win32', environ={'PATH': ''})() == ('install dir', exe)
    assert ansys_release(exe) == ('install path', '2025 R1')


def test_ada_has_no_aedt(tmp_path: Path) -> None:
    for _engine, rel in LINUX_MODULE:
        _plant(tmp_path / ADA, rel)
    path = str(tmp_path / ADA / 'ansys/bin') + toolchecks.os.pathsep + str(tmp_path / ADA / 'fluent/bin')
    assert ansys_find('aedt', platform='linux', environ={'PATH': path})() is None


# --- planted controls --------------------------------------------------------------------------------


@pytest.mark.parametrize('engine', sorted(ANSYS_ENGINES))
@pytest.mark.parametrize('platform', ['linux', 'win32'])
def test_an_empty_path_and_no_install_finds_nothing(engine: str, platform: str) -> None:
    assert ansys_find(engine, platform=platform, environ={'PATH': ''})() is None


@pytest.mark.parametrize('wrong', ['v25', 'v2511', 'vlatest', 'v25a'])
def test_a_directory_not_spelled_as_a_release_is_never_picked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wrong: str
) -> None:
    monkeypatch.setattr(toolchecks, 'ANSYS_INSTALLS', {'windows': (str(tmp_path),)})
    _plant(tmp_path / wrong, 'AnsysEM/ansysedt.exe')
    assert ansys_find('aedt', platform='win32', environ={'PATH': ''})() is None
    assert ansys_release(tmp_path / wrong / 'AnsysEM/ansysedt.exe') is None


def test_an_awp_variable_with_another_suffix_is_not_a_release(tmp_path: Path) -> None:
    _plant(tmp_path / 'v251', 'fluent/bin/fluent')
    environ = {'AWP_ROOT': str(tmp_path / 'v251'), 'AWP_ROOT25': str(tmp_path / 'v251')}
    assert ansys_find('fluent', platform='linux', environ=environ)() is None


def test_the_smoke_only_starts_the_binary_with_its_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def run(argv: list[str], timeout: float) -> tuple[bool, str]:
        seen.append(argv)
        assert timeout <= toolchecks.SMOKE_TIMEOUT_S
        return True, 'exit 0: ok'

    monkeypatch.setattr(toolchecks, '_run', run)
    assert CHECKS['mapdl'].smoke(Path('mapdl')) == (True, 'exit 0: ok')
    assert seen == [['mapdl', '-v']]


# --- lsdyna: the wrapper answers no flag licence-free, so its smoke runs nothing ------------------------


@pytest.mark.parametrize('solver', ['lsdyna_sp.e', 'lsdyna_dp.e'])
def test_lsdyna_smoke_passes_on_the_linux_wrappers_solver_binary(tmp_path: Path, solver: str) -> None:
    wrapper = _plant(tmp_path / ADA, 'ansys/bin/lsdyna')
    _plant(tmp_path / ADA, f'ansys/bin/linx64/{solver}')
    passed, detail = CHECKS['lsdyna'].smoke(wrapper)
    assert passed, detail


@pytest.mark.parametrize('solver', ['lsdyna_sp.exe', 'lsdyna_dp.exe'])
def test_lsdyna_smoke_passes_on_the_windows_solver_binary(tmp_path: Path, solver: str) -> None:
    exe = _plant(tmp_path / 'v251', f'ansys/bin/winx64/{solver}')
    assert CHECKS['lsdyna'].smoke(exe)[0]


def test_lsdyna_smoke_starts_no_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def refuse(*_a: object, **_k: object) -> object:
        msg = 'the lsdyna smoke ran a child'
        raise AssertionError(msg)

    monkeypatch.setattr(toolchecks, '_run', refuse)
    monkeypatch.setattr(toolchecks, '_probe', refuse)
    wrapper = _plant(tmp_path / ADA, 'ansys/bin/lsdyna')
    _plant(tmp_path / ADA, 'ansys/bin/linx64/lsdyna_sp.e')
    assert CHECKS['lsdyna'].smoke(wrapper)[0]


def test_lsdyna_smoke_fails_when_the_solver_binary_is_missing(tmp_path: Path) -> None:
    wrapper = _plant(tmp_path / ADA, 'ansys/bin/lsdyna')
    _plant(tmp_path / ADA, 'ansys/bin/linx64/lsdyna_mpp.e')
    passed, detail = CHECKS['lsdyna'].smoke(wrapper)
    assert not passed
    assert 'lsdyna_sp.e' in detail


def test_lsdyna_smoke_fails_when_the_solver_binary_is_not_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wrapper = _plant(tmp_path / ADA, 'ansys/bin/lsdyna')
    solver = _plant(tmp_path / ADA, 'ansys/bin/linx64/lsdyna_sp.e')
    monkeypatch.setattr(toolchecks.os, 'access', lambda p, _mode: Path(p) != solver)
    assert not CHECKS['lsdyna'].smoke(wrapper)[0]


def test_windows_finds_the_ls_dyna_suite_and_reads_its_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    suites = {'windows': ((str(tmp_path), 'LS-DYNA Suite*/lsdyna/lsdyna_[sd]p.e'),)}
    engine = toolchecks.ANSYS_ENGINES['lsdyna']
    monkeypatch.setitem(
        toolchecks.ANSYS_ENGINES, 'lsdyna', toolchecks.AnsysEngine(engine.on_path, engine.in_release, (), suites)
    )
    exe = _plant(tmp_path / 'LS-DYNA Suite R16.1', 'lsdyna/lsdyna_dp.e')
    assert ansys_find('lsdyna', platform='win32', environ={'PATH': ''})() == ('install dir', exe)
    assert ansys_release(exe) == ('install path', 'R16.1')
    assert CHECKS['lsdyna'].smoke(exe)[0]
