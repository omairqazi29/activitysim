"""Post mouse and keyboard events with SendInput.

Nothing in this module calls Win32 at import time, so it can be imported on
macOS. The runner still asks for command chords; the mapping to Ctrl or Alt
lives in chord().
"""

from __future__ import annotations

import ctypes
import random
import time

from activitysim.geom import Point, bezier_path
from activitysim.input import (
    CHAR_KEYS,
    COMMAND_ONLY,
    FLAG,
    REFUSED_KEYS,
    SAFE_HOTKEYS,
    assert_typable,
    can_type,
)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

# Arrows and page keys. Without this flag NumLock turns them into digits.
_EXTENDED_VKS = frozenset({0x21, 0x22, 0x25, 0x26, 0x27, 0x28})

# Always set VIRTUALDESK so a negative multi-monitor origin is addressable.
ABSOLUTE_FLAGS = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
SCROLL_FLAGS = MOUSEEVENTF_WHEEL

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SPACE = 0x20
VK_OEM_1 = 0xBA
VK_OEM_PLUS = 0xBB
VK_OEM_MINUS = 0xBD
VK_OEM_PERIOD = 0xBE
VK_OEM_2 = 0xBF
VK_OEM_3 = 0xC0

_MOD_VK = {
    "shift": VK_SHIFT,
    "control": VK_CONTROL,
    "option": VK_MENU,
}

# US virtual-key codes. Letters are 0x41-0x5A. The shift bit matches CHAR_KEYS.
_WIN_CHARS: dict[str, tuple[int, bool]] = {}
for _char in "abcdefghijklmnopqrstuvwxyz":
    _WIN_CHARS[_char] = (ord(_char.upper()), False)
    _WIN_CHARS[_char.upper()] = (ord(_char.upper()), True)
for _digit in "0123456789":
    _WIN_CHARS[_digit] = (ord(_digit), False)
_WIN_CHARS.update({
    "/": (VK_OEM_2, False),
    "?": (VK_OEM_2, True),
    ":": (VK_OEM_1, True),
    ".": (VK_OEM_PERIOD, False),
    ">": (VK_OEM_PERIOD, True),
    "-": (VK_OEM_MINUS, False),
    "_": (VK_OEM_MINUS, True),
    "=": (VK_OEM_PLUS, False),
    "+": (VK_OEM_PLUS, True),
    "~": (VK_OEM_3, True),
    " ": (VK_SPACE, False),
    "%": (ord("5"), True),
    "&": (ord("7"), True),
    "#": (ord("3"), True),
})

NAMED_VK = {
    "escape": 0x1B,
    "return": 0x0D,
    "tab": 0x09,
    "left": 0x25,
    "right": 0x27,
    "down": 0x28,
    "up": 0x26,
    "pagedown": 0x22,
    "pageup": 0x21,
    "l": 0x4C,
    "f": 0x46,
    "r": 0x52,
    "a": 0x41,
    "1": 0x31,
    "2": 0x32,
    "3": 0x33,
    "4": 0x34,
    "5": 0x35,
    "6": 0x36,
    "7": 0x37,
    "8": 0x38,
}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", ctypes.c_uint32),
        ("wParamL", ctypes.c_uint16),
        ("wParamH", ctypes.c_uint16),
    )


class _INPUTUNION(ctypes.Union):
    _fields_ = (
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    )


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (
        ("type", ctypes.c_uint32),
        ("u", _INPUTUNION),
    )


def absolute_coord(pos: float, origin: float, size: float) -> int:
    """Map one physical pixel into the 0..65535 absolute range.

    size is the virtual-screen extent. The far edge is size-1, not size, so a
    pointer on the last pixel reaches 65535. origin may be negative.
    """

    span = max(float(size) - 1.0, 1.0)
    raw = round((float(pos) - float(origin)) * 65535.0 / span)
    if raw < 0:
        return 0
    if raw > 65535:
        return 65535
    return int(raw)


def absolute_point(
    x: float,
    y: float,
    origin_x: float,
    origin_y: float,
    width: float,
    height: float,
) -> tuple[int, int]:
    return (
        absolute_coord(x, origin_x, width),
        absolute_coord(y, origin_y, height),
    )


