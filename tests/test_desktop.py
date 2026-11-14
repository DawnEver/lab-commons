"""``lab_commons.desktop`` -- the platform-neutral door: facts, the ownership guard, label resolution, PNG.

Every act is driven against a FAKE backend that records what reached it, so the planted controls (a
window outside the owned pids, a recycled handle, a missing or ambiguous label) prove a refusal happens
BEFORE the backend is asked to touch anything.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest

from lab_commons import desktop
from lab_commons.desktop import (
    Control,
    ControlDisabledError,
    ControlGoneError,
    LabelAmbiguousError,
    LabelNotFoundError,
    NotActionableError,
    NotOwnedError,
    UnsupportedError,
    Window,
    png_bytes,
)
from lab_commons.desktop.__main__ import main
from lab_commons.desktop._core import pick


def _window(
    handle: int = 0x10,
    pid: int = 7,
    title: str = 'Solver - run',
    *,
    visible: bool = True,
    rect: tuple[int, int, int, int] = (0, 0, 4, 2),
    owner: int = 0,
) -> Window:
    return Window(
        handle=handle, pid=pid, title=title, rect=rect, visible=visible, enabled=True, responding=True, owner=owner
    )


class FakeBackend:
    """Records every act it is asked to perform; the guard's refusals must leave ``acts`` empty."""

    def __init__(self, shown: list[Window]) -> None:
        """Show *shown* as the host's windows."""
        self.shown = shown
        self.acts: list[tuple[object, ...]] = []
        #: The window a ``close`` asks to close stays open (a modal it owns blocks it), as JMAG's did live.
        self.stays = False

    def windows(self) -> list[Window]:
        return list(self.shown)

    def capture(self, window: Window, timeout_s: float) -> bytes:
        return png_bytes(1, 1, b'\x00\x00\xff\xff')

    def controls(self, window: Window) -> list[Control]:
        return [Control(role='button', name='OK', value=None, enabled=True, actions=('press',))]

    def press(self, window: Window, label: str | None, id: str | None) -> None:  # noqa: A002 -- the door's name
        self.acts.append(('press', window.handle, label or id))

    def set_value(self, window: Window, label: str | None, id: str | None, text: str) -> None:  # noqa: A002
        self.acts.append(('set_value', window.handle, label or id, text))

    def close(self, window: Window) -> None:
        self.acts.append(('close', window.handle))
        if not self.stays:
            self.shown = [w for w in self.shown if w.handle != window.handle]


class TestWindows:
    def test_filters_by_pid_title_and_visibility(self) -> None:
        fake = FakeBackend([_window(1, 7, 'Solver'), _window(2, 8, 'Other'), _window(3, 7, 'hidden', visible=False)])
        assert [w.handle for w in desktop.windows(backend=fake)] == [1, 2]
        assert [w.handle for w in desktop.windows(pids={7}, backend=fake)] == [1]
        assert [w.handle for w in desktop.windows(title='oth', backend=fake)] == [2], 'title is a case-blind substring'
        assert [w.handle for w in desktop.windows(pids={7}, hidden=True, backend=fake)] == [1, 3]


