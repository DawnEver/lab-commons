"""SAFE COMPILATION of user C into a shared library: gcc, clang and MSVC (v1 ruling D125 F8).

C taken from a user's file runs inside the process that loads it -- a library constructor runs on load, before any
function is called -- so building it is gated four ways, each refused by name:

- TRUST. :func:`compile_library` refuses (:class:`Untrusted`) unless the caller passes ``trusted=True``; the
  refusal names the caller's own *door* (the flag or declaration that grants trust). Nothing here reads a default.
- CONFINEMENT. A ``#include`` must be a literal relative name without ``..`` (:func:`confine`,
  :class:`UnconfinedInclude`); the include path is the caller's directories plus the toolchain's system headers.
- A SCRUBBED ENVIRONMENT. The compiler runs with the caller's environment (read once at the product's entry, never
  here) minus the variables that widen what a family reads or runs (:func:`scrubbed`): ``CPATH`` and its kin,
  ``GCC_EXEC_PREFIX`` and ``COMPILER_PATH`` for gcc and clang; ``CL`` and ``_CL_`` (injected flags) for MSVC, whose
  ``INCLUDE``/``LIB`` are the toolchain's own system paths and stay.
- A CONTENT-KEYED CACHE. A library lives at ``<store>/<key>/`` with the sha256 recorded beside it; the key hashes,
  length-prefixed, the block's name and source, the family, compiler path, version and target, the flags and the
  CONTENT of every file under the include directories. A build happens in its own directory, removed on failure,
  and is renamed into place -- atomic, and the first build wins on every OS. Each loaded instance takes its own
  verified copy (:func:`instance_copy`), so its C statics are its own.

A :class:`Compiler` is probed once from a path the caller located (:meth:`Compiler.probe`); this module never searches
``PATH``. Every child is started through the one launch door, :mod:`lab_commons.launch`.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import shutil
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from lab_commons import launch

__all__ = [
    'FAMILIES',
    'CompileFailed',
    'CompileRefused',
    'Compiler',
    'UnconfinedInclude',
    'Untrusted',
    'compile_library',
    'confine',
    'instance_copy',
    'scrubbed',
]

#: The compiler families this module drives, each with its own command line.
FAMILIES: Final = frozenset({'gcc', 'clang', 'msvc'})
#: Per family, the environment variables that would widen what the compiler reads or which programs it runs.
_SCRUBBED: Final = {
    'gcc': ('CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'OBJC_INCLUDE_PATH', 'GCC_EXEC_PREFIX', 'COMPILER_PATH'),
    'clang': ('CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'OBJC_INCLUDE_PATH', 'GCC_EXEC_PREFIX', 'COMPILER_PATH'),
    'msvc': ('CL', '_CL_'),
}
#: MSVC names only C11 and C17; a gcc-style standard maps to the nearest it has.
_MSVC_STANDARD: Final = {'c90': 'c11', 'c99': 'c11', 'gnu90': 'c11', 'gnu99': 'c11', 'c11': 'c11', 'c17': 'c17'}
_SUFFIX: Final = {'win32': '.dll', 'darwin': '.dylib'}.get(sys.platform, '.so')
_INCLUDE: Final = re.compile(r'^\s*#\s*include\b(.*)$', re.MULTILINE)
_LITERAL: Final = re.compile(r'^\s*[<"]([^>"]+)[>"]\s*(?://.*|/\*.*)?$')
_MSVC_TARGET: Final = re.compile(r'\bfor\s+(x64|x86|ARM64|ARM)\b', re.IGNORECASE)


class CompileRefused(RuntimeError):
    """User C that was not, or could not be, built; the message names the block and why."""


class Untrusted(CompileRefused, PermissionError):
    """The caller did not declare that the run trusts this C."""


class UnconfinedInclude(CompileRefused):
    """A ``#include`` reaching outside the include path: absolute, ``..``, a drive, or computed."""


class CompileFailed(CompileRefused):
    """The compiler refused the source (its diagnostics follow), or a built library no longer verifies."""


def scrubbed(family: str, env: Mapping[str, str]) -> dict[str, str]:
    """*env* without the variables that widen what a *family* compiler reads or runs."""
    drop = set(_SCRUBBED[family])
    return {key: value for key, value in env.items() if key not in drop}


