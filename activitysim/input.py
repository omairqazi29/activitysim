"""Post mouse and keyboard events with CoreGraphics.

Events are posted as HID input so an idle check sees clicks and keystrokes.
type_text() is used for a GitHub advanced-search query after the search field
is focused, and for a github.com URL after Command+L. Cmd+Q, Cmd+W, and a bare
Space bar are refused. Space inside a query is typed only as text.
"""

from __future__ import annotations

import ctypes
import random
import subprocess
import sys
import time
from ctypes import (
    POINTER,
    c_bool,
    c_double,
    c_int32,
    c_int64,
    c_uint16,
    c_uint32,
    c_uint64,
    c_void_p,
)
from ctypes import Structure

from activitysim.geom import Point, bezier_path

CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
APPLICATION_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"

# Event constants from CGEventTypes.h / IOLLEvent.h.
MOUSE_MOVED = 5
LEFT_DOWN = 1
LEFT_UP = 2
HID_TAP = 0
SCROLL_LINE = 1
MOUSE_DELTA_X = 4
MOUSE_DELTA_Y = 5
# HID system state, so posted clicks and keys are the same class of event
# an activity check attributes to a person at the keyboard.
SOURCE_HID = 1

FLAG = {
    "shift": 0x00020000,
    "control": 0x00040000,
    "option": 0x00080000,
    "command": 0x00100000,
}
MOD_KEYCODE = {
    "shift": 56,
    "control": 59,
    "option": 58,
    "command": 55,
}

# US ANSI virtual key codes. Value is (keycode, needs_shift).
_LETTERS = {
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4,
    "i": 34, "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35,
    "q": 12, "r": 15, "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7,
    "y": 16, "z": 6,
}
_DIGITS = {
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21,
    "5": 23, "6": 22, "7": 26, "8": 28, "9": 25,
}

CHAR_KEYS: dict[str, tuple[int, bool]] = {}
for _char, _code in _LETTERS.items():
    CHAR_KEYS[_char] = (_code, False)
    CHAR_KEYS[_char.upper()] = (_code, True)
for _char, _code in _DIGITS.items():
    CHAR_KEYS[_char] = (_code, False)
CHAR_KEYS.update({
    "/": (44, False),
    ":": (41, True),
    ".": (47, False),
    "-": (27, False),
    "_": (27, True),
    "?": (44, True),
    ">": (47, True),
    " ": (49, False),
    "=": (24, False),
    "+": (24, True),
    "%": (23, True),
    "&": (26, True),
    "#": (20, True),
    "~": (50, True),
})

NAMED_KEYS = {
    "escape": 53,
    "return": 36,
    "tab": 48,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    "pagedown": 121,
    "pageup": 116,
    "l": 37,
    "f": 3,
    "r": 15,
    "a": 0,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "5": 23,
    "6": 22,
    "7": 26,
    "8": 28,
}

# These keys insert text unless Command is held. Command+A selects the search
# field. Command+1 through Command+8 are tab switches.
COMMAND_ONLY = frozenset({"a", *(str(number) for number in range(1, 9))})

# Nothing that quits, closes a tab, or activates a focused button.
REFUSED_KEYS = {"q", "w", "space"}
SAFE_HOTKEYS = frozenset(NAMED_KEYS)


