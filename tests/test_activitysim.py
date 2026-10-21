"""Safety and behavior tests. These do not open Chrome or post input events."""

from __future__ import annotations

import base64
import hashlib
import json
import random
import socket
import subprocess
import tempfile
import threading
import unittest
import urllib.parse
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from activitysim.ax import chrome_pid, set_chrome_pid
from activitysim.cdp import CdpError, call
from activitysim.chrome import (
    assert_locked_profile,
    host_blocked,
    launch_command,
    pid_literal,
    script_activate,
    script_bounds,
    script_front_pid,
    script_fullscreen,
    scrub_signins,
)
from activitysim.cli import main, parse_duration, root_error, validate_run
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
from activitysim.input import MacInput, accessibility_prompt_dictionary, assert_typable, can_type
from activitysim.osa import as_text
from activitysim.places import Places, load_url_file, safe_url
from activitysim.runner import Config, Runner
from activitysim.search import (
    QUERIES,
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


class LockedChromeTests(unittest.TestCase):
    def test_applescript_quoting(self) -> None:
        self.assertEqual(as_text('a"b\\c'), '"a\\"b\\\\c"')
        with self.assertRaises(ValueError):
            as_text("line\nbreak")

    def test_launch_is_a_separate_signed_out_chrome(self) -> None:
        binary = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        profile = Path("/tmp/activitysim-locked")
        command = launch_command(binary, profile, "https://github.com/search/advanced", 9333)
        self.assertEqual(command[0], str(binary))
        self.assertIn(f"--user-data-dir={profile.resolve()}", command)
        self.assertIn("--disable-sync", command)
        self.assertIn("--use-mock-keychain", command)
        self.assertIn("--remote-debugging-port=9333", command)
        text = " ".join(command)
        self.assertNotIn("Application Support/Google/Chrome", text)
        self.assertIn("MAP x.com ~NOTFOUND", text)
        self.assertIn("MAP *.x.com ~NOTFOUND", text)
        self.assertIn("MAP twitter.com ~NOTFOUND", text)
        self.assertIn("MAP *.twitter.com ~NOTFOUND", text)
        self.assertNotIn("tell application", text)

    def test_signed_in_profile_and_x_are_refused(self) -> None:
        real = Path.home() / "Library/Application Support/Google/Chrome"
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
            self.assertTrue(
                text in QUERIES or text.startswith("https://github.com/search"),
                text,
            )
            self.assertNotIn("javascript:", text)
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
        stderr = StringIO()
        with redirect_stdout(StringIO()):
            from contextlib import redirect_stderr
            with redirect_stderr(stderr):
                code = main(["run", "--urls", "/tmp/does-not-exist-activitysim-urls.txt"])
        self.assertEqual(code, 2)
        self.assertIn("not found", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
