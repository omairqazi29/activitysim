"""Windows windows, processes, and the stay-awake flag.

Win32 is loaded only when a function needs it. Parsing process text and
building the PowerShell argv are pure and safe to call on macOS.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

# SetThreadExecutionState flags. OR them; do not change the power plan.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
AWAKE_FLAGS = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED

SW_RESTORE = 9
WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Fixed script. Line breaks in a command line are flattened so one process is
# one row. The profile path is filtered in Python, never interpolated here.
# .NET \u escapes cover the separators str.splitlines() treats as breaks
# besides CR, LF, and tab: VT, FF, U+001C..U+001E, U+0085, U+2028, U+2029.
_PROCESS_SCRIPT = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "Get-CimInstance -ClassName Win32_Process -Filter \"Name = 'chrome.exe'\" "
    "| ForEach-Object { "
    "$cmd = $_.CommandLine; "
    "if ($null -eq $cmd) { $cmd = '' }; "
    "$cmd = $cmd -replace '[\\r\\n\\t\\v\\f\\u001c\\u001d\\u001e\\u0085\\u2028\\u2029]', ' '; "
    "Write-Output ($_.ProcessId.ToString() + [char]9 + $cmd) "
    "}"
)

_CACHE: tuple[float, str] | None = None
_CACHE_TTL = 0.5
_DPI_READY = False


def powershell_path(system_root: str | None = None) -> Path:
    """System PowerShell. PATH and the working directory are not searched."""

    if system_root is None:
        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    return Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def process_list_argv() -> list[str]:
    """Argv for the chrome.exe listing. It does not contain a profile path."""

    return [
        str(powershell_path()),
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        _PROCESS_SCRIPT,
    ]


def basename(path: str) -> str:
    """Last path component. Slashes and backslashes both count."""

    normalized = path.replace("\\", "/").rstrip("/")
    if not normalized:
        return ""
    return normalized.rsplit("/", 1)[-1]


def app_name_for_image(image: str | None) -> str | None:
    """Foreground process name. Any chrome.exe path is 'Google Chrome'."""

    if image is None or not str(image).strip():
        return None
    base = basename(str(image))
    if not base:
        return None
    if base.lower() == "chrome.exe":
        return "Google Chrome"
    return base


def executable_token(command: str) -> str:
    """The program token at the start of a Windows command line."""

    text = command.strip()
    if not text:
        return ""
    if text[0] == '"':
        end = text.find('"', 1)
        if end < 0:
            return text[1:]
        return text[1:end]
    part, _, _rest = text.partition(" ")
    return part


def decode_process_output(raw: bytes) -> str:
    """PowerShell writes UTF-16 when stdout is a pipe, and UTF-8 otherwise."""

    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if len(raw) >= 4 and raw[1] == 0 and raw[3] == 0:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


# str.splitlines() also breaks on these. Strip them before splitting so a
# chrome.exe command line cannot forge a second pid row.
_EMBEDDED_BREAKS = ("\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


def iter_process_rows(text: str):
    flat = text
    for char in _EMBEDDED_BREAKS:
        flat = flat.replace(char, " ")
    for piece in flat.split("\n"):
        for line in piece.split("\r"):
            raw = line.strip().lstrip("\ufeff")
            if not raw:
                continue
            pid_text, sep, command = raw.partition("\t")
            if not sep or not pid_text.strip().isdigit():
                continue
            yield int(pid_text.strip()), command.strip()


def command_is_locked_chrome(
    command: str,
    profile: Path,
    has_profile: Callable[[str, Path], bool],
) -> bool:
    """True for the browser process on this profile, not a --type= child.

    The executable's basename must be chrome.exe. The Windows install path
    contains Google\\Chrome, which is not the substring 'Google Chrome'.
    """

    if "--type=" in command:
        return False
    if basename(executable_token(command)).lower() != "chrome.exe":
        return False
    return bool(has_profile(command, profile))


def parse_locked_chrome_pids(
    text: str,
    profile: Path,
    has_profile: Callable[[str, Path], bool],
) -> list[int]:
    found: list[int] = []
    for pid, command in iter_process_rows(text):
        if command_is_locked_chrome(command, profile, has_profile):
            found.append(pid)
    return found


def command_line_for_pid(text: str, pid: int) -> str:
    for found, command in iter_process_rows(text):
        if found == pid:
            return command
    return ""


def clear_process_cache() -> None:
    global _CACHE
    _CACHE = None


def read_process_list(*, fresh: bool = False) -> str:
    """Run the fixed PowerShell argv. Failures are not cached.

    The cache timestamp is taken after a zero exit, so a slow listing stays
    fresh for the TTL. fresh=True ignores the cache. A missing system
    powershell.exe is refused instead of searching PATH.
    """

    global _CACHE
    if not fresh and _CACHE is not None and time.time() - _CACHE[0] < _CACHE_TTL:
        return _CACHE[1]
    exe = powershell_path()
    if not exe.is_file():
        return ""
    kwargs: dict = {"capture_output": True, "check": False, "timeout": 5}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if flags:
        kwargs["creationflags"] = flags
    try:
        result = subprocess.run(process_list_argv(), **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    text = decode_process_output(result.stdout or b"")
    _CACHE = (time.time(), text)
    return text


def ensure_process_dpi() -> bool:
    """Per-monitor V2, then the older process-wide call. Retry until one works."""

    global _DPI_READY
    if _DPI_READY:
        return True
    try:
        user32 = _dll("user32")
    except OSError:
        return False
    try:
        aware = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if aware is not None:
            aware.argtypes = (ctypes.c_void_p,)
            aware.restype = ctypes.c_int
            if aware(ctypes.c_void_p(-4)):
                _DPI_READY = True
                return True
    except (OSError, AttributeError, ctypes.ArgumentError):
        pass
    try:
        older = getattr(user32, "SetProcessDPIAware", None)
        if older is not None:
            older.argtypes = ()
            older.restype = ctypes.c_int
            if older():
                _DPI_READY = True
                return True
    except (OSError, AttributeError, ctypes.ArgumentError):
        pass
    return False


def _dll(name: str):
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        raise OSError(f"{name} is not available")
    return getattr(windll, name)


_USER32 = None
_KERNEL32 = None
_SHELL32 = None


def _user32():
    global _USER32
    if _USER32 is not None:
        return _USER32
    user32 = _dll("user32")
    user32.GetForegroundWindow.argtypes = ()
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    )
    user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
    user32.IsWindowVisible.argtypes = (ctypes.c_void_p,)
    user32.IsWindowVisible.restype = ctypes.c_int
    user32.IsIconic.argtypes = (ctypes.c_void_p,)
    user32.IsIconic.restype = ctypes.c_int
    user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
    user32.ShowWindow.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = (ctypes.c_void_p, ctypes.POINTER(RECT))
    user32.GetWindowRect.restype = ctypes.c_int
    user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
    user32.SetForegroundWindow.restype = ctypes.c_int
    user32.PostMessageW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    )
    user32.PostMessageW.restype = ctypes.c_int
    user32.AttachThreadInput.argtypes = (ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int)
    user32.AttachThreadInput.restype = ctypes.c_int
    _USER32 = user32
    return user32


def _kernel32():
    global _KERNEL32
    if _KERNEL32 is not None:
        return _KERNEL32
    kernel32 = _dll("kernel32")
    kernel32.GetCurrentThreadId.argtypes = ()
    kernel32.GetCurrentThreadId.restype = ctypes.c_uint32
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.SetThreadExecutionState.argtypes = (ctypes.c_uint32,)
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
    _KERNEL32 = kernel32
    return kernel32


class RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_int32),
        ("top", ctypes.c_int32),
        ("right", ctypes.c_int32),
        ("bottom", ctypes.c_int32),
    )


def _pid_of_window(user32, hwnd) -> int | None:
    pid = ctypes.c_uint32(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == 0:
        return None
    return int(pid.value)


def _enum_windows(user32, callback) -> None:
    winfunc = getattr(ctypes, "WINFUNCTYPE", None)
    if winfunc is None:
        if not user32.EnumWindows(callback, 0):
            raise OSError("EnumWindows failed")
        return
    proto = winfunc(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)
    user32.EnumWindows.argtypes = (proto, ctypes.c_ssize_t)
    user32.EnumWindows.restype = ctypes.c_int
    wrapped = proto(callback)
    if not user32.EnumWindows(wrapped, 0):
        raise OSError("EnumWindows failed")


def _largest_window(pid: int):
    user32 = _user32()
    found: list[tuple[int, int]] = []

    def callback(hwnd, _lparam):
        window_pid = _pid_of_window(user32, hwnd)
        if window_pid != int(pid):
            return 1
        if not user32.IsWindowVisible(hwnd):
            return 1
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return 1
        width = max(0, int(rect.right) - int(rect.left))
        height = max(0, int(rect.bottom) - int(rect.top))
        found.append((width * height, hwnd))
        return 1

    _enum_windows(user32, callback)
    if not found:
        return None
    found.sort(key=lambda item: item[0])
    return found[-1][1]


def window_bounds(pid: int) -> tuple[float, float, float, float] | None:
    """Outer rectangle of the largest visible top-level window for pid."""

    ensure_process_dpi()
    user32 = _user32()
    hwnd = _largest_window(pid)
    if not hwnd:
        return None
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (float(rect.left), float(rect.top), float(rect.right), float(rect.bottom))


def request_close(pid: int) -> None:
    """Ask the main window to close. TerminateProcess is the caller's fallback."""

    user32 = _user32()
    hwnd = _largest_window(pid)
    if not hwnd:
        return
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def main_window_is_front(pid: int) -> bool:
    """True when the foreground window is this pid's largest top-level window."""

    user32 = _user32()
    hwnd = _largest_window(pid)
    if not hwnd:
        return False
    front = user32.GetForegroundWindow()
    if not front:
        return False
    return int(front) == int(hwnd)


