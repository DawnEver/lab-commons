"""Pack and unpack: a zip of content-addressed members and a manifest.toml, verified both ways (v1 D125 F7).

A pack carries each distinct content ONCE, under ``objects/<sha[:2]>/<sha>``, and ``manifest.toml`` names every
member by its relative path, sha256 and size. Unpacking re-hashes every byte and refuses a pack that lies -- a member
whose bytes do not hash to the manifest, an entry the manifest does not name, a name that escapes the destination.
"""

from __future__ import annotations

import hashlib
import tomllib
import zipfile
from pathlib import Path

import pytest

from lab_commons.objects import MANIFEST, Member, PackRefused, pack, read_manifest, unpack


def _files(tmp_path: Path) -> dict[str, Path]:
    src = tmp_path / 'src'
    (src / 'mesh').mkdir(parents=True)
    (src / 'case.toml').write_text('name = "ipm"\n', encoding='utf-8')
    (src / 'mesh' / 'a.msh').write_bytes(b'\x00mesh')
    (src / 'mesh' / 'b.msh').write_bytes(b'\x00mesh')
    return {'case.toml': src / 'case.toml', 'mesh/a.msh': src / 'mesh' / 'a.msh', 'mesh/b.msh': src / 'mesh' / 'b.msh'}


def test_a_pack_round_trips_and_stores_each_content_once(tmp_path: Path) -> None:
    archive = pack(_files(tmp_path), tmp_path / 'case.zip', meta={'case': 'ipm'})
    with zipfile.ZipFile(archive) as book:
        objects = [n for n in book.namelist() if n.startswith('objects/')]
        manifest = tomllib.loads(book.read(MANIFEST).decode('utf-8'))
    assert len(objects) == 2  # the two identical meshes share one object
    assert manifest['meta'] == {'case': 'ipm'}
    sha = hashlib.sha256(b'\x00mesh').hexdigest()
    assert Member('mesh/a.msh', sha, 5) in read_manifest(archive)
    out = unpack(archive, tmp_path / 'out')
    assert sorted(out) == ['case.toml', 'mesh/a.msh', 'mesh/b.msh']
    assert (tmp_path / 'out' / 'mesh' / 'b.msh').read_bytes() == b'\x00mesh'


def _tampered(tmp_path: Path, extra: dict[str, bytes], manifest: str | None = None) -> Path:
    good = pack(_files(tmp_path), tmp_path / 'good.zip')
    bad = tmp_path / 'bad.zip'
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, 'w') as dst:
        for name in src.namelist():
            if name not in extra and not (name == MANIFEST and manifest is not None):
                dst.writestr(name, src.read(name))
        for name, data in extra.items():
            dst.writestr(name, data)
        if manifest is not None:
            dst.writestr(MANIFEST, manifest)
    return bad


def test_a_member_whose_bytes_lie_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    """PLANTED CONTROL: swap one object's bytes under its own name."""
    sha = hashlib.sha256(b'\x00mesh').hexdigest()
    bad = _tampered(tmp_path, {f'objects/{sha[:2]}/{sha}': b'\x00MESH'})
    with pytest.raises(PackRefused, match=r'mesh/a\.msh does not hash to its manifest'):
        unpack(bad, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_an_entry_the_manifest_does_not_name_is_refused(tmp_path: Path) -> None:
    bad = _tampered(tmp_path, {'objects/zz/stowaway': b'x'})
    with pytest.raises(PackRefused, match=r"names no 'objects/zz/stowaway'"):
        unpack(bad, tmp_path / 'out')


@pytest.mark.parametrize('name', ['../escape.txt', '/abs.txt', 'C:/abs.txt', 'a/../../b'])
def test_a_member_name_that_escapes_the_destination_is_refused(tmp_path: Path, name: str) -> None:
    sha = hashlib.sha256(b'\x00mesh').hexdigest()
    manifest = f'[pack]\nformat = 1\n\n[[member]]\nname = "{name}"\nsha256 = "{sha}"\nsize = 5\n'
    bad = _tampered(tmp_path, {}, manifest=manifest)
    with pytest.raises(PackRefused, match=r'is not a relative path inside the pack'):
        unpack(bad, tmp_path / 'out')


def test_pack_refuses_an_unsafe_member_name(tmp_path: Path) -> None:
    with pytest.raises(PackRefused, match=r'is not a relative path inside the pack'):
        pack({'../x': _files(tmp_path)['case.toml']}, tmp_path / 'x.zip')
