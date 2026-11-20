"""An HPC account's layout: one producer (:func:`lab_commons.hpc.layout.account_paths`), under the declared root."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lab_commons.hpc import layout
from lab_commons.hpc.layout import ROOT, AccountPaths, RootUndeclared, account_paths, export_line, run_tree

HPC = Path(layout.__file__).parent

#: Modules that legitimately name the LOGIN home: the home quota/census door, a job's own declared workdir (``~``
#: expanded by :func:`lab_commons.hpc.cluster._sh_path`, its default ``~``), and the slurm probe's home inode row.
LOGIN_HOME_MODULES = frozenset({'home.py', 'cluster.py', 'config.py', 'jobs.py', 'slurm.py'})


def _home_literals(source: str) -> list[str]:
    """Every non-docstring string literal of *source* that names a home path or the retired ``ci`` tree."""
    tree = ast.parse(source)
    docs = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            text = node.value
            if '~/' in text or '$HOME' in text or '/ci/' in text or text.startswith('ci/') or 'lab-forge' in text:
                found.append(text)
    return found


def test_every_path_derives_from_the_declared_root_and_serving_dirs_only_for_a_serving_role() -> None:
    research = account_paths('/epccfs/tc077/tc077/u/', serves=False, account='u')
    assert research.root == '/epccfs/tc077/tc077/u'
    assert research.repo('dev-lab') == '/epccfs/tc077/tc077/u/repos/dev-lab', 'the kit is just repos/dev-lab'
    assert research.serving == (), 'a research account holds no run trees, leases, artifacts or scheduler state'
    assert '/epccfs/tc077/tc077/u/cache' in research.owned
    verify = account_paths('/gpfs01/home/v/lab', serves=True)
    assert verify.serving == tuple(f'/gpfs01/home/v/lab/{p}' for p in ('runs', 'leases', 'artifacts', 'serve'))
    assert set(verify.owned) == set(verify.shared) | set(verify.serving)


@pytest.mark.parametrize('root', ['', '~/lab', 'lab', '$HOME/lab'])
def test_an_undeclared_or_home_relative_root_is_refused_by_name(root: str) -> None:
    with pytest.raises(RootUndeclared, match='root'):
        account_paths(root, serves=False, account='u')


def test_remote_scripts_name_the_root_by_its_variable_and_refuse_it_unset() -> None:
    assert ROOT == '"$LAB_ROOT"'
    assert run_tree('a' * 40) == f'"$LAB_ROOT"/runs/{"a" * 40}/tree'
    assert 'hpcaccount deploy-fleet' in layout.REQUIRE_ROOT, 'the refusal names its door'
    assert export_line(AccountPaths('/epccfs/p q', serves=False)) == "export LAB_ROOT='/epccfs/p q'"


def test_no_hpc_module_hard_codes_a_home_or_ci_path() -> None:
    offenders = {
        path.name: literals
        for path in sorted(HPC.glob('*.py'))
        if path.name not in LOGIN_HOME_MODULES and (literals := _home_literals(path.read_text(encoding='utf-8')))
    }
    assert offenders == {}, 'derive the path from lab_commons.hpc.layout, never a home literal'


def test_the_scan_catches_a_planted_home_path() -> None:
    """PLANTED CONTROL: the scan above can say no."""
    assert _home_literals("JOBS = '~/ci/jobs'\n") == ['~/ci/jobs']
    assert _home_literals('X = f\'"$HOME"/ci/{a}\'\n'), 'an f-string part is a literal too'
    assert _home_literals('"""~/ci in a docstring is prose."""\n') == []


def test_on_the_account_the_root_comes_from_its_deploy_and_unset_is_refused() -> None:
    assert layout.local_root({'LAB_ROOT': '/epccfs/p/u'}).root == '/epccfs/p/u'
    with pytest.raises(RootUndeclared, match='deploy-fleet'):
        layout.local_root({})  # PLANTED CONTROL: no default, no home fallback
