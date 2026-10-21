"""Control one Chrome window that this program created.

Scripts set URLs and switch tabs. They never send keystrokes. The caller's
existing Chrome windows are not referenced.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from activitysim.osa import OsaError, as_text, run_osascript
from activitysim.places import safe_url

ChromeError = OsaError


def find_chrome() -> Path | None:
    candidates = (
        Path("/Applications/Google Chrome.app"),
        Path.home() / "Applications" / "Google Chrome.app",
    )
    for path in candidates:
        if path.exists():
            return path
    return None


def script_open_window(url: str, incognito: bool = False) -> str:
    """Open one window on the URL and enter macOS full screen.

    The menu title is English, same assumption as the US keyboard used for typing.
    If that click fails, the window is zoomed so it still fills the screen.
    """

    _require_url(url)
    if incognito:
        make = 'set w to make new window with properties {mode:"incognito"}'
    else:
        make = "set w to make new window"
    return f"""
tell application "Google Chrome"
  activate
  {make}
  delay 0.4
  set URL of active tab of w to {as_text(url)}
  set index of w to 1
  set wid to id of w
end tell
try
  tell application "System Events"
    tell process "Google Chrome"
      set frontmost to true
      repeat with mi in menu items of menu "View" of menu bar 1
        if name of mi is "Enter Full Screen" then
          click mi
          exit repeat
        end if
      end repeat
    end tell
  end tell
  delay 0.8
on error
  tell application "Google Chrome"
    set zoomed of window id wid to true
  end tell
end try
return wid
"""


def script_new_tab(window_id: str, url: str) -> str:
    _require_url(url)
    return f"""
tell application "Google Chrome"
  tell window id {as_text(window_id)}
    make new tab with properties {{URL:{as_text(url)}}}
  end tell
end tell
"""


def script_navigate(window_id: str, url: str) -> str:
    _require_url(url)
    return f"""
tell application "Google Chrome"
  set URL of active tab of window id {as_text(window_id)} to {as_text(url)}
end tell
"""


def script_switch(window_id: str, index: int) -> str:
    if index < 1:
        raise ValueError("tab index starts at 1")
    return f"""
tell application "Google Chrome"
  tell window id {as_text(window_id)}
    set active tab index to {int(index)}
  end tell
  set index of window id {as_text(window_id)} to 1
  activate
end tell
"""


def script_close_oldest(window_id: str) -> str:
    return f"""
tell application "Google Chrome"
  tell window id {as_text(window_id)}
    if (count of tabs) is greater than 1 then
      close tab 1
    end if
  end tell
end tell
"""


def script_back(window_id: str) -> str:
    return f"""
tell application "Google Chrome"
  tell active tab of window id {as_text(window_id)} to go back
end tell
"""


def script_reload(window_id: str) -> str:
    return f"""
tell application "Google Chrome"
  tell active tab of window id {as_text(window_id)} to reload
end tell
"""


def script_bounds(window_id: str) -> str:
    return f"""
tell application "Google Chrome"
  set b to bounds of window id {as_text(window_id)}
end tell
return (item 1 of b as text) & " " & (item 2 of b as text) & " " & (item 3 of b as text) & " " & (item 4 of b as text)
"""


def script_tab_count(window_id: str) -> str:
    return f"""
tell application "Google Chrome"
  return (count of tabs of window id {as_text(window_id)}) as text
end tell
"""


def script_scroll(window_id: str, pixels: int) -> str:
    # Positive pixels scroll down the page. Only an int is interpolated.
    amount = int(pixels)
    return f"""
tell application "Google Chrome"
  tell active tab of window id {as_text(window_id)}
    execute javascript "window.scrollBy(0, {amount})"
  end tell
end tell
"""


def script_close_window(window_id: str) -> str:
    return f"""
tell application "Google Chrome"
  if exists window id {as_text(window_id)} then
    close window id {as_text(window_id)}
  end if
end tell
"""


def script_current_url(window_id: str) -> str:
    return f"""
tell application "Google Chrome"
  return URL of active tab of window id {as_text(window_id)}
end tell
"""


def script_activate(window_id: str) -> str:
    return f"""
tell application "Google Chrome"
  set index of window id {as_text(window_id)} to 1
  activate
end tell
"""


def _require_url(url: str) -> None:
    if not safe_url(url):
        raise ValueError(f"refusing URL {url!r}")


class Chrome:
    def __init__(self, incognito: bool = False) -> None:
        self.incognito = incognito
        self.window_id: str | None = None
        self._js: bool | None = None
        self._js_explained = False

    def installed(self) -> bool:
        return find_chrome() is not None

    def open_window(self, url: str) -> str:
        _require_url(url)
        self._launch_if_needed()
        script = script_open_window(url, incognito=self.incognito)
        try:
            self.window_id = run_osascript(script, timeout=30)
        except OsaError:
            if not self.incognito:
                raise
            self.window_id = run_osascript(script_open_window(url, incognito=False), timeout=30)
            self.incognito = False
        if not self.window_id:
            raise ChromeError("Chrome did not return a window id")
        return self.window_id

    def activate(self) -> None:
        self._require_window()
        run_osascript(script_activate(self.window_id or ""))

    def current_url(self) -> str:
        self._require_window()
        return run_osascript(script_current_url(self.window_id or ""))

    def bounds(self) -> tuple[float, float, float, float]:
        self._require_window()
        raw = run_osascript(script_bounds(self.window_id or ""))
        parts = [float(piece) for piece in raw.split()]
        if len(parts) != 4:
            raise ChromeError(f"unexpected window bounds {raw!r}")
        left, top, right, bottom = parts
        if right <= left or bottom <= top:
            raise ChromeError(f"unexpected window bounds {raw!r}")
        return left, top, right, bottom

    def tab_count(self) -> int:
        self._require_window()
        raw = run_osascript(script_tab_count(self.window_id or ""))
        return int(raw.strip())

    def new_tab(self, url: str) -> None:
        self._require_window()
        run_osascript(script_new_tab(self.window_id or "", url))

    def navigate(self, url: str) -> None:
        self._require_window()
        run_osascript(script_navigate(self.window_id or "", url))

    def switch_tab(self, index: int) -> None:
        self._require_window()
        run_osascript(script_switch(self.window_id or "", index))

    def close_oldest(self) -> None:
        self._require_window()
        run_osascript(script_close_oldest(self.window_id or ""))

    def back(self) -> None:
        self._require_window()
        run_osascript(script_back(self.window_id or ""))

    def reload(self) -> None:
        self._require_window()
        run_osascript(script_reload(self.window_id or ""))

    def scroll_by(self, pixels: int) -> bool:
        """Scroll with JavaScript when Chrome allows it. Otherwise return False."""

        if self._js is False:
            return False
        self._require_window()
        try:
            run_osascript(script_scroll(self.window_id or "", pixels))
        except OsaError as exc:
            message = str(exc)
            if "JavaScript" in message or "turned off" in message:
                self._js = False
            return False
        self._js = True
        return True

    def close_window(self) -> None:
        if not self.window_id:
            return
        run_osascript(script_close_window(self.window_id))
        self.window_id = None

    def _require_window(self) -> None:
        if not self.window_id:
            raise ChromeError("the simulator window is not open")

    def _launch_if_needed(self) -> None:
        try:
            running = run_osascript('tell application "System Events" to (name of processes) contains "Google Chrome"')
        except OsaError:
            running = "false"
        if running.strip().lower() == "true":
            return
        if find_chrome() is None:
            raise ChromeError("Google Chrome is not installed")
        subprocess.run(["open", "-a", "Google Chrome"], check=False)
        time.sleep(1.5)
