"""AT-SPI over ``gdbus``: the X11 backend's controls and acts, stdlib only, every value an argv element.

The accessibility bus is found through the session bus (``org.a11y.Bus.GetAddress``); its registry lists
one accessible root per application, whose bus name maps to a pid through
``org.freedesktop.DBus.GetConnectionUnixProcessID``. AT-SPI knows no X window id, so a window is matched
to the ONE frame of an application OF ITS PID whose name is exactly its title -- none or several is
refused by name, and an application of any other pid is never walked. Each call is one ``gdbus call``
with its own deadline; one missed is :class:`WindowBusyError`. Every argument is a GVariant literal built
here (:func:`gvariant_string`), never a shell string; every answer is GVariant text read by
:func:`parse_gvariant`.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lab_commons.desktop._core import Control, UnsupportedError, Window, WindowBusyError, pick

__all__ = ['Atspi', 'gvariant_string', 'parse_gvariant']

#: ``argv -> stdout bytes``; raises ``subprocess.TimeoutExpired`` past its deadline.
Runner = Callable[[list[str], float], bytes]

_A11Y = 'org.a11y.atspi.'
_ROOT = '/org/a11y/atspi/accessible/root'
#: ``ATSPI_STATE_ENABLED``: bit 8 of the first state word.
_STATE_ENABLED = 1 << 8
#: The deadline on one ``gdbus call``, seconds.
_CALL_S = 5.0


@dataclass(frozen=True)
class _Node:
    bus: str
    path: str
    interfaces: frozenset[str]


class Atspi:
    """The AT-SPI bus of this session; *run* and *which* are the seams the unit tests fill."""

    def __init__(self, run: Runner, which: Callable[[str], str | None]) -> None:
        self._run = run
        self._which = which
        self._address: str | None = None

    # ---- the verbs ---------------------------------------------------------------------------------------

    def controls(self, window: Window) -> list[Control]:
        return [control for control, _ in self._walk(window)]

    def press(self, window: Window, label: str | None, id: str | None) -> None:  # noqa: A002 -- Control.id
        node = pick(window, 'press', self._walk(window), label=label, id=id)
        self._call(window, node.bus, node.path, f'{_A11Y}Action.DoAction', '0')

    def set_value(self, window: Window, label: str | None, id: str | None, text: str) -> None:  # noqa: A002
        node = pick(window, 'set_value', self._walk(window), label=label, id=id)
        if f'{_A11Y}EditableText' in node.interfaces:
            self._call(window, node.bus, node.path, f'{_A11Y}EditableText.SetTextContents', gvariant_string(text))
            return
        try:
            number = float(text)
        except ValueError:
            msg = f'the control {label or id!r} holds a number (AT-SPI Value); {text!r} is not a number'
            raise ValueError(msg) from None
        self._call(
            window, node.bus, node.path, 'org.freedesktop.DBus.Properties.Set',
            gvariant_string(f'{_A11Y}Value'), gvariant_string('CurrentValue'), f'<{number!r}>',
        )  # fmt: skip

    def close(self, window: Window) -> None:
        frame = self._frame(window)
        actions = self._call(window, frame.bus, frame.path, f'{_A11Y}Action.GetActions')[0] if (
            f'{_A11Y}Action' in frame.interfaces
        ) else []  # fmt: skip
        names = [row[0] for row in actions]
        if 'close' not in names:
            msg = f'window {window.handle:#x} ({window.title!r}) exposes no AT-SPI close action (it has {names})'
            raise UnsupportedError(msg, 'desktop.not_closable')
        self._call(window, frame.bus, frame.path, f'{_A11Y}Action.DoAction', str(names.index('close')))

    # ---- the tree ----------------------------------------------------------------------------------------

    def _frame(self, window: Window) -> _Node:
        """The ONE frame titled exactly ``window.title`` among the applications of ``window.pid``."""
        frames = []
        for bus, path in self._call(window, f'{_A11Y}Registry', _ROOT, f'{_A11Y}Accessible.GetChildren')[0]:
            pid = self._call(
                window, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus.GetConnectionUnixProcessID', gvariant_string(bus),
            )[0]  # fmt: skip
            if pid != window.pid:
                continue
            for child_bus, child in self._children(window, bus, path):
                if self._name(window, child_bus, child) == window.title:
                    frames.append(_Node(child_bus, child, self._interfaces(window, child_bus, child)))
        if len(frames) != 1:
            found = 'no' if not frames else f'{len(frames)}'
            msg = (
                f'{found} AT-SPI frame(s) of pid {window.pid} titled exactly {window.title!r} '
                f'(window {window.handle:#x}): the application must expose its window to AT-SPI, once'
            )
            raise UnsupportedError(msg, 'desktop.no_atspi')
        return frames[0]

    def _walk(self, window: Window) -> list[tuple[Control, _Node]]:
        frame = self._frame(window)
        found: list[tuple[Control, _Node]] = []
        pending = [(child, None) for child in self._children(window, frame.bus, frame.path)]
        while pending:
            (bus, path), parent = pending.pop(0)
            node = _Node(bus, path, self._interfaces(window, bus, path))
            control = self._control(window, node, parent)
            found.append((control, node))
            # depth first, in the tree's own order
            pending[:0] = [(child, control.id) for child in self._children(window, bus, path)]
        return found

    def _control(self, window: Window, node: _Node, parent: str | None) -> Control:
        call = lambda method, *args: self._call(window, node.bus, node.path, method, *args)[0]  # noqa: E731
        actions: list[str] = []
        value: str | None = None
        if f'{_A11Y}Action' in node.interfaces:
            actions.append('press')
        if f'{_A11Y}Text' in node.interfaces:
            value = call(f'{_A11Y}Text.GetText', '0', '-1')
        if f'{_A11Y}Value' in node.interfaces:
            value = str(call('org.freedesktop.DBus.Properties.Get', gvariant_string(f'{_A11Y}Value'),
                             gvariant_string('CurrentValue')))  # fmt: skip
        if {f'{_A11Y}EditableText', f'{_A11Y}Value'} & node.interfaces:
            actions.append('set_value')
        return Control(
            role=call(f'{_A11Y}Accessible.GetRoleName'),
            name=self._name(window, node.bus, node.path),
            value=value,
            enabled=bool(call(f'{_A11Y}Accessible.GetState')[0] & _STATE_ENABLED),
            actions=tuple(actions),
            id=f'{node.bus}:{node.path}',
            parent=parent,
            automation_id=call(
                'org.freedesktop.DBus.Properties.Get', gvariant_string(f'{_A11Y}Accessible'),
                gvariant_string('AccessibleId'),
            ) or None,
        )  # fmt: skip

    def _children(self, window: Window, bus: str, path: str) -> list[tuple[str, str]]:
        return [tuple(c) for c in self._call(window, bus, path, f'{_A11Y}Accessible.GetChildren')[0]]

    def _name(self, window: Window, bus: str, path: str) -> str:
        return self._call(
            window, bus, path, 'org.freedesktop.DBus.Properties.Get',
            gvariant_string(f'{_A11Y}Accessible'), gvariant_string('Name'),
        )[0]  # fmt: skip

    def _interfaces(self, window: Window, bus: str, path: str) -> frozenset[str]:
        return frozenset(self._call(window, bus, path, f'{_A11Y}Accessible.GetInterfaces')[0])

    # ---- the wire ----------------------------------------------------------------------------------------

    def _bus_address(self) -> str:
        if self._address is None:
            if self._which('gdbus') is None:
                msg = 'gdbus (libglib2.0-bin) is not installed: the AT-SPI bus cannot be reached without it'
                raise UnsupportedError(msg, 'desktop.no_atspi')
            argv = ['gdbus', 'call', '--session', '--dest', 'org.a11y.Bus', '--object-path', '/org/a11y/bus',
                    '--method', 'org.a11y.Bus.GetAddress']  # fmt: skip
            try:
                self._address = parse_gvariant(self._run(argv, _CALL_S).decode('utf-8', 'replace'))[0]
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, IndexError) as absent:
                msg = 'no AT-SPI bus answers on this session (at-spi2-core not running, or accessibility off)'
                raise UnsupportedError(msg, 'desktop.no_atspi') from absent
        return self._address

    def _call(self, window: Window, dest: str, path: str, method: str, *args: str) -> Any:  # noqa: ANN401 -- GVariant
        argv = ['gdbus', 'call', '--address', self._bus_address(), '--dest', dest, '--object-path', path,
                '--method', method, *args]  # fmt: skip
        try:
            answer = self._run(argv, _CALL_S)
        except subprocess.TimeoutExpired as late:
            msg = f'AT-SPI {method} on {dest}{path} for window {window.handle:#x} missed {_CALL_S:g} s: busy'
            raise WindowBusyError(msg) from late
        return parse_gvariant(answer.decode('utf-8', 'replace'))


def gvariant_string(text: str) -> str:
    """*text* as one single-quoted GVariant string literal (backslash, quote and controls escaped)."""
    escaped = text.replace('\\', '\\\\').replace("'", "\\'")
    return "'" + re.sub(r'[\x00-\x1f\x7f]', lambda m: f'\\u{ord(m.group()):04x}', escaped) + "'"


#: A type keyword ``gdbus`` prints before a value whose type the bare literal would not carry.
_TYPE_WORDS = frozenset(
    {'byte', 'int16', 'uint16', 'int32', 'uint32', 'int64', 'uint64', 'handle', 'double', 'boolean', 'string',
     'objectpath', 'signature'}
)  # fmt: skip
_TOKEN = re.compile(
    r"\s*(?:(?P<num>[-+]?(?:0x[0-9a-fA-F]+|\d+\.?\d*(?:[eE][-+]?\d+)?|inf|nan))|(?P<word>[A-Za-z_]\w*)|(?P<at>@\S+)|(?P<q>['\"])|(?P<p>[()\[\]{}<>,:]))"
)
_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', 'v': '\v', 'a': '\a'}


def parse_gvariant(text: str) -> Any:  # noqa: ANN401 -- the value of the text
    """The Python value of ``gdbus`` output: tuples, lists, dicts, strings, numbers, booleans; variants unwrapped."""
    value, at = _value(text, 0)
    if text[at:].strip():
        msg = f'GVariant text has trailing {text[at:].strip()!r}'
        raise ValueError(msg)
    return value


def _value(text: str, at: int) -> tuple[Any, int]:  # noqa: C901, PLR0911, PLR0912 -- one branch per GVariant form
    found = _TOKEN.match(text, at)
    if found is None:
        msg = f'GVariant text unreadable at {text[at : at + 20]!r}'
        raise ValueError(msg)
    at = found.end()
    if found['at']:  # a type annotation (``@a(so)``): the value follows
        return _value(text, at)
    if found['num']:
        number = found['num']
        if number.lower().startswith(('0x', '-0x', '+0x')):
            return int(number, 16), at
        return (float(number) if any(c in number for c in '.eEn') else int(number)), at
    if found['word']:
        word = found['word']
        if word in _TYPE_WORDS:
            return _value(text, at)
        if word in {'true', 'false'}:
            return word == 'true', at
        if word == 'nothing':
            return None, at
        msg = f'GVariant text has an unknown word {word!r}'
        raise ValueError(msg)
    if found['q']:
        return _string(text, at, found['q'])
    opener = found['p']
    if opener == '<':
        inner, at = _value(text, at)
        return inner, _expect(text, at, '>')
    if opener in '([{':
        closer = {'(': ')', '[': ']', '{': '}'}[opener]
        items: list[Any] = []
        pairs: dict[Any, Any] = {}
        while True:
            end = re.compile(r'\s*' + re.escape(closer)).match(text, at)
            if end:
                at = end.end()
                break
            item, at = _value(text, at)
            if opener == '{':
                at = _expect(text, at, ':')
                pairs[item], at = _value(text, at)
            else:
                items.append(item)
            comma = re.compile(r'\s*,').match(text, at)
            at = comma.end() if comma else at
        if opener == '(':
            return tuple(items), at
        return (pairs if opener == '{' else items), at
    msg = f'GVariant text has a stray {opener!r}'
    raise ValueError(msg)


def _string(text: str, at: int, quote: str) -> tuple[str, int]:
    out: list[str] = []
    while at < len(text):
        char = text[at]
        if char == quote:
            return ''.join(out), at + 1
        if char == '\\':
            code = text[at + 1]
            if code == 'u':
                out.append(chr(int(text[at + 2 : at + 6], 16)))
                at += 6
                continue
            if code == 'U':
                out.append(chr(int(text[at + 2 : at + 10], 16)))
                at += 10
                continue
            out.append(_ESCAPES.get(code, code))
            at += 2
            continue
        out.append(char)
        at += 1
    msg = 'GVariant text has an unterminated string'
    raise ValueError(msg)


def _expect(text: str, at: int, token: str) -> int:
    found = re.compile(r'\s*' + re.escape(token)).match(text, at)
    if found is None:
        msg = f'GVariant text expected {token!r} at {text[at : at + 20]!r}'
        raise ValueError(msg)
    return found.end()