class CGPoint(Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class CGRect(Structure):
    _fields_ = [("x", c_double), ("y", c_double), ("width", c_double), ("height", c_double)]


def can_type(text: str) -> bool:
    return 0 < len(text) <= 240 and all(ch in CHAR_KEYS for ch in text)


def assert_typable(text: str) -> None:
    if not can_type(text):
        raise ValueError(f"refusing to type {text!r}")


class MacInput:
    def __init__(self, rng: random.Random | None = None, sleeper=time.sleep) -> None:
        self.rng = rng or random.Random()
        self.sleep = sleeper
        self._cg = ctypes.cdll.LoadLibrary(CORE_GRAPHICS)
        self._bind()
        self._source = self._cg.CGEventSourceCreate(SOURCE_HID)
        self._available: bool | None = None

    def request_access(self, wait_seconds: float = 90) -> bool:
        return _request_access(self, wait_seconds)

    def available(self) -> bool:
        """True when a 2-pixel nudge actually moves the cursor, then put it back."""

        if self._available is not None:
            return self._available
        try:
            x, y = self.position()
            self._post_move(x + 2, y, dx=2, dy=0)
            self.sleep(0.08)
            nx, ny = self.position()
            moved = abs(nx - x) > 0.5 or abs(ny - y) > 0.5
            if moved:
                self._post_move(x, y, dx=x - nx, dy=y - ny)
            self._available = moved
        except (OSError, ctypes.ArgumentError):
            self._available = False
        return self._available

    def position(self) -> Point:
        event = self._cg.CGEventCreate(None)
        if not event:
            raise OSError("CGEventCreate failed")
        point = self._cg.CGEventGetLocation(event)
        self._cg.CFRelease(event)
        return (float(point.x), float(point.y))

    def displays(self) -> list[tuple[float, float, float, float]]:
        max_displays = 16
        array = (c_uint32 * max_displays)()
        count = c_uint32(0)
        err = self._cg.CGGetActiveDisplayList(max_displays, array, ctypes.byref(count))
        if err != 0 or count.value == 0:
            return [self._main_display()]
        rects = []
        for index in range(min(count.value, max_displays)):
            rect = self._cg.CGDisplayBounds(array[index])
            rects.append((rect.x, rect.y, rect.x + rect.width, rect.y + rect.height))
        return rects or [self._main_display()]

    def screen(self) -> tuple[float, float, float, float]:
        return self._main_display()

    def move_to(self, x: float, y: float) -> None:
        start = self.position()
        points = bezier_path(start, (x, y), self.rng)
        previous = start
        for point in points:
            dx = point[0] - previous[0]
            dy = point[1] - previous[1]
            self._post_move(point[0], point[1], dx=dx, dy=dy)
            previous = point
            self.sleep(0.012)

    def click(self, x: float, y: float) -> None:
        self._post_button(LEFT_DOWN, x, y)
        self.sleep(self.rng.uniform(0.03, 0.08))
        self._post_button(LEFT_UP, x, y)

    def scroll(self, page_delta: int) -> None:
        """Positive page_delta scrolls down the page under the pointer.

        A positive CoreGraphics wheel value scrolls toward the top, so the
        sign is flipped here.
        """

        lines = int(page_delta)
        if lines == 0:
            return
        event = self._cg.CGEventCreateScrollWheelEvent2(
            self._source, SCROLL_LINE, 1, -lines * 2, 0, 0
        )
        if not event:
            return
        self._cg.CGEventPost(HID_TAP, event)
        self._cg.CFRelease(event)

    def hotkey(self, key: str, modifiers: tuple[str, ...] | list[str] = ()) -> None:
        if key in REFUSED_KEYS or key not in SAFE_HOTKEYS:
            raise ValueError(f"refusing key {key!r}")
        mods = tuple(modifiers)
        if key in COMMAND_ONLY and "command" not in mods:
            raise ValueError(f"{key} is only sent with command")
        for name in mods:
            if name not in FLAG:
                raise ValueError(f"refusing modifier {name!r}")
        flags = 0
        for name in mods:
            flags |= FLAG[name]
        for name in mods:
            self._post_key(MOD_KEYCODE[name], True, flags)
            self.sleep(0.012)
        self._post_key(NAMED_KEYS[key], True, flags)
        self.sleep(0.02)
        self._post_key(NAMED_KEYS[key], False, flags)
        for name in reversed(mods):
            self.sleep(0.012)
            self._post_key(MOD_KEYCODE[name], False, 0)

    def type_text(self, text: str) -> None:
        assert_typable(text)
        for char in text:
            keycode, shift = CHAR_KEYS[char]
            if shift:
                self._post_key(MOD_KEYCODE["shift"], True, FLAG["shift"])
                self.sleep(0.01)
            flags = FLAG["shift"] if shift else 0
            self._post_key(keycode, True, flags)
            self.sleep(0.01)
            self._post_key(keycode, False, flags)
            if shift:
                self._post_key(MOD_KEYCODE["shift"], False, 0)
            self.sleep(self.rng.uniform(0.02, 0.08))

    def omnibox(self, url: str) -> bool:
        """Focus the address bar, type a URL, and press Return.

        Returns False when the URL cannot be typed. The caller then navigates
        with AppleScript instead.
        """

        if not can_type(url):
            return False
        self.hotkey("l", ("command",))
        self.sleep(0.18)
        self.type_text(url)
        self.sleep(0.08)
        self.hotkey("return", ())
        return True

    def _main_display(self) -> tuple[float, float, float, float]:
        display = self._cg.CGMainDisplayID()
        rect = self._cg.CGDisplayBounds(display)
        return (rect.x, rect.y, rect.x + rect.width, rect.y + rect.height)

    def _post_move(self, x: float, y: float, dx: float, dy: float) -> None:
        event = self._cg.CGEventCreateMouseEvent(self._source, MOUSE_MOVED, CGPoint(x, y), 0)
        if not event:
            raise OSError("CGEventCreateMouseEvent failed")
        self._cg.CGEventSetIntegerValueField(event, MOUSE_DELTA_X, int(dx))
        self._cg.CGEventSetIntegerValueField(event, MOUSE_DELTA_Y, int(dy))
        self._cg.CGEventPost(HID_TAP, event)
        self._cg.CFRelease(event)

    def _post_button(self, kind: int, x: float, y: float) -> None:
        event = self._cg.CGEventCreateMouseEvent(self._source, kind, CGPoint(x, y), 0)
        if not event:
            raise OSError("CGEventCreateMouseEvent failed")
        self._cg.CGEventPost(HID_TAP, event)
        self._cg.CFRelease(event)

    def _post_key(self, keycode: int, down: bool, flags: int) -> None:
        event = self._cg.CGEventCreateKeyboardEvent(self._source, keycode, down)
        if not event:
            raise OSError("CGEventCreateKeyboardEvent failed")
        self._cg.CGEventSetFlags(event, flags)
        self._cg.CGEventPost(HID_TAP, event)
        self._cg.CFRelease(event)

    def _bind(self) -> None:
        cg = self._cg
        cg.CGEventCreate.restype = c_void_p
        cg.CGEventCreate.argtypes = [c_void_p]
        cg.CGEventGetLocation.restype = CGPoint
        cg.CGEventGetLocation.argtypes = [c_void_p]
        cg.CGEventCreateMouseEvent.restype = c_void_p
        cg.CGEventCreateMouseEvent.argtypes = [c_void_p, c_uint32, CGPoint, c_uint32]
        cg.CGEventCreateKeyboardEvent.restype = c_void_p
        cg.CGEventCreateKeyboardEvent.argtypes = [c_void_p, c_uint16, c_bool]
        cg.CGEventCreateScrollWheelEvent2.restype = c_void_p
        cg.CGEventCreateScrollWheelEvent2.argtypes = [
            c_void_p, c_uint32, c_uint32, c_int32, c_int32, c_int32
        ]
        cg.CGEventSetFlags.argtypes = [c_void_p, c_uint64]
        cg.CGEventSetIntegerValueField.argtypes = [c_void_p, c_uint32, c_int64]
        cg.CGEventPost.argtypes = [c_uint32, c_void_p]
        cg.CFRelease.argtypes = [c_void_p]
        cg.CGEventSourceCreate.restype = c_void_p
        cg.CGEventSourceCreate.argtypes = [c_int32]
        cg.CGMainDisplayID.restype = c_uint32
        cg.CGDisplayBounds.restype = CGRect
        cg.CGDisplayBounds.argtypes = [c_uint32]
        cg.CGGetActiveDisplayList.restype = c_int32
        cg.CGGetActiveDisplayList.argtypes = [c_uint32, POINTER(c_uint32), POINTER(c_uint32)]


SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
    "?Privacy_Accessibility"
)


