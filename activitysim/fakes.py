"""Stand-ins used by the dry run and the tests. They perform no real input."""

from __future__ import annotations

from activitysim.input import assert_typable, can_type
from activitysim.places import safe_url


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


class FakePointer:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.pos = (400.0, 400.0)
        self.clicks: list[tuple[float, float]] = []
        self.moves: list[tuple[float, float]] = []
        self.scrolls: list[int] = []
        self.keys: list[tuple[str, tuple[str, ...]]] = []
        self.typed: list[str] = []
        self.omniboxes: list[str] = []
        self.front = "Google Chrome"
        self.asked = False

    def available(self) -> bool:
        return self.ready

    def position(self) -> tuple[float, float]:
        return self.pos

    def displays(self) -> list[tuple[float, float, float, float]]:
        return [(0.0, 0.0, 1728.0, 1117.0)]

    def screen(self) -> tuple[float, float, float, float]:
        return (0.0, 0.0, 1728.0, 1117.0)

    def move_to(self, x: float, y: float) -> None:
        self.pos = (float(x), float(y))
        self.moves.append(self.pos)

    def click(self, x: float, y: float) -> None:
        self.pos = (float(x), float(y))
        self.clicks.append(self.pos)

    def scroll(self, page_delta: int) -> None:
        self.scrolls.append(int(page_delta))

    def hotkey(self, key: str, modifiers: tuple[str, ...] | list[str] = ()) -> None:
        mods = tuple(modifiers)
        if key in {"q", "w", "space"}:
            raise ValueError(f"refusing key {key!r}")
        if key.isdigit() and "command" not in mods:
            raise ValueError(f"{key} is only sent with command")
        self.keys.append((key, mods))

    def type_text(self, text: str) -> None:
        assert_typable(text)
        self.typed.append(text)

    def request_access(self) -> bool:
        self.asked = True
        return self.ready

    def omnibox(self, url: str) -> bool:
        if not can_type(url) or not safe_url(url):
            return False
        self.hotkey("l", ("command",))
        self.type_text(url)
        self.hotkey("return", ())
        self.omniboxes.append(url)
        return True


class FakeBrowser:
    def __init__(self) -> None:
        self.tabs: list[str] = []
        self.active = 1
        self.window_id: str | None = None
        self.bounds_value = (0.0, 33.0, 1400.0, 900.0)
        self.closed_windows = 0
        self.backs = 0
        self.reloads = 0
        self.js = False
        self.activated = 0

    def installed(self) -> bool:
        return True

    def open_window(self, url: str) -> str:
        if not safe_url(url):
            raise ValueError(url)
        self.tabs = [url]
        self.active = 1
        self.window_id = "dry-window"
        return self.window_id

    def activate(self) -> None:
        self.activated += 1

    def bounds(self) -> tuple[float, float, float, float]:
        return self.bounds_value

    def tab_count(self) -> int:
        return len(self.tabs)

    def new_tab(self, url: str) -> None:
        if not safe_url(url):
            raise ValueError(url)
        self.tabs.append(url)
        self.active = len(self.tabs)

    def navigate(self, url: str) -> None:
        if not safe_url(url):
            raise ValueError(url)
        if not self.tabs:
            self.tabs = [url]
            self.active = 1
            return
        self.tabs[self.active - 1] = url

    def switch_tab(self, index: int) -> None:
        if index < 1 or index > len(self.tabs):
            raise ValueError(f"bad tab {index}")
        self.active = index

    def close_oldest(self) -> None:
        if len(self.tabs) <= 1:
            return
        self.tabs.pop(0)
        if self.active > 1:
            self.active -= 1
        self.active = min(self.active, len(self.tabs))

    def back(self) -> None:
        self.backs += 1

    def reload(self) -> None:
        self.reloads += 1

    def scroll_by(self, pixels: int) -> bool:
        return self.js

    def close_window(self) -> None:
        self.closed_windows += 1
        self.window_id = None
        self.tabs = []

    def current_url(self) -> str:
        if not self.tabs:
            return ""
        return self.tabs[self.active - 1]

    def mark_url(self, url: str) -> None:
        if not safe_url(url):
            raise ValueError(url)
        if not self.tabs:
            self.tabs = [url]
            self.active = 1
            return
        self.tabs[self.active - 1] = url
