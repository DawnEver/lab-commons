"""The X11 backend's AT-SPI half against a fake ``gdbus``: what it SENDS (argv only) and how it READS GVariant text.

The fake answers by ``(dest, object path, method, args)`` and fails anything else, the way a real bus
answers an unknown object; so a call the backend should not make is a failure, not a silent ``None``.
"""

from __future__ import annotations

import subprocess

import pytest

from lab_commons.desktop import (
    Control,
    ControlDisabledError,
    ControlGoneError,
    LabelAmbiguousError,
    UnsupportedError,
    Window,
    _atspi,
    _x11,
)

_WINDOW = Window(handle=0x1A00003, pid=4242, title='Solver "A"', rect=(0, 0, 2, 1), visible=True, enabled=None,
                 responding=None)  # fmt: skip
_ADDRESS = 'unix:path=/run/user/1000/at-spi/bus_0,guid=ab'
_ROOT = '/org/a11y/atspi/accessible/root'
_A11Y = 'org.a11y.atspi.'
_PROP = 'org.freedesktop.DBus.Properties.Get'
_ENABLED = 1 << 8  # ATSPI_STATE_ENABLED in the first state word


def _node(path: str, role: str, name: str, interfaces: list[str], *, enabled: bool = True,
          children: tuple[str, ...] = (), ident: str = '') -> dict[tuple[str, str, tuple[str, ...]], str]:  # fmt: skip
    kids = ', '.join(f"(':1.7', objectpath '{c}')" for c in children)
    ifaces = ', '.join(f"'{_A11Y}{i}'" for i in ['Accessible', *interfaces])
    return {
        (path, f'{_A11Y}Accessible.GetChildren', ()): f'([{kids}],)' if kids else '(@a(so) [],)',
        (path, f'{_A11Y}Accessible.GetRoleName', ()): f"('{role}',)",
        (path, _PROP, (f"'{_A11Y}Accessible'", "'Name'")): f"(<'{name}'>,)",
        (path, _PROP, (f"'{_A11Y}Accessible'", "'AccessibleId'")): f"(<'{ident}'>,)",
        (path, f'{_A11Y}Accessible.GetInterfaces', ()): f'([{ifaces}],)',
        (path, f'{_A11Y}Accessible.GetState', ()): f'([uint32 {_ENABLED if enabled else 0}, 0],)',
    }


class _Bus:
    """A fake ``gdbus``: one app (pid 4242, bus :1.7) with the window's frame, and a foreign app with a twin."""

    def __init__(self, *, ok_twice: bool = False) -> None:
        self.argvs: list[list[str]] = []
        frame_kids = ('/ok', '/name', '/gain', '/label', '/off') + (('/ok2',) if ok_twice else ())
        self.answers: dict[tuple[str, str, str, tuple[str, ...]], str] = {
            ('org.a11y.Bus', '/org/a11y/bus', 'org.a11y.Bus.GetAddress', ()): f"('{_ADDRESS}',)",
            ('org.a11y.atspi.Registry', _ROOT, f'{_A11Y}Accessible.GetChildren', ()): (
                f"([(':1.9', objectpath '{_ROOT}'), (':1.7', objectpath '{_ROOT}')],)"
            ),
            ('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus.GetConnectionUnixProcessID',
             ("':1.7'",)): '(uint32 4242,)',
            ('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus.GetConnectionUnixProcessID',
             ("':1.9'",)): '(uint32 99,)',
        }  # fmt: skip
        tree = {
            **_node(_ROOT, 'application', 'solver', [], children=('/other', '/frame')),
            **_node('/other', 'frame', 'Other', []),
            **_node('/frame', 'frame', 'Solver "A"', ['Action'], children=frame_kids),
            **_node('/ok', 'push button', 'OK', ['Action'], ident='ok-button'),
            **_node('/ok2', 'push button', 'OK', ['Action']),
            **_node('/name', 'text', 'Run name:', ['Text', 'EditableText']),
            **_node('/gain', 'spin button', 'Gain', ['Value']),
            **_node('/label', 'label', 'Proceed?', [], children=('/hint',)),
            **_node('/hint', 'label', 'why', []),
            **_node('/off', 'push button', 'Apply', ['Action'], enabled=False),
            ('/frame', f'{_A11Y}Action.GetActions', ()): "([('activate', '', ''), ('close', 'Close', '')],)",
            ('/name', f'{_A11Y}Text.GetText', ('0', '-1')): "('first',)",
            ('/gain', _PROP, (f"'{_A11Y}Value'", "'CurrentValue'")): '(<1.5>,)',
            ('/ok', f'{_A11Y}Action.DoAction', ('0',)): '(true,)',
            ('/ok2', f'{_A11Y}Action.DoAction', ('0',)): '(true,)',
            ('/frame', f'{_A11Y}Action.DoAction', ('1',)): '(true,)',
            ('/name', f'{_A11Y}EditableText.SetTextContents', ("'it\\'s \\\\ new'",)): '(true,)',
            ('/gain', 'org.freedesktop.DBus.Properties.Set', (f"'{_A11Y}Value'", "'CurrentValue'", '<2.5>')): '()',
        }
        for (path, method, args), answer in tree.items():
            self.answers[(':1.7', path, method, args)] = answer
        # the foreign app (pid 99) has a frame with the SAME title: it must never be read or acted on
        for (path, method, args), answer in _node(_ROOT, 'application', 'twin', [], children=('/frame',)).items():
            self.answers[(':1.9', path, method, args)] = answer.replace(':1.7', ':1.9')
        for (path, method, args), answer in _node('/frame', 'frame', 'Solver "A"', []).items():
            self.answers[(':1.9', path, method, args)] = answer

    def __call__(self, argv: list[str], timeout_s: float) -> bytes:
        self.argvs.append(argv)
        assert argv[:2] == ['gdbus', 'call']
        flags = dict(zip(argv[2:-1:2], argv[3::2], strict=False))
        if '--session' in argv:
            dest, path, method, args = argv[4], argv[6], argv[8], tuple(argv[9:])
        else:
            assert argv[2:4] == ['--address', _ADDRESS], argv
            at = argv.index('--method')
            dest, path, method, args = flags['--dest'], flags['--object-path'], argv[at + 1], tuple(argv[at + 2 :])
        answer = self.answers.get((dest, path, method, args))
        if answer is None:
            raise subprocess.CalledProcessError(1, argv, stderr=b'GDBus.Error:org.freedesktop.DBus.Error.UnknownObject')
        return answer.encode() + b'\n'

    def methods(self) -> list[tuple[str, str]]:
        return [(a[a.index('--object-path') + 1], a[a.index('--method') + 1]) for a in self.argvs]


