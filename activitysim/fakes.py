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
        if (key.isdigit() or key in {"a", "p"}) and "command" not in mods:
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
        self.pointer = None

    def installed(self) -> bool:
        return True

    def open_window(self, url: str) -> str:
        if not safe_url(url):
            raise ValueError(url)
        self.tabs = [url]
        self.active = 1
        self.window_id = "dry-window"
        return self.window_id

    def attach(self, pointer) -> None:
        self.pointer = pointer

    def activate(self) -> None:
        self.activated += 1
        if self.pointer is not None:
            self.pointer.front = "Google Chrome"

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


class FakeEditor:
    """A stand-in Visual Studio Code. It records opens and does not launch."""

    def __init__(self) -> None:
        self.present = True
        self.pointer = None
        self.opened: list[str] = []
        self.activated = 0
        self.bounds_value = (40.0, 40.0, 1500.0, 980.0)
        self._front = True

    def attach(self, pointer) -> None:
        self.pointer = pointer

    def installed(self) -> bool:
        return self.present

    def open_folder(self, folder: str) -> None:
        self.opened.append(str(folder))
        self.activate()

    def activate(self) -> None:
        self.activated += 1
        if self.pointer is not None and self._front:
            self.pointer.front = "Code"

    def is_front(self) -> bool:
        if not self._front:
            return False
        if self.pointer is None:
            return True
        return self.pointer.front == "Code"

    def bounds(self) -> tuple[float, float, float, float]:
        return self.bounds_value


class FakeWork:
    """Checkouts that do not call git."""

    def __init__(self) -> None:
        self.present = True
        self.repos = ("pallets/flask", "encode/httpx", "tiangolo/fastapi")
        self.i = 0
        self.checkouts: list[str] = []
        self.tree = ["README.md", "src/app.py", "src/util.py", "docs/guide.md"]

    def ready(self) -> bool:
        return self.present

    def next_repo(self) -> str:
        repo = self.repos[self.i % len(self.repos)]
        self.i += 1
        return repo

    def checkout(self, repo: str) -> str:
        self.checkouts.append(repo)
        return f"/tmp/activitysim-code/{repo}"

    def files(self, folder: str) -> list[str]:
        return list(self.tree)