@dataclass(frozen=True)
class Compiler:
    """A C compiler of *family* run as *argv* (its command prefix), with its full *version* text and *target*."""

    family: str
    argv: tuple[str, ...]
    version: str
    target: str

    def __post_init__(self) -> None:
        """Refuse a family this module has no command line for."""
        if self.family not in FAMILIES:
            msg = f'compiler family {self.family!r} is not one of {sorted(FAMILIES)}'
            raise CompileRefused(msg)

    @classmethod
    def probe(cls, family: str, path: Path, *, env: Mapping[str, str]) -> Compiler:
        """The compiler at *path*, its version and target read by running it (MSVC: its banner)."""
        exe = str(path.resolve())
        clean = scrubbed(family, env)
        if family == 'msvc':
            banner = launch.run([exe], capture_output=True, text=True, check=False, env=clean)
            text = (banner.stderr or banner.stdout).strip()
            found = _MSVC_TARGET.search(text)
            return cls(family, (exe,), text, found.group(1).lower() if found else '')
        version = launch.run([exe, '--version'], capture_output=True, text=True, check=False, env=clean)
        machine = launch.run([exe, '-dumpmachine'], capture_output=True, text=True, check=False, env=clean)
        return cls(family, (exe,), version.stdout.strip(), machine.stdout.strip())

    def command(self, source: Path, out: Path, includes: Sequence[str], standard: str) -> list[str]:
        """The command line that builds *source* into the shared library *out*."""
        if self.family == 'msvc':
            std = _MSVC_STANDARD.get(standard, standard)
            return [*self.argv, '/nologo', '/LD', '/O2', '/W0', f'/std:{std}', *(f'/I{d}' for d in includes),
                    f'/Fe:{out}', str(source)]  # fmt: skip
        return [*self.argv, '-shared', '-O2', '-fPIC', '-w', f'-std={standard}', *(f'-I{d}' for d in includes),
                '-o', str(out), str(source)]  # fmt: skip


def confine(name: str, source: str) -> None:
    """Refuse an include of *source* that is not a literal relative name without ``..``.

    Raises:
        UnconfinedInclude: naming the line.

    """
    for match in _INCLUDE.finditer(source):
        line = match.group(0).strip()
        literal = _LITERAL.match(match.group(1))
        if literal is None:
            msg = f'C {name!r}: {line!r} is a computed include; only a literal <name> or "name" is taken'
            raise UnconfinedInclude(msg)
        target = literal.group(1).replace('\\', '/')
        if target.startswith('/') or re.match(r'^[A-Za-z]:', target) or '..' in target.split('/'):
            msg = f'C {name!r}: {line!r} reaches outside the include path (absolute or ..)'
            raise UnconfinedInclude(msg)


def _field(text: str | bytes) -> bytes:
    data = text.encode('utf-8') if isinstance(text, str) else text
    return str(len(data)).encode('ascii') + b':' + data


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(fields: Sequence[str], includes: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for field in fields:
        digest.update(_field(field))
    for directory in includes:
        for header in sorted(p for p in Path(directory).rglob('*') if p.is_file()):
            digest.update(_field(header.relative_to(directory).as_posix()) + _field(header.read_bytes()))
    return digest.hexdigest()[:32]


def compile_library(
    name: str,
    source: str,
    *,
    compiler: Compiler,
    store: Path,
    env: Mapping[str, str],
    trusted: bool,
    door: str = 'the caller grants trust (trusted=True)',
    include_dirs: Sequence[Path] = (),
    standard: str = 'c99',
) -> Path:
    """The verified shared library of *source* (one translation unit named *name*), built once per content key.

    Raises:
        Untrusted: *trusted* is false; the message names *door*.
        UnconfinedInclude: an include outside the include path.
        CompileFailed: an include directory is missing, or the compiler refused the source (its diagnostics).

    """
    if not trusted:
        msg = f'C {name!r} runs inside the process that loads it, refused unless trusted: {door}'
        raise Untrusted(msg)
    confine(name, source)
    includes = tuple(str(Path(d).resolve()) for d in include_dirs)
    for directory in includes:
        if not Path(directory).is_dir():
            msg = f'C {name!r}: include directory {directory} is not a directory'
            raise CompileFailed(msg)
    fields = [name, source, compiler.family, *compiler.argv, compiler.version, compiler.target, standard, _SUFFIX]
    final = store / _key(fields, includes)
    library = final / f'{name}{_SUFFIX}'
    if library.is_file():
        return library
    store.mkdir(parents=True, exist_ok=True)
    work = store / f'.build-{uuid.uuid4().hex}'
    work.mkdir()
    try:
        unit = work / f'{name}.c'
        unit.write_text(source, encoding='utf-8')
        built = work / library.name
        command = compiler.command(unit, built, includes, standard)
        done = launch.run(
            command, capture_output=True, text=True, cwd=work, check=False, env=scrubbed(compiler.family, env)
        )
        if done.returncode != 0 or not built.is_file():
            msg = f'C {name!r} does not compile:\n{(done.stderr or done.stdout).strip()}'
            raise CompileFailed(msg)
        (work / f'{library.name}.sha256').write_text(_sha256(built), encoding='ascii')
        for other in work.iterdir():
            if other.name not in {library.name, f'{library.name}.sha256'}:
                other.unlink()
        with contextlib.suppress(OSError):  # another build renamed its directory first: that one is this one
            work.rename(final)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return library


def instance_copy(library: Path, directory: Path) -> Path:
    """A private copy of *library* for ONE loaded instance, verified against the sha256 recorded at its build.

    Raises:
        CompileFailed: the library's bytes no longer match its recorded sha256.

    """
    recorded = (library.parent / f'{library.name}.sha256').read_text(encoding='ascii').strip()
    data = library.read_bytes()
    if hashlib.sha256(data).hexdigest() != recorded:
        msg = f'{library} does not match the sha256 recorded at its build: rebuild it (delete {library.parent})'
        raise CompileFailed(msg)
    directory.mkdir(parents=True, exist_ok=True)
    copy = directory / library.name
    copy.write_bytes(data)
    return copy