def accessibility_trusted() -> bool:
    try:
        app = ctypes.cdll.LoadLibrary(APPLICATION_SERVICES)
    except OSError:
        return False
    app.AXIsProcessTrusted.restype = c_bool
    return bool(app.AXIsProcessTrusted())


def _symbol_address(name: str) -> int:
    ctypes.cdll.LoadLibrary(APPLICATION_SERVICES)
    ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    libc = ctypes.CDLL(None)
    libc.dlsym.argtypes = [c_void_p, ctypes.c_char_p]
    libc.dlsym.restype = c_void_p
    found = libc.dlsym(c_void_p(-2), name.encode())
    if not found:
        raise OSError(f"missing symbol {name}")
    return found


def _symbol_value(name: str) -> c_void_p:
    return ctypes.cast(_symbol_address(name), POINTER(c_void_p)).contents


def accessibility_prompt_dictionary():
    """Build the options dictionary for the system permission dialog.

    Creating it does not show the dialog. Passing it to
    AXIsProcessTrustedWithOptions does.
    """

    cf = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    cf.CFDictionaryCreate.restype = c_void_p
    cf.CFDictionaryCreate.argtypes = [
        c_void_p,
        POINTER(c_void_p),
        POINTER(c_void_p),
        ctypes.c_long,
        c_void_p,
        c_void_p,
    ]
    keys = (c_void_p * 1)(_symbol_value("kAXTrustedCheckOptionPrompt"))
    values = (c_void_p * 1)(_symbol_value("kCFBooleanTrue"))
    return cf.CFDictionaryCreate(
        None,
        keys,
        values,
        1,
        c_void_p(_symbol_address("kCFTypeDictionaryKeyCallBacks")),
        c_void_p(_symbol_address("kCFTypeDictionaryValueCallBacks")),
    )


def prompt_accessibility() -> None:
    """Ask macOS to show the Accessibility permission dialog for this app."""

    try:
        options = accessibility_prompt_dictionary()
        app = ctypes.cdll.LoadLibrary(APPLICATION_SERVICES)
        app.AXIsProcessTrustedWithOptions.restype = c_bool
        app.AXIsProcessTrustedWithOptions.argtypes = [c_void_p]
        app.AXIsProcessTrustedWithOptions(options)
    except (OSError, ctypes.ArgumentError):
        return


def open_accessibility_settings() -> None:
    subprocess.run(["open", SETTINGS_URL], check=False)


def _request_access(pointer: "MacInput", wait_seconds: float = 90) -> bool:
    print("macOS needs Accessibility permission for real mouse and keyboard events.", flush=True)
    print(f"python: {sys.executable}", flush=True)
    print("Turn on the switch for the app that launched this (Terminal, iTerm, or Cursor).", flush=True)
    prompt_accessibility()
    open_accessibility_settings()
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        pointer._available = None
        if pointer.available():
            print("Accessibility is on. Starting clicks.", flush=True)
            return True
        remaining = int(deadline - time.time())
        print(f"waiting for Accessibility, {remaining}s left", flush=True)
        time.sleep(1)
    return False
