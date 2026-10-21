"""A separate signed-out Chrome. The user's Chrome is never addressed.

Controlling Chrome by application name attaches to the already running
signed-in Chrome. This file launches the Chrome binary with its own profile
directory and talks to that process by pid. Cookies and the login keychain
from the signed-in profile are not copied in.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.parse
from pathlib import Path

from activitysim.ax import content_bounds, enable_web_content, set_chrome_pid
from activitysim.cdp import CdpError, activate_page, call, close_page, list_pages, open_page
from activitysim.osa import OsaError, run_osascript
from activitysim.places import safe_url

ChromeError = OsaError

LOCKED_PROFILE = Path.home() / "Library/Application Support/activitysim/locked-chrome"
REAL_PROFILE = Path.home() / "Library/Application Support/Google/Chrome"

# Hosts that can act on the X account. They do not resolve in the locked Chrome.
_BLOCKED_HOSTS = ("x.com", "twitter.com", "t.co", "twimg.com")

_AUTH_PATHS = (
    "Local State",
    "Default/Cookies",
    "Default/Cookies-journal",
    "Default/Login Data",
    "Default/Login Data-journal",
    "Default/Login Data For Account",
    "Default/Login Data For Account-journal",
    "Default/Web Data",
    "Default/Web Data-journal",
    "Default/Account Web Data",
    "Default/Account Web Data-journal",
    "Default/Extension Cookies",
    "Default/Extension Cookies-journal",
    "Default/Network",
    "Default/Sessions",
    "Default/Session Storage",
    "Default/Local Storage",
    "Default/IndexedDB",
    "Default/Service Worker",
    "Default/Sync Data",
    "Default/Storage",
    "Default/GCM Store",
    "Default/Preferences",
    "Default/Secure Preferences",
)


def find_chrome() -> Path | None:
    candidates = (
        Path("/Applications/Google Chrome.app"),
        Path.home() / "Applications" / "Google Chrome.app",
    )
    for path in candidates:
        if path.exists():
            return path
    return None


def chrome_binary() -> Path | None:
    app = find_chrome()
    if app is None:
        return None
    binary = app / "Contents/MacOS/Google Chrome"
    if binary.exists():
        return binary
    return None


def assert_locked_profile(profile: Path) -> Path:
    """Refuse the signed-in Chrome directory and any path that contains it."""

    chosen = profile.expanduser().resolve()
    real = REAL_PROFILE.resolve()
    if chosen == real or real in chosen.parents or chosen in real.parents:
        raise ValueError("refusing the signed-in Chrome profile")
    if chosen == Path(chosen.anchor):
        raise ValueError("refusing the signed-in Chrome profile")
    return chosen


def host_blocked(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == name or host.endswith("." + name) for name in _BLOCKED_HOSTS)


def launch_command(binary: Path, profile: Path, url: str, port: int) -> list[str]:
    """Arguments for a signed-out Chrome. The signed-in profile is not named."""

    profile = assert_locked_profile(profile)
    if not safe_url(url) or host_blocked(url):
        raise ValueError(f"refusing URL {url!r}")
    if isinstance(port, bool) or not isinstance(port, int) or port < 1 or port > 65535:
        raise ValueError("bad debugger port")
    rules = ",".join(f"MAP {name} ~NOTFOUND,MAP *.{name} ~NOTFOUND" for name in _BLOCKED_HOSTS)
    return [
        str(binary),
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--use-mock-keychain",
        "--no-service-autorun",
        "--disable-background-networking",
        f"--remote-debugging-port={port}",
        f"--remote-allow-origins=http://127.0.0.1:{port}",
        f"--host-resolver-rules={rules}",
        "--new-window",
        url,
    ]


def scrub_signins(profile: Path) -> None:
    """Delete saved sessions inside the locked profile and nowhere else."""

    root = assert_locked_profile(profile)
    root.mkdir(parents=True, exist_ok=True)
    for relative in _AUTH_PATHS:
        _remove_inside(root, relative)
    (root / "First Run").write_text("", encoding="utf-8")


def pid_literal(pid: int) -> str:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("bad pid")
    return str(pid)


def script_activate(pid: int) -> str:
    number = pid_literal(pid)
    return f"""
tell application "System Events"
  set frontmost of (first process whose unix id is {number}) to true
end tell
"""


def script_bounds(pid: int) -> str:
    number = pid_literal(pid)
    return f"""
tell application "System Events"
  tell (first process whose unix id is {number})
    set p to position of window 1
    set s to size of window 1
  end tell
