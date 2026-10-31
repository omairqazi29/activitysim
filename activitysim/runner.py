"""Drive the editor and the signed-out Chrome with real pointer and keyboard events.

A session checks out a public repository and opens it in one fullscreen
Visual Studio Code window first. Later folders reuse that window. Later
chapters are mostly that editor, then GitHub, and about one
chapter in ten is a benchmark site. AppleScript and the debugger only open
windows and read pages. The clicks and keystrokes are real input. A normal
run prints one line when the first window opens. Errors are printed on their own.
"""

from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from activitysim.cdp import CdpError
from activitysim.editor import find_token
from activitysim.geom import (
    away_from_hot_corners,
    editor_point,
    in_editor_body,
    pointer_moved,
    user_left_work,
)
from activitysim.input import can_type
from activitysim.osa import OsaError
from activitysim.places import repo_name
from activitysim.search import (
    ADVANCED_URL,
    QueryDeck,
    in_readme_zone,
    in_result_zone,
    in_search_zone,
    is_advanced_url,
    readme_point,
    result_points,
    results_url,
    search_box_point,
)
from activitysim.sites import Catalog, allowed_url, page_label

TEXT_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"})

# Six editor chapters, three GitHub chapters, one benchmark site.
CHAPTER_DECK = (
    "code",
    "code",
    "code",
    "code",
    "code",
    "code",
    "github",
    "github",
    "github",
    "site",
)
SITE_PAGES = 3


def _in_window(point: tuple[float, float], bounds: tuple[float, float, float, float]) -> bool:
    x, y = point
    left, top, right, bottom = bounds
    return (left + 8) <= x <= (right - 8) and (top + 8) <= y <= (bottom - 8)


