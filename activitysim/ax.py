"""Read Chrome's accessibility tree so clicks land on search results.

Web content is only exposed after AXEnhancedUserInterface is set, and only
when this process has Accessibility permission. Callers fall back to the
fixed result column in search.py when the tree is empty.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_bool, c_char_p, c_int32, c_long, c_void_p
from dataclasses import dataclass

from activitysim.osa import OsaError
from activitysim.search import Rect, accept_result, in_search_zone

APPLICATION_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
UTF8 = 0x08000100
AX_POINT = 1
AX_SIZE = 2
TEXT_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"})


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


@dataclass(frozen=True)
class Element:
    role: str
    title: str
    url: str
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)


def _libraries():
    app = ctypes.cdll.LoadLibrary(APPLICATION_SERVICES)
    cf = ctypes.cdll.LoadLibrary(CORE_FOUNDATION)
    return app, cf


def _bind(app, cf) -> None:
    app.AXUIElementCreateApplication.restype = c_void_p
    app.AXUIElementCreateApplication.argtypes = [c_int32]
    app.AXUIElementCreateSystemWide.restype = c_void_p
    app.AXUIElementCreateSystemWide.argtypes = []
    app.AXUIElementCopyAttributeValue.restype = c_int32
    app.AXUIElementCopyAttributeValue.argtypes = [c_void_p, c_void_p, POINTER(c_void_p)]
    app.AXUIElementSetAttributeValue.restype = c_int32
    app.AXUIElementSetAttributeValue.argtypes = [c_void_p, c_void_p, c_void_p]
    app.AXValueGetValue.restype = c_bool
    app.AXValueGetValue.argtypes = [c_void_p, c_int32, c_void_p]
    cf.CFStringCreateWithCString.restype = c_void_p
    cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, ctypes.c_uint32]
    cf.CFStringGetCString.restype = c_bool
    cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_long, ctypes.c_uint32]
    cf.CFStringGetTypeID.restype = ctypes.c_ulong
    cf.CFURLGetTypeID.restype = ctypes.c_ulong
    cf.CFArrayGetTypeID.restype = ctypes.c_ulong
    cf.CFGetTypeID.restype = ctypes.c_ulong
    cf.CFGetTypeID.argtypes = [c_void_p]
    cf.CFArrayGetCount.restype = c_long
    cf.CFArrayGetCount.argtypes = [c_void_p]
    cf.CFArrayGetValueAtIndex.restype = c_void_p
    cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_long]
    cf.CFURLGetString.restype = c_void_p
    cf.CFURLGetString.argtypes = [c_void_p]
    cf.CFRetain.argtypes = [c_void_p]
    cf.CFRetain.restype = c_void_p
    cf.CFRelease.argtypes = [c_void_p]
    true = _symbol_value("kCFBooleanTrue")
    cf.kCFBooleanTrue = true


_CACHE: dict[str, c_void_p] = {}
_BOUND = False
_APP = None
_CF = None


def _symbol_address(name: str) -> int:
    libc = ctypes.CDLL(None)
    libc.dlsym.argtypes = [c_void_p, c_char_p]
    libc.dlsym.restype = c_void_p
    found = libc.dlsym(c_void_p(-2), name.encode())
    if not found:
        raise OSError(f"missing symbol {name}")
    return found


def _symbol_value(name: str) -> c_void_p:
    return ctypes.cast(_symbol_address(name), POINTER(c_void_p)).contents


def _ready():
    global _BOUND, _APP, _CF
    if not _BOUND:
        _APP, _CF = _libraries()
        _bind(_APP, _CF)
        _BOUND = True
    return _APP, _CF


def _cfstring(text: str) -> c_void_p:
    cached = _CACHE.get(text)
    if cached is not None:
        return cached
    _app, cf = _ready()
    created = cf.CFStringCreateWithCString(None, text.encode(), UTF8)
    if not created:
        raise OSError(f"could not create CFString {text}")
    _CACHE[text] = c_void_p(created)
    return _CACHE[text]


def _release(value) -> None:
    if not value:
        return
    _app, cf = _ready()
    cf.CFRelease(value)


def _copy(element, name: str):
    app, _cf = _ready()
    out = c_void_p()
    err = app.AXUIElementCopyAttributeValue(element, _cfstring(name), byref(out))
    if err != 0 or not out.value:
        return None
    return out.value


def _string(value) -> str:
    if not value:
        return ""
    _app, cf = _ready()
    type_id = cf.CFGetTypeID(value)
    if type_id == cf.CFURLGetTypeID():
        value = cf.CFURLGetString(value)
        if not value:
            return ""
        type_id = cf.CFGetTypeID(value)
    if type_id != cf.CFStringGetTypeID():
        return ""
    buffer = ctypes.create_string_buffer(1024)
    ok = cf.CFStringGetCString(value, buffer, len(buffer), UTF8)
    if not ok:
        return ""
    return buffer.value.decode("utf-8", "replace")


def _ax_value(value, kind: int, holder) -> bool:
    app, _cf = _ready()
    return bool(app.AXValueGetValue(value, kind, byref(holder)))


def _origin(element) -> tuple[float, float] | None:
    raw = _copy(element, "AXPosition")
    if not raw:
        return None
    try:
        point = CGPoint()
        if not _ax_value(raw, AX_POINT, point):
            return None
        return (float(point.x), float(point.y))
    finally:
        _release(raw)


def _size(element) -> tuple[float, float] | None:
    raw = _copy(element, "AXSize")
    if not raw:
        return None
    try:
        size = CGSize()
        if not _ax_value(raw, AX_SIZE, size):
            return None
        return (float(size.width), float(size.height))
    finally:
        _release(raw)


def _attr_string(element, name: str) -> str:
    raw = _copy(element, name)
    if not raw:
        return ""
    try:
        return _string(raw)
    finally:
        _release(raw)


def _children(element) -> list:
    raw = _copy(element, "AXChildren")
    if not raw:
        return []
    _app, cf = _ready()
    try:
        if cf.CFGetTypeID(raw) != cf.CFArrayGetTypeID():
            return []
        count = min(int(cf.CFArrayGetCount(raw)), 40)
        kids = []
        for index in range(count):
            child = cf.CFArrayGetValueAtIndex(raw, index)
            if child:
                cf.CFRetain(child)
                kids.append(child)
        return kids
    finally:
        _release(raw)


def _describe(element) -> Element | None:
    origin = _origin(element)
    size = _size(element)
    if origin is None or size is None or size[0] < 2 or size[1] < 2:
        return None
    title = _attr_string(element, "AXTitle") or _attr_string(element, "AXDescription")
    url = _attr_string(element, "AXURL") or _attr_string(element, "AXValue")
    return Element(
        role=_attr_string(element, "AXRole"),
        title=title,
        url=url,
        x=origin[0],
        y=origin[1],
        width=size[0],
        height=size[1],
    )


_PINNED_PID: int | None = None


def set_chrome_pid(pid: int | None) -> None:
    """Pin clicks to the signed-out Chrome. None reads no Chrome at all."""

    global _PINNED_PID
    if pid is None:
        _PINNED_PID = None
        return
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("bad pid")
    _PINNED_PID = pid


def chrome_pid() -> int | None:
    return _PINNED_PID


def _enable_web_content(application) -> None:
    app, cf = _ready()
    app.AXUIElementSetAttributeValue(
        application, _cfstring("AXEnhancedUserInterface"), cf.kCFBooleanTrue
    )


def _focused_window(application):
    window = _copy(application, "AXFocusedWindow")
    if window:
        return window
    windows = _children_via_windows(application)
    if not windows:
        return None
    chosen = windows[0]
    for extra in windows[1:]:
        _release(extra)
    return chosen


def _children_via_windows(application) -> list:
    raw = _copy(application, "AXWindows")
    if not raw:
        return []
    _app, cf = _ready()
    try:
        if cf.CFGetTypeID(raw) != cf.CFArrayGetTypeID():
            return []
        count = min(int(cf.CFArrayGetCount(raw)), 8)
        windows = []
        for index in range(count):
            window = cf.CFArrayGetValueAtIndex(raw, index)
            if window:
                cf.CFRetain(window)
                windows.append(window)
        return windows
    finally:
        _release(raw)


def _walk(root) -> list[Element]:
    found: list[Element] = []
    interesting = TEXT_ROLES.union({"AXLink"})
    queue = [root]
    seen = 0
    while queue and seen < 700 and len(found) < 60:
        element = queue.pop(0)
        seen += 1
        role = _attr_string(element, "AXRole")
        if role in interesting:
            described = _describe(element)
            if described is not None:
                found.append(described)
        if seen < 700:
            for child in _children(element):
                queue.append(child)
        if element != root:
            _release(element)
    for leftover in queue:
        _release(leftover)
    return found


def _page_elements() -> list[Element]:
    pid = chrome_pid()
    if not pid:
        return []
    app, _cf = _ready()
    application = app.AXUIElementCreateApplication(pid)
    if not application:
        return []
    try:
        _enable_web_content(application)
        window = _focused_window(application)
        if not window:
            return []
        try:
            return _walk(window)
        finally:
            _release(window)
    finally:
        _release(application)


def focused_role() -> str:
    """Role of the focused control, or an empty string if it cannot be read."""

    try:
        app, _cf = _ready()
        system = app.AXUIElementCreateSystemWide()
        if not system:
            return ""
        try:
            focused = _copy(system, "AXFocusedUIElement")
            if not focused:
                return ""
            try:
                return _attr_string(focused, "AXRole")
            finally:
                _release(focused)
        finally:
            _release(system)
    except (OSError, ctypes.ArgumentError):
        return ""


def search_field(bounds: Rect) -> tuple[float, float] | None:
    try:
        elements = _page_elements()
    except (OSError, ctypes.ArgumentError, OsaError):
        return None
    candidates = []
    for element in elements:
        if element.role not in TEXT_ROLES:
            continue
        center = element.center
        if not in_search_zone(center, bounds):
            continue
        label = f"{element.title} {element.url}".lower()
        score = element.width
        if "search" in label or "advanced" in label:
            score += 1000
        candidates.append((score, center))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def result_clicks(bounds: Rect) -> list[tuple[float, float]]:
    try:
        elements = _page_elements()
    except (OSError, ctypes.ArgumentError, OsaError):
        return []
    points = []
    seen: set[str] = set()
    for element in elements:
        if element.role != "AXLink":
            continue
        center = element.center
        if not accept_result(element.url, element.title, center, bounds):
            continue
        if element.url in seen:
            continue
        seen.add(element.url)
        points.append(center)
    points.sort(key=lambda point: (point[1], point[0]))
    return points[:8]
