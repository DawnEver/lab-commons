"""Safe compilation of user C into a shared library (v1 ruling D125 F8).

C taken from a user's file runs inside the process that loads it, so building it is GATED: refused unless the caller
says the run trusts it, includes confined to literal relative names, the compiler's environment scrubbed of what
widens what it reads or runs, and the library cached by the content of everything it was built from. A FAKE compiler
(a Python script) makes every rule observable on any host; one test drives a real gcc where the host has one.
"""

from __future__ import annotations

import ctypes
import json
import shutil
import sys
from pathlib import Path

import pytest

from lab_commons.ccompile import (
    CompileFailed,
    Compiler,
    UnconfinedInclude,
    Untrusted,
    compile_library,
    confine,
    instance_copy,
    scrubbed,
)

_FAKE = r"""
import hashlib, json, os, sys
args = sys.argv[1:]
if args == ['--version']:
    print('fakecc 1.0'); sys.exit(0)
if args == ['-dumpmachine']:
    print('x86_64-fake'); sys.exit(0)
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calls.jsonl')
with open(log, 'a', encoding='utf-8') as f:
    f.write(json.dumps({'args': args, 'env': sorted(os.environ)}) + '\n')
source = open(args[-1], encoding='utf-8').read()
if 'SYNTAX ERROR' in source:
    print('block.c:1: error: expected ;', file=sys.stderr); sys.exit(1)
flags = [i for i, a in enumerate(args) if a == '-o' or a.startswith('/Fe:')]
out = args[flags[0]][4:] if args[flags[0]].startswith('/Fe:') else args[flags[0] + 1]
open(out, 'wb').write(hashlib.sha256(source.encode()).digest())
"""

ENV = {'PATH': 'p', 'CPATH': '/evil', 'GCC_EXEC_PREFIX': '/evil', 'CL': '/evil', '_CL_': '/evil', 'SYSTEMROOT': 'C:/W'}


def _fake(tmp_path: Path, family: str = 'gcc') -> Compiler:
    script = tmp_path / 'cc' / 'fakecc.py'
    script.parent.mkdir()
    script.write_text(_FAKE, encoding='utf-8')
    return Compiler(family, (sys.executable, str(script)), 'fakecc 1.0', 'x86_64-fake')


def _calls(compiler: Compiler) -> list[dict[str, object]]:
    log = Path(compiler.argv[-1]).parent / 'calls.jsonl'
    return [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()] if log.is_file() else []


def _build(compiler: Compiler, store: Path, source: str = 'int f(void){return 1;}', **kw: object) -> Path:
    kw.setdefault('trusted', True)
    return compile_library('block', source, compiler=compiler, store=store, env=ENV, **kw)  # type: ignore[arg-type]


def test_untrusted_c_is_refused_naming_the_door_and_nothing_is_built(tmp_path: Path) -> None:
    compiler = _fake(tmp_path)
    with pytest.raises(Untrusted, match=r"C 'block' .* refused unless trusted: pass --set cscript.trust=true"):
        _build(compiler, tmp_path / 'store', trusted=False, door='pass --set cscript.trust=true')
    assert _calls(compiler) == []


@pytest.mark.parametrize(
    ('line', 'why'),
    [('#include "/etc/passwd"', 'absolute or ..'), ('#include "../up.h"', 'absolute or ..'),
     ('#include "C:/x.h"', 'absolute or ..'), ('#include HEADER', 'computed include')],
)  # fmt: skip
def test_an_include_outside_the_include_path_is_refused(line: str, why: str) -> None:
    with pytest.raises(UnconfinedInclude, match=why):
        confine('block', f'{line}\nint f(void);')


def test_a_literal_relative_include_is_taken() -> None:
    confine('block', '#include <math.h>\n#include "local/h.h" // comment\nint f(void);')  # PLANTED CONTROL


