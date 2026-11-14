"""The X11 backend: ``xprop``/``xwininfo`` for windows, ``xwd`` for pixels, AT-SPI for controls (stdlib only).

Every command goes through one injected runner, so the parsing and the command lines are unit-tested
on any OS against recorded output. Controls and acts go through the AT-SPI bus (:mod:`._atspi`, over
``gdbus``). Refused by name: no ``DISPLAY`` (a Wayland-only session is ``desktop.no_x11``), a missing X
tool (``desktop.tool_missing``), and every control verb when the AT-SPI bus or ``gdbus`` is absent or the
window's frame is not on it (``desktop.no_atspi``).

``enabled`` reads ``None``: X11 says nothing about modal-blocked input. ``responding`` reads ``None`` too:
the one X11 probe, ``_NET_WM_PING``, is a ClientMessage whose answer arrives as an EVENT on the root
window, so reading it needs an X connection that selects ``SubstructureNotify`` there and stays open
until the reply or the deadline. The command-line tools used here each open, act and close; none sends
a ping and waits for its pong, and speaking the X protocol from Python would be a persistent connection
(or a dependency) this backend does not take. A guessed ``True`` would be the lie the field forbids.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
from collections.abc import Callable, Mapping

from lab_commons.desktop._atspi import Atspi
from lab_commons.desktop._core import Control, UnsupportedError, Window, WindowBusyError, png_bytes

__all__ = ['X11', 'decode_xwd']

#: ``argv -> stdout bytes``; raises ``subprocess.TimeoutExpired`` past its deadline.
Runner = Callable[[list[str], float], bytes]


def _run(argv: list[str], timeout_s: float) -> bytes:
    return subprocess.run(argv, capture_output=True, check=True, timeout=timeout_s).stdout


class X11:
    """The X11 desktop of ``$DISPLAY``; *run*, *env* and *which* are the seams the unit tests fill."""

    def __init__(
        self,
        *,
        run: Runner = _run,
        env: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        env = os.environ if env is None else env
        if not env.get('DISPLAY'):
            kind = 'a Wayland session without XWayland' if env.get('WAYLAND_DISPLAY') else 'no X display'
            msg = f'the desktop backend needs X11 ($DISPLAY); this is {kind}'
            raise UnsupportedError(msg, 'desktop.no_x11')
        self._run = run
        self._which = which
        self._atspi = Atspi(run, which)

    def _tool(self, argv: list[str], timeout_s: float = 5.0) -> bytes:
        if self._which(argv[0]) is None:
            msg = f'{argv[0]} is not installed (x11-utils / x11-apps): the X11 desktop cannot read without it'
            raise UnsupportedError(msg, 'desktop.tool_missing')
        return self._run(argv, timeout_s)

    def windows(self) -> list[Window]:
        listed = self._tool(['xprop', '-root', '_NET_CLIENT_LIST']).decode('utf-8', 'replace')
        found = []
        for handle in re.findall(r'0x[0-9a-fA-F]+', listed.partition('#')[2]):
            try:
                found.append(self._window(int(handle, 16)))
            except subprocess.CalledProcessError:  # it closed between the list and the read: not listed
                continue
        return found

    def _window(self, handle: int) -> Window:
        props = self._tool(['xprop', '-id', hex(handle), '_NET_WM_PID', '_NET_WM_NAME', 'WM_NAME'])
        info = self._tool(['xwininfo', '-id', hex(handle)])
        return window_from(handle, props.decode('utf-8', 'replace'), info.decode('utf-8', 'replace'))

    def capture(self, window: Window, timeout_s: float) -> bytes:
        try:
            dump = self._tool(['xwd', '-silent', '-id', hex(window.handle)], timeout_s)
        except subprocess.TimeoutExpired as late:
            msg = f'xwd did not capture window {window.handle:#x} ({window.title!r}) within {timeout_s:g} s'
            raise WindowBusyError(msg) from late
        return decode_xwd(dump)

    def controls(self, window: Window) -> list[Control]:
        return self._atspi.controls(window)

    def press(self, window: Window, label: str) -> None:
        self._atspi.press(window, label)

    def set_value(self, window: Window, label: str, text: str) -> None:
        self._atspi.set_value(window, label, text)

    def close(self, window: Window) -> None:
        self._atspi.close(window)


def window_from(handle: int, props: str, info: str) -> Window:
    """A :class:`Window` from ``xprop -id`` and ``xwininfo -id`` output."""
    pid = re.search(r'_NET_WM_PID\(CARDINAL\) = (\d+)', props)
    title = re.search(r'_NET_WM_NAME\([^)]*\) = "(.*)"', props) or re.search(r'WM_NAME\([^)]*\) = "(.*)"', props)

    def number(field: str) -> int:
        found = re.search(rf'{field}:\s+(-?\d+)', info)
        return int(found.group(1)) if found else 0

    left, top = number('Absolute upper-left X'), number('Absolute upper-left Y')
    return Window(
        handle=handle,
        pid=int(pid.group(1)) if pid else 0,
        title=title.group(1).replace('\\"', '"') if title else '',
        rect=(left, top, left + number('Width'), top + number('Height')),
        visible='Map State: IsViewable' in info,
        enabled=None,
        responding=None,
    )


#: The XWD header: 25 big-endian CARD32 fields (X11 ``XWDFileHeader``).
_XWD_FIELDS = 25
_ZPIXMAP = 2


def decode_xwd(dump: bytes) -> bytes:
    """PNG bytes of an ``xwd`` dump; only the 32-bit-per-pixel TrueColor ZPixmap X servers write today."""
    fields = struct.unpack('>25I', dump[: 4 * _XWD_FIELDS])
    header_size, _version, form, _depth, width, height = fields[:6]
    byte_order, bits_per_pixel, bytes_per_line = fields[7], fields[11], fields[12]
    masks, colours = fields[14:17], fields[19]
    if form != _ZPIXMAP or bits_per_pixel != 32 or masks != (0xFF0000, 0xFF00, 0xFF):  # noqa: PLR2004 -- the one format
        msg = f'xwd wrote format {form}, {bits_per_pixel} bpp, masks {[hex(m) for m in masks]}: only 32-bit RGB is read'
        raise UnsupportedError(msg, 'desktop.capture_format')
    start = header_size + 12 * colours
    bgra = bytearray()
    for y in range(height):
        row = dump[start + y * bytes_per_line : start + y * bytes_per_line + width * 4]
        if byte_order == 0:  # LSBFirst: each pixel's bytes are B G R X
            bgra += row
        else:  # MSBFirst: X R G B
            swapped = bytearray(len(row))
            swapped[0::4], swapped[1::4], swapped[2::4], swapped[3::4] = row[3::4], row[2::4], row[1::4], row[0::4]
            bgra += swapped
    return png_bytes(width, height, bytes(bgra))