def wheel_delta(page_delta: int) -> int:
    """Positive page_delta scrolls down. Windows' positive wheel scrolls up."""

    return -int(page_delta) * 120


def virtual_key(char: str) -> tuple[int, bool]:
    """US virtual key and shift flag. The character must be in CHAR_KEYS."""

    try:
        return _WIN_CHARS[char]
    except KeyError as exc:
        raise ValueError(f"refusing to type {char!r}") from exc


def windows_keys_cover_allowlist() -> bool:
    return set(_WIN_CHARS) == set(CHAR_KEYS)


def chord(key: str, modifiers: tuple[str, ...] | list[str] = ()) -> list[tuple[int, bool]]:
    """Map a Mac-style chord onto Windows virtual keys.

    Each entry is (vk, is_modifier). command+left/right is Alt, because
    Ctrl+Left is word-left. Other command chords are Ctrl. Refusals match
    MacInput and happen before any SendInput call.
    """

    if key in REFUSED_KEYS or key not in SAFE_HOTKEYS:
        raise ValueError(f"refusing key {key!r}")
    mods = tuple(modifiers)
    if key in COMMAND_ONLY and "command" not in mods:
        raise ValueError(f"{key} is only sent with command")
    for name in mods:
        if name not in FLAG:
            raise ValueError(f"refusing modifier {name!r}")
    sequence: list[tuple[int, bool]] = []
    for name in mods:
        if name == "command":
            vk = VK_MENU if key in {"left", "right"} else VK_CONTROL
        else:
            vk = _MOD_VK[name]
        sequence.append((vk, True))
    sequence.append((NAMED_VK[key], False))
    return sequence


_USER32 = None


def _load_user32():
    global _USER32
    if _USER32 is not None:
        return _USER32
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        raise OSError("user32 is not available")
    user32 = windll.user32
    user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = ctypes.c_uint
    user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
    user32.GetCursorPos.restype = ctypes.c_int
    user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    user32.SetCursorPos.restype = ctypes.c_int
    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    user32.GetSystemMetrics.restype = ctypes.c_int
    _USER32 = user32
    return user32


class POINT(ctypes.Structure):
    _fields_ = (("x", ctypes.c_int32), ("y", ctypes.c_int32))


def _ensure_dpi() -> None:
    from activitysim.winchrome import ensure_process_dpi

    ensure_process_dpi()