def test_the_compiler_environment_is_scrubbed_per_family(tmp_path: Path) -> None:
    assert scrubbed('gcc', ENV) == {'PATH': 'p', 'CL': '/evil', '_CL_': '/evil', 'SYSTEMROOT': 'C:/W'}
    assert scrubbed('msvc', ENV) == {'PATH': 'p', 'CPATH': '/evil', 'GCC_EXEC_PREFIX': '/evil', 'SYSTEMROOT': 'C:/W'}
    compiler = _fake(tmp_path)
    _build(compiler, tmp_path / 'store')
    seen = set(_calls(compiler)[0]['env'])  # type: ignore[arg-type]
    assert 'CPATH' not in seen
    assert 'GCC_EXEC_PREFIX' not in seen
    assert 'PATH' in seen


def test_each_family_gets_its_own_command(tmp_path: Path) -> None:
    (tmp_path / 'm').mkdir()
    gcc, msvc = _fake(tmp_path), Compiler('msvc', _fake(tmp_path / 'm').argv, 'fake', 'x64')
    _build(gcc, tmp_path / 's1')
    _build(msvc, tmp_path / 's2')
    gcc_args, msvc_args = _calls(gcc)[0]['args'], _calls(msvc)[0]['args']
    assert {'-shared', '-fPIC', '-std=c99'} <= set(gcc_args)  # type: ignore[arg-type]
    assert {'/LD', '/nologo', '/std:c11'} <= set(msvc_args)  # type: ignore[arg-type]
    assert any(a.startswith('/Fe:') for a in msvc_args)  # type: ignore[union-attr]


def test_a_library_is_built_once_per_content_key(tmp_path: Path) -> None:
    compiler, store, headers = _fake(tmp_path), tmp_path / 'store', tmp_path / 'inc'
    headers.mkdir()
    (headers / 'h.h').write_text('#define N 1\n', encoding='utf-8')
    first = _build(compiler, store, include_dirs=[headers])
    assert _build(compiler, store, include_dirs=[headers]) == first
    assert len(_calls(compiler)) == 1  # the second build was a cache hit
    assert _build(compiler, store, 'int f(void){return 2;}', include_dirs=[headers]) != first
    (headers / 'h.h').write_text('#define N 2\n', encoding='utf-8')
    assert _build(compiler, store, include_dirs=[headers]) != first  # a header's CONTENT is in the key
    assert len(_calls(compiler)) == 3


def test_a_failed_compile_names_the_diagnostics_and_leaves_nothing(tmp_path: Path) -> None:
    compiler, store = _fake(tmp_path), tmp_path / 'store'
    with pytest.raises(CompileFailed, match=r"C 'block' does not compile:\nblock.c:1: error"):
        _build(compiler, store, 'SYNTAX ERROR')
    assert [p.name for p in store.iterdir()] == []


def test_an_instance_copy_is_verified_against_the_recorded_sha256(tmp_path: Path) -> None:
    library = _build(_fake(tmp_path), tmp_path / 'store')
    copy = instance_copy(library, tmp_path / 'one')
    assert copy.read_bytes() == library.read_bytes()
    library.write_bytes(b'tampered')  # PLANTED CONTROL
    with pytest.raises(CompileFailed, match='does not match the sha256 recorded at its build'):
        instance_copy(library, tmp_path / 'two')


_GCC = shutil.which('gcc')


@pytest.mark.not_applicable
@pytest.mark.skipif(_GCC is None, reason='this host has no gcc on PATH')
def test_a_real_gcc_builds_a_library_ctypes_can_call(tmp_path: Path) -> None:
    import os  # noqa: PLC0415 -- the real compiler needs this process's PATH, read here only

    compiler = Compiler.probe('gcc', Path(str(_GCC)), env=dict(os.environ))
    library = compile_library(
        'add',
        'int add(int a, int b) { return a + b; }\n',
        compiler=compiler,
        store=tmp_path / 'store',
        env=dict(os.environ),
        trusted=True,
    )
    loaded = ctypes.CDLL(str(instance_copy(library, tmp_path / 'instance')))
    assert loaded.add(2, 3) == 5
