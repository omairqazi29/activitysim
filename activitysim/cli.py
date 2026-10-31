"""Command line for activitysim."""

from __future__ import annotations

import argparse
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

from activitysim import __version__
from activitysim.browse import locate_link, source_link
from activitysim.chrome import Chrome, find_chrome
from activitysim.editor import CodeEditor, GitWork, find_code
from activitysim.fakes import FakeBrowser, FakeClock, FakeEditor, FakePointer, FakeWork
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
Browse public pages and move the pointer on your Mac or Windows PC.

This checks out a public GitHub repository and opens that folder in one
fullscreen Visual Studio Code window. Later repositories reuse that window.
It has a separate profile, not the Code you are signed into. It opens files,
scrolls, and searches in them. It does not save. If Code asks to save, it
chooses Don't Save. GitHub is next: repositories
it has not already opened, and advanced
search. About one chapter in ten is a public benchmark site such as DeepSWE,
ProgramBench, SWE-bench, or Terminal-Bench. A site visit is short. A
repository page also opens a source file when the file tree is on screen,
then issues, pull requests, or commits. The quiet stretch while reading is
short, so the pointer keeps moving. If Visual Studio Code or git is missing,
those chapters stay on GitHub.
Those are real mouse clicks and keystrokes, which is what an idle check
looks for. Your signed-in Chrome is not used, so the window has no X or
GitHub session.

A normal run prints "launching dev browser" and then stays quiet. If something
fails, that error is printed instead. --dry-run still prints the session.

On a Mac, if Accessibility is off, macOS is prompted and System Settings opens.
The run waits until the switch is on. Without that permission it stops, because
AppleScript alone does not count as activity.

On Windows, mouse and keyboard events are posted with SendInput. Run as the
normal user, not as Administrator.

Your signed-in Chrome is left alone. Clicks in Chrome stay on the search box and
the result list, not on Star, Fork, or Watch. In the editor, clicks stay in the
text area. Power settings are not changed.

  python3 -m activitysim
  python3 -m activitysim run --for 2h
  python3 -m activitysim doctor
  python3 -m activitysim run --help

On a Mac, stay awake with the command you already planned, in another terminal:

  sudo pmset -a disablesleep 1

Put it back later:

  sudo pmset -a disablesleep 0

Run activitysim itself as your user, not with sudo. sudo is only for pmset.

On Windows, --caffeinate calls SetThreadExecutionState and keeps this process
awake until exit. It does not change the power plan. Stop with Ctrl+C.
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


def root_message(is_root: bool, platform: str) -> str | None:
    """Refusal text for an elevated process. Tests pass the platform in."""

    if not is_root:
        return None
    if platform == "win32":
        return "Run activitysim as the normal user, not as Administrator."
    return (
        "Run activitysim as your user, not with sudo. "
        "sudo is only for the separate pmset command."
    )


def root_error(euid: int) -> str | None:
    return root_message(euid == 0, sys.platform)


def is_elevated() -> bool:
    """True for uid 0 on macOS and for an Administrator token on Windows.

    os.geteuid does not exist on Windows and is not called there.
    """

    if sys.platform == "win32":
        from activitysim.winchrome import is_user_admin

        return is_user_admin()
    return os.geteuid() == 0


def build_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="activitysim run",
        description="Open a signed-out Chrome window and look busy inside it.",
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
        default=0.45,
        help="shortest quiet stretch between page changes, in seconds. Reading waits about a third of this",
    )
    parser.add_argument(
        "--max-gap",
        type=float,
        default=1.6,
        help="longest quiet stretch between page changes, in seconds. Reading waits about 0.4 of this",
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
        help=(
            "keep this process awake until exit. "
            "On macOS this runs caffeinate -imsu -w. "
            "On Windows this calls SetThreadExecutionState and does not change the power plan"
        ),
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
    problem = root_message(is_elevated(), sys.platform)
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
    awake = False
    try:
        if args.caffeinate and not config.dry_run:
            if sys.platform == "win32":
                from activitysim.winchrome import AWAKE_FLAGS, set_execution_state

                set_execution_state(AWAKE_FLAGS)
                awake = True
            else:
                caffeinate = subprocess.Popen(
                    ["caffeinate", "-imsu", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        if config.dry_run:
            clock = FakeClock()
            browser = FakeBrowser()
            pointer = FakePointer(ready=True)
            editor = FakeEditor()
            work = FakeWork()
            browser.attach(pointer)
            editor.attach(pointer)
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
                editor=editor,
                work=work,
            )
        elif sys.platform == "win32":
            from activitysim.winchrome import frontmost_app as win_frontmost
            from activitysim.wininput import WinInput
            from activitysim.winpage import focused_role_from, result_clicks_from, search_field_from

            browser = Chrome()
            pointer = WinInput(rng)
            runner = Runner(
                config,
                browser,
                pointer,
                places,
                rng,
                frontmost=win_frontmost,
                focused_role=lambda: focused_role_from(browser),
                find_search_box=lambda bounds: search_field_from(browser, bounds),
                find_results=lambda bounds: result_clicks_from(browser, bounds),
                find_link=lambda url: locate_link(browser, url),
                find_source=lambda: source_link(browser),
                editor=CodeEditor(),
                work=GitWork(rng),
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
                find_link=lambda url: locate_link(browser, url),
                find_source=lambda: source_link(browser),
                editor=CodeEditor(),
                work=GitWork(rng),
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
        if awake:
            from activitysim.winchrome import ES_CONTINUOUS, set_execution_state

            set_execution_state(ES_CONTINUOUS)
        if caffeinate is not None and caffeinate.poll() is None:
            caffeinate.terminate()


def doctor(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="activitysim doctor")
    if sys.platform != "win32":
        parser.add_argument(
            "--open-settings",
            action="store_true",
            help="open the Accessibility privacy pane",
        )
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        return _doctor_windows()
    print(f"activitysim {__version__}")
    chrome = find_chrome()
    if chrome is None:
        print("Google Chrome: not installed")
    else:
        print(f"Google Chrome: {chrome}")
        print(f"signed-out profile: {Chrome().profile}")
        print("your signed-in Chrome is not used")
    print(f"python: {sys.executable}")
    code = find_code()
    if code is None:
        print("Visual Studio Code: not installed")
    else:
        print(f"Visual Studio Code: {code}")
    git = shutil.which("git")
    print(f"git: {git or 'not installed'}")
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


def _doctor_windows() -> int:
    """Print the Chrome path and nudge the pointer. No Accessibility pane."""

    from activitysim.wininput import WinInput

    print(f"activitysim {__version__}")
    chrome = find_chrome()
    if chrome is None:
        print("Google Chrome: not installed")
    else:
        print(f"Google Chrome: {chrome}")
        print(f"signed-out profile: {Chrome().profile}")
        print("your signed-in Chrome is not used")
    print(f"python: {sys.executable}")
    code = find_code()
    if code is None:
        print("Visual Studio Code: not installed")
    else:
        print(f"Visual Studio Code: {code}")
    git = shutil.which("git")
    print(f"git: {git or 'not installed'}")
    print("nudging the pointer 2 pixels")
    ready = WinInput().available()
    if ready:
        print("pointer: moves. keyboard control can run")
    else:
        print("pointer: did not move")
        print("This must run as the interactive user, not as Administrator.")
    return 0 if chrome is not None else 1
