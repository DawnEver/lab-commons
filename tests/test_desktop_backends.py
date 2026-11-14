"""Every desktop backend's command lines and parsing, against recorded output: runs on any OS.

The live halves (a real window) are ``test_desktop_live.py``; these pin what each backend SENDS (argv only,
never a shell string) and how it READS what comes back, including each refusal by name.
"""

from __future__ import annotations

import json
import struct
import subprocess
import zlib

import pytest

from lab_commons.desktop import (
    Control,
    ControlGoneError,
    LabelAmbiguousError,
    UnsupportedError,
    Window,
    _darwin,
    _win,
    _x11,
)

_WINDOW = Window(handle=0x1A00003, pid=4242, title='Solver "A"', rect=(0, 0, 2, 1), visible=True, enabled=None,
                 responding=None)  # fmt: skip


def _pixels(png: bytes) -> bytes:
    at, data = 8, b''
    while at < len(png):
        (length,) = struct.unpack('>I', png[at : at + 4])
        if png[at + 4 : at + 8] == b'IDAT':
            data += png[at + 8 : at + 8 + length]
        at += 12 + length
    return zlib.decompress(data)


class TestWin:
    def test_roles_follow_the_uia_control_type_ids(self) -> None:
        assert _win.ROLES[50000] == 'button'
        assert _win.ROLES[50004] == 'edit'
        assert _win.ROLES[50020] == 'text'
        assert _win.ROLES[50032] == 'window'
        assert _win.ROLES[50038] == 'separator'

    def test_the_bitmap_header_asks_for_top_down_32_bit_rows(self) -> None:
        size, width, height, planes, bits = struct.unpack('<IiiHH', _win.bitmap_info(640, 480)[:16])
        assert (size, width, height, planes, bits) == (40, 640, -480, 1, 32)


class _Recorder:
    def __init__(self, answers: dict[str, bytes]) -> None:
        self.answers = answers
        self.argvs: list[list[str]] = []

    def __call__(self, argv: list[str], timeout_s: float) -> bytes:
        self.argvs.append(argv)
        for key, answer in self.answers.items():
            if key in ' '.join(argv):
                return answer
        raise subprocess.CalledProcessError(1, argv, stderr=b'no such window')


_XPROP_ROOT = b'_NET_CLIENT_LIST(WINDOW): window id # 0x1a00003, 0x1c00007\n'
_XPROP_A = b'_NET_WM_PID(CARDINAL) = 4242\n_NET_WM_NAME(UTF8_STRING) = "Solver \\"A\\""\n'
_XWININFO_A = (
    b'  Absolute upper-left X:  10\n  Absolute upper-left Y:  20\n  Width: 300\n  Height: 200\n'
    b'  Map State: IsViewable\n'
)


class TestX11:
    def _x11(self, recorder: _Recorder, **env: str) -> _x11.X11:
        return _x11.X11(run=recorder, env=env or {'DISPLAY': ':99'}, which=lambda name: f'/usr/bin/{name}')

    def test_no_display_is_refused_by_name_and_wayland_is_named(self) -> None:
        with pytest.raises(UnsupportedError) as refused:
            _x11.X11(env={'WAYLAND_DISPLAY': 'wayland-0'})
        assert refused.value.reason == 'desktop.no_x11'
        assert 'Wayland' in str(refused.value)

    def test_xwayland_counts_as_x11(self) -> None:
        self._x11(_Recorder({}), DISPLAY=':0', WAYLAND_DISPLAY='wayland-0')

    def test_a_missing_tool_is_refused_by_name(self) -> None:
        x11 = _x11.X11(run=_Recorder({}), env={'DISPLAY': ':0'}, which=lambda _name: None)
        with pytest.raises(UnsupportedError) as refused:
            x11.windows()
        assert refused.value.reason == 'desktop.tool_missing'

    def test_windows_read_the_client_list_then_each_window(self) -> None:
        recorder = _Recorder(
            {'-root': _XPROP_ROOT, 'xprop -id 0x1a00003': _XPROP_A, 'xwininfo -id 0x1a00003': _XWININFO_A}
        )
        found = self._x11(recorder).windows()
        assert found == [
            Window(
                handle=0x1A00003,
                pid=4242,
                title='Solver "A"',
                rect=(10, 20, 310, 220),
                visible=True,
                enabled=None,
                responding=None,
            )
        ], 'the vanished 0x1c00007 is skipped, never listed with made-up facts'
        assert recorder.argvs[0] == ['xprop', '-root', '_NET_CLIENT_LIST']

    def test_control_verbs_refuse_by_name_never_no_op(self) -> None:
        x11 = self._x11(_Recorder({}))
        for verb in (lambda: x11.controls(_WINDOW), lambda: x11.press(_WINDOW, 'OK', None), lambda: x11.close(_WINDOW)):
            with pytest.raises(UnsupportedError) as refused:
                verb()
            assert refused.value.reason == 'desktop.no_atspi'

    @pytest.mark.parametrize('byte_order', [0, 1], ids=['lsb', 'msb'])
    def test_capture_decodes_a_32_bit_xwd_dump(self, byte_order: int) -> None:
        name = b'win\x00'
        header_size = 100 + len(name)
        fields = [header_size, 7, 2, 24, 2, 1, 0, byte_order, 32, byte_order, 32, 32, 12, 4,
                  0xFF0000, 0xFF00, 0xFF, 8, 0, 0, 2, 1, 0, 0, 0]  # fmt: skip
        red, green = (
            (b'\x00\x00\xff\x00', b'\x00\xff\x00\x00')
            if byte_order == 0
            else (b'\x00\xff\x00\x00', b'\x00\x00\xff\x00')
        )
        dump = struct.pack('>25I', *fields) + name + red + green + b'\x00' * 4  # 4 bytes of row padding
        recorder = _Recorder({'xwd': dump})
        png = self._x11(recorder).capture(_WINDOW, 5.0)
        assert recorder.argvs == [['xwd', '-silent', '-id', '0x1a00003']]
        assert _pixels(png) == b'\x00' + bytes([255, 0, 0, 0, 255, 0])

    def test_an_unread_xwd_format_is_refused_by_name(self) -> None:
        fields = [100, 7, 2, 16, 1, 1, 0, 0, 16, 0, 16, 16, 2, 4, 0xF800, 0x7E0, 0x1F, 6, 0, 0, 1, 1, 0, 0, 0]
        with pytest.raises(UnsupportedError) as refused:
            _x11.decode_xwd(struct.pack('>25I', *fields) + b'\x00\x00')
        assert refused.value.reason == 'desktop.capture_format'


