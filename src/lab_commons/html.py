"""Case-owned HTML identities, safe data embedding and portable report delivery.

This module knows neither motors nor windings, plotting libraries nor run clocks.
Producers own report content and resolve their run directory. Only producer-owned
markup belongs in ``body_html``, ``css`` and ``javascript``; untrusted values belong
in escaped text, :func:`key_value_table`, or JSON data blocks.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import quote

__all__ = [
    'ReportIdentity',
    'html_document',
    'key_value_table',
    'relative_asset_url',
    'script_json',
    'write_html_report',
]

_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_SCRIPT_ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]*\Z')
_DEVICE_NAMES = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    | {f'{prefix}{i}' for prefix in ('COM', 'LPT') for i in '123456789¹²³'}
)
_MAX_FILENAME_BYTES = 255
_KIND_SEPARATOR = '--'


def _validate_name(value: str) -> None:
    if not isinstance(value, str):
        message = 'a report identity is an explicit name, not a path or coerced value'
        raise TypeError(message)
    if (
        not value
        or value != value.strip()
        or '..' in value
        or _KIND_SEPARATOR in value
        or value.startswith('-')
        or value.endswith(('-', '.'))
        or _INVALID_NAME.search(value)
        or value.split('.', 1)[0].rstrip(' ').upper() in _DEVICE_NAMES
        or value.lower().endswith(('.html', '.htm'))
        or len(value.encode('utf-8')) > _MAX_FILENAME_BYTES
    ):
        message = f'unsafe or file-shaped report identity: {value!r}'
        raise ValueError(message)


@dataclass(frozen=True)
class ReportIdentity:
    """Business-supplied case identity and optional distinct report kind.

    Primary reports use ``<case>.html``; multiple reports in one run use
    ``<case>--<kind>.html``. The reserved separator cannot appear in either
    component and neither component may start or end with a hyphen, so dotted
    primary case names and separator-boundary hyphens cannot cause collisions.
    Names are preserved, never inferred or silently slugged.
    """

    case_name: str
    report_kind: str | None = None

    def __post_init__(self) -> None:
        """Refuse ambiguous names before a caller can create an artifact."""
        _validate_name(self.case_name)
        if self.report_kind is not None:
            _validate_name(self.report_kind)
        if len(self.filename.encode('utf-8')) > _MAX_FILENAME_BYTES:
            message = 'the combined report filename exceeds the portable 255-byte limit'
            raise ValueError(message)

    @property
    def stem(self) -> str:
        """The artifact stem, also suitable for a report's sibling asset directory."""
        return self.case_name if self.report_kind is None else f'{self.case_name}{_KIND_SEPARATOR}{self.report_kind}'

    @property
    def filename(self) -> str:
        """The one filename derived from this report identity."""
        return f'{self.stem}.html'


def script_json(value: object) -> str:
    """Serialize strict JSON safely inside an HTML script-data element."""
    return (
        json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        .replace('<', r'\u003c')
        .replace('>', r'\u003e')
        .replace('&', r'\u0026')
    )


def html_document(
    title: str,
    body_html: str,
    *,
    css: str = '',
    javascript: str = '',
    json_data: Mapping[str, object] | None = None,
) -> str:
    """Wrap trusted producer markup and safe data in one UTF-8 HTML document.

    Args:
        title: Plain text, escaped by this function.
        body_html: Trusted HTML produced by the business renderer, not arbitrary data.
        css: Trusted inline CSS without style tags.
        javascript: Trusted application code without script tags.
        json_data: Data blocks keyed by explicit, validated script element identities.

    Returns:
        A standalone document shell; asset or plotting dependencies remain explicit
        in the producer's content rather than being selected by this mechanism.

    """
    blocks = []
    for identity, value in (json_data or {}).items():
        if not isinstance(identity, str) or not _SCRIPT_ID.fullmatch(identity):
            message = f'invalid JSON script element identity: {identity!r}'
            raise ValueError(message)
        blocks.append(f'<script type="application/json" id="{identity}">{script_json(value)}</script>')
    style = f'<style>{css}</style>' if css else ''
    script = f'<script>{javascript}</script>' if javascript else ''
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'<title>{escape(title)}</title>{style}</head>\n<body>{body_html}\n'
        + '\n'.join(blocks)
        + script
        + '</body></html>\n'
    )


def write_html_report(directory: Path | str, identity: ReportIdentity, document: str) -> Path:
    """Atomically write the identity-owned UTF-8/LF artifact into a caller's run directory."""
    if not isinstance(identity, ReportIdentity):
        message = 'HTML report delivery requires an explicit ReportIdentity'
        raise TypeError(message)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / identity.filename
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            newline='\n',
            dir=directory,
            prefix='.html-report-',
            suffix='.tmp',
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(document.replace('\r\n', '\n').replace('\r', '\n'))
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def relative_asset_url(asset: Path | str, report_directory: Path | str) -> str:
    """Return a percent-encoded relative URL for an asset confined to the report directory."""
    relative = Path(asset).resolve().relative_to(Path(report_directory).resolve())
    return quote(relative.as_posix(), safe='/')


def key_value_table(values: Mapping[object, object], *, min_col_width: int = 200) -> str:
    """Render escaped key/value pairs, retaining dotted nested parent paths."""
    if not isinstance(min_col_width, int) or isinstance(min_col_width, bool):
        message = 'minimum column width must be an integer'
        raise TypeError(message)
    if min_col_width <= 0:
        message = 'minimum column width must be positive'
        raise ValueError(message)
    items = []
    active = set()

    def append(mapping: Mapping[object, object], prefix: str) -> None:
        identity = id(mapping)
        if identity in active:
            message = 'recursive mappings cannot form an HTML key/value table'
            raise ValueError(message)
        active.add(identity)
        try:
            for key, value in mapping.items():
                path = f'{prefix}.{key}' if prefix else str(key)
                if isinstance(value, Mapping) and value:
                    append(value, path)
                else:
                    items.append(
                        '<div class="kv-pair">'
                        f'<div class="cell header">{escape(path)}</div>'
                        f'<div class="cell value">{escape(str(value))}</div></div>'
                    )
        finally:
            active.remove(identity)

    append(values, '')
    return (
        f'<div class="responsive-table" style="--min-col-width:{min_col_width}px;">'
        '<div class="responsive-grid">' + ''.join(items) + '</div></div>'
    )