class WinInput:
    def __init__(self, rng: random.Random | None = None, sleeper=time.sleep) -> None:
        self.rng = rng or random.Random()
        self.sleep = sleeper
        self._available: bool | None = None

    def request_access(self, wait_seconds: float = 90) -> bool:
        """Report a failed nudge. Windows has no Accessibility wait."""

        del wait_seconds
        self._available = None
        if self.available():
            return True
        print("Windows did not move the pointer.", flush=True)
        print(
            "activitysim must run as the normal interactive user, not as Administrator.",
            flush=True,
        )
        return False

    def available(self) -> bool:
        """True when a 2-pixel nudge actually moves the cursor, then put it back."""

        if self._available is not None:
            return self._available
        try:
            x, y = self.position()
            self._set_cursor(x + 2, y)
            self.sleep(0.08)
            nx, ny = self.position()
            moved = abs(nx - x) > 0.5 or abs(ny - y) > 0.5
            if moved:
                self._set_cursor(x, y)
            self._available = moved
        except (OSError, AttributeError, ctypes.ArgumentError):
            self._available = False
        return self._available

    def position(self) -> Point:
        _ensure_dpi()
        point = POINT()
        if not _load_user32().GetCursorPos(ctypes.byref(point)):
            raise OSError("GetCursorPos failed")
        return (float(point.x), float(point.y))

    def displays(self) -> list[tuple[float, float, float, float]]:
        return [self.screen()]

    def screen(self) -> tuple[float, float, float, float]:
        _ensure_dpi()
        user32 = _load_user32()
        origin_x = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        origin_y = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        return (
            float(origin_x),
            float(origin_y),
            float(origin_x + width),
            float(origin_y + height),
        )

    def move_to(self, x: float, y: float) -> None:
        start = self.position()
        points = bezier_path(start, (x, y), self.rng)
        for point in points:
            self._mouse_at(point[0], point[1], ABSOLUTE_FLAGS)
            self.sleep(0.012)

    def click(self, x: float, y: float) -> None:
        self._mouse_at(x, y, ABSOLUTE_FLAGS | MOUSEEVENTF_LEFTDOWN)
        self.sleep(self.rng.uniform(0.03, 0.08))
        self._mouse_at(x, y, ABSOLUTE_FLAGS | MOUSEEVENTF_LEFTUP)

    def scroll(self, page_delta: int) -> None:
        """Positive page_delta scrolls down. The cursor is not moved."""

        amount = wheel_delta(page_delta)
        if amount == 0:
            return
        self._mouse(0, 0, amount, SCROLL_FLAGS)

    def hotkey(self, key: str, modifiers: tuple[str, ...] | list[str] = ()) -> None:
        sequence = chord(key, modifiers)
        self._post_chord(sequence)

    def type_text(self, text: str) -> None:
        assert_typable(text)
        for char in text:
            vk, shift = virtual_key(char)
            if shift:
                self._key(VK_SHIFT, True)
                self.sleep(0.01)
            self._key(vk, True)
            self.sleep(0.01)
            self._key(vk, False)
            if shift:
                self._key(VK_SHIFT, False)
            self.sleep(self.rng.uniform(0.02, 0.08))

    def omnibox(self, url: str) -> bool:
        """Focus the address bar, type a URL, and press Return.

        Returns False when the URL cannot be typed, before any key is posted.
        """

        if not can_type(url):
            return False
        self.hotkey("l", ("command",))
        self.sleep(0.18)
        self.type_text(url)
        self.sleep(0.08)
        self.hotkey("return", ())
        return True

    def _virtual_metrics(self) -> tuple[int, int, int, int]:
        _ensure_dpi()
        user32 = _load_user32()
        return (
            int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
        )

    def _mouse_at(self, x: float, y: float, flags: int) -> None:
        origin_x, origin_y, width, height = self._virtual_metrics()
        ax, ay = absolute_point(x, y, origin_x, origin_y, width, height)
        self._mouse(ax, ay, 0, flags)

    def _mouse(self, dx: int, dy: int, data: int, flags: int) -> None:
        event = INPUT()
        event.type = INPUT_MOUSE
        event.mi.dx = int(dx)
        event.mi.dy = int(dy)
        event.mi.mouseData = int(data) & 0xFFFFFFFF
        event.mi.dwFlags = int(flags)
        event.mi.time = 0
        event.mi.dwExtraInfo = 0
        self._send((event,))

    def _post_chord(self, sequence: list[tuple[int, bool]]) -> None:
        for index, (vk, _is_modifier) in enumerate(sequence):
            self._key(vk, True)
            if index != len(sequence) - 1:
                self.sleep(0.012)
        self.sleep(0.02)
        for vk, _is_modifier in reversed(sequence):
            self._key(vk, False)
            self.sleep(0.012)

    def _key(self, vk: int, down: bool) -> None:
        event = INPUT()
        event.type = INPUT_KEYBOARD
        event.ki.wVk = int(vk)
        event.ki.wScan = 0
        flags = 0 if down else KEYEVENTF_KEYUP
        if int(vk) in _EXTENDED_VKS:
            flags |= KEYEVENTF_EXTENDEDKEY
        event.ki.dwFlags = flags
        event.ki.time = 0
        event.ki.dwExtraInfo = 0
        self._send((event,))

    def _send(self, events: tuple[INPUT, ...]) -> None:
        count = len(events)
        if count == 0:
            return
        array = (INPUT * count)()
        for index, event in enumerate(events):
            array[index] = event
        sent = int(_load_user32().SendInput(count, array, ctypes.sizeof(INPUT)))
        if sent != count:
            raise OSError(f"SendInput posted {sent} of {count}")

    def _set_cursor(self, x: float, y: float) -> None:
        if not _load_user32().SetCursorPos(int(round(x)), int(round(y))):
            raise OSError("SetCursorPos failed")
