"""App-agnostic TOML read/write + filesystem-presence helpers.

Provenance: extracted from motronics-studio's ``core/utils/file_io.py`` (byte-identical
across the motronics/optimi-lab/wdg-lab family; see ``finding-shared-lab-infra-extraction.md``),
the strongest of the near-identical copies — it is the only fork whose :func:`read_toml`
accepts both a path and in-memory ``bytes``. Pure ``rtoml`` + ``pathlib``/``shutil`` mechanics,
no concept from any single lab's domain.
"""

import os
from pathlib import Path
from shutil import copyfile

from rtoml import (
    dumps,
    loads,
)

from lab_commons.log import log

__all__ = ['check_path', 'dumps', 'list_files_in_dir', 'loads', 'read_toml', 'save_toml']


def read_toml(source: Path | str | bytes) -> dict:
    """Read TOML from a file path or parse in-memory ``bytes``.

    Args:
        source: A filesystem path to a ``.toml`` file as a ``Path`` or ``str``
                (opened and read), or ``bytes`` of TOML content (parsed directly,
                decoded as UTF-8). To parse an in-memory ``str``, use
                :func:`loads` — a ``str`` here is always a path, never content, so
                a Windows drive path (like ``D:/...``) is not mistaken for TOML.

    Returns:
        Parsed TOML content as a ``dict``.

    Raises:
        OSError: Failed to open the file (path source only).

    """
    if isinstance(source, bytes):
        return loads(source.decode('utf-8'))
    source = Path(source)
    try:
        with Path.open(source, encoding='utf-8') as f:
            return loads(f.read())
    except OSError as e:
        msg = f'Failed to open file {source}! {e}'
        log(msg=msg, level='ERROR')
        raise OSError(msg) from e


def save_toml(file_path: Path, dict_data: dict) -> None:
    """Save data to a TOML file.

    Args:
    ----
        file_path(Path): Path to the file.
        dict_data(dict): Data to save.

    """
    try:
        # Create the parent dir if missing: save targets like the user modeling
        # library (config/lib/...) may not exist yet on a fresh machine.
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with Path.open(file_path, 'w', encoding='utf-8') as f:
            f.write(dumps(dict_data))
    except OSError as e:
        msg = f'Failed to save file {file_path}! {e}'
        log(msg=msg, level='ERROR')
        raise OSError(msg) from e


def check_path(target_path: Path, default_path: Path | None = None, is_dir: bool = False) -> bool:
    """Check whether a path exists. Create parent directories if missing.
    If target_path does not exist and default_path is provided, the default file
    will be copied to target_path.

    Args:
    ----
        target_path(Path): Target path.
        default_path(Path): Default path to copy from if target missing.
        is_dir(bool): Whether the target is a directory.

    Returns:
    -------
        flag (bool): True if the target exists, False otherwise.

    """
    if Path.exists(target_path):
        return True
    msg = f'{target_path} does not exist, '
    if is_dir:
        target_path.mkdir(parents=True, exist_ok=True)
        msg += 'directory has been created. '
    else:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if default_path is not None:
            check_path(default_path)
            copyfile(default_path, target_path)
            msg += f'default file {default_path} copied to target path. '
    log(msg, level='DEBUG')
    return False


def list_files_in_dir(dir_path: Path, file_name_suffix: str | None = None) -> list[str]:
    """List files in a directory matching a specific suffix.

    Args:
        dir_path(Path): Directory path.
        file_name_suffix(str): Suffix to filter filenames (matches files that end with this string).

    Returns:
        valid_file_list(list[str]): List of filenames.

    """
    valid_file_list = []
    if check_path(dir_path, is_dir=True):
        for _, _, files in os.walk(dir_path):
            for file in files:
                if file_name_suffix is None or file.endswith(file_name_suffix):
                    valid_file_list.append(file)
    else:
        msg = f'{dir_path} does not exist!'
        log(msg=msg, level='WARNING')
    return valid_file_list