def _x(bus: _Bus) -> _x11.X11:
    return _x11.X11(run=bus, env={'DISPLAY': ':0'}, which=lambda name: f'/usr/bin/{name}')


class TestGVariant:
    @pytest.mark.parametrize(
        ('text', 'value'),
        [
            ("('unix:path=/a,guid=b',)", ('unix:path=/a,guid=b',)),
            ("([(':1.7', objectpath '/r')],)", ([(':1.7', '/r')],)),
            ('(@a(so) [],)', ([],)),
            ("(<'it\\'s'>,)", ("it's",)),
            ('(uint32 4242,)', (4242,)),
            ('([uint32 256, 0],)', ([256, 0],)),
            ('(true,)', (True,)),
            ('(<1.5>,)', (1.5,)),
            ('(<-3>,)', (-3,)),
            ('()', ()),
            ("({'a': <1>},)", ({'a': 1},)),
            ('("double \\"q\\"",)', ('double "q"',)),
        ],
    )
    def test_gdbus_text_is_parsed(self, text: str, value: object) -> None:
        assert _atspi.parse_gvariant(text) == value

    def test_trailing_garbage_is_refused(self) -> None:
        with pytest.raises(ValueError, match='GVariant'):
            _atspi.parse_gvariant('(1,) x')

    @pytest.mark.parametrize(('text', 'literal'), [('plain', "'plain'"), ("it's \\", "'it\\'s \\\\'"),
                                                   ('a\nb', "'a\\u000ab'")])  # fmt: skip
    def test_a_string_argument_is_a_quoted_literal(self, text: str, literal: str) -> None:
        assert _atspi.gvariant_string(text) == literal
        assert _atspi.parse_gvariant(f'({literal},)') == (text,)