class TestOwnershipGuard:
    """PLANTED: an act on a window whose pid the caller does not own never reaches the backend."""

    @pytest.mark.parametrize(
        'act',
        [
            lambda w, o, b: desktop.press(w, 'OK', owned=o, backend=b),
            lambda w, o, b: desktop.set_value(w, 'Name', 'x', owned=o, backend=b),
            lambda w, o, b: desktop.close(w, owned=o, backend=b),
        ],
        ids=['press', 'set_value', 'close'],
    )
    def test_a_window_outside_the_owned_pids_is_refused(self, act) -> None:
        window = _window(pid=7)
        fake = FakeBackend([window])
        with pytest.raises(NotOwnedError) as refused:
            act(window, {8}, fake)
        assert refused.value.reason == 'desktop.not_owned'
        assert fake.acts == []

    def test_a_handle_now_held_by_another_pid_is_refused(self) -> None:
        fake = FakeBackend([_window(0x10, pid=99)])  # the handle was recycled since the caller listed it
        with pytest.raises(NotOwnedError):
            desktop.press(_window(0x10, pid=7), 'OK', owned={7}, backend=fake)
        assert fake.acts == []

    def test_a_window_that_is_gone_is_refused(self) -> None:
        fake = FakeBackend([])
        with pytest.raises(NotOwnedError):
            desktop.close(_window(), owned={7}, backend=fake)
        assert fake.acts == []

    def test_the_control_an_owned_act_reaches_the_backend_once(self) -> None:
        window = _window()
        fake = FakeBackend([window])
        desktop.press(window, 'OK', owned={7}, backend=fake)
        desktop.set_value(window, 'Name', 'v', owned=[7], backend=fake)
        desktop.close(window, owned={7}, backend=fake)
        assert fake.acts == [('press', 0x10, 'OK'), ('set_value', 0x10, 'Name', 'v'), ('close', 0x10)]

    def test_an_act_by_id_reaches_the_backend_with_the_id(self) -> None:
        window = _window()
        fake = FakeBackend([window])
        desktop.press(window, id='42.7', owned={7}, backend=fake)
        desktop.set_value(window, text='v', id='42.8', owned={7}, backend=fake)
        assert fake.acts == [('press', 0x10, '42.7'), ('set_value', 0x10, '42.8', 'v')]

    @pytest.mark.parametrize('chosen', [{}, {'label': 'OK', 'id': '42.7'}], ids=['neither', 'both'])
    def test_exactly_one_of_label_and_id_is_named(self, chosen: dict[str, str]) -> None:
        window = _window()
        fake = FakeBackend([window])
        with pytest.raises(TypeError, match='exactly one'):
            desktop.press(window, owned={7}, backend=fake, **chosen)
        assert fake.acts == []


class TestCloseIsVerified:
    """A close is done only when the window is GONE.

    Measured 2026-11-14: a JMAG main window disabled under its Startup modal stayed open while ``close`` said done.
    """

    def test_a_window_that_closes_is_done(self) -> None:
        window = _window()
        fake = FakeBackend([window])
        desktop.close(window, owned={7}, backend=fake, timeout_s=0.5)
        assert fake.shown == []

    def test_planted_a_window_kept_open_by_its_owned_modal_is_not_closed(self) -> None:
        window = _window(0x10)
        modal = _window(0x20, title='JMAG-Designer: Startup', owner=0x10)
        fake = FakeBackend([window, modal])
        fake.stays = True
        with pytest.raises(desktop.NotClosedError) as refused:
            desktop.close(window, owned={7}, backend=fake, timeout_s=0.3)
        assert refused.value.reason == 'desktop.not_closed'
        assert 'JMAG-Designer: Startup' in str(refused.value)
        assert '0x20' in str(refused.value)

    def test_a_window_that_stays_with_no_owned_window_is_not_closed(self) -> None:
        window = _window()
        fake = FakeBackend([window])
        fake.stays = True
        with pytest.raises(desktop.NotClosedError, match='no owned window'):
            desktop.close(window, owned={7}, backend=fake, timeout_s=0.2)


class TestNoArea:
    """A zero-area window (Workbench's 0x0 'Internet Explorer_Hidden', 2026-11-14) is refused by its own reason."""

    def test_a_window_states_its_area(self) -> None:
        assert _window(rect=(10, 10, 14, 12)).area == 8
        assert _window(rect=(0, 0, 0, 0)).area == 0
        assert _window(rect=(5, 5, 3, 9)).area == 0, 'an inverted rect has no area'

    def test_capture_of_a_zero_area_window_is_no_area_not_busy(self) -> None:
        fake = FakeBackend([])
        with pytest.raises(desktop.NoAreaError) as refused:
            desktop.capture(_window(rect=(0, 0, 0, 0)), backend=fake)
        assert refused.value.reason == 'desktop.no_area'
        assert not isinstance(refused.value, desktop.WindowBusyError)