def activate(pid: int) -> None:
    """Restore the window if iconic, then SetForegroundWindow. No keystrokes."""

    ensure_process_dpi()
    user32 = _user32()
    kernel32 = _kernel32()
    hwnd = _largest_window(pid)
    if not hwnd:
        raise OSError("the signed-out Chrome window is not on screen")
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    foreground = user32.GetForegroundWindow()
    this_thread = int(kernel32.GetCurrentThreadId())
    foreground_thread = 0
    if foreground:
        foreground_thread = int(user32.GetWindowThreadProcessId(foreground, None))
    attached = False
    if foreground_thread and foreground_thread != this_thread:
        attached = bool(user32.AttachThreadInput(this_thread, foreground_thread, True))
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(this_thread, foreground_thread, False)


def foreground_pid() -> int | None:
    user32 = _user32()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    return _pid_of_window(user32, hwnd)


def is_front(pid: int) -> bool:
    current = foreground_pid()
    return current is not None and current == int(pid)


def process_image(pid: int) -> str | None:
    if not pid:
        return None
    try:
        kernel32 = _kernel32()
        query = kernel32.QueryFullProcessImageNameW
    except (OSError, AttributeError):
        return None
    query.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_wchar),
        ctypes.POINTER(ctypes.c_uint32),
    )
    query.restype = ctypes.c_int
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
    if not handle:
        return None
    try:
        size = ctypes.c_uint32(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        ok = query(handle, 0, buffer, ctypes.byref(size))
        if not ok:
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def frontmost_app() -> str | None:
    """'Google Chrome' when the foreground image is chrome.exe, else the exe name."""

    try:
        pid = foreground_pid()
    except OSError:
        return None
    if not pid:
        return None
    try:
        image = process_image(pid)
    except OSError:
        return None
    return app_name_for_image(image)


def set_execution_state(flags: int) -> int:
    """Call SetThreadExecutionState. flags is AWAKE_FLAGS or ES_CONTINUOUS."""

    kernel32 = _kernel32()
    return int(kernel32.SetThreadExecutionState(int(flags) & 0xFFFFFFFF))


def is_user_admin() -> bool:
    """True when this process is elevated. Called only while performing the check."""

    global _SHELL32
    if _SHELL32 is None:
        shell32 = _dll("shell32")
        shell32.IsUserAnAdmin.argtypes = ()
        shell32.IsUserAnAdmin.restype = ctypes.c_int
        _SHELL32 = shell32
    return bool(_SHELL32.IsUserAnAdmin())