end tell
return (item 1 of p as text) & " " & (item 2 of p as text) & " " & (item 1 of s as text) & " " & (item 2 of s as text)
"""


def script_fullscreen(pid: int) -> str:
    number = pid_literal(pid)
    return f"""
tell application "System Events"
  tell (first process whose unix id is {number})
    set frontmost to true
    repeat with mi in menu items of menu "View" of menu bar 1
      if name of mi is "Enter Full Screen" then
        click mi
        exit repeat
      end if
    end repeat
  end tell
end tell
"""


def script_front_pid() -> str:
    return """
tell application "System Events"
  return unix id of first application process whose frontmost is true
end tell
"""


def _remove_inside(root: Path, relative: str) -> None:
    raw = root / relative
    if not raw.exists() and not raw.is_symlink():
        return
    parent = raw.parent.resolve()
    if parent != root and root not in parent.parents:
        raise ValueError("refusing to delete outside the locked profile")
    if raw.is_symlink() or raw.is_file():
        raw.unlink()
        return
    if root not in raw.resolve().parents:
        raise ValueError("refusing to delete outside the locked profile")
    shutil.rmtree(raw)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _process_command(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-ww", "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def _command_has_profile(command: str, profile: Path) -> bool:
    needle = f"--user-data-dir={profile}"
    start = 0
    while True:
        index = command.find(needle, start)
        if index < 0:
            return False
        after = index + len(needle)
        if after >= len(command) or command[after] in " \t":
            return True
        start = after


def _is_our_process(pid: int, profile: Path) -> bool:
    command = _process_command(pid)
    if "--type=" in command or "Google Chrome" not in command:
        return False
    return _command_has_profile(command, profile)


def pids_for_profile(profile: Path) -> list[int]:
    profile = assert_locked_profile(profile)
    result = subprocess.run(
        ["ps", "-axww", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    found: list[int] = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        pid_text, _, command = text.partition(" ")
        if not pid_text.isdigit():
            continue
        if "--type=" in command or "Google Chrome" not in command:
            continue
        if _command_has_profile(command, profile):
            found.append(int(pid_text))
    return found


def _stop_existing(profile: Path) -> None:
    for pid in pids_for_profile(profile):
        try:
            if _is_our_process(pid, profile):
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.time() + 3
    while time.time() < deadline and pids_for_profile(profile):
        time.sleep(0.1)


class Chrome:
    """One signed-out Chrome process, started by this program."""

    def __init__(self, profile: Path | None = None) -> None:
        self.profile = assert_locked_profile(profile or LOCKED_PROFILE)
        self.pid: int | None = None
        self.port: int | None = None
        self.window_id: str | None = None
        self._process: subprocess.Popen[str] | None = None
        self._ws = ""

    def installed(self) -> bool:
        return chrome_binary() is not None

    def open_window(self, url: str) -> str:
        _require_url(url)
        binary = chrome_binary()
        if binary is None:
            raise ChromeError("Google Chrome is not installed")
        _stop_existing(self.profile)
        if pids_for_profile(self.profile):
            raise ChromeError("an older signed-out Chrome is still running")
        scrub_signins(self.profile)
        port = _free_port()
        command = launch_command(binary, self.profile, url, port)
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.pid = self._process.pid
        self.port = port
        try:
            self._wait_until_ours()
            pages = self._wait_for_pages()
            self._ws = str(pages[0].get("webSocketDebuggerUrl") or "")
            self.window_id = str(self.pid)
            set_chrome_pid(self.pid)
            enable_web_content(self.pid)
            call(self._ws, "Page.reload", {})
            self._wait_until_loaded()
            self._enter_fullscreen()
        except (ChromeError, CdpError, OSError):
            self.close_window()
            raise
        return self.window_id

    def activate(self) -> None:
        self._require_pid()
        run_osascript(script_activate(self.pid or 0))

    def is_front(self) -> bool:
        if not self.pid:
            return False
        try:
            front = run_osascript(script_front_pid()).strip()
        except OsaError:
            return False
        return front.isdigit() and int(front) == self.pid

    def current_url(self) -> str:
        self._require_page()
        result = call(
            self._ws,
            "Runtime.evaluate",
            {"expression": "location.href", "returnByValue": True},
        )
        value = result.get("result", {}).get("value", "")
        return value if isinstance(value, str) else ""

    def bounds(self) -> tuple[float, float, float, float]:
        self._require_pid()
        set_chrome_pid(self.pid)
        frame = content_bounds()
        if frame is None:
            raise ChromeError("the signed-out Chrome window is not on screen")
        left, top, right, bottom = frame
        if right - left < 400 or bottom - top < 300:
            raise ChromeError(f"unexpected window bounds {frame!r}")
        return frame

    def tab_count(self) -> int:
        return len(self._pages())

    def new_tab(self, url: str) -> None:
        _require_url(url)
        self._require_port()
        open_page(self.port or 0, url)
        self._remember_page()

    def navigate(self, url: str) -> None:
        _require_url(url)
        self._require_page()
        call(self._ws, "Page.navigate", {"url": url})

    def switch_tab(self, index: int) -> None:
        pages = self._pages()
        if index < 1 or index > len(pages):
            raise ValueError(f"bad tab {index}")
        target = str(pages[index - 1].get("id") or "")
        activate_page(self.port or 0, target)
        self._ws = str(pages[index - 1].get("webSocketDebuggerUrl") or "")

    def close_oldest(self) -> None:
        pages = self._pages()
        if len(pages) <= 1:
            return
        close_page(self.port or 0, str(pages[0].get("id") or ""))
        self._remember_page()

    def back(self) -> None:
        self._require_page()
        call(self._ws, "Runtime.evaluate", {"expression": "history.back()", "returnByValue": True})

    def reload(self) -> None:
        self._require_page()
        call(self._ws, "Page.reload", {})

    def scroll_by(self, pixels: int) -> bool:
        self._require_page()
        amount = int(pixels)
        call(
            self._ws,
            "Runtime.evaluate",
            {"expression": f"window.scrollBy(0, {amount})", "returnByValue": True},
        )
        return True

    def close_window(self) -> None:
        process = self._process
        pid = self.pid
        profile = self.profile
        launched = process is not None or pid is not None
        self._process = None
        self.pid = None
        self.port = None
        self.window_id = None
        self._ws = ""
        set_chrome_pid(None)
        if not launched:
            return
        _stop_existing(profile)
        if process is None:
            return
        if process.poll() is None and pid and _is_our_process(pid, profile):
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if pid and _is_our_process(pid, profile):
                process.kill()

    def _wait_until_loaded(self) -> None:
        deadline = time.time() + 12
        while time.time() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise ChromeError("signed-out Chrome exited")
            try:
                result = call(
                    self._ws,
                    "Runtime.evaluate",
                    {"expression": "document.readyState", "returnByValue": True},
                )
            except CdpError:
                time.sleep(0.2)
                continue
            if result.get("result", {}).get("value") == "complete":
                return
            time.sleep(0.2)

    def _enter_fullscreen(self) -> None:
        try:
            run_osascript(script_fullscreen(self.pid or 0), timeout=8)
            time.sleep(0.4)
        except OsaError:
            return

    def _wait_until_ours(self) -> None:
        deadline = time.time() + 8
        while time.time() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise ChromeError("signed-out Chrome exited")
            if self.pid and _is_our_process(self.pid, self.profile):
                return
            time.sleep(0.1)
        raise ChromeError("signed-out Chrome did not stay on its own profile")

    def _wait_for_pages(self) -> list[dict]:
        deadline = time.time() + 20
        while time.time() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise ChromeError("signed-out Chrome exited")
            try:
                pages = list_pages(self.port or 0)
            except CdpError:
                time.sleep(0.2)
                continue
            if pages:
                return pages
            time.sleep(0.2)
        raise ChromeError("signed-out Chrome did not open a window")

    def _pages(self) -> list[dict]:
        self._require_port()
        try:
            return list_pages(self.port or 0)
        except CdpError as exc:
            raise ChromeError(str(exc)) from exc

    def _remember_page(self) -> None:
        pages = self._pages()
        if pages:
            self._ws = str(pages[-1].get("webSocketDebuggerUrl") or "")

    def _require_pid(self) -> None:
        if not self.pid or not _is_our_process(self.pid, self.profile):
            raise ChromeError("the signed-out Chrome is not running")

    def _require_port(self) -> None:
        self._require_pid()
        if not self.port:
            raise ChromeError("the signed-out Chrome has no debugger")

    def _require_page(self) -> None:
        self._require_port()
        if not self._ws:
            self._remember_page()
        if not self._ws:
            raise ChromeError("the signed-out Chrome has no page")


def _require_url(url: str) -> None:
    if not safe_url(url) or host_blocked(url):
        raise ValueError(f"refusing URL {url!r}")