class TestPick:
    def _c(self, name: str, *, enabled: bool = True, id: str = '', actions: tuple[str, ...] = ('press',)) -> Control:  # noqa: A002
        return Control(role='button', name=name, value=None, enabled=enabled, actions=actions, id=id)

    def test_exactly_one_label_is_picked(self) -> None:
        assert (
            pick(_window(), 'press', [(self._c('OK'), 'ref-ok'), (self._c('Cancel'), 'ref-c')], label='OK') == 'ref-ok'
        )

    def test_a_label_counts_only_controls_that_support_the_act(self) -> None:
        text = self._c('OK', actions=())
        assert pick(_window(), 'press', [(text, 'text'), (self._c('OK'), 'button')], label='OK') == 'button'

    def test_the_label_is_exact_not_a_substring_or_case_blind(self) -> None:
        with pytest.raises(LabelNotFoundError) as missing:
            pick(_window(), 'press', [(self._c('OK'), 1), (self._c('OK all'), 2)], label='ok')
        assert missing.value.reason == 'desktop.label_not_found'

    def test_two_controls_with_the_label_are_refused_not_guessed(self) -> None:
        with pytest.raises(LabelAmbiguousError) as twice:
            pick(_window(), 'press', [(self._c('OK'), 1), (self._c('OK'), 2)], label='OK')
        assert twice.value.reason == 'desktop.label_ambiguous'

    def test_a_disabled_control_is_refused(self) -> None:
        with pytest.raises(ControlDisabledError) as disabled:
            pick(_window(), 'press', [(self._c('OK', enabled=False), 1)], label='OK')
        assert disabled.value.reason == 'desktop.control_disabled'

    def test_an_id_picks_its_control_whatever_its_label(self) -> None:
        found = [(self._c('OK', id='1.1'), 'a'), (self._c('OK', id='1.2'), 'b')]
        assert pick(_window(), 'press', found, id='1.2') == 'b'

    def test_planted_an_id_not_in_the_tree_now_is_gone(self) -> None:
        with pytest.raises(ControlGoneError) as gone:
            pick(_window(), 'press', [(self._c('OK', id='1.1'), 'a')], id='1.9')
        assert gone.value.reason == 'desktop.control_gone'

    def test_an_id_whose_control_lacks_the_act_is_refused(self) -> None:
        with pytest.raises(NotActionableError) as lacking:
            pick(_window(), 'set_value', [(self._c('OK', id='1.1'), 'a')], id='1.1')
        assert lacking.value.reason == 'desktop.not_actionable'

    def test_a_disabled_control_by_id_is_refused(self) -> None:
        with pytest.raises(ControlDisabledError):
            pick(_window(), 'press', [(self._c('OK', id='1.1', enabled=False), 'a')], id='1.1')


class TestBackendChoice:
    def test_an_unknown_platform_is_refused_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(desktop._door.sys, 'platform', 'sunos5')
        with pytest.raises(UnsupportedError) as refused:
            desktop.windows()
        assert refused.value.reason == 'desktop.unsupported_platform'


