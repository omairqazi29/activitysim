"""Safety and behavior tests. These do not open Chrome or post input events."""

from __future__ import annotations

import random
import subprocess
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from activitysim.chrome import (
    script_activate,
    script_back,
    script_bounds,
    script_close_oldest,
    script_close_window,
    script_current_url,
    script_navigate,
    script_new_tab,
    script_open_window,
    script_reload,
    script_scroll,
    script_switch,
    script_tab_count,
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
        pause=overrides.pop("pause", 180),
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
            script_navigate("1", "javascript:alert(1)")

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


class ScriptTests(unittest.TestCase):
    def test_applescript_quoting(self) -> None:
        self.assertEqual(as_text('a"b\\c'), '"a\\"b\\\\c"')
        with self.assertRaises(ValueError):
            as_text("line\nbreak")

    def test_navigation_scripts_have_no_keystrokes(self) -> None:
        url = 'https://example.com/a"b'
        scripts = [
            script_open_window("https://github.com/trending", incognito=True),
            script_open_window("https://github.com/trending", incognito=False),
            script_new_tab('win"1', "https://github.com/python/cpython/issues"),
            script_navigate("42", url),
            script_switch("42", 2),
            script_close_oldest("42"),
            script_back("42"),
            script_reload("42"),
            script_bounds("42"),
            script_tab_count("42"),
            script_scroll("42", 240),
            script_scroll("42", -120),
            script_close_window("42"),
            script_current_url("42"),
            script_activate("42"),
        ]
        self.assertIn('mode:"incognito"', scripts[0])
        self.assertIn(r'a\"b', scripts[3])
        self.assertIn(r'win\"1', scripts[2])
        for script in scripts:
            self.assertNotIn("keystroke", script)
            self.assertNotIn("key code", script)
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
