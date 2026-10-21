"""Command line for activitysim."""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
from pathlib import Path

from activitysim import __version__
from activitysim.chrome import Chrome, find_chrome
from activitysim.fakes import FakeBrowser, FakeClock, FakePointer
from activitysim.ax import focused_role, result_clicks, search_field
from activitysim.input import (
    MacInput,
    accessibility_trusted,
    open_accessibility_settings,
    prompt_accessibility,
)
from activitysim.osa import OsaError, frontmost_app
from activitysim.places import Places, load_url_file
from activitysim.runner import Config, Runner

HELP = f"""\
activitysim {__version__}
Browse public pages and move the pointer on your Mac.

This opens a separate signed-out Chrome on GitHub advanced search, types a
query, and clicks through the repository results. Those are real mouse clicks
and keystrokes, which is what an idle check looks for. Your signed-in Chrome
is not used, so the window has no X or GitHub session.

If Accessibility is off, macOS is prompted and System Settings opens. The run
waits until the switch is on. Without that permission it stops, because
AppleScript alone does not count as activity.

Your signed-in Chrome is left alone. Clicks stay on the search box and
the result list, not on Star, Fork, or Watch. Power settings are not changed.

  python3 -m activitysim
  python3 -m activitysim run --for 2h
  python3 -m activitysim doctor
  python3 -m activitysim run --help

Stay awake with the command you already planned, in another terminal:

  sudo pmset -a disablesleep 1

Put it back later:

  sudo pmset -a disablesleep 0

Run activitysim itself as your user, not with sudo.
"""


def parse_duration(text: str) -> float:
    """Return seconds. Bare numbers are seconds. 45s, 10m, and 2h are accepted."""

    raw = text.strip().lower()
    if raw in {"", "0", "forever", "inf"}:
        return 0
    units = {"s": 1, "m": 60, "h": 3600}
    if raw[-1] in units:
        number = raw[:-1]
        if not number:
            raise ValueError(f"invalid duration {text!r}")
        return float(number) * units[raw[-1]]
    return float(raw)


def root_error(euid: int) -> str | None:
    if euid == 0:
        return (
            "Run activitysim as your user, not with sudo. "
            "sudo is only for the separate pmset command."
        )
    return None


def build_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="activitysim run",
        description="Open a full screen Chrome window and look busy inside it.",
    )
    parser.add_argument(
        "--for",
        dest="duration_text",
        default="forever",
        help="how long to run: 30s, 45m, 2h, or forever",
    )
    parser.add_argument(
        "--min-gap",
        type=float,
        default=2,
        help="shortest quiet stretch between actions, in seconds",
    )
    parser.add_argument(
        "--max-gap",
        type=float,
        default=6,
        help="longest quiet stretch between actions, in seconds",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=30,
        help="back off this long after you move the mouse or leave Chrome. 0 disables",
    )
    parser.add_argument(
        "--max-tabs",
        type=int,
        default=5,
        help="cap on tabs in the simulator window",
    )
    parser.add_argument(
        "--mouse-only",
        action="store_true",
        help="move the pointer only. no Chrome and no keys",
    )
    parser.add_argument(
        "--no-keyboard",
        action="store_true",
        help="browse and move the pointer, but do not type",
    )
    parser.add_argument(
        "--incognito",
        action="store_true",
        help="accepted for old commands. the window is always a separate signed-out Chrome",
    )
    parser.add_argument(
        "--close-on-exit",
        action="store_true",
        help="close the simulator window when the program stops",
    )
    parser.add_argument(
        "--urls",
        type=Path,
        help="extra http(s) URLs, one per line. see urls.example.txt",
    )
    parser.add_argument("--seed", type=int, help="random seed, so a session can be repeated")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the session without opening Chrome or moving anything",
    )
    parser.add_argument(
        "--caffeinate",
        action="store_true",
        help="also run caffeinate so idle sleep stays off until this exits",
    )
    return parser