def _decode_png(data: bytes) -> tuple[int, int, bytes]:
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    at, chunks = 8, {}
    while at < len(data):
        (length,) = struct.unpack('>I', data[at : at + 4])
        kind, body = data[at + 4 : at + 8], data[at + 8 : at + 8 + length]
        (crc,) = struct.unpack('>I', data[at + 8 + length : at + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        chunks[kind] = chunks.get(kind, b'') + body
        at += 12 + length
    width, height, depth, colour = struct.unpack('>IIBB', chunks[b'IHDR'][:10])
    assert (depth, colour) == (8, 2), 'RGB, 8 bits per channel'
    return width, height, zlib.decompress(chunks[b'IDAT'])


class TestPng:
    def test_bgra_top_down_becomes_rgb_rows(self) -> None:
        bgra = bytes([1, 2, 3, 255, 4, 5, 6, 255, 7, 8, 9, 255, 10, 11, 12, 255])  # 2 x 2
        width, height, raw = _decode_png(png_bytes(2, 2, bgra))
        assert (width, height) == (2, 2)
        assert raw == b'\x00' + bytes([3, 2, 1, 6, 5, 4]) + b'\x00' + bytes([9, 8, 7, 12, 11, 10])

    def test_a_short_buffer_is_refused(self) -> None:
        with pytest.raises(ValueError, match='bytes'):
            png_bytes(2, 2, b'\x00' * 15)


class TestCli:
    """``python -m lab_commons.desktop``: one JSON array, refusals named on stderr, acts need --pid and one window."""

    def _fake(self, monkeypatch: pytest.MonkeyPatch, shown: list[Window]) -> FakeBackend:
        fake = FakeBackend(shown)
        monkeypatch.setattr(desktop._door, 'backend', lambda: fake)
        return fake

    def test_windows_json_is_one_array_with_cpu_seconds(self, monkeypatch, capsys) -> None:
        self._fake(monkeypatch, [_window(1, 7, 'Solver'), _window(2, 8, 'Other')])
        assert main(['windows', '--pid', '7', '--json']) == 0
        (row,) = json.loads(capsys.readouterr().out)
        assert (row['handle'], row['pid'], row['title'], row['rect']) == (1, 7, 'Solver', [0, 0, 4, 2])
        assert 'cpu_s' in row

    def test_capture_writes_one_png_per_window(self, monkeypatch, capsys, tmp_path) -> None:
        self._fake(monkeypatch, [_window(0x1F, 7)])
        assert main(['capture', '--pid', '7', '--out', str(tmp_path), '--json']) == 0
        (row,) = json.loads(capsys.readouterr().out)
        assert Path(row['png']).read_bytes().startswith(b'\x89PNG')

    def test_capture_reports_each_window_and_continues_past_a_refusal(self, monkeypatch, capsys, tmp_path) -> None:
        self._fake(monkeypatch, [_window(0x1E, 7, rect=(0, 0, 0, 0)), _window(0x1F, 7)])
        assert main(['capture', '--pid', '7', '--out', str(tmp_path), '--json']) == 0
        hidden, shown = json.loads(capsys.readouterr().out)
        assert (hidden['handle'], hidden['refused']) == (0x1E, 'desktop.no_area')
        assert 'png' not in hidden
        assert Path(shown['png']).read_bytes().startswith(b'\x89PNG')

    def test_capture_with_nothing_captured_exits_1(self, monkeypatch, capsys, tmp_path) -> None:
        self._fake(monkeypatch, [_window(0x1E, 7, rect=(0, 0, 0, 0))])
        assert main(['capture', '--pid', '7', '--out', str(tmp_path), '--json']) == 1
        (row,) = json.loads(capsys.readouterr().out)
        assert row['refused'] == 'desktop.no_area'

    def test_windows_rows_carry_the_area(self, monkeypatch, capsys) -> None:
        self._fake(monkeypatch, [_window(1, 7, rect=(0, 0, 0, 0))])
        assert main(['windows', '--json']) == 0
        assert json.loads(capsys.readouterr().out)[0]['area'] == 0

    def test_controls_json(self, monkeypatch, capsys) -> None:
        self._fake(monkeypatch, [_window()])
        assert main(['controls', '--json']) == 0
        assert json.loads(capsys.readouterr().out)[0]['name'] == 'OK'

    def test_press_reaches_the_backend_once(self, monkeypatch, capsys) -> None:
        fake = self._fake(monkeypatch, [_window()])
        assert main(['press', '--pid', '7', '--label', 'OK', '--json']) == 0
        assert fake.acts == [('press', 0x10, 'OK')]
        assert json.loads(capsys.readouterr().out)[0]['done'] == 'press'

    def test_press_by_id(self, monkeypatch) -> None:
        fake = self._fake(monkeypatch, [_window()])
        assert main(['press', '--pid', '7', '--id', '42.7']) == 0
        assert fake.acts == [('press', 0x10, '42.7')]

    def test_label_and_id_together_are_a_usage_error(self, monkeypatch) -> None:
        fake = self._fake(monkeypatch, [_window()])
        with pytest.raises(SystemExit):
            main(['press', '--pid', '7', '--label', 'OK', '--id', '42.7'])
        assert fake.acts == []

    def test_an_act_without_pid_is_a_usage_error(self, monkeypatch) -> None:
        fake = self._fake(monkeypatch, [_window()])
        with pytest.raises(SystemExit) as usage:
            main(['close', '--title', 'Solver'])
        assert usage.value.code == 2
        assert fake.acts == []

    def test_an_act_on_two_windows_is_a_usage_error(self, monkeypatch) -> None:
        fake = self._fake(monkeypatch, [_window(1), _window(2)])
        with pytest.raises(SystemExit):
            main(['set-value', '--pid', '7', '--label', 'Name', '--value', 'v'])
        assert fake.acts == []

    def test_a_refusal_is_named_on_stderr_and_exits_1(self, monkeypatch, capsys) -> None:
        fake = self._fake(monkeypatch, [_window()])

        def refuse(*_args: object) -> None:
            said = 'no OK here'
            raise LabelNotFoundError(said)

        monkeypatch.setattr(fake, 'press', refuse)
        assert main(['press', '--pid', '7', '--label', 'OK']) == 1
        assert 'desktop: desktop.label_not_found -- no OK here' in capsys.readouterr().err