@dataclass
class Config:
    min_gap: float = 0.45
    max_gap: float = 1.6
    pause: float = 30
    max_tabs: int = 5
    duration: float = 0
    startup_delay: float = 0.3
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
        find_link=None,
        find_source=None,
        editor=None,
        work=None,
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
        self.find_link = find_link
        self.find_source = find_source
        self.editor = editor
        self.work = work
        self.queries = QueryDeck(self.rng)
        self.catalog = Catalog(self.rng)
        self.history: list[str] = []
        self.actions: list[str] = []
        self.can_point = False
        self.can_keys = False
        self._last_pos: tuple[float, float] | None = None
        self._last_front: str | None = None
        self._announced = False
        self._started = False
        self._kinds: list[str] = []
        self.mode = "search"
        self.points: list[tuple[float, float]] = []
        self.pi = 0
        self.per_query = 4
        self.reads_left = 0
        self.results_url = ""
        self.tour_urls: list[str] = []
        self.tour_i = 0
        self.tour_phase = "open"
        self.tour_reads = 0
        self.repo_urls: list[str] = []
        self.repo_i = 0
        self.repo_phase = "open"
        self.repo_reads = 0
        self._sourced = False
        self._driving: str | None = None
        self._github_repo = True
        self.code_files: list[str] = []
        self.code_i = 0
        self.code_phase = "open"
        self.code_reads = 0

    def log(self, message: str, *, error: bool = False) -> None:
        self.history.append(message)
        if self.config.dry_run:
            print(f"t+{self.clock():6.1f}s  {message}", flush=True)
            return
        if error:
            print(message, file=sys.stderr, flush=True)

    def _announce(self) -> None:
        if self.config.dry_run or self._announced:
            return
        self._announced = True
        print("launching dev browser", flush=True)

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
        self._last_front = self.frontmost()
        self.log("signed-out Chrome only. your X and GitHub logins are not in it")
        self._begin_chapter()
        self._last_pos = self.pointer.position()
        while not self._expired(start):
            self._sleep(self._gap())
            if self._expired(start):
                break
            if self._should_pause():
                self._pause(start)
                continue
            try:
                self._step()
            except (OsaError, OSError, ValueError, CdpError) as exc:
                self.log(f"step failed: {exc}", error=True)
            self._note_front()
            self._remember()
        self.log("duration elapsed")

    def _banner(self) -> None:
        self.log(f"seed {self.config.seed}")
        if self.config.dry_run:
            self.log("dry run, nothing will move and Chrome will not open")
        else:
            self.log("the editor first, then GitHub, and a benchmark site about one time in ten")
        self.log("Ctrl+C stops the simulator. power settings are not changed")
        if not self.config.dry_run:
            if sys.platform == "win32":
                self.log(
                    "--caffeinate keeps the machine awake until exit. "
                    "power settings are not changed"
                )
            else:
                self.log("keep the machine awake separately: sudo pmset -a disablesleep 1")

    def _prepare_input(self) -> bool:
        self.can_point = bool(self.pointer.available())
        if not self.can_point:
            if sys.platform == "win32":
                self.log("the pointer could not be moved", error=True)
                self.log(
                    "this process must be the interactive user, not Administrator",
                    error=True,
                )
            else:
                self.log(
                    "Accessibility is required for real mouse and keyboard clicks",
                    error=True,
                )
            ask = getattr(self.pointer, "request_access", None)
            if ask and ask():
                self.can_point = bool(self.pointer.available())
        if not self.can_point:
            if sys.platform == "win32":
                self.log("stopped. the pointer could not be moved", error=True)
            else:
                self.log(
                    "stopped. no Accessibility permission, so clicks would not happen",
                    error=True,
                )
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
        if self.mode == "tour":
            if self._tour_continues():
                return
        elif self.mode == "repo":
            if self._repo_continues():
                return
        elif self.mode == "code":
            if self._code_continues():
                return
        elif self.mode == "results" and self.pi < min(len(self.points), self.per_query):
            self._open_result()
            return
        self._begin_chapter()

    def _begin_chapter(self) -> None:
        if not self._started:
            self._started = True
            if self._code_available():
                self._start_code()
            else:
                self._start_github()
            return
        kind = self._next_kind()
        if kind in {"site", "tour"}:
            self._start_tour()
        elif kind == "repo":
            self._start_repo()
        elif kind == "search":
            self._search()
        elif kind == "code":
            self._start_code()
        else:
            self._start_github()

    def _next_kind(self) -> str:
        if not self._kinds:
            kinds = list(CHAPTER_DECK)
            self.rng.shuffle(kinds)
            self._kinds = kinds
        return self._kinds.pop()

    def _code_available(self) -> bool:
        if self.editor is None or self.work is None:
            return False
        try:
            return bool(self.editor.installed()) and bool(self.work.ready())
        except (OSError, OsaError, ValueError, AttributeError):
            return False

    def _start_github(self) -> None:
        self.log("chapter github")
        if self._github_repo:
            self._github_repo = False
            self._start_repo()
            return
        self._github_repo = True
        self._search()

    def _start_code(self) -> None:
        if not self._code_available():
            self._start_github()
            return
        try:
            repo = self.work.next_repo()
            folder = self.work.checkout(repo)
        except (OSError, OsaError, ValueError, AttributeError):
            self._start_github()
            return
        if not folder:
            self.log(f"skip checkout {repo}")
            self._start_github()
            return
        try:
            found = self.work.files(folder) or []
        except (OSError, OsaError, ValueError, AttributeError):
            found = []
        files = [
            path
            for path in found
            if isinstance(path, str) and can_type(path) and ".." not in path.split("/")
        ]
        if not files:
            self.log(f"skip checkout {repo}")
            self._start_github()
            return
        self.rng.shuffle(files)
        count = min(len(files), self.rng.randint(4, 6))
        self.code_files = files[:count]
        self.code_i = 0
        self.code_phase = "open"
        self.code_reads = 0
        self.mode = "code"
        try:
            self.editor.open_folder(str(folder))
        except (OSError, OsaError, ValueError) as exc:
            self.log(f"step failed: {exc}", error=True)
            self._start_github()
            return
        self._announce()
        self.actions.append("code")
        self.log(f"checkout {repo}")
        self.log(f"chapter code {repo}")
        self._code_continues()

    def _code_continues(self) -> bool:
        if self.code_phase == "read" and self.code_reads > 0:
            self._read_code()
            self.code_reads -= 1
            if self.code_reads == 0:
                self.code_i += 1
                self.code_phase = "open"
            return True
        if self.code_i >= len(self.code_files):
            return False
        self._quick_open(self.code_files[self.code_i])
        self.code_reads = self.rng.randint(2, 3)
        self.code_phase = "read"
        return True

    def _quick_open(self, relative: str) -> None:
        if not can_type(relative) or ".." in relative.split("/") or relative.startswith("/"):
            return
        if not (self.can_keys and self._arm_editor()):
            self.log("skipped keys, the editor is not in front")
            return
        # Chat keeps focus across the folder switch and would take this path.
        self._close_editor_chat()
        if not self._editor_is_front():
            self.log("skipped keys, the editor is not in front")
            return
        self.pointer.hotkey("p", ("command",))
        self._sleep(0.12)
        if not self._editor_is_front():
            self.log("skipped keys, the editor is not in front")
            return
        self.pointer.type_text(relative)
        # Return is the save sheet's default button. Don't Save first.
        if self._clear_save_prompt():
            self._sleep(0.15)
        if not self._editor_is_front():
            self.log("skipped keys, the editor is not in front")
            return
        self.pointer.hotkey("return", ())
        self.actions.append("edit")
        self.log(f"open {relative}")

    def _read_code(self) -> None:
        self.actions.append("read")
        if not self._arm_editor():
            self.log("skipped keys, the editor is not in front")
            return
        bounds = self._editor_bounds()
        if bounds is not None and self.can_point:
            point = editor_point(bounds, self.rng)
            displays = self._displays()
            if (
                point is not None
                and in_editor_body(point, bounds)
                and (not displays or away_from_hot_corners(point, displays))
            ):
                self.pointer.move_to(*point)
                for _ in range(2):
                    if not self._editor_is_front():
                        self.log("skipped keys, the editor is not in front")
                        return
                    self.pointer.scroll(self._scroll_delta())
                    self._sleep(self.rng.uniform(0.03, 0.08))
                self.log("scroll the file")
                if self.rng.random() < 0.45:
                    self._press_in_editor(point, "inside the file")
        if not (self.can_keys and self._editor_is_front()):
            return
        key = self.rng.choice(("down", "down", "down", "pagedown", "pagedown", "up"))
        count = self.rng.randint(4, 8)
        for _ in range(count):
            if not self._editor_is_front():
                self.log("skipped keys, the editor is not in front")
                return
            self.pointer.hotkey(key, ())
            self._sleep(self.rng.uniform(0.03, 0.08))
        self.log(f"key {key} x{count}")
        if self.rng.random() < 0.35 and self.code_i < len(self.code_files):
            self._find_in_file(self.code_files[self.code_i])

    def _find_in_file(self, relative: str) -> None:
        token = find_token(relative)
        if not token or not self._editor_is_front():
            return
        self._close_editor_chat()
        if self._clear_save_prompt():
            self._sleep(0.15)
        if not self._editor_is_front():
            return
        self.pointer.hotkey("f", ("command",))
        self._sleep(0.08)
        if not self._editor_is_front():
            self.log("skipped keys, the editor is not in front")
            return
        self.pointer.type_text(token)
        self.pointer.hotkey("escape", ())
        self.log(f"find {token}")

    def _press_in_editor(self, point: tuple[float, float], label: str) -> bool:
        if not self.can_point or not self._editor_is_front():
            return False
        self.pointer.move_to(point[0], point[1])
        self._sleep(self.rng.uniform(0.05, 0.15))
        if not self._editor_is_front():
            self.log("skipped click, the editor is not in front")
            return False
        self.pointer.click(point[0], point[1])
        self.log(f"click {label}")
        return True

    def _arm_editor(self) -> bool:
        if self.editor is None:
            return False
        try:
            self.editor.activate()
        except (OSError, OsaError, ValueError):
            return False
        self._clear_save_prompt()
        if not self._editor_is_front():
            return False
        self._mark_driving()
        return True

    def _close_editor_chat(self) -> None:
        """Hide Chat and Claude Code before a path or a find token is typed."""

        editor = self.editor
        if editor is None:
            return
        close = getattr(editor, "close_chat_panel", None)
        if not callable(close):
            return
        try:
            close()
        except (OSError, OsaError, ValueError, TypeError):
            return

    def _clear_save_prompt(self) -> bool:
        """Don't Save, then restore full screen if that sheet left it.

        Opened files are read-only, so this is the leftover from an older
        session. Save is never clicked: Return would confirm it.
        """

        editor = self.editor
        if editor is None:
            return False
        dismiss = getattr(editor, "dismiss_save_prompt", None)
        if not callable(dismiss):
            return False
        try:
            dismissed = bool(dismiss())
        except (OSError, OsaError, ValueError):
            return False
        if not dismissed:
            return False
        enter = getattr(editor, "enter_fullscreen", None)
        if callable(enter):
            try:
                enter()
            except (OSError, OsaError, ValueError, TypeError):
                return True
        return True

    def _editor_is_front(self) -> bool:
        if self.editor is None:
            return False
        check = getattr(self.editor, "is_front", None)
        if not callable(check):
            return False
        try:
            return bool(check())
        except (OSError, OsaError, ValueError):
            return False

    def _editor_bounds(self):
        if self.editor is None:
            return None
        try:
            return self.editor.bounds()
        except (OsaError, OSError, ValueError):
            return None

    def _mark_driving(self) -> None:
        try:
            name = self.frontmost()
        except (OSError, ValueError):
            return
        if name:
            self._driving = name
            self._last_front = name

    def _start_tour(self) -> None:
        if self.places is not None:
            self.places.leave_page()
            self.places.plan_repo(None)
        self.tour_urls = self.catalog.tour()[:SITE_PAGES]
        self.tour_i = 0
        self.tour_phase = "open"
        self.tour_reads = 0
        self.mode = "tour"
        if not self.tour_urls:
            self._search()
            return
        self.log("chapter site")
        self._tour_continues()

    def _tour_continues(self) -> bool:
        if self.tour_phase == "read" and self.tour_reads > 0:
            self._read()
            self.tour_reads -= 1
            if self.tour_reads == 0:
                self.tour_i += 1
                self.tour_phase = "open"
            return True
        if self.tour_i >= len(self.tour_urls):
            return False
        self._visit(self.tour_urls[self.tour_i])
        self.tour_reads = self.rng.randint(2, 3)
        self.tour_phase = "read"
        return True

    def _start_repo(self) -> None:
        if self.places is None:
            self._search()
            return
        self.repo_urls = self.places.repo_burst()
        self.repo_i = 0
        self.repo_phase = "open"
        self.repo_reads = 0
        self._sourced = False
        self.mode = "repo"
        self._repo_continues()

    def _repo_continues(self) -> bool:
        if self.repo_phase == "read" and self.repo_reads > 0:
            self._read()
            self.repo_reads -= 1
            if self.repo_reads == 0:
                self.repo_i += 1
                self.repo_phase = "open"
            return True
        if self.repo_i >= len(self.repo_urls):
            return False
        self._visit(self.repo_urls[self.repo_i])
        self.repo_reads = self.rng.randint(2, 3)
        self.repo_phase = "read"
        self._queue_source()
        return True

    def _visit(self, url: str) -> None:
        if not allowed_url(url):
            raise ValueError(f"refusing to open {url!r}")
        if self.places is not None:
            self.places.leave_page()
        label = page_label(url)
        opened = False
        if not self._window_open():
            self._open_fresh(url)
            opened = True
        else:
            point = self._link_point(url)
            if point is not None and self._click(point, label):
                self._remember_navigation(url)
                opened = True
            elif self.can_keys and can_type(url):
                self._type_url(url)
                opened = True
            else:
                self.browser.navigate(url)
                self._remember_navigation(url)
                opened = True
        if self.places is not None:
            self.places.saw_repo(url)
        if opened:
            self.actions.append("visit")
            self.log(f"open {url}")
        self._settle()

    def _link_point(self, url: str) -> tuple[float, float] | None:
        if self.find_link is None:
            return None
        try:
            found = self.find_link(url)
        except (OSError, OsaError, ValueError, TypeError, RuntimeError):
            return None
        if not found or len(found) != 2:
            return None
        try:
            point = (float(found[0]), float(found[1]))
        except (TypeError, ValueError):
            return None
        bounds = self._bounds()
        if bounds is None or not _in_window(point, bounds):
            return None
        displays = self._displays()
        if displays and not away_from_hot_corners(point, displays):
            return None
        return point

    def _displays(self) -> list[tuple[float, float, float, float]]:
        getter = getattr(self.pointer, "displays", None)
        if not callable(getter):
            return []
        try:
            found = getter() or []
        except (OSError, ValueError, TypeError):
            return []
        return list(found)

    def _search(self) -> None:
        if self.places is not None:
            self.places.leave_page()
            self.places.plan_repo(None)
        query = self.queries.next()
        self.per_query = self.rng.randint(3, 5)
        self.log(f"github advanced search: {query}")
        self.actions.append("search")
        if not self._window_open():
            self._open_fresh(ADVANCED_URL)
        elif self.can_keys and not self._on_advanced():
            self._type_url(ADVANCED_URL)
            self._settle()
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
        if (
            clicked
            and self._text_focused()
            and not self._popup_blocks_keys()
            and self.can_keys
            and can_type(query)
            and self._our_window_is_front()
        ):
            self.pointer.hotkey("a", ("command",))
            self.pointer.type_text(query)
            self.pointer.hotkey("return", ())
            target = results_url(query)
            self.results_url = target
            self._remember_navigation(target)
            self.log(f"typed {query}")
            self._settle()
            return
        target = results_url(query)
        self.results_url = target
        self.browser.navigate(target)
        self._remember_navigation(target)
        self.log(f"open {target}")
        self._settle()

    def _open_result(self) -> None:
        bounds = self._bounds()
        point = self.points[self.pi]
        self.pi += 1
        if bounds is None or not in_result_zone(point, bounds, self._pixel_scale()):
            self.log("skipped a result outside the list column")
            return
        self.actions.append("result")
        if not self._click(point, f"result {self.pi}"):
            return
        self.reads_left = self.rng.randint(2, 4)
        self.mode = "item"

    def _read(self) -> None:
        if self.places is not None and self.places.saw_repo(self._current_url()):
            self.reads_left = 0
            self.log("skip a repo already open")
            return
        self.actions.append("read")
        if not self._scan():
            return
        if self.rng.random() < 0.45:
            self._click_body()

    def _scan(self) -> bool:
        """Scroll down the page and page through it. False if Chrome is not in front."""

        bounds = self._bounds()
        scale = self._pixel_scale()
        if bounds is not None and self.can_point:
            hover = readme_point(bounds, self.rng, scale)
            if hover is not None and in_readme_zone(hover, bounds, scale):
                if not self._arm():
                    self.log("skipped keys, signed-out Chrome is not in front")
                    return False
                self.pointer.move_to(*hover)
                for _ in range(2):
                    if not self._our_window_is_front():
                        self.log("skipped keys, signed-out Chrome is not in front")
                        return False
                    self.pointer.scroll(self._scroll_delta())
                    self._sleep(self.rng.uniform(0.03, 0.08))
                self.log("scroll the result")
        if not (self.can_keys and self._arm()):
            return self._our_window_is_front()
        key = self.rng.choice(("down", "down", "down", "pagedown", "pagedown", "up"))
        count = self.rng.randint(4, 8)
        for _ in range(count):
            if not self._our_window_is_front():
                self.log("skipped keys, signed-out Chrome is not in front")
                return False
            self.pointer.hotkey(key, ())
            self._sleep(self.rng.uniform(0.03, 0.08))
        self.log(f"key {key} x{count}")
        return True

    def _scroll_delta(self) -> int:
        if self.rng.random() < 0.12:
            return self.rng.choice((-2, -1))
        return self.rng.choice((4, 5, 6, 7))

    def _click_body(self) -> bool:
        bounds = self._bounds()
        if bounds is None or not self.can_point:
            return False
        scale = self._pixel_scale()
        point = readme_point(bounds, self.rng, scale)
        if point is None or not in_readme_zone(point, bounds, scale):
            return False
        return self._click(point, "inside the result")

    def _back(self) -> None:
        if self.places is not None:
            self.places.leave_page()
        self.actions.append("back")
        if self.can_keys and self._arm():
            self.pointer.hotkey("left", ("command",))
            self.log("back to results")
        else:
            self.browser.back()
            self.log("back to results")
        self._settle()
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
        scale = self._pixel_scale()
        found = [point for point in found if in_result_zone(point, bounds, scale)]
        if len(found) < 2:
            found = result_points(bounds, scale=scale)
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
            scale = self._pixel_scale()
            if point and in_search_zone(tuple(point), bounds, scale):
                return tuple(point)
        scale = self._pixel_scale()
        point = search_box_point(bounds, scale)
        if point and in_search_zone(point, bounds, scale):
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

    def _pixel_scale(self) -> float:
        """Physical pixels per CSS pixel. macOS and fakes stay at 1."""

        getter = getattr(self.browser, "pixel_scale", None)
        if getter is None:
            return 1.0
        try:
            value = getter() if callable(getter) else float(getter)
            value = float(value)
        except (TypeError, ValueError, OSError, RuntimeError):
            return 1.0
        if not value > 0:
            return 1.0
        return value

    def _popup_blocks_keys(self) -> bool:
        """True when a Chrome dialog, not the page window, has the OS focus."""

        check = getattr(self.browser, "main_window_is_front", None)
        if not callable(check):
            return False
        try:
            return not bool(check())
        except (OSError, OsaError, ValueError, RuntimeError):
            return True

    def _omnibox_is_safe(self) -> bool:
        """Enter may go to the omnibox, not to a dialog's default button."""

        if self._popup_blocks_keys():
            return False
        if sys.platform == "win32" and callable(getattr(self.browser, "main_window_is_front", None)):
            return True
        return self._text_focused()

    def _window_open(self) -> bool:
        return bool(getattr(self.browser, "window_id", None))

    def _open_fresh(self, url: str) -> None:
        self.browser.open_window(url)
        self._announce()
        self.log(f"opened Chrome at {url}")
        self._remember_navigation(url)

    def _type_url(self, url: str) -> None:
        if not self._arm():
            self.browser.navigate(url)
            self._remember_navigation(url)
            return
        self.pointer.hotkey("l", ("command",))
        self._sleep(0.12)
        if not self._our_window_is_front() or not self._omnibox_is_safe():
            self.log("skipped typing, signed-out Chrome is not in front")
            self.browser.navigate(url)
            self._remember_navigation(url)
            return
        self.pointer.type_text(url)
        self.pointer.hotkey("return", ())
        self._remember_navigation(url)

    def _click(self, point: tuple[float, float], label: str) -> bool:
        if not self.can_point or not self._arm():
            return False
        self.pointer.move_to(point[0], point[1])
        self._sleep(self.rng.uniform(0.05, 0.15))
        if not self._our_window_is_front():
            self.log("skipped click, signed-out Chrome is not in front")
            return False
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

    def _our_window_is_front(self) -> bool:
        check = getattr(self.browser, "is_front", None)
        if check is None:
            return self.frontmost() == "Google Chrome"
        try:
            return bool(check())
        except (OsaError, OSError, ValueError):
            return False

    def _other_chrome_is_front(self) -> bool:
        """The signed-in Chrome is in front. Both processes are named Google Chrome."""

        if not getattr(self.browser, "window_id", None):
            return False
        check = getattr(self.browser, "is_front", None)
        if check is None or self.frontmost() != "Google Chrome":
            return False
        return not self._our_window_is_front()

    def _arm(self) -> bool:
        self._activate()
        if not self._our_window_is_front():
            return False
        self._mark_driving()
        return True

    def _activate(self) -> None:
        self.browser.activate()

    def _bounds(self):
        try:
            return self.browser.bounds()
        except (OsaError, OSError, ValueError):
            return None

    def _note_front(self) -> None:
        front = self.frontmost()
        if front is None:
            return
        # A departure has to stay visible until the next pause check. Recording
        # the user's app here would hide the switch.
        if self._driving and front != self._driving:
            return
        self._last_front = front

    def _remember(self) -> None:
        self._last_pos = self.pointer.position()

    def _should_pause(self) -> bool:
        current = self.pointer.position()
        moved = pointer_moved(self._last_pos, current, self.config.tolerance)
        front = self.frontmost()
        left = user_left_work(self._last_front, front, self._driving)
        self._last_pos = current
        if front is not None:
            self._last_front = front
        if self.config.pause <= 0:
            return False
        return moved or left or self._other_chrome_is_front()

    def _pause(self, start: float) -> None:
        self.log(f"paused {self.config.pause:.0f}s because you are using the machine")
        end = self.clock() + self.config.pause
        while not self._expired(start):
            remaining = end - self.clock()
            if remaining <= 0:
                break
            self._sleep(min(5, remaining))
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
        return moved or switched or self._other_chrome_is_front()

    def _expired(self, start: float) -> bool:
        return bool(self.config.duration) and (self.clock() - start) >= self.config.duration

    def _gap(self) -> float:
        """Quiet time before the next action. Reading the open page waits less."""

        low = self.config.min_gap
        high = self.config.max_gap
        if self._reading_now():
            low *= 0.35
            high = max(low, self.config.max_gap * 0.4)
        return self.rng.uniform(low, high)

    def _reading_now(self) -> bool:
        if self.mode == "item" and self.reads_left > 0:
            return True
        if self.mode == "tour" and self.tour_phase == "read" and self.tour_reads > 0:
            return True
        if self.mode == "repo" and self.repo_phase == "read" and self.repo_reads > 0:
            return True
        if self.mode == "code" and self.code_phase == "read" and self.code_reads > 0:
            return True
        return self.mode == "results" and self.pi < min(len(self.points), self.per_query)

    def _settle(self) -> None:
        """Wait until the navigation has committed, then stop once the page is ready."""

        self._sleep(self.rng.uniform(0.25, 0.4))
        deadline = self.clock() + 1.8
        while self.clock() < deadline:
            state = self._ready_state()
            if state is None or state == "complete":
                break
            self._sleep(0.1)
        self._sleep(self.rng.uniform(0.05, 0.12))

    def _ready_state(self) -> str | None:
        evaluate = getattr(self.browser, "evaluate", None)
        if not callable(evaluate):
            return None
        try:
            state = evaluate("document.readyState")
        except (OSError, RuntimeError, ValueError, TypeError):
            return "loading"
        if not isinstance(state, str):
            return "loading"
        return state

    def _queue_source(self) -> None:
        """Open one source file from the repo root before the review tabs."""

        if self._sourced or self.find_source is None or self.mode != "repo":
            return
        current = self._current_url()
        name = repo_name(current)
        if not name:
            return
        if urlsplit(current).path.strip("/").lower() != name.lower():
            return
        self._sourced = True
        try:
            found = self.find_source()
        except (OSError, OsaError, ValueError, TypeError, RuntimeError):
            return
        if not isinstance(found, str):
            return
        url = found.split("#", 1)[0]
        if not self._repo_file(url, name) or url in self.repo_urls:
            return
        self.repo_urls.insert(self.repo_i + 1, url)
        self.log(f"source {url}")

    def _repo_file(self, url: str, name: str) -> bool:
        if not allowed_url(url):
            return False
        other = repo_name(url)
        if other is None or other.lower() != name.lower():
            return False
        pieces = [part for part in urlsplit(url).path.split("/") if part]
        return len(pieces) >= 4 and pieces[2] == "blob"

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        end = self.clock() + seconds
        while True:
            remaining = end - self.clock()
            if remaining <= 0:
                return
            self.sleep(min(0.25, remaining))