def _ax(path: str, parent: str | None, role: str, name: str, *, actions: tuple[str, ...] | list[str] = (),
        identifier: str | None = None) -> dict[str, object]:  # fmt: skip
    return {'id': path, 'parent': parent, 'role': role, 'name': name, 'value': None, 'enabled': True,
            'actions': list(actions), 'help': '', 'identifier': identifier}  # fmt: skip


class TestDarwin:
    def _mac(self, recorder: _Recorder) -> _darwin.Darwin:
        return _darwin.Darwin(run=recorder, which=lambda name: f'/usr/bin/{name}')

    def test_windows_come_from_cgwindowlist_rows(self) -> None:
        rows = [
            {'id': 7, 'pid': 4242, 'title': 'Solver', 'layer': 0, 'onscreen': True, 'x': 1, 'y': 2, 'w': 30, 'h': 40},
            {'id': 8, 'pid': 1, 'title': 'Menubar', 'layer': 25, 'onscreen': True, 'x': 0, 'y': 0, 'w': 9, 'h': 9},
        ]
        recorder = _Recorder({'CGWindowListCopyWindowInfo': json.dumps(rows).encode()})
        found = self._mac(recorder).windows()
        assert found[0] == Window(handle=7, pid=4242, title='Solver', rect=(1, 2, 31, 42), visible=True, enabled=None,
                                  responding=None)  # fmt: skip
        assert not found[1].visible, 'a layer-25 window is not an application window'
        assert recorder.argvs[0][:4] == ['osascript', '-l', 'JavaScript', '-e']

    def test_press_resolves_the_label_then_passes_every_value_as_argv(self) -> None:
        rows = [
            _ax('0', None, 'AXGroup', 'Licence expired'),
            _ax('0.0', '0', 'AXButton', 'OK', actions=['AXPress'], identifier='ok-id'),
        ]
        recorder = _Recorder({'function walk': json.dumps(rows).encode(), "byName('AXPress')": b''})
        mac = self._mac(recorder)
        assert mac.controls(_WINDOW) == [
            Control(role='group', name='Licence expired', value=None, enabled=True, id='0'),
            Control(role='button', name='OK', value=None, enabled=True, actions=('press',), id='0.0', parent='0',
                    automation_id='ok-id'),
        ]  # fmt: skip
        mac.press(_WINDOW, 'OK', None)
        press = recorder.argvs[-1]
        assert press[5:] == ['4242', 'Solver "A"', '0.0', 'OK'], 'pid, title, path, name: argv, never script source'
        assert 'Solver' not in press[4], 'the title is never spliced into the script'

    def test_press_by_id_passes_that_path_and_a_gone_id_is_refused(self) -> None:
        rows = [
            _ax('0', None, 'AXButton', 'OK', actions=['AXPress']),
            _ax('1', None, 'AXButton', 'OK', actions=['AXPress']),
        ]
        recorder = _Recorder({'function walk': json.dumps(rows).encode(), "byName('AXPress')": b''})
        mac = self._mac(recorder)
        mac.press(_WINDOW, None, '1')
        assert recorder.argvs[-1][7] == '1'
        with pytest.raises(ControlGoneError):
            mac.press(_WINDOW, None, '2')

    def test_set_value_needs_an_editable_control(self) -> None:
        rows = [_ax('0', None, 'AXTextField', 'Name'), _ax('1', None, 'AXTextField', 'Name')]
        recorder = _Recorder({'function walk': json.dumps(rows).encode()})
        with pytest.raises(LabelAmbiguousError):
            self._mac(recorder).set_value(_WINDOW, 'Name', None, 'b')

    def test_a_permission_refusal_is_named(self) -> None:
        def refuse(argv: list[str], _timeout: float) -> bytes:
            raise subprocess.CalledProcessError(
                1, argv, stderr=b'execution error: not allowed assistive access (-1719)'
            )

        with pytest.raises(UnsupportedError) as refused:
            _darwin.Darwin(run=refuse, which=lambda n: n).controls(_WINDOW)
        assert refused.value.reason == 'desktop.no_accessibility'

    def test_capture_names_the_window_id(self) -> None:
        recorder = _Recorder({'screencapture': b''})
        with pytest.raises(UnsupportedError) as refused:
            self._mac(recorder).capture(_WINDOW, 5.0)  # the fake writes no file: Screen Recording is missing
        assert refused.value.reason == 'desktop.no_accessibility'
        assert recorder.argvs[0][:5] == ['screencapture', '-x', '-o', '-l', str(0x1A00003)]