class TestAtspi:
    def test_controls_read_the_frame_of_the_windows_pid_and_title(self) -> None:
        bus = _Bus()
        found = _x(bus).controls(_WINDOW)
        assert found == [
            Control(role='push button', name='OK', value=None, enabled=True, actions=('press',), id=':1.7:/ok',
                    automation_id='ok-button'),
            Control(role='text', name='Run name:', value='first', enabled=True, actions=('set_value',),
                    id=':1.7:/name'),
            Control(role='spin button', name='Gain', value='1.5', enabled=True, actions=('set_value',),
                    id=':1.7:/gain'),
            Control(role='label', name='Proceed?', value=None, enabled=True, id=':1.7:/label'),
            Control(role='label', name='why', value=None, enabled=True, id=':1.7:/hint', parent=':1.7:/label'),
            Control(role='push button', name='Apply', value=None, enabled=False, actions=('press',),
                    id=':1.7:/off'),
        ]  # fmt: skip
        assert bus.argvs[0] == ['gdbus', 'call', '--session', '--dest', 'org.a11y.Bus', '--object-path',
                                '/org/a11y/bus', '--method', 'org.a11y.Bus.GetAddress']  # fmt: skip
        assert ':1.9' not in [a[a.index('--dest') + 1] for a in bus.argvs], 'a foreign app is never walked'

    def test_press_does_action_zero_of_the_one_labelled_control(self) -> None:
        bus = _Bus()
        _x(bus).press(_WINDOW, 'OK', None)
        assert bus.methods()[-1] == ('/ok', f'{_A11Y}Action.DoAction')
        assert bus.argvs[-1][-1] == '0'

    def test_press_by_id_acts_on_that_object(self) -> None:
        bus = _Bus(ok_twice=True)
        _x(bus).press(_WINDOW, None, ':1.7:/ok2')
        assert bus.methods()[-1] == ('/ok2', f'{_A11Y}Action.DoAction')

    def test_planted_an_id_no_longer_in_the_tree_is_gone(self) -> None:
        bus = _Bus()
        with pytest.raises(ControlGoneError):
            _x(bus).press(_WINDOW, None, ':1.7:/ok2')
        assert all(m != f'{_A11Y}Action.DoAction' for _, m in bus.methods())

    def test_two_controls_with_one_label_are_refused(self) -> None:
        bus = _Bus(ok_twice=True)
        with pytest.raises(LabelAmbiguousError):
            _x(bus).press(_WINDOW, 'OK', None)
        assert all(m != f'{_A11Y}Action.DoAction' for _, m in bus.methods())

    def test_a_disabled_control_is_refused(self) -> None:
        with pytest.raises(ControlDisabledError):
            _x(_Bus()).press(_WINDOW, 'Apply', None)

    def test_set_value_on_editable_text_passes_the_text_as_one_quoted_argument(self) -> None:
        bus = _Bus()
        _x(bus).set_value(_WINDOW, 'Run name:', None, "it's \\ new")
        assert bus.methods()[-1] == ('/name', f'{_A11Y}EditableText.SetTextContents')
        assert bus.argvs[-1][-1] == "'it\\'s \\\\ new'"

    def test_set_value_on_a_value_control_sets_its_number(self) -> None:
        bus = _Bus()
        _x(bus).set_value(_WINDOW, 'Gain', None, '2.5')
        assert bus.argvs[-1][-3:] == [f"'{_A11Y}Value'", "'CurrentValue'", '<2.5>']

    def test_a_value_control_refuses_text_that_is_not_a_number(self) -> None:
        bus = _Bus()
        with pytest.raises(ValueError, match='number'):
            _x(bus).set_value(_WINDOW, 'Gain', None, 'high')
        assert all(m != 'org.freedesktop.DBus.Properties.Set' for _, m in bus.methods())

    def test_close_does_the_frames_own_close_action(self) -> None:
        bus = _Bus()
        _x(bus).close(_WINDOW)
        assert bus.methods()[-1] == ('/frame', f'{_A11Y}Action.DoAction')
        assert bus.argvs[-1][-1] == '1'

    def test_a_frame_without_a_close_action_is_not_closable(self) -> None:
        bus = _Bus()
        bus.answers[(':1.7', '/frame', f'{_A11Y}Action.GetActions', ())] = "([('activate', '', '')],)"
        with pytest.raises(UnsupportedError) as refused:
            _x(bus).close(_WINDOW)
        assert refused.value.reason == 'desktop.not_closable'

    def test_no_frame_with_the_windows_title_is_refused_by_name(self) -> None:
        window = Window(**{**_WINDOW.__dict__, 'title': 'Gone'})
        with pytest.raises(UnsupportedError) as refused:
            _x(_Bus()).controls(window)
        assert refused.value.reason == 'desktop.no_atspi'
        assert 'Gone' in str(refused.value)

    def test_an_absent_bus_is_refused_by_name(self) -> None:
        bus = _Bus()
        del bus.answers[('org.a11y.Bus', '/org/a11y/bus', 'org.a11y.Bus.GetAddress', ())]
        x11 = _x(bus)
        for verb in (lambda: x11.controls(_WINDOW), lambda: x11.press(_WINDOW, 'OK', None),
                     lambda: x11.set_value(_WINDOW, 'Gain', None, '1'), lambda: x11.close(_WINDOW)):  # fmt: skip
            with pytest.raises(UnsupportedError) as refused:
                verb()
            assert refused.value.reason == 'desktop.no_atspi'

    def test_a_missing_gdbus_is_the_absent_bus(self) -> None:
        x11 = _x11.X11(run=_Bus(), env={'DISPLAY': ':0'}, which=lambda name: None if name == 'gdbus' else name)
        with pytest.raises(UnsupportedError) as refused:
            x11.controls(_WINDOW)
        assert refused.value.reason == 'desktop.no_atspi'
        assert 'gdbus' in str(refused.value)

    def test_a_call_past_its_deadline_is_busy(self) -> None:
        def slow(argv: list[str], timeout_s: float) -> bytes:
            if 'GetAddress' in argv[-1]:
                return f"('{_ADDRESS}',)".encode()
            raise subprocess.TimeoutExpired(argv, timeout_s)

        from lab_commons.desktop import WindowBusyError  # noqa: PLC0415 -- local to the one test that needs it

        with pytest.raises(WindowBusyError):
            _x11.X11(run=slow, env={'DISPLAY': ':0'}, which=lambda n: n).controls(_WINDOW)