def validate_run(args: argparse.Namespace) -> Config:
    if args.min_gap <= 0 or args.max_gap <= 0:
        raise ValueError("gaps must be greater than 0")
    if args.min_gap > args.max_gap:
        raise ValueError("--min-gap cannot be greater than --max-gap")
    if args.max_tabs < 1:
        raise ValueError("--max-tabs must be at least 1")
    if args.pause < 0:
        raise ValueError("--pause cannot be negative")
    try:
        duration = parse_duration(args.duration_text)
    except ValueError as exc:
        raise ValueError(f"invalid --for {args.duration_text!r}") from exc
    if duration < 0:
        raise ValueError("--for cannot be negative")
    if args.urls is not None and not args.urls.is_file():
        raise ValueError(f"URL file not found: {args.urls}")
    return Config(
        min_gap=args.min_gap,
        max_gap=args.max_gap,
        pause=args.pause,
        max_tabs=args.max_tabs,
        duration=duration,
        mouse_only=args.mouse_only,
        no_keyboard=args.no_keyboard,
        dry_run=args.dry_run,
        seed=args.seed,
    )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    if argv in (["-h"], ["--help"]):
        print(HELP)
        return 0
    if not argv:
        argv = ["run"]
    if argv[0] in {"-v", "--version"}:
        print(__version__)
        return 0
    if argv[0] == "doctor":
        return doctor(argv[1:])
    if argv[0] != "run":
        argv = ["run", *argv]
    if len(argv) > 1 and argv[1] in {"-h", "--help"}:
        build_run_parser().print_help()
        return 0
    parser = build_run_parser()
    try:
        args = parser.parse_args(argv[1:])
        config = validate_run(args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    problem = root_error(os.geteuid())
    if problem:
        print(problem, file=sys.stderr)
        return 2
    return run(config, args)


def run(config: Config, args: argparse.Namespace) -> int:
    seed = config.seed if config.seed is not None else random.SystemRandom().randrange(1, 2**31)
    config.seed = seed
    rng = random.Random(seed)
    extra = load_url_file(args.urls) if args.urls else []
    places = Places(rng, extra)
    caffeinate = None
    browser = None
    try:
        if args.caffeinate and not config.dry_run:
            caffeinate = subprocess.Popen(
                ["caffeinate", "-imsu", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if config.dry_run:
            clock = FakeClock()
            browser = FakeBrowser()
            pointer = FakePointer(ready=True)
            runner = Runner(
                config,
                browser,
                pointer,
                places,
                rng,
                frontmost=lambda: pointer.front,
                clock=clock.time,
                sleep=clock.sleep,
                focused_role=lambda: "AXTextField",
            )
        else:
            browser = Chrome()
            pointer = MacInput(rng)
            runner = Runner(
                config,
                browser,
                pointer,
                places,
                rng,
                frontmost=frontmost_app,
                focused_role=focused_role,
                find_search_box=search_field,
                find_results=result_clicks,
            )
        runner.run()
        return 0
    except OsaError as exc:
        print(f"Chrome: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.close_on_exit and browser is not None and not config.dry_run:
            try:
                browser.close_window()
            except OsaError as exc:
                print(f"could not close the simulator window: {exc}", file=sys.stderr)
        if caffeinate is not None and caffeinate.poll() is None:
            caffeinate.terminate()


def doctor(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="activitysim doctor")
    parser.add_argument(
        "--open-settings",
        action="store_true",
        help="open the Accessibility privacy pane",
    )
    args = parser.parse_args(argv)
    print(f"activitysim {__version__}")
    chrome = find_chrome()
    if chrome is None:
        print("Google Chrome: not installed")
    else:
        print(f"Google Chrome: {chrome}")
        print(f"signed-out profile: {Chrome().profile}")
        print("your signed-in Chrome is not used")
    print(f"python: {sys.executable}")
    print(f"accessibility trusted: {accessibility_trusted()}")
    print("asking macOS for Accessibility, then nudging the pointer 2 pixels")
    prompt_accessibility()
    ready = MacInput().available()
    if ready:
        print("pointer: moves. keyboard control can run")
    else:
        print("pointer: did not move")
        open_accessibility_settings()
        print("System Settings is open on Accessibility.")
        print("Turn on the switch for the app that launched this (Terminal, iTerm, or Cursor).")
        print("If that app is already on, also enable the python binary printed above.")
        print("Then run activitysim again. It will wait and continue once the switch is on.")
    if args.open_settings and ready:
        open_accessibility_settings()
    return 0 if chrome is not None else 1
