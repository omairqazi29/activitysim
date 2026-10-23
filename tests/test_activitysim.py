"""Safety and behavior tests. These do not open Chrome or post input events."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from io import StringIO
from pathlib import Path

from activitysim.ax import chrome_pid, set_chrome_pid
from activitysim.cdp import CdpError, call
from activitysim.chrome import (
    LOCKED_PROFILE,
    REAL_PROFILE,
    Chrome,
    ChromeError,
    _command_has_profile,
    _stop_existing,
    assert_locked_profile,
    chrome_candidates,
    host_blocked,
    launch_command,
    locked_profile_path,
    pid_literal,
    real_profile_path,
    script_activate,
    script_bounds,
    script_front_pid,
    script_fullscreen,
    scrub_signins,
)
from activitysim.cli import _doctor_windows, main, parse_duration, root_error, root_message, validate_run
from activitysim.fakes import FakeBrowser, FakeClock, FakePointer
from activitysim.geom import (
    away_from_hot_corners,
    bezier_path,
    hover_point,
    in_tab_strip,
    next_gap,
    pointer_moved,
    tab_strip_point,
    user_left_chrome,
)
from activitysim.input import (
    CHAR_KEYS,
    SAFE_HOTKEYS,
    MacInput,
    accessibility_prompt_dictionary,
    assert_typable,
    can_type,
)
from activitysim.osa import OsaError, as_text
from activitysim.browse import locate_link
from activitysim.places import REPOS, Places, load_url_file, safe_url
from activitysim.sites import ASTRA, DEEPSWE, SITES, Catalog, allowed_url
from activitysim.runner import Config, Runner
from activitysim.winchrome import (
    AWAKE_FLAGS,
    ES_CONTINUOUS,
    ES_DISPLAY_REQUIRED,
    ES_SYSTEM_REQUIRED,
    SW_RESTORE,
    WM_CLOSE,
    app_name_for_image,
    basename,
    clear_process_cache,
    command_line_for_pid,
    decode_process_output,
    ensure_process_dpi,
    parse_locked_chrome_pids,
    process_list_argv,
    read_process_list,
)
from activitysim.wininput import (
    ABSOLUTE_FLAGS,
    INPUT,
    KEYBDINPUT,
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_VIRTUALDESK,
    MOUSEINPUT,
    NAMED_VK,
    SCROLL_FLAGS,
    VK_CONTROL,
    VK_MENU,
    WinInput,
    absolute_coord,
    absolute_point,
    chord,
    virtual_key,
    wheel_delta,
    windows_keys_cover_allowlist,
)
from activitysim.winpage import (
    FOCUSED_EXPRESSION,
    RESULTS_EXPRESSION,
    SEARCH_EXPRESSION,
    css_center_to_screen,
    css_point_in_window,
    focused_role_from,
    result_clicks_from,
    result_points_from_payload,
    role_from_active,
    search_field_from,
    search_point,
)
from activitysim.search import (
    QUERIES,
    QueryDeck,
    accept_result,
    bad_label,
    content_frame,
    in_readme_zone,
    in_result_zone,
    in_search_zone,
    is_result_href,
    result_points,
    results_url,
    search_box_point,
)


def make_runner(**overrides) -> tuple[Runner, FakeBrowser, FakePointer, FakeClock]:
    clock = FakeClock()
    browser = FakeBrowser()
    pointer = FakePointer(ready=overrides.pop("ready", True))
    seed = overrides.pop("seed", 1)
    rng = random.Random(seed)
    config = Config(
        min_gap=1,
        max_gap=1,
        pause=overrides.pop("pause", 30),
        max_tabs=overrides.pop("max_tabs", 5),
        duration=overrides.pop("duration", 20),
        startup_delay=overrides.pop("startup_delay", 0),
        mouse_only=overrides.pop("mouse_only", False),
        no_keyboard=overrides.pop("no_keyboard", False),
        dry_run=True,
        seed=seed,
    )
    places = Places(rng, overrides.pop("extra", []))
    front = overrides.pop("front", None)
    runner = Runner(
        config,
        browser,
        pointer,
        places,
        rng,
        frontmost=(lambda: pointer.front) if front is None else front,
        clock=clock.time,
        sleep=clock.sleep,
        focused_role=lambda: "AXTextField",
    )
    if overrides:
        raise TypeError(f"unexpected {sorted(overrides)}")
    return runner, browser, pointer, clock


class GeometryTests(unittest.TestCase):
    def test_tab_strip_stays_off_the_traffic_lights_and_corners(self) -> None:
        bounds = (0.0, 33.0, 1728.0, 1117.0)
        displays = [(0.0, 0.0, 1728.0, 1117.0)]
        rng = random.Random(0)
        for _ in range(200):
            point = tab_strip_point(bounds, displays, rng)
            self.assertIsNotNone(point)
            assert point is not None
            self.assertTrue(in_tab_strip(point, bounds))
            self.assertGreaterEqual(point[0], 100)
            self.assertLessEqual(point[0], 1728 - 90)
            self.assertTrue(away_from_hot_corners(point, displays))

    def test_hover_point_is_below_the_toolbar(self) -> None:
        bounds = (0.0, 33.0, 1728.0, 1117.0)
        displays = [(0.0, 0.0, 1728.0, 1117.0)]
        rng = random.Random(1)
        for _ in range(100):
            point = hover_point(bounds, displays, rng)
            self.assertIsNotNone(point)
            assert point is not None
            self.assertGreaterEqual(point[1], 33 + 160)
            self.assertLessEqual(point[1], 1117 - 100)
            self.assertTrue(away_from_hot_corners(point, displays))

    def test_narrow_window_is_not_clicked(self) -> None:
        self.assertIsNone(tab_strip_point((0, 0, 100, 100), [], random.Random(0)))

    def test_pointer_moved_and_leaving_chrome(self) -> None:
        self.assertFalse(pointer_moved((0, 0), (10, 10), tolerance=36))
        self.assertTrue(pointer_moved((0, 0), (40, 0), tolerance=36))
        self.assertFalse(pointer_moved(None, (1, 1)))
        self.assertFalse(user_left_chrome(None, "Code"))
        self.assertFalse(user_left_chrome("Google Chrome", "Google Chrome"))
        self.assertTrue(user_left_chrome("Google Chrome", "Code"))

    def test_bezier_ends_on_the_target(self) -> None:
        rng = random.Random(2)
        end = (800.0, 240.0)
        path = bezier_path((10.0, 10.0), end, rng)
        self.assertEqual(path[-1], end)
        self.assertGreater(len(path), 2)

    def test_gap_is_positive(self) -> None:
        rng = random.Random(3)
        for _ in range(50):
            gap = next_gap(rng, 6, 22)
            self.assertGreaterEqual(gap, 6)
            self.assertLessEqual(gap, 66)


class PlaceTests(unittest.TestCase):
    def test_builtin_urls_are_safe_and_typable(self) -> None:
        places = Places(random.Random(4), [])
        for _ in range(80):
            url = places.next()
            self.assertTrue(safe_url(url), url)
            self.assertTrue(url.startswith("https://"), url)
            self.assertTrue(can_type(url), url)

    def test_repo_burst_starts_at_the_repo_root(self) -> None:
        burst = Places(random.Random(5), [])._repo_burst()
        path = urllib.parse.urlsplit(burst[0]).path.strip("/")
        self.assertEqual(path.count("/"), 1)
        self.assertGreaterEqual(len(burst), 2)

    def test_rejects_dangerous_urls(self) -> None:
        self.assertFalse(safe_url("javascript:alert(1)"))
        self.assertFalse(safe_url("file:///etc/passwd"))
        self.assertFalse(safe_url("https://user:pass@example.com/a"))
        self.assertFalse(safe_url("https://example.com/a b"))
        with self.assertRaises(ValueError):
            launch_command(
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/tmp/activitysim-locked"),
                "javascript:alert(1)",
                9222,
            )

    def test_url_file_keeps_http_and_drops_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "urls.txt"
            path.write_text(
                "\n".join([
                    "# comment",
                    "javascript:alert(1)",
                    "https://example.com/a",
                    "http://example.com/b",
                    "file:///tmp/x",
                    "https://user:pw@example.com/secret",
                    "",
                ]),
                encoding="utf-8",
            )
            self.assertEqual(
                load_url_file(path),
                ["https://example.com/a", "http://example.com/b"],
            )

    def test_extra_urls_are_filtered(self) -> None:
        places = Places(random.Random(0), ["javascript:alert(1)", "https://example.com/a"])
        self.assertEqual(places.extra, ["https://example.com/a"])

    def test_repo_list_is_unique_and_typable(self) -> None:
        folded = [repo.lower() for repo in REPOS]
        self.assertEqual(len(folded), len(set(folded)))
        self.assertGreater(len(REPOS), 100)
        for repo in REPOS:
            url = f"https://github.com/{repo}"
            self.assertTrue(can_type(url), url)
            self.assertTrue(safe_url(url), url)

    def test_repo_bursts_do_not_repeat_until_the_list_is_used(self) -> None:
        places = Places(random.Random(9), [])
        names = []
        for _ in range(40):
            burst = places.repo_burst()
            name = urllib.parse.urlsplit(burst[0]).path.strip("/")
            self.assertEqual(name.count("/"), 1)
            self.assertGreaterEqual(len(burst), 2)
            names.append(name.lower())
        self.assertEqual(len(names), len(set(names)))

    def test_a_repeat_visit_is_recognized(self) -> None:
        places = Places(random.Random(1), [])
        first = places.repo_burst()[0]
        self.assertFalse(places.saw_repo(first))
        places.leave_page()
        places.plan_repo(None)
        self.assertTrue(places.saw_repo(first))


class LockedChromeTests(unittest.TestCase):
    def test_applescript_quoting(self) -> None:
        self.assertEqual(as_text('a"b\\c'), '"a\\"b\\\\c"')
        with self.assertRaises(ValueError):
            as_text("line\nbreak")

    def test_launch_is_a_separate_signed_out_chrome(self) -> None:
        binary = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        profile = Path("/tmp/activitysim-locked")
        command = launch_command(
            binary, profile, "https://github.com/search/advanced", 9333, system="darwin"
        )
        self.assertEqual(command[0], str(binary))
        self.assertIn(f"--user-data-dir={profile.resolve()}", command)
        self.assertIn("--disable-sync", command)
        self.assertIn("--use-mock-keychain", command)
        self.assertIn("--remote-debugging-port=9333", command)
        self.assertNotIn("--start-fullscreen", command)
        text = " ".join(command)
        self.assertNotIn("Application Support/Google/Chrome", text)
        self.assertIn("MAP x.com ~NOTFOUND", text)
        self.assertIn("MAP *.x.com ~NOTFOUND", text)
        self.assertIn("MAP twitter.com ~NOTFOUND", text)
        self.assertIn("MAP *.twitter.com ~NOTFOUND", text)
        self.assertNotIn("tell application", text)

    def test_signed_in_profile_and_x_are_refused(self) -> None:
        real = REAL_PROFILE
        with self.assertRaises(ValueError):
            assert_locked_profile(real)
        with self.assertRaises(ValueError):
            assert_locked_profile(real / "Default")
        with self.assertRaises(ValueError):
            launch_command(Path("/bin/chrome"), real, "https://example.com", 1)
        self.assertTrue(host_blocked("https://x.com/omairqazi29/lists"))
        self.assertTrue(host_blocked("https://mobile.twitter.com/home"))
        self.assertTrue(host_blocked("https://pbs.twimg.com/a.png"))
        self.assertFalse(host_blocked("https://github.com/search/advanced"))
        self.assertFalse(host_blocked("https://example.com"))
        self.assertFalse(host_blocked("https://notx.com"))
        with self.assertRaises(ValueError):
            launch_command(
                Path("/bin/chrome"),
                Path("/tmp/activitysim-locked"),
                "https://x.com/home",
                1,
            )

    def test_scrub_deletes_sessions_and_leaves_outside_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "locked"
            outside = Path(tmp) / "secret"
            outside.write_text("keep", encoding="utf-8")
            cookies = profile / "Default" / "Cookies"
            cookies.parent.mkdir(parents=True)
            cookies.write_text("session", encoding="utf-8")
            link = profile / "Default" / "Login Data"
            link.symlink_to(outside)
            scrub_signins(profile)
            self.assertFalse(cookies.exists())
            self.assertFalse(link.exists())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
            self.assertTrue((profile / "First Run").is_file())

    def test_accessibility_pid_never_guesses_a_chrome(self) -> None:
        set_chrome_pid(None)
        self.addCleanup(set_chrome_pid, None)
        self.assertIsNone(chrome_pid())
        set_chrome_pid(4321)
        self.assertEqual(chrome_pid(), 4321)
        with self.assertRaises(ValueError):
            set_chrome_pid(0)

    def test_window_scripts_name_a_pid_and_have_no_keystrokes(self) -> None:
        self.assertEqual(pid_literal(42), "42")
        with self.assertRaises(ValueError):
            pid_literal(True)  # type: ignore[arg-type]
        scripts = [
            script_activate(42),
            script_bounds(42),
            script_fullscreen(42),
            script_front_pid(),
        ]
        self.assertIn("unix id is 42", scripts[0])
        self.assertIn("Enter Full Screen", scripts[2])
        for script in scripts:
            self.assertNotIn("keystroke", script)
            self.assertNotIn("key code", script)
            self.assertNotIn('tell application "Google Chrome"', script)
        if shutil.which("osacompile") is None:
            return
        with tempfile.TemporaryDirectory() as tmp:
            for index, script in enumerate(scripts):
                source = Path(tmp) / f"{index}.applescript"
                output = Path(tmp) / f"{index}.scpt"
                source.write_text(script, encoding="utf-8")
                result = subprocess.run(
                    ["osacompile", "-o", str(output), str(source)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(output.exists())

    def test_source_never_addresses_chrome_by_application_name(self) -> None:
        root = Path(__file__).resolve().parents[1] / "activitysim"
        for path in root.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('tell application "Google Chrome"', text, path.name)
            self.assertNotIn('"-a", "Google Chrome"', text, path.name)

    def test_debugger_client_round_trip(self) -> None:
        payload = json.dumps({"id": 1, "result": {"result": {"value": "https://example.com"}}})
        frame = bytes([0x81, len(payload)]) + payload.encode()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        box: dict[str, bytes] = {}

        def serve() -> None:
            conn, _addr = server.accept()
            with conn:
                data = bytearray()
                while b"\r\n\r\n" not in data:
                    data.extend(conn.recv(4096))
                key = ""
                for line in data.split(b"\r\n\r\n", 1)[0].decode().split("\r\n"):
                    if line.lower().startswith("sec-websocket-key:"):
                        key = line.split(":", 1)[1].strip()
                accept = base64.b64encode(
                    hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
                ).decode()
                handshake = (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n"
                    "\r\n"
                ).encode()
                conn.sendall(handshake + frame)
                incoming = bytearray()
                while len(incoming) < 2:
                    incoming.extend(conn.recv(4096))
                length = incoming[1] & 0x7F
                need = 2 + 4 + length
                while len(incoming) < need:
                    incoming.extend(conn.recv(4096))
                mask = incoming[2:6]
                raw = incoming[6:6 + length]
                box["client"] = bytes(byte ^ mask[index % 4] for index, byte in enumerate(raw))

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            result = call(f"ws://127.0.0.1:{port}/devtools/page/ABC", "Runtime.evaluate")
        finally:
            thread.join(timeout=5)
            server.close()
        self.assertEqual(result["result"]["value"], "https://example.com")
        self.assertIn(b"Runtime.evaluate", box["client"])
        with self.assertRaises(CdpError):
            call("ws://example.com/devtools/page/ABC", "Runtime.evaluate")


class InputPolicyTests(unittest.TestCase):
    def test_hotkey_refuses_quit_close_and_space_without_posting(self) -> None:
        pointer = MacInput.__new__(MacInput)
        for key, mods in (("q", ("command",)), ("w", ("command",)), ("space", ())):
            with self.assertRaises(ValueError):
                pointer.hotkey(key, mods)
        with self.assertRaises(ValueError):
            pointer.hotkey("not-a-key", ())
        with self.assertRaises(ValueError):
            pointer.hotkey("1", ())

    def test_typing_allowlist(self) -> None:
        assert_typable("https://github.com/python/cpython/issues")
        assert_typable("language:python stars:>1000")
        assert_typable(results_url("language:python stars:>1000"))
        for text in ("", "javascript:alert(1)", "a" * 241, "line\nbreak"):
            with self.assertRaises(ValueError):
                assert_typable(text)

    def test_permission_dictionary_builds_without_prompting(self) -> None:
        self.assertTrue(accessibility_prompt_dictionary())


class SearchGeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bounds = (0.0, 33.0, 1400.0, 900.0)

    def test_queries_and_result_urls_are_typable(self) -> None:
        self.assertTrue(can_type("https://github.com/search/advanced"))
        for query in QUERIES:
            self.assertTrue(can_type(query), query)
            url = results_url(query)
            self.assertTrue(can_type(url), url)
            self.assertTrue(safe_url(url), url)
            self.assertNotIn(" ", url)

    def test_query_deck_does_not_repeat_a_query(self) -> None:
        deck = QueryDeck(random.Random(4))
        got = [deck.next() for _ in range(36)]
        self.assertEqual(len(got), len(set(got)))
        for query in got:
            self.assertTrue(query.startswith("language:"), query)
            self.assertTrue(can_type(query), query)
            self.assertTrue(can_type(results_url(query)), query)
            self.assertNotIn("stars:>1000", query)

    def test_benchmark_pages_are_safe_to_type(self) -> None:
        seen = []
        for site in SITES:
            for url in (site.home, *site.nav, *site.tasks, *site.traces):
                self.assertTrue(allowed_url(url), url)
                seen.append(url)
        for url in ASTRA:
            self.assertTrue(allowed_url(url), url)
        self.assertGreater(len(DEEPSWE.tasks), 100)
        self.assertTrue(DEEPSWE.traces)

    def test_nav_order_and_tasks_change_between_visits(self) -> None:
        orders = set()
        for seed in range(12):
            catalog = Catalog(random.Random(seed))
            urls = catalog.tour(DEEPSWE)
            nav = tuple(url for url in urls if url in DEEPSWE.nav)
            orders.add(nav)
            self.assertTrue(any("/trials/" in url for url in urls), urls)
        self.assertGreater(len(orders), 1)

        catalog = Catalog(random.Random(2))
        first = catalog.tour(DEEPSWE)
        second = catalog.tour(DEEPSWE)

        def tasks(urls: list[str]) -> set[str]:
            found = set()
            for url in urls:
                if "/tasks/" in url and not url.rstrip("/").endswith("/tasks"):
                    found.add(url)
            return found

        self.assertTrue(tasks(first))
        self.assertTrue(tasks(first).isdisjoint(tasks(second)))
        both = first + second + catalog.tour() + catalog.tour()
        self.assertTrue(any("gpt-6-astra" in url for url in both))

    def test_a_visible_link_becomes_a_window_point(self) -> None:
        class Page:
            def bounds(self):
                return (0.0, 100.0, 1000.0, 900.0)

            def evaluate(self, _expression):
                return {
                    "outerWidth": 1000,
                    "outerHeight": 800,
                    "innerWidth": 1000,
                    "innerHeight": 800,
                    "left": 100,
                    "top": 40,
                    "width": 80,
                    "height": 20,
                }

        point = locate_link(Page(), "https://deepswe.datacurve.ai/blog")
        self.assertEqual(point, (140.0, 150.0))
        self.assertIsNone(locate_link(Page(), "javascript:alert(1)"))

        class Empty(Page):
            def evaluate(self, _expression):
                return None

        self.assertIsNone(locate_link(Empty(), "https://deepswe.datacurve.ai/blog"))

    def test_result_points_avoid_the_star_column(self) -> None:
        points = result_points(self.bounds)
        self.assertGreaterEqual(len(points), 3)
        for point in points:
            self.assertTrue(in_result_zone(point, self.bounds), point)

    def test_search_box_is_in_the_header_band(self) -> None:
        point = search_box_point(self.bounds)
        self.assertIsNotNone(point)
        assert point is not None
        self.assertTrue(in_search_zone(point, self.bounds))

    def test_result_links_reject_star_and_filters(self) -> None:
        self.assertTrue(is_result_href("https://github.com/python/cpython"))
        self.assertFalse(is_result_href("https://github.com/topics/python"))
        self.assertFalse(is_result_href("https://github.com/python/cpython/stargazers"))
        self.assertTrue(bad_label("Star this repository"))
        good = (700.0, 400.0)
        self.assertTrue(
            accept_result("https://github.com/python/cpython", "python/cpython", good, self.bounds)
        )
        self.assertFalse(
            accept_result("https://github.com/python/cpython", "Star this repository", good, self.bounds)
        )
        star_side = (1300.0, 400.0)
        self.assertFalse(
            accept_result("https://github.com/python/cpython", "python/cpython", star_side, self.bounds)
        )

    def test_fullscreen_strip_is_not_the_page(self) -> None:
        # Measured from the signed-out Chrome: menu strips, then the page.
        strip = (0.0, 0.0, 1728.0, 33.0)
        popover = (680.0, 45.0, 1048.0, 95.0)
        page = (0.0, 121.0, 1728.0, 1117.0)
        self.assertEqual(content_frame([strip, popover, page]), page)
        self.assertIsNone(content_frame([strip, popover]))
        repo = (454.0, 338.0)
        star = (1286.0, 344.0)
        search = (945.0, 289.0)
        self.assertTrue(in_result_zone(repo, page), repo)
        self.assertFalse(in_result_zone(star, page), star)
        self.assertTrue(in_search_zone(search, page), search)
        self.assertGreaterEqual(len(result_points(page)), 3)


class RunnerTests(unittest.TestCase):
    def test_session_clicks_results_and_types_the_search(self) -> None:
        runner, browser, pointer, _clock = make_runner(duration=22, seed=12)
        with redirect_stdout(StringIO()):
            runner.run()
        self.assertTrue(browser.window_id)
        self.assertTrue(pointer.typed)
        for text in pointer.typed:
            self.assertNotIn("javascript:", text)
            self.assertTrue(can_type(text), text)
            if "://" in text:
                self.assertTrue(allowed_url(text), text)
            else:
                self.assertTrue(text.startswith("language:"), text)
        self.assertGreater(len(pointer.clicks), 0)
        self.assertIn("result", runner.actions)
        for point in pointer.clicks:
            self.assertTrue(
                in_search_zone(point, browser.bounds_value)
                or in_result_zone(point, browser.bounds_value)
                or in_readme_zone(point, browser.bounds_value),
                point,
            )
        for key, mods in pointer.keys:
            self.assertNotIn(key, {"q", "w", "space"})
            if key.isdigit() or key == "a":
                self.assertIn("command", mods)
        self.assertTrue(any("advanced search" in line for line in runner.history))

    def test_without_accessibility_does_not_pretend_to_run(self) -> None:
        runner, browser, pointer, _clock = make_runner(duration=18, ready=False, seed=4)
        with redirect_stdout(StringIO()):
            runner.run()
        self.assertTrue(pointer.asked)
        self.assertIsNone(browser.window_id)
        self.assertEqual(pointer.clicks, [])
        self.assertEqual(pointer.keys, [])
        self.assertTrue(any("Accessibility" in line for line in runner.history))

    def test_mouse_move_pauses_and_then_stops_for_the_duration(self) -> None:
        runner, _browser, pointer, clock = make_runner(
            duration=8, pause=30, startup_delay=0.5, seed=5
        )
        bumped = {"done": False}

        def sleep_and_bump(seconds: float) -> None:
            clock.sleep(seconds)
            if not bumped["done"] and clock.now >= 0.25:
                pointer.pos = (900.0, 900.0)
                bumped["done"] = True

        runner.sleep = sleep_and_bump
        with redirect_stdout(StringIO()):
            runner.run()
        text = "\n".join(runner.history)
        self.assertIn("paused", text)
        self.assertNotIn("still in use", text)
        self.assertIn("duration elapsed", text)

    def test_leaving_chrome_pauses_once(self) -> None:
        calls = {"n": 0}

        def front() -> str:
            calls["n"] += 1
            # Startup samples Chrome. The next loop sees that the user left.
            return "Google Chrome" if calls["n"] < 3 else "Code"

        runner, _browser, _pointer, _clock = make_runner(duration=6, pause=30, seed=6)
        runner.frontmost = front
        with redirect_stdout(StringIO()):
            runner.run()
        self.assertTrue(any(line.startswith("paused") for line in runner.history))

    def test_sleep_ignores_a_clock_that_jumps_past_the_deadline(self) -> None:
        runner, _browser, _pointer, clock = make_runner(duration=5, seed=1)
        naps: list[float] = []

        def jump_ahead(seconds: float) -> None:
            self.assertGreaterEqual(seconds, 0)
            naps.append(seconds)
            clock.now += seconds + 10

        runner.sleep = jump_ahead
        runner._sleep(1)
        self.assertEqual(naps, [0.25])

    def test_signed_in_chrome_is_not_clicked(self) -> None:
        runner, browser, pointer, _clock = make_runner(duration=6, pause=30, seed=6)
        browser.is_front = lambda: False  # type: ignore[method-assign]
        runner.frontmost = lambda: "Google Chrome"
        with redirect_stdout(StringIO()):
            runner.run()
        self.assertTrue(any(line.startswith("paused") for line in runner.history))
        self.assertEqual(pointer.clicks, [])
        self.assertEqual(pointer.typed, [])

    def test_real_run_prints_only_the_launch_line(self) -> None:
        runner, _browser, _pointer, _clock = make_runner(duration=8, seed=2)
        runner.config.dry_run = False
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            runner.run()
        self.assertEqual(stdout.getvalue(), "launching dev browser\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_a_failed_launch_does_not_print_the_launch_line(self) -> None:
        runner, browser, _pointer, _clock = make_runner(duration=8, seed=2)
        runner.config.dry_run = False
        browser.installed = lambda: False
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(OsaError):
                runner.run()
        self.assertNotIn("launching dev browser", stdout.getvalue())
        self.assertEqual(stdout.getvalue(), "")

        runner, _browser, _pointer, _clock = make_runner(duration=6, ready=False, seed=2)
        runner.config.dry_run = False
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            runner.run()
        self.assertNotIn("launching dev browser", stdout.getvalue())
        self.assertIn("Accessibility", stderr.getvalue())

    def test_later_chapters_open_benchmarks_and_fresh_repos(self) -> None:
        runner, browser, pointer, _clock = make_runner(duration=160, seed=3)
        runner._kinds = ["repo", "tour", "repo"]
        with redirect_stdout(StringIO()):
            runner.run()
        text = "\n".join(runner.history)
        hosts = (
            "deepswe.datacurve.ai",
            "programbench.com",
            "swebench.com",
            "tbench.ai",
            "livecodebench.github.io",
            "swe-bench-live.github.io",
            "swesmith.com",
        )
        self.assertTrue(any(host in text for host in hosts), text)
        repos = []
        for line in runner.history:
            if "github.com/" not in line or "/search" in line:
                continue
            path = urllib.parse.urlsplit(line.split(" ", 1)[-1]).path.strip("/")
            parts = path.split("/")
            if len(parts) >= 2:
                repos.append(f"{parts[0]}/{parts[1]}".lower())
        self.assertGreaterEqual(len(set(repos)), 2, repos)

        runner, browser, pointer, _clock = make_runner(duration=4, seed=1)
        runner.can_point = True
        runner.can_keys = True
        runner.find_link = lambda _url: (220.0, 240.0)
        with redirect_stdout(StringIO()):
            runner._visit("https://deepswe.datacurve.ai/blog")
        self.assertIn((220.0, 240.0), pointer.clicks)
        self.assertEqual(browser.current_url(), "https://deepswe.datacurve.ai/blog")
        self.assertTrue(any("click nav" in line for line in runner.history))

        runner, _browser, pointer, _clock = make_runner(duration=4, seed=1)
        runner.can_point = True
        runner.can_keys = True
        runner.find_link = lambda _url: (1.0, 1.0)
        with redirect_stdout(StringIO()):
            runner._visit("https://deepswe.datacurve.ai/run")
        self.assertNotIn((1.0, 1.0), pointer.clicks)
        self.assertIn("https://deepswe.datacurve.ai/run", pointer.typed)


class CliTests(unittest.TestCase):
    def test_duration_units(self) -> None:
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("45m"), 2700)
        self.assertEqual(parse_duration("30s"), 30)
        self.assertEqual(parse_duration("15"), 15)
        self.assertEqual(parse_duration("forever"), 0)
        with self.assertRaises(ValueError):
            parse_duration("h")

    def test_root_is_refused(self) -> None:
        self.assertIsNone(root_error(501))
        self.assertIn("sudo", root_error(0) or "")

    def test_invalid_gaps_are_rejected(self) -> None:
        parser = __import__("activitysim.cli", fromlist=["build_run_parser"]).build_run_parser()
        args = parser.parse_args(["--min-gap", "10", "--max-gap", "2"])
        with self.assertRaises(ValueError):
            validate_run(args)

    def test_dry_run_command_finishes(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = main([
                "run", "--dry-run", "--for", "12s", "--seed", "7",
                "--min-gap", "1", "--max-gap", "1",
            ])
        self.assertEqual(code, 0)
        self.assertIn("dry run", stdout.getvalue())
        self.assertIn("advanced search", stdout.getvalue())
        self.assertIn("click", stdout.getvalue())

    def test_help_and_bad_url_file(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(main(["--help"]), 0)
        self.assertIn("pmset", stdout.getvalue())
        self.assertIn("Windows", stdout.getvalue())
        stderr = StringIO()
        with redirect_stdout(StringIO()):
            from contextlib import redirect_stderr
            with redirect_stderr(stderr):
                code = main(["run", "--urls", "/tmp/does-not-exist-activitysim-urls.txt"])
        self.assertEqual(code, 2)
        self.assertIn("not found", stderr.getvalue())


class WindowsLogicTests(unittest.TestCase):
    def test_absolute_coords_use_a_negative_origin_and_the_far_edge(self) -> None:
        self.assertEqual(absolute_coord(-1920, -1920, 3840), 0)
        self.assertEqual(absolute_coord(-1920 + 3839, -1920, 3840), 65535)
        self.assertEqual(absolute_coord(0, 0, 1920), 0)
        self.assertEqual(absolute_coord(1919, 0, 1920), 65535)
        self.assertEqual(absolute_coord(-5000, -1920, 3840), 0)
        self.assertEqual(absolute_coord(99999, 0, 1920), 65535)
        self.assertEqual(absolute_coord(1, 0, 3), round(65535 / 2))
        self.assertEqual(
            absolute_point(-1920, -1080, -1920, -1080, 3840, 2160),
            (0, 0),
        )
        self.assertEqual(
            absolute_point(-1920 + 3839, -1080 + 2159, -1920, -1080, 3840, 2160),
            (65535, 65535),
        )

    def test_sendinput_extra_info_is_pointer_sized(self) -> None:
        mouse = {name: kind for name, kind in MOUSEINPUT._fields_}
        keys = {name: kind for name, kind in KEYBDINPUT._fields_}
        self.assertIs(mouse["dwExtraInfo"], ctypes.c_size_t)
        self.assertIs(keys["dwExtraInfo"], ctypes.c_size_t)
        self.assertNotEqual(mouse["dwExtraInfo"], ctypes.c_void_p)
        if ctypes.sizeof(ctypes.c_size_t) == 8:
            self.assertEqual(ctypes.sizeof(MOUSEINPUT), 32)
            self.assertEqual(ctypes.sizeof(INPUT), 40)
        self.assertEqual(absolute_coord(0, 0, 1), 0)

    def test_scroll_wheel_sign_and_flags(self) -> None:
        self.assertEqual(wheel_delta(1), -120)
        self.assertEqual(wheel_delta(3), -360)
        self.assertEqual(wheel_delta(-2), 240)
        self.assertEqual(wheel_delta(0), 0)
        self.assertEqual(SCROLL_FLAGS & MOUSEEVENTF_MOVE, 0)
        self.assertEqual(SCROLL_FLAGS & MOUSEEVENTF_ABSOLUTE, 0)
        self.assertEqual(SCROLL_FLAGS & MOUSEEVENTF_VIRTUALDESK, 0)
        self.assertTrue(ABSOLUTE_FLAGS & MOUSEEVENTF_VIRTUALDESK)
        self.assertTrue(ABSOLUTE_FLAGS & MOUSEEVENTF_ABSOLUTE)

    def test_command_chords_map_to_ctrl_or_alt(self) -> None:
        self.assertEqual(chord("l", ("command",)), [(VK_CONTROL, True), (0x4C, False)])
        self.assertEqual(chord("a", ("command",)), [(VK_CONTROL, True), (0x41, False)])
        self.assertEqual(chord("left", ("command",)), [(VK_MENU, True), (0x25, False)])
        self.assertEqual(chord("1", ("command",)), [(VK_CONTROL, True), (0x31, False)])
        self.assertEqual(chord("right", ("command",)), [(VK_MENU, True), (0x27, False)])
        self.assertNotIn(VK_CONTROL, [vk for vk, _mod in chord("left", ("command",))])
        self.assertEqual(chord("return", ()), [(0x0D, False)])
        self.assertEqual(chord("down", ()), [(0x28, False)])
        self.assertEqual(chord("up", ()), [(0x26, False)])
        self.assertEqual(chord("pagedown", ()), [(0x22, False)])
        for number in range(2, 9):
            self.assertEqual(
                chord(str(number), ("command",)),
                [(VK_CONTROL, True), (0x30 + number, False)],
            )
        self.assertTrue(set(SAFE_HOTKEYS) <= set(NAMED_VK))

    def test_win_hotkey_refuses_without_sendinput(self) -> None:
        pointer = WinInput.__new__(WinInput)
        for key, mods in (
            ("q", ("command",)),
            ("w", ("command",)),
            ("space", ()),
            ("space", ("command",)),
        ):
            with self.assertRaises(ValueError):
                pointer.hotkey(key, mods)
        with self.assertRaises(ValueError):
            pointer.hotkey("not-a-key", ())
        with self.assertRaises(ValueError):
            pointer.hotkey("1", ())
        with self.assertRaises(ValueError):
            pointer.hotkey("a", ())
        with self.assertRaises(ValueError):
            pointer.hotkey("left", ("super",))
        self.assertFalse(pointer.omnibox("javascript:alert(1)"))
        self.assertFalse(pointer.omnibox(""))

    def test_every_typable_character_has_a_windows_key(self) -> None:
        self.assertTrue(windows_keys_cover_allowlist())
        for char in CHAR_KEYS:
            vk, shift = virtual_key(char)
            self.assertIsInstance(vk, int)
            self.assertIsInstance(shift, bool)
        self.assertEqual(virtual_key(" "), (0x20, False))
        self.assertEqual(virtual_key(":"), (0xBA, True))
        self.assertEqual(virtual_key("/"), (0xBF, False))
        self.assertEqual(virtual_key("~"), (0xC0, True))
        self.assertEqual(virtual_key("A"), (0x41, True))
        self.assertEqual(virtual_key("a"), (0x41, False))
        self.assertEqual(virtual_key(">"), (0xBE, True))

    def test_process_parser_matches_chrome_exe_without_the_mac_name(self) -> None:
        profile = Path("/tmp/activitysim-locked-win")
        exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        self.assertNotIn("Google Chrome", exe)
        ours = f'"{exe}" --user-data-dir={profile} --new-window'
        renderer = f'"{exe}" --type=renderer --user-data-dir={profile}'
        longer = f'"{exe}" --user-data-dir={profile}-other'
        signed_in = rf'"{exe}" --user-data-dir=C:\Users\me\AppData\Local\Google\Chrome\User Data'
        code = r"C:\Users\me\AppData\Local\Programs\Microsoft VS Code\Code.exe --wait"
        mixed = (
            r'"C:\Program Files\Google\Chrome\Application\Chrome.EXE" '
            f"--user-data-dir={profile}"
        )
        spaced = Path("/tmp/activitysim locked win")
        quoted = f'"{exe}" "--user-data-dir={spaced}" --new-window'
        quoted_prefix = f'"{exe}" "--user-data-dir={spaced}-extra" --new-window'
        text = "\n".join([
            f"111\t{ours}",
            f"222\t{renderer}",
            f"333\t{longer}",
            f"444\t{signed_in}",
            f"555\t{code}",
            "not-a-pid\tignored",
            "",
            f"777\t{mixed}",
            f"888\t{quoted}",
            f"999\t{quoted_prefix}",
        ])
        self.assertEqual(
            parse_locked_chrome_pids(text, profile, _command_has_profile),
            [111, 777],
        )
        self.assertEqual(
            parse_locked_chrome_pids(f"888\t{quoted}\n999\t{quoted_prefix}", spaced, _command_has_profile),
            [888],
        )
        bare = r"C:\Chrome\chrome.exe --user-data-dir=/tmp/activitysim-locked-win"
        notepad = r"C:\Windows\notepad.exe --user-data-dir=/tmp/activitysim-locked-win chrome.exe"
        self.assertEqual(parse_locked_chrome_pids(f"321\t{bare}", profile, _command_has_profile), [321])
        self.assertEqual(parse_locked_chrome_pids(f"654\t{notepad}", profile, _command_has_profile), [])
        self.assertEqual(command_line_for_pid(f"321\t{bare}\n654\t{notepad}", 321), bare)
        self.assertEqual(command_line_for_pid("321\t" + bare, 999), "")
        user_data = Path(r"C:\Users\me\AppData\Local\Google\Chrome\User Data")
        user_only = Path(r"C:\Users\me\AppData\Local\Google\Chrome\User")
        quoted_user = f'"{exe}" "--user-data-dir={user_data}"'
        self.assertTrue(_command_has_profile(quoted_user, user_data))
        self.assertFalse(_command_has_profile(quoted_user, user_only))
        self.assertFalse(_command_has_profile(f'"{exe}" "--user-data-dir={user_data} backup"', user_data))
        self.assertFalse(_command_has_profile(f'"{exe}" --foo="--user-data-dir={profile}"', profile))
        mac_profile = Path("/Users/me/Library/Application Support/activitysim/locked-chrome")
        mac_command = (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
            f"--user-data-dir={mac_profile} --no-first-run"
        )
        self.assertTrue(_command_has_profile(mac_command, mac_profile))
        self.assertFalse(_command_has_profile(mac_command, Path(str(mac_profile) + "-extra")))

    def test_embedded_line_separator_does_not_forge_a_pid(self) -> None:
        profile = Path("/tmp/activitysim-locked-win")
        exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        ours = f'"{exe}" --user-data-dir={profile} --new-window'
        for sep in ("\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            forged = f"111\t{ours}{sep}222\t{ours}\n333\t{ours}"
            self.assertEqual(
                parse_locked_chrome_pids(forged, profile, _command_has_profile),
                [111, 333],
            )
            self.assertEqual(command_line_for_pid(forged, 222), "")
            self.assertNotIn(sep, command_line_for_pid(forged, 111))
        crlf = f"111\t{ours}\r\n444\t{ours}\r555\t{ours}"
        self.assertEqual(
            parse_locked_chrome_pids(crlf, profile, _command_has_profile),
            [111, 444, 555],
        )

    def test_process_argv_does_not_contain_a_profile_path(self) -> None:
        sample = r"C:\Users\someone\AppData\Local\activitysim\locked-chrome"
        argv = process_list_argv()
        self.assertIsInstance(argv, list)
        self.assertEqual(basename(argv[0]), "powershell.exe")
        self.assertIn("System32", argv[0].replace("/", "\\"))
        self.assertIn("-NoProfile", argv)
        self.assertIn("-NonInteractive", argv)
        joined = " ".join(argv)
        self.assertIn("chrome.exe", joined)
        self.assertIn("-replace", joined)
        self.assertIn(r"\r", joined)
        self.assertIn(r"\n", joined)
        self.assertIn(r"\t", joined)
        self.assertIn(r"\v", joined)
        self.assertIn(r"\f", joined)
        self.assertIn(r"\u001c", joined)
        self.assertIn(r"\u0085", joined)
        self.assertIn(r"\u2028", joined)
        self.assertIn(r"\u2029", joined)
        self.assertNotIn(sample, joined)
        self.assertNotIn("activitysim", joined)

    def test_process_output_decodes_utf16_and_utf8(self) -> None:
        text = "111\tchrome.exe\n"
        self.assertEqual(decode_process_output(text.encode("utf-8")), text)
        self.assertEqual(decode_process_output(b"\xef\xbb\xbf" + text.encode("utf-8")), text)
        self.assertEqual(decode_process_output(text.encode("utf-16")), text)
        self.assertEqual(decode_process_output(b"\xfe\xff" + text.encode("utf-16-be")), text)
        self.assertEqual(decode_process_output(text.encode("utf-16-le")), text)

    def test_foreground_chrome_exe_is_named_google_chrome(self) -> None:
        path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        self.assertNotIn("Google Chrome", path)
        self.assertEqual(app_name_for_image(path), "Google Chrome")
        self.assertEqual(
            app_name_for_image(r"C:\Program Files\Google\Chrome\Application\CHROME.EXE"),
            "Google Chrome",
        )
        self.assertEqual(app_name_for_image(r"D:\apps\Code.exe"), "Code.exe")
        self.assertIsNone(app_name_for_image(None))
        self.assertIsNone(app_name_for_image(""))

    def test_css_center_converts_to_physical_pixels(self) -> None:
        point = css_center_to_screen(
            screen_x=10,
            screen_y=40,
            outer_width=1200,
            outer_height=800,
            inner_width=1180,
            inner_height=700,
            device_pixel_ratio=2,
            left=100,
            top=50,
            width=80,
            height=20,
        )
        self.assertEqual(point, (320.0, 380.0))
        self.assertEqual(
            css_center_to_screen(0, 0, 100, 100, 100, 100, 1, 0, 0, 10, 10),
            (5.0, 5.0),
        )
        window = css_point_in_window(
            (0.0, 0.0, 2000.0, 1600.0),
            1000,
            800,
            980,
            700,
            0,
            0,
            0,
            0,
        )
        self.assertEqual(window, (20.0, 180.0))

    def test_result_payload_uses_accept_result(self) -> None:
        bounds = (0.0, 0.0, 1400.0, 900.0)
        goods = []
        for index in range(9):
            goods.append({
                "href": f"https://github.com/octo/repo{index}",
                "title": f"octo/repo{index}",
                "left": 400,
                "top": 200 + index * 50,
                "width": 100,
                "height": 20,
            })
        payload = {
            "outerWidth": 1400,
            "outerHeight": 900,
            "innerWidth": 1400,
            "innerHeight": 900,
            "devicePixelRatio": 2,
            "links": [
                goods[0],
                {
                    "href": "https://github.com/python/stars",
                    "title": "Star this repository",
                    "left": 400,
                    "top": 360,
                    "width": 100,
                    "height": 20,
                },
                {
                    "href": "javascript:alert(1)",
                    "title": "script",
                    "left": 400,
                    "top": 420,
                    "width": 100,
                    "height": 20,
                },
                {
                    "href": "https://x.com/home",
                    "title": "x",
                    "left": 400,
                    "top": 480,
                    "width": 100,
                    "height": 20,
                },
                {
                    "href": "file:///tmp/secret",
                    "title": "file",
                    "left": 400,
                    "top": 520,
                    "width": 100,
                    "height": 20,
                },
                {
                    "href": "https://github.com/python/rightside",
                    "title": "python/rightside",
                    "left": 1200,
                    "top": 300,
                    "width": 100,
                    "height": 20,
                },
                *goods[1:],
            ],
        }
        points = result_points_from_payload(payload, bounds)
        expected = [(450.0, 210.0 + index * 50) for index in range(8)]
        self.assertEqual(len(points), 8)
        for point, want in zip(points, expected):
            self.assertAlmostEqual(point[0], want[0])
            self.assertAlmostEqual(point[1], want[1])
        self.assertGreater(points[-1][1], points[0][1])
        self.assertAlmostEqual(points[-1][1], 560.0)
        self.assertEqual(result_points_from_payload({"links": goods}, bounds), [])
        self.assertEqual(result_points_from_payload(["nope"], bounds), [])
        self.assertEqual(result_points_from_payload({"outerWidth": 1, "links": [goods[0]]}, bounds), [])

    def test_search_point_uses_the_largest_field(self) -> None:
        payload = {
            "screenX": 0,
            "screenY": 0,
            "outerWidth": 1000,
            "outerHeight": 800,
            "innerWidth": 1000,
            "innerHeight": 800,
            "devicePixelRatio": 1,
            "fields": [
                {"left": 10, "top": 10, "width": 20, "height": 10},
                {"left": 100, "top": 40, "width": 200, "height": 30},
            ],
        }
        bounds = (0.0, 0.0, 1000.0, 800.0)
        point = search_point(payload, bounds)
        self.assertIsNotNone(point)
        assert point is not None
        self.assertAlmostEqual(point[0], 200.0)
        self.assertAlmostEqual(point[1], 55.0)
        wide = dict(payload)
        wide["devicePixelRatio"] = 2
        again = search_point(wide, bounds)
        self.assertIsNotNone(again)
        assert again is not None
        self.assertAlmostEqual(again[0], point[0])
        self.assertAlmostEqual(again[1], point[1])
        doubled = search_point(payload, (0.0, 0.0, 2000.0, 1600.0))
        self.assertIsNotNone(doubled)
        assert doubled is not None
        self.assertAlmostEqual(doubled[0], 400.0)
        self.assertAlmostEqual(doubled[1], 110.0)
        self.assertIsNone(search_point({"fields": []}, bounds))
        self.assertIsNone(search_point("nope", bounds))
        self.assertIsNone(search_point({"outerWidth": 1000, "fields": "nope"}, bounds))
        self.assertIsNone(
            search_point(
                {
                    "outerWidth": 1000,
                    "outerHeight": 800,
                    "innerWidth": 1000,
                    "innerHeight": 800,
                    "fields": [{"top": 1, "height": 1}],
                },
                bounds,
            )
        )

    def test_focused_role_mapping(self) -> None:
        self.assertEqual(role_from_active({"tag": "input", "editable": False}), "AXTextField")
        self.assertEqual(role_from_active({"tag": "input", "type": "search"}), "AXTextField")
        self.assertEqual(role_from_active({"tag": "input", "type": "checkbox"}), "button")
        self.assertEqual(role_from_active({"tag": "input", "type": "submit"}), "button")
        self.assertEqual(role_from_active({"tag": "textarea", "editable": False}), "AXTextField")
        self.assertEqual(role_from_active({"tag": "div", "editable": True}), "AXTextField")
        self.assertEqual(role_from_active({"tag": "div", "editable": "true"}), "AXTextField")
        self.assertEqual(role_from_active({"tag": "div", "editable": "false"}), "button")
        self.assertEqual(role_from_active({"tag": "", "editable": True}), "AXTextField")
        self.assertEqual(role_from_active({"tag": "a", "editable": False}), "button")
        self.assertEqual(role_from_active({"tag": "", "editable": False}), "")
        self.assertEqual(role_from_active(None), "")
        self.assertEqual(role_from_active({"tag": "BUTTON"}), "button")

    def test_awake_flags_and_root_message(self) -> None:
        self.assertEqual(ES_CONTINUOUS, 0x80000000)
        self.assertEqual(ES_SYSTEM_REQUIRED, 0x00000001)
        self.assertEqual(ES_DISPLAY_REQUIRED, 0x00000002)
        self.assertEqual(AWAKE_FLAGS, ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        self.assertEqual(AWAKE_FLAGS, 0x80000003)
        mac = root_message(True, "darwin")
        win = root_message(True, "win32")
        self.assertIn("sudo", mac or "")
        self.assertIn("pmset", mac or "")
        self.assertIn("Administrator", win or "")
        self.assertNotIn("pmset", win or "")
        self.assertNotIn("sudo", win or "")
        self.assertIsNone(root_message(False, "win32"))
        self.assertIsNone(root_message(False, "darwin"))

    def test_windows_paths_and_fullscreen_flag(self) -> None:
        env = {
            "PROGRAMFILES": r"D:\PF",
            "PROGRAMFILES(X86)": r"D:\PF86",
            "LOCALAPPDATA": r"D:\Local",
        }
        home = Path("/Users/me")
        self.assertEqual(
            chrome_candidates("win32", env, home),
            (
                Path(r"D:\PF") / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(r"D:\PF86") / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(r"D:\Local") / "Google" / "Chrome" / "Application" / "chrome.exe",
            ),
        )
        self.assertEqual(
            locked_profile_path("win32", {}, home),
            home / "AppData" / "Local" / "activitysim" / "locked-chrome",
        )
        locked = locked_profile_path("win32", env, home)
        real = real_profile_path("win32", env, home)
        self.assertEqual(locked, Path(r"D:\Local") / "activitysim" / "locked-chrome")
        self.assertEqual(real, Path(r"D:\Local") / "Google" / "Chrome" / "User Data")
        self.assertNotEqual(locked, real)
        self.assertEqual(
            real_profile_path("win32", {}, home),
            home / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
        )
        self.assertEqual(
            chrome_candidates("win32", {}, home)[0],
            Path(r"C:\Program Files") / "Google" / "Chrome" / "Application" / "chrome.exe",
        )
        self.assertEqual(
            chrome_candidates("win32", {}, home)[1],
            Path(r"C:\Program Files (x86)") / "Google" / "Chrome" / "Application" / "chrome.exe",
        )
        self.assertEqual(
            chrome_candidates("darwin", {}, home),
            (
                Path("/Applications/Google Chrome.app"),
                home / "Applications" / "Google Chrome.app",
            ),
        )
        command = launch_command(
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path("/tmp/activitysim-locked"),
            "https://github.com/search/advanced",
            9333,
            system="win32",
        )
        self.assertEqual(command[-2], "--start-fullscreen")
        self.assertEqual(command[-1], "https://github.com/search/advanced")
        self.assertIn("--use-mock-keychain", command)
        self.assertIn("--disable-sync", command)
        self.assertIn("MAP x.com ~NOTFOUND", " ".join(command))
        self.assertNotIn("tell application", " ".join(command))

    def test_awake_banner_stays_pmset_on_mac(self) -> None:
        runner, _browser, _pointer, _clock = make_runner(duration=1)
        runner.config.dry_run = False
        original = sys.platform
        try:
            sys.platform = "darwin"
            runner.history.clear()
            with redirect_stdout(StringIO()):
                runner._banner()
            self.assertIn(
                "keep the machine awake separately: sudo pmset -a disablesleep 1",
                runner.history,
            )
            runner.history.clear()
            sys.platform = "win32"
            with redirect_stdout(StringIO()):
                runner._banner()
            self.assertIn(
                "--caffeinate keeps the machine awake until exit. power settings are not changed",
                runner.history,
            )
            self.assertFalse(any("pmset" in line for line in runner.history))
            runner.history.clear()
            runner.config.dry_run = True
            with redirect_stdout(StringIO()):
                runner._banner()
            self.assertFalse(any("--caffeinate" in line for line in runner.history))
            self.assertFalse(any("pmset" in line for line in runner.history))
        finally:
            sys.platform = original

    @unittest.skipUnless(sys.platform == "win32", "windows profile constants")
    def test_windows_profile_constants_follow_the_env(self) -> None:
        self.assertEqual(LOCKED_PROFILE, locked_profile_path())
        self.assertEqual(REAL_PROFILE, real_profile_path())

    @unittest.skipUnless(sys.platform == "darwin", "macOS profile layout")
    def test_mac_profile_constants_stay_in_library(self) -> None:
        self.assertEqual(
            LOCKED_PROFILE,
            Path.home() / "Library/Application Support/activitysim/locked-chrome",
        )
        self.assertEqual(
            REAL_PROFILE,
            Path.home() / "Library/Application Support/Google/Chrome",
        )


class _SendUser32:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.cursor = (10, 10)
        self.moves: list[tuple[int, int]] = []
        self.fail: int | None = None

    def SendInput(self, count, array, _size):
        if self.fail is not None:
            return self.fail
        for index in range(count):
            event = array[index]
            self.events.append({
                "type": int(event.type),
                "dx": int(event.mi.dx),
                "dy": int(event.mi.dy),
                "data": int(event.mi.mouseData),
                "mflags": int(event.mi.dwFlags),
                "vk": int(event.ki.wVk),
                "kflags": int(event.ki.dwFlags),
            })
        return count

    def GetCursorPos(self, point):
        point._obj.x, point._obj.y = self.cursor
        return 1

    def SetCursorPos(self, x, y):
        self.cursor = (int(x), int(y))
        self.moves.append(self.cursor)
        return 1

    def GetSystemMetrics(self, index):
        return {76: 0, 77: 0, 78: 1920, 79: 1080}.get(index, 0)


def _pointer() -> WinInput:
    pointer = WinInput.__new__(WinInput)
    pointer.rng = random.Random(0)
    pointer.sleep = lambda _seconds: None
    pointer._available = None
    return pointer


class _WindowUser32:
    def __init__(self) -> None:
        self.windows: dict[int, dict] = {}
        self.foreground = 0
        self.thread_id = 1
        self.shown: list[tuple[int, int]] = []
        self.attached: list[tuple[int, int, bool]] = []
        self.closed: list[int] = []
        self.fail_foreground = False

    def EnumWindows(self, callback, _lparam):
        for hwnd in list(self.windows):
            callback(hwnd, 0)
        return 1

    def GetWindowThreadProcessId(self, hwnd, out):
        info = self.windows.get(int(hwnd))
        if out is not None:
            out._obj.value = 0 if info is None else int(info["pid"])
        if info is None:
            return 0
        return int(info.get("thread", 4))

    def IsWindowVisible(self, hwnd):
        return 1 if self.windows[int(hwnd)].get("visible", True) else 0

    def GetWindowRect(self, hwnd, rect):
        info = self.windows[int(hwnd)]
        if info.get("bad_rect"):
            return 0
        left, top, right, bottom = info["rect"]
        rect._obj.left = left
        rect._obj.top = top
        rect._obj.right = right
        rect._obj.bottom = bottom
        return 1

    def IsIconic(self, hwnd):
        return 1 if self.windows[int(hwnd)].get("iconic") else 0

    def ShowWindow(self, hwnd, command):
        self.shown.append((int(hwnd), int(command)))
        return 1

    def GetForegroundWindow(self):
        return self.foreground

    def GetCurrentThreadId(self):
        return self.thread_id

    def AttachThreadInput(self, source, target, attach):
        self.attached.append((int(source), int(target), bool(attach)))
        return 1

    def SetForegroundWindow(self, hwnd):
        if self.fail_foreground:
            raise OSError("foreground failed")
        self.foreground = int(hwnd)
        return 1

    def PostMessageW(self, hwnd, message, _wparam, _lparam):
        self.closed.append((int(hwnd), int(message)))
        return 1


class _Page:
    def __init__(self, payload, pid=None) -> None:
        self.payload = payload
        self.pid = pid
        self.scale = None

    def evaluate(self, _expression):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload

    def remember_pixel_scale(self, scale):
        self.scale = scale


class WindowAndInputTests(unittest.TestCase):
    def test_sendinput_records_wheel_keys_and_restores_the_cursor(self) -> None:
        fake = _SendUser32()
        pointer = _pointer()
        with patch("activitysim.wininput._load_user32", return_value=fake):
            pointer.scroll(2)
            self.assertEqual(len(fake.events), 1)
            wheel = fake.events[0]
            self.assertEqual(wheel["type"], 0)
            self.assertEqual(wheel["data"], (-240) & 0xFFFFFFFF)
            self.assertEqual(wheel["mflags"] & MOUSEEVENTF_MOVE, 0)
            self.assertEqual(wheel["dx"], 0)
            self.assertEqual(wheel["dy"], 0)
            fake.events.clear()
            pointer.hotkey("left", ("command",))
            keys = [event for event in fake.events if event["type"] == 1]
            self.assertEqual([event["vk"] for event in keys], [0x12, 0x25, 0x25, 0x12])
            self.assertEqual(keys[0]["kflags"], 0)
            self.assertEqual(keys[1]["kflags"], KEYEVENTF_EXTENDEDKEY)
            self.assertEqual(keys[2]["kflags"], KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)
            self.assertEqual(keys[3]["kflags"], KEYEVENTF_KEYUP)
            fake.events.clear()
            pointer.type_text("A")
            typed = [event["vk"] for event in fake.events if event["type"] == 1]
            self.assertEqual(typed, [0x10, 0x41, 0x41, 0x10])
            fake.events.clear()
            pointer.click(10, 20)
            clicks = [event for event in fake.events if event["type"] == 0]
            self.assertEqual(len(clicks), 2)
            self.assertTrue(all(event["mflags"] & MOUSEEVENTF_MOVE for event in clicks))
            fake.fail = 0
            with self.assertRaises(OSError):
                pointer.scroll(1)
            fake.fail = None
            fake.cursor = (10, 10)
            pointer._available = None
            self.assertTrue(pointer.available())
            self.assertEqual(fake.moves[0], (12, 10))
            self.assertEqual(fake.cursor, (10, 10))

    def test_largest_window_restore_and_detach(self) -> None:
        from activitysim import winchrome

        fake = _WindowUser32()
        fake.windows = {
            1: {"pid": 42, "visible": False, "rect": (0, 0, 2000, 2000), "thread": 3},
            # Visible, enumerated first, and smaller than hwnd 5.
            4: {"pid": 42, "visible": True, "rect": (0, 0, 450, 320), "thread": 3},
            5: {"pid": 42, "visible": True, "iconic": True, "rect": (10, 10, 900, 700), "thread": 3},
            7: {"pid": 42, "visible": True, "bad_rect": True, "rect": (0, 0, 10, 10), "thread": 3},
            8: {"pid": 99, "visible": True, "rect": (0, 0, 3000, 3000), "thread": 8},
        }
        fake.foreground = 8
        fake.fail_foreground = True
        kernel = fake
        with patch("activitysim.winchrome._user32", return_value=fake), patch(
            "activitysim.winchrome._kernel32", return_value=kernel
        ):
            with self.assertRaises(OSError):
                winchrome.activate(42)
        self.assertEqual(fake.shown, [(5, SW_RESTORE)])
        self.assertEqual(fake.attached[0][:2], (1, 8))
        self.assertTrue(fake.attached[0][2])
        self.assertEqual(fake.attached[-1], (1, 8, False))
        fake.fail_foreground = False
        fake.windows[5]["iconic"] = False
        with patch("activitysim.winchrome._user32", return_value=fake), patch(
            "activitysim.winchrome._kernel32", return_value=kernel
        ):
            self.assertEqual(winchrome.window_bounds(42), (10.0, 10.0, 900.0, 700.0))
            self.assertNotEqual(winchrome.window_bounds(42), (0.0, 0.0, 450.0, 320.0))
            winchrome.request_close(42)
        self.assertEqual(fake.closed, [(5, WM_CLOSE)])

    def test_chrome_window_methods_on_win32(self) -> None:
        browser = Chrome.__new__(Chrome)
        browser.pid = 42
        browser.profile = Path("/tmp/activitysim-locked")
        original = sys.platform
        sys.platform = "win32"
        try:
            with patch("activitysim.chrome._is_our_process", return_value=True), patch(
                "activitysim.winchrome.window_bounds", return_value=(0.0, 0.0, 100.0, 100.0)
            ):
                with self.assertRaises(ChromeError):
                    browser.bounds()
            with patch("activitysim.chrome._is_our_process", return_value=True), patch(
                "activitysim.winchrome.window_bounds", side_effect=OSError("gone")
            ):
                with self.assertRaises(ChromeError):
                    browser.bounds()
            large = (10.0, 20.0, 810.0, 620.0)
            with patch("activitysim.chrome._is_our_process", return_value=True), patch(
                "activitysim.winchrome.window_bounds", return_value=large
            ):
                found = browser.bounds()
            self.assertEqual(found, large)
            self.assertGreaterEqual(found[2] - found[0], 400)
            self.assertGreaterEqual(found[3] - found[1], 300)
            with patch("activitysim.chrome._is_our_process", return_value=True), patch(
                "activitysim.winchrome.activate", side_effect=OSError("no window")
            ):
                with self.assertRaises(ChromeError):
                    browser.activate()
            with patch("activitysim.winchrome.is_front", side_effect=OSError("no")):
                self.assertFalse(browser.is_front())
            with patch("activitysim.winchrome.is_front", return_value=True):
                self.assertTrue(browser.is_front())
            called = []
            with patch("activitysim.chrome.run_osascript", side_effect=lambda *_a, **_k: called.append(1)):
                browser._enter_fullscreen()
            self.assertEqual(called, [])
        finally:
            sys.platform = original

    def test_stop_existing_closes_then_kills_and_ignores_a_dead_pid(self) -> None:
        profile = Path("/tmp/activitysim-stop-win")
        resolved = assert_locked_profile(profile)
        line = f'15\t"C:\\Chrome\\chrome.exe" --user-data-dir={resolved}\n'
        order: list[str] = []
        fresh_calls: list[bool] = []

        def fake_read_kw(*, fresh=False):
            fresh_calls.append(fresh)
            return line

        def close(pid):
            order.append(f"close {pid}")
            raise OSError(87, "dead")

        def kill(pid, _sig):
            order.append(f"kill {pid}")
            raise OSError(87, "dead")

        clock = {"n": 0}

        def now():
            clock["n"] += 10
            return clock["n"]

        original = sys.platform
        sys.platform = "win32"
        try:
            with patch("activitysim.winchrome.read_process_list", side_effect=fake_read_kw), patch(
                "activitysim.winchrome.request_close", side_effect=close
            ), patch("activitysim.chrome.os.kill", side_effect=kill), patch(
                "activitysim.chrome.time.time", side_effect=now
            ), patch("activitysim.chrome.time.sleep", lambda _seconds: None):
                _stop_existing(profile)
        finally:
            sys.platform = original
        self.assertEqual(fresh_calls, [True, False, True, False])
        self.assertEqual(order, ["close 15", "kill 15"])
        self.assertIn("kill 15", order)

    def test_process_list_cache_timeout_and_missing_exe(self) -> None:
        from activitysim import winchrome

        clear_process_cache()
        clock = {"t": 0.0}
        calls = {"n": 0}

        class Result:
            returncode = 0
            stdout = b"111\tok\n"

        def fake_run(_argv, **kwargs):
            self.assertEqual(kwargs.get("timeout"), 5)
            calls["n"] += 1
            clock["t"] = 10.0
            return Result()

        with tempfile.NamedTemporaryFile() as tmp:
            with patch("activitysim.winchrome.powershell_path", return_value=Path(tmp.name)), patch(
                "activitysim.winchrome.subprocess.run", side_effect=fake_run
            ), patch("activitysim.winchrome.time.time", side_effect=lambda: clock["t"]):
                self.assertEqual(read_process_list(), "111\tok\n")
                self.assertEqual(read_process_list(), "111\tok\n")
                self.assertEqual(calls["n"], 1)
                self.assertEqual(read_process_list(fresh=True), "111\tok\n")
                self.assertEqual(calls["n"], 2)
                clock["t"] = 10.6
                self.assertEqual(read_process_list(), "111\tok\n")
                self.assertEqual(calls["n"], 3)

        def boom(*_args, **_kwargs):
            raise OSError("missing")

        clear_process_cache()
        with tempfile.NamedTemporaryFile() as tmp:
            with patch("activitysim.winchrome.powershell_path", return_value=Path(tmp.name)), patch(
                "activitysim.winchrome.subprocess.run", side_effect=boom
            ):
                self.assertEqual(read_process_list(fresh=True), "")
                self.assertIsNone(winchrome._CACHE)
                self.assertEqual(read_process_list(fresh=True), "")

        class Bad:
            returncode = 1
            stdout = b""

        clear_process_cache()
        with tempfile.NamedTemporaryFile() as tmp:
            with patch("activitysim.winchrome.powershell_path", return_value=Path(tmp.name)), patch(
                "activitysim.winchrome.subprocess.run", return_value=Bad()
            ):
                self.assertEqual(read_process_list(fresh=True), "")
                self.assertIsNone(winchrome._CACHE)

        with patch(
            "activitysim.winchrome.powershell_path", return_value=Path("/no/such/powershell.exe")
        ), patch("activitysim.winchrome.subprocess.run", side_effect=AssertionError("ran")):
            self.assertEqual(read_process_list(fresh=True), "")

    def test_dpi_flag_is_set_only_after_success(self) -> None:
        from activitysim import winchrome

        class Call:
            def __init__(self, result) -> None:
                self.result = result
                self.argtypes = None
                self.restype = None

            def __call__(self, *_args):
                return self.result

        user = type("User", (), {})()
        user.SetProcessDpiAwarenessContext = Call(0)
        user.SetProcessDPIAware = Call(0)
        winchrome._DPI_READY = False
        with patch("activitysim.winchrome._dll", return_value=user):
            self.assertFalse(ensure_process_dpi())
        self.assertFalse(winchrome._DPI_READY)
        user.SetProcessDPIAware = Call(1)
        with patch("activitysim.winchrome._dll", return_value=user):
            self.assertTrue(ensure_process_dpi())
        self.assertTrue(winchrome._DPI_READY)
        winchrome._DPI_READY = False

    def test_finder_and_focus_drive_the_runner(self) -> None:
        runner, browser, pointer, _clock = make_runner(duration=25, seed=12)
        wanted = [(500.0, 400.0), (520.0, 500.0)]
        runner.find_results = lambda _bounds: list(wanted)
        runner.find_search_box = lambda _bounds: (400.0, 200.0)
        with redirect_stdout(StringIO()):
            runner.run()
        for point in wanted:
            self.assertIn(point, pointer.clicks)
        self.assertIn((400.0, 200.0), pointer.clicks)
        for point in result_points(browser.bounds_value):
            self.assertNotIn(point, pointer.clicks)

        runner, browser, pointer, _clock = make_runner(duration=25, seed=12)
        runner.find_results = lambda _bounds: [(500.0, 400.0)]
        with redirect_stdout(StringIO()):
            runner.run()
        self.assertNotIn((500.0, 400.0), pointer.clicks)
        self.assertTrue(any(point in pointer.clicks for point in result_points(browser.bounds_value)))

        runner, _browser, pointer, _clock = make_runner(duration=25, seed=12)
        runner.find_results = lambda _bounds: [(10.0, 10.0), (12.0, 12.0)]
        with redirect_stdout(StringIO()):
            runner.run()
        self.assertNotIn((10.0, 10.0), pointer.clicks)

        runner, browser, pointer, _clock = make_runner(duration=25, seed=12)

        def boom(_bounds):
            raise OSError("no tree")

        runner.find_results = boom
        runner.find_search_box = boom
        with redirect_stdout(StringIO()):
            runner.run()
        self.assertTrue(pointer.clicks)
        box = search_box_point(browser.bounds_value)
        self.assertIsNotNone(box)
        self.assertIn(box, pointer.clicks)

        runner, browser, pointer, _clock = make_runner(duration=20, seed=3)
        browser.pixel_scale = lambda: 2.0
        self.assertFalse(in_search_zone((400.0, 400.0), browser.bounds_value, 1))
        self.assertTrue(in_search_zone((400.0, 400.0), browser.bounds_value, 2))
        runner.find_search_box = lambda _bounds: (400.0, 400.0)
        runner.find_results = lambda _bounds: [(500.0, 500.0), (520.0, 600.0)]
        with redirect_stdout(StringIO()):
            runner.run()
        self.assertIn((400.0, 400.0), pointer.clicks)

        tall = (0.0, 0.0, 1400.0, 1800.0)
        measured = _Page({
            "outerWidth": 1400,
            "outerHeight": 900,
            "innerWidth": 1400,
            "innerHeight": 800,
            "devicePixelRatio": 1,
            "fields": [{"left": 100, "top": 40, "width": 200, "height": 30}],
        })
        search_field_from(measured, tall)
        self.assertEqual(measured.scale, 2)
        self.assertNotEqual(measured.scale, 1)

        runner, browser, pointer, _clock = make_runner(duration=25, seed=12)
        browser.pixel_scale = lambda: measured.scale
        runner.find_search_box = lambda _bounds: None
        runner.find_results = lambda _bounds: []
        with redirect_stdout(StringIO()):
            runner.run()
        bounds = browser.bounds_value
        scaled = search_box_point(bounds, 2)
        plain = search_box_point(bounds, 1)
        self.assertIsNotNone(scaled)
        self.assertIsNotNone(plain)
        assert scaled is not None and plain is not None
        self.assertEqual(scaled[1], bounds[1] + 350)
        self.assertNotEqual(scaled, plain)
        self.assertIn(scaled, pointer.clicks)
        self.assertNotIn(plain, pointer.clicks)

        low = (500.0, 250.0)
        kept = [(500.0, 450.0), (520.0, 600.0)]
        self.assertTrue(in_result_zone(low, bounds, 1))
        self.assertFalse(in_result_zone(low, bounds, 2))
        for point in kept:
            self.assertTrue(in_result_zone(point, bounds, 1))
            self.assertTrue(in_result_zone(point, bounds, 2))

        def clicks_at(scale: float) -> list[tuple[float, float]]:
            again, host, hand, _clock = make_runner(duration=25, seed=12)
            host.pixel_scale = lambda: scale
            again.find_results = lambda _bounds: [low, *kept]
            with redirect_stdout(StringIO()):
                again.run()
            return hand.clicks

        self.assertIn(low, clicks_at(1))
        self.assertNotIn(low, clicks_at(measured.scale))

        runner, _browser, pointer, _clock = make_runner(duration=20, seed=4)
        runner.focused_role = lambda: "button"
        with redirect_stdout(StringIO()):
            runner.run()
        self.assertFalse(any(text.startswith("language:") for text in pointer.typed))
        self.assertTrue(any(line.startswith("open ") for line in runner.history))

        original = sys.platform
        sys.platform = "win32"
        try:
            runner, browser, pointer, _clock = make_runner(duration=12, seed=4, ready=False)
            with redirect_stdout(StringIO()):
                runner.run()
            text = "\n".join(runner.history)
            self.assertNotIn("Accessibility", text)
            self.assertIn("interactive user", text)
            runner, browser, pointer, _clock = make_runner(duration=16, seed=4)
            browser.main_window_is_front = lambda: False
            with redirect_stdout(StringIO()):
                runner.run()
            self.assertFalse(any(text.startswith("language:") for text in pointer.typed))
        finally:
            sys.platform = original

        advanced = "https://github.com/search/advanced"

        def step_off_advanced(front: bool) -> FakePointer:
            runner, browser, pointer, _clock = make_runner(duration=16, seed=4)
            browser.open_window("https://github.com/")
            self.assertNotEqual(browser.current_url(), advanced)
            browser.main_window_is_front = lambda: front
            with redirect_stdout(StringIO()):
                self.assertTrue(runner._prepare_input())
                runner._step()
            return pointer

        blocked = step_off_advanced(False)
        self.assertFalse(any(key == "return" for key, _mods in blocked.keys))
        self.assertNotIn(advanced, blocked.typed)
        self.assertFalse(any("://" in text for text in blocked.typed))
        opened = step_off_advanced(True)
        self.assertIn(advanced, opened.typed)
        self.assertTrue(any(key == "return" for key, _mods in opened.keys))

    def test_page_adapters_and_dialog_focus(self) -> None:
        bounds = (0.0, 0.0, 1400.0, 900.0)
        payload = {
            "outerWidth": 1400,
            "outerHeight": 900,
            "innerWidth": 1400,
            "innerHeight": 900,
            "fields": [{"left": 100, "top": 40, "width": 200, "height": 30}],
            "links": [{
                "href": "https://github.com/python/cpython",
                "title": "python/cpython",
                "left": 400,
                "top": 300,
                "width": 100,
                "height": 20,
            }],
            "tag": "input",
            "type": "search",
            "editable": False,
        }
        for key in ("outerWidth", "outerHeight", "innerWidth", "innerHeight", "fields"):
            self.assertIn(key, SEARCH_EXPRESSION)
        self.assertIn("links", RESULTS_EXPRESSION)
        self.assertIn("outerWidth", RESULTS_EXPRESSION)
        for key in ("tag", "type", "editable"):
            self.assertIn(key, FOCUSED_EXPRESSION)
        page = _Page(payload)
        self.assertEqual(search_field_from(page, bounds), (200.0, 55.0))
        self.assertEqual(page.scale, 1.0)
        tall = (0.0, 0.0, 1400.0, 1800.0)
        dpi = dict(payload)
        dpi["outerHeight"] = 900
        dpi["devicePixelRatio"] = 1
        scaled_page = _Page(dpi)
        search_field_from(scaled_page, tall)
        self.assertEqual(scaled_page.scale, 2)
        self.assertNotEqual(scaled_page.scale, dpi["devicePixelRatio"])
        found = result_clicks_from(page, bounds)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0][0], 450.0)
        self.assertAlmostEqual(found[0][1], 310.0)
        self.assertEqual(focused_role_from(page), "AXTextField")
        down = _Page(RuntimeError("down"))
        self.assertIsNone(search_field_from(down, bounds))
        self.assertEqual(result_clicks_from(down, bounds), [])
        self.assertEqual(focused_role_from(down), "")
        dialog = _Page(payload, pid=5)
        with patch("activitysim.winchrome.main_window_is_front", return_value=True):
            self.assertEqual(focused_role_from(dialog), "AXTextField")
        with patch("activitysim.winchrome.main_window_is_front", return_value=False):
            self.assertEqual(focused_role_from(dialog), "")

    def test_windows_caffeinate_and_elevation_do_not_touch_geteuid(self) -> None:
        original = sys.platform
        sys.platform = "win32"
        flags: list[int] = []
        try:
            with patch("os.geteuid", side_effect=AssertionError("geteuid")), patch(
                "activitysim.winchrome.is_user_admin", return_value=True
            ):
                stderr = StringIO()
                with redirect_stderr(stderr):
                    code = main(["run", "--dry-run", "--for", "1s", "--seed", "1"])
            self.assertEqual(code, 2)
            self.assertIn("Administrator", stderr.getvalue())

            class Boom:
                def __init__(self, *_args, **_kwargs) -> None:
                    raise AssertionError("constructed")

            with patch("os.geteuid", side_effect=AssertionError("geteuid")), patch(
                "activitysim.winchrome.is_user_admin", return_value=False
            ), patch("activitysim.winchrome.set_execution_state", side_effect=flags.append), patch(
                "activitysim.wininput.WinInput", Boom
            ), patch("activitysim.cli.Chrome", Boom):
                with redirect_stdout(StringIO()):
                    code = main([
                        "run", "--dry-run", "--caffeinate", "--for", "1s",
                        "--seed", "1", "--min-gap", "1", "--max-gap", "1",
                    ])
            self.assertEqual(code, 0)
            self.assertEqual(flags, [])

            runner = type("Runner", (), {})()
            runner.run = lambda: None
            flags.clear()
            with patch("os.geteuid", side_effect=AssertionError("geteuid")), patch(
                "activitysim.winchrome.is_user_admin", return_value=False
            ), patch("activitysim.winchrome.set_execution_state", side_effect=flags.append), patch(
                "activitysim.cli.Runner", return_value=runner
            ), patch("activitysim.cli.Chrome", return_value=object()), patch(
                "activitysim.wininput.WinInput", return_value=object()
            ):
                with redirect_stdout(StringIO()):
                    code = main([
                        "run", "--caffeinate", "--for", "1s",
                        "--seed", "1", "--min-gap", "1", "--max-gap", "1",
                    ])
            self.assertEqual(code, 0)
            self.assertEqual(flags, [AWAKE_FLAGS, ES_CONTINUOUS])

            def fail():
                raise OsaError("stopped")

            runner.run = fail
            flags.clear()
            with patch("os.geteuid", side_effect=AssertionError("geteuid")), patch(
                "activitysim.winchrome.is_user_admin", return_value=False
            ), patch("activitysim.winchrome.set_execution_state", side_effect=flags.append), patch(
                "activitysim.cli.Runner", return_value=runner
            ), patch("activitysim.cli.Chrome", return_value=object()), patch(
                "activitysim.wininput.WinInput", return_value=object()
            ):
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    code = main([
                        "run", "--caffeinate", "--for", "1s",
                        "--seed", "1", "--min-gap", "1", "--max-gap", "1",
                    ])
            self.assertEqual(code, 1)
            self.assertEqual(flags, [AWAKE_FLAGS, ES_CONTINUOUS])
        finally:
            sys.platform = original

    def test_windows_doctor_does_not_mention_accessibility(self) -> None:
        class Pointer:
            def available(self) -> bool:
                return False

        chrome = type("Chrome", (), {"profile": Path("C:/profile")})()
        with patch("activitysim.cli.find_chrome", return_value=Path("C:/chrome.exe")), patch(
            "activitysim.cli.Chrome", return_value=chrome
        ), patch("activitysim.wininput.WinInput", return_value=Pointer()):
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = _doctor_windows()
        text = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("C:/chrome.exe", text)
        self.assertIn("C:/profile", text)
        self.assertIn("did not move", text)
        self.assertIn("interactive user", text)
        self.assertNotIn("Accessibility", text)
        self.assertNotIn("System Settings", text)
        self.assertNotIn("AXIsProcessTrusted", text)


if __name__ == "__main__":
    unittest.main()
