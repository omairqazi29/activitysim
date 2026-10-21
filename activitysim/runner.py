"""Drive GitHub advanced search with real pointer and keyboard events.

Each step clicks or types. AppleScript is only used to open the window and to
read the current URL. Activity checks that ignore synthetic-looking browser
automation still see mouse-down, mouse-up, and key events.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from activitysim.geom import pointer_moved, user_left_chrome
from activitysim.input import can_type
from activitysim.osa import OsaError
from activitysim.search import (
    ADVANCED_URL,
    QUERIES,
    in_readme_zone,
    in_result_zone,
    in_search_zone,
    is_advanced_url,
    readme_point,
    result_points,
    results_url,
    search_box_point,
)

TEXT_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"})


@dataclass
class Config:
    min_gap: float = 2
    max_gap: float = 6
    pause: float = 180
    max_tabs: int = 5
    duration: float = 0
    startup_delay: float = 1
    tolerance: float = 36
    mouse_only: bool = False
    no_keyboard: bool = False
    dry_run: bool = False
    seed: int | None = None


class Runner:
    def __init__(
        self,
        config: Config,
        browser,
        pointer,
        places=None,
        rng: random.Random | None = None,
        frontmost=None,
        clock=time.time,
        sleep=time.sleep,
        focused_role=None,
        find_search_box=None,
        find_results=None,
    ) -> None:
        self.config = config
        self.browser = browser
        self.pointer = pointer
        self.places = places
        self.rng = rng or random.Random()
        self.frontmost = frontmost or (lambda: "Google Chrome")
        self.clock = clock
        self.sleep = sleep
        self.focused_role = focused_role
        self.find_search_box = find_search_box
        self.find_results = find_results
        self.history: list[str] = []
        self.actions: list[str] = []
        self.can_point = False
        self.can_keys = False
        self._last_pos: tuple[float, float] | None = None
        self._last_front: str | None = None
        self.mode = "search"
        self.points: list[tuple[float, float]] = []
        self.pi = 0
        self.per_query = 4
        self.reads_left = 0
        self.results_url = ""

    def log(self, message: str) -> None:
        if self.config.dry_run:
            stamp = f"t+{self.clock():6.1f}s"
        else:
            stamp = time.strftime("%H:%M:%S")
        print(f"{stamp}  {message}", flush=True)
        self.history.append(message)

    def run(self) -> None:
        try:
            self._loop()
        except KeyboardInterrupt:
            self.log("stopped")

    def _loop(self) -> None:
        start = self.clock()
        self._banner()
        if not self._prepare_input():
            return
        self._last_pos = self.pointer.position()
        self._sleep(self.config.startup_delay)
        if self._expired(start):
            self.log("duration elapsed")
            return
        if self._should_pause():
            self._pause(start)
            if self._expired(start):
                self.log("duration elapsed")
                return
        if not self.browser.installed():
            raise OsaError("Google Chrome is not installed")
        self.browser.open_window(ADVANCED_URL)
        self._last_front = self.frontmost()
        self.log(f"opened Chrome at {ADVANCED_URL}")
        self.log("other Chrome windows are not controlled")
        self.mode = "search"
        while not self._expired(start):
            self._sleep(self.rng.uniform(self.config.min_gap, self.config.max_gap))
            if self._expired(start):
                break
            if self._should_pause():
                self._pause(start)
                continue
            try:
                self._step()
            except (OsaError, OSError, ValueError) as exc:
                self.log(f"step failed: {exc}")
            self._note_front()
            self._remember()
        self.log("duration elapsed")

    def _banner(self) -> None:
        self.log(f"seed {self.config.seed}")
        if self.config.dry_run:
            self.log("dry run, nothing will move and Chrome will not open")
        else:
            self.log("github advanced search, then real clicks through the results")
        self.log("Ctrl+C stops the simulator. power settings are not changed")
        if not self.config.dry_run:
            self.log("keep the machine awake separately: sudo pmset -a disablesleep 1")

    def _prepare_input(self) -> bool:
        self.can_point = bool(self.pointer.available())
        if not self.can_point:
            self.log("Accessibility is required for real mouse and keyboard clicks")
            ask = getattr(self.pointer, "request_access", None)
            if ask and ask():
                self.can_point = bool(self.pointer.available())
        if not self.can_point:
            self.log("stopped. no Accessibility permission, so clicks would not happen")
            return False
        self.can_keys = self.can_point and not self.config.no_keyboard and not self.config.mouse_only
        if self.can_keys:
            self.log("pointer and keyboard events are on")
        else:
            self.log("pointer clicks are on, keyboard is off")
        return True

    def _step(self) -> None:
        if self.mode == "item":
            if self.reads_left > 0:
                self._read()
                self.reads_left -= 1
            else:
                self._back()
                self.mode = "results"
            return
        if self.mode == "results" and self.pi < min(len(self.points), self.per_query):
            self._open_result()
            return
        self._search()

    def _search(self) -> None:
        query = self.rng.choice(QUERIES)
        self.per_query = self.rng.randint(3, 5)
        self.log(f"github advanced search: {query}")
        self.actions.append("search")
        if self.can_keys and not self._on_advanced():
            self._type_url(ADVANCED_URL)
            self._sleep(self.rng.uniform(1.2, 2.0))
        self._submit_query(query)
        self.points = self._collect_results()
        self.pi = 0
        self.mode = "results"
        self.log(f"{len(self.points)} results to click")

    def _submit_query(self, query: str) -> None:
        box = self._search_box()
        clicked = False
        if box is not None and self.can_point:
            clicked = self._click(box, "advanced search box")
            self._sleep(0.15)
        if clicked and self._text_focused() and self.can_keys and can_type(query):
            self.pointer.hotkey("a", ("command",))
            self.pointer.type_text(query)
            self.pointer.hotkey("return", ())
            target = results_url(query)
            self.results_url = target
            self._remember_navigation(target)
            self.log(f"typed {query}")
            self._sleep(self.rng.uniform(1.6, 2.6))
            return
        target = results_url(query)
        self.results_url = target
        if self.can_keys and can_type(target):
            self.log("typing the results url")
            self._type_url(target)
        else:
            self.browser.navigate(target)
            self._remember_navigation(target)
            self.log(f"open {target}")
        self._sleep(self.rng.uniform(1.6, 2.6))

    def _open_result(self) -> None:
        bounds = self._bounds()
        point = self.points[self.pi]
        self.pi += 1
        if bounds is None or not in_result_zone(point, bounds):
            self.log("skipped a result outside the list column")
            return
        self.actions.append("result")
        if not self._click(point, f"result {self.pi}"):
            return
        self.reads_left = self.rng.randint(2, 3)
        self.mode = "item"

    def _read(self) -> None:
        self.actions.append("read")
        if self.can_keys and self._arm():
            key = self.rng.choice(["down", "down", "pagedown", "up"])
            count = self.rng.randint(2, 4)
            for _ in range(count):
                self.pointer.hotkey(key, ())
                self._sleep(self.rng.uniform(0.05, 0.16))
            self.log(f"key {key} x{count}")
        bounds = self._bounds()
        if bounds is None or not self.can_point:
            return
        if self.rng.random() < 0.5:
            point = readme_point(bounds, self.rng)
            if point is not None and in_readme_zone(point, bounds):
                self._click(point, "inside the result")
                return
        hover = readme_point(bounds, self.rng)
        if hover is not None:
            self.pointer.move_to(*hover)
            self.pointer.scroll(self.rng.choice([3, 4, 5, -2]))
            self.log("scroll the result")

    def _back(self) -> None:
        self.actions.append("back")
        if self.can_keys and self._arm():
            self.pointer.hotkey("left", ("command",))
            self.log("back to results")
        else:
            self.browser.back()
            self.log("back to results")
        self._sleep(self.rng.uniform(0.5, 0.9))
        if self.results_url:
            self._remember_navigation(self.results_url)

    def _collect_results(self) -> list[tuple[float, float]]:
        bounds = self._bounds()
        if bounds is None:
            return []
        found: list[tuple[float, float]] = []
        if self.find_results is not None:
            try:
                found = [tuple(point) for point in (self.find_results(bounds) or [])]
            except (OSError, OsaError, ValueError, TypeError):
                found = []
        found = [point for point in found if in_result_zone(point, bounds)]
        if len(found) < 2:
            found = result_points(bounds)
        return found[:8]

    def _search_box(self) -> tuple[float, float] | None:
        bounds = self._bounds()
        if bounds is None:
            return None
        if self.find_search_box is not None:
            try:
                point = self.find_search_box(bounds)
            except (OSError, OsaError, ValueError, TypeError):
                point = None
            if point and in_search_zone(tuple(point), bounds):
                return tuple(point)
        point = search_box_point(bounds)
        if point and in_search_zone(point, bounds):
            return point
        return None

    def _text_focused(self) -> bool:
        if not self.can_keys:
            return False
        if self.focused_role is None:
            return False
        try:
            role = self.focused_role() or ""
        except (OSError, OsaError, ValueError):
            return False
        return role in TEXT_ROLES

    def _type_url(self, url: str) -> None:
        if not self._arm():
            self.browser.navigate(url)
            self._remember_navigation(url)
            return
        self.pointer.hotkey("l", ("command",))
        self._sleep(0.12)
        self.pointer.type_text(url)
        self.pointer.hotkey("return", ())
        self._remember_navigation(url)

    def _click(self, point: tuple[float, float], label: str) -> bool:
        if not self.can_point or not self._arm():
            return False
        self.pointer.move_to(point[0], point[1])
        self._sleep(self.rng.uniform(0.05, 0.15))
        self.pointer.click(point[0], point[1])
        self.log(f"click {label}")
        return True

    def _remember_navigation(self, url: str) -> None:
        mark = getattr(self.browser, "mark_url", None)
        if mark:
            mark(url)

    def _on_advanced(self) -> bool:
        return is_advanced_url(self._current_url())

    def _current_url(self) -> str:
        try:
            return self.browser.current_url() or ""
        except (OsaError, OSError, ValueError, AttributeError):
            return ""

    def _arm(self) -> bool:
        self._activate()
        return self.frontmost() == "Google Chrome"

    def _activate(self) -> None:
        self.browser.activate()

    def _bounds(self):
        try:
            return self.browser.bounds()
        except (OsaError, OSError, ValueError):
            return None

    def _note_front(self) -> None:
        front = self.frontmost()
        if front is not None:
            self._last_front = front

    def _remember(self) -> None:
        self._last_pos = self.pointer.position()

    def _should_pause(self) -> bool:
        current = self.pointer.position()
        moved = pointer_moved(self._last_pos, current, self.config.tolerance)
        front = self.frontmost()
        left = user_left_chrome(self._last_front, front)
        self._last_pos = current
        if front is not None:
            self._last_front = front
        if self.config.pause <= 0:
            return False
        return moved or left

    def _pause(self, start: float) -> None:
        self.log(f"paused {self.config.pause:.0f}s because you are using the machine")
        end = self.clock() + self.config.pause
        while not self._expired(start) and self.clock() < end:
            self._sleep(min(5, end - self.clock()))
            if self._still_active():
                self.log("still in use, pause extended")
                end = self.clock() + self.config.pause

    def _still_active(self) -> bool:
        current = self.pointer.position()
        moved = pointer_moved(self._last_pos, current, self.config.tolerance)
        self._last_pos = current
        front = self.frontmost()
        switched = front is not None and self._last_front is not None and front != self._last_front
        if front is not None:
            self._last_front = front
        return moved or switched

    def _expired(self, start: float) -> bool:
        return bool(self.config.duration) and (self.clock() - start) >= self.config.duration

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        end = self.clock() + seconds
        while self.clock() < end - 1e-9:
            self.sleep(min(0.25, end - self.clock()))
