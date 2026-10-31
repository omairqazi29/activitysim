"""Check out a public repository and open it in a separate Visual Studio Code.

The checkout lives under activitysim's own directory. The editor is one
fullscreen window on activitysim's user-data directory, not the signed-in
Code profile. Later repositories reuse that window. Quick Open and Find type
only paths and tokens that came from that checkout.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Mapping

from activitysim.chrome import (
    _command_has_profile,
    pid_literal,
    script_activate,
    script_front_pid,
    script_tell_pid,
)
from activitysim.input import can_type
from activitysim.osa import OsaError, run_osascript

# Modest public repositories. Depth-1 clones of these finish quickly.
# Kernels, compilers, and monorepos stay on the GitHub page instead.
CODE_REPOS: tuple[str, ...] = (
    "pallets/flask",
    "pallets/click",
    "encode/httpx",
    "encode/starlette",
    "psf/requests",
    "tiangolo/fastapi",
    "pydantic/pydantic",
    "Kludex/uvicorn",
    "Textualize/textual",
    "pytest-dev/pytest",
    "psf/black",
    "aio-libs/aiohttp",
    "honojs/hono",
    "fastify/fastify",
    "expressjs/express",
    "colinhacks/zod",
    "spf13/cobra",
    "gin-gonic/gin",
    "charmbracelet/bubbletea",
    "charmbracelet/gum",
    "jesseduffield/lazygit",
    "sharkdp/fd",
    "sharkdp/bat",
    "ajeetdsouza/zoxide",
    "jqlang/jq",
    "mikefarah/yq",
    "casey/just",
    "go-task/task",
    "koalaman/shellcheck",
    "BurntSushi/ripgrep",
    "cli/cli",
    "astral-sh/ruff",
    "PyCQA/isort",
    "PyCQA/flake8",
    "tobymao/sqlglot",
    "labstack/echo",
    "rs/zerolog",
    "ahmetb/kubectx",
    "stern/stern",
    "direnv/direnv",
)

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SKIP_DIRS = frozenset({
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".venv",
    "venv",
    "coverage",
    "third_party",
})
_SOURCE_SUFFIXES = frozenset({
    ".py",
    ".go",
    ".rs",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".c",
    ".h",
    ".cc",
    ".hh",
    ".rb",
    ".java",
    ".md",
    ".toml",
    ".yml",
    ".yaml",
})

# Merged into activitysim's editor profile. Other keys in that file stay.
_SETTING_OVERRIDES = {
    "workbench.startupEditor": "none",
    "workbench.welcomePage.walkthroughs.openOnInstall": False,
    "security.workspace.trust.enabled": False,
    "security.workspace.trust.startupPrompt": "never",
    "update.mode": "none",
    "telemetry.telemetryLevel": "off",
    "window.restoreWindows": "none",
    "window.restoreFullscreen": True,
    "window.nativeFullScreen": True,
    "window.confirmBeforeClose": "never",
    # A typed path must not land in the buffer when Quick Open is not focused.
    "files.readonlyInclude": {"*": True, "**/*": True},
    "files.hotExit": "off",
    "extensions.autoUpdate": False,
}

# Fixed script. The profile path is filtered in Python, never interpolated here.
_CODE_LIST_SCRIPT = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "Get-CimInstance -ClassName Win32_Process -Filter \"Name = 'Code.exe'\" "
    "| ForEach-Object { "
    "$cmd = $_.CommandLine; "
    "if ($null -eq $cmd) { $cmd = '' }; "
    "$cmd = $cmd -replace '[\\r\\n\\t\\v\\f\\u001c\\u001d\\u001e\\u0085\\u2028\\u2029]', ' '; "
    "Write-Output ($_.ProcessId.ToString() + [char]9 + $cmd) "
    "}"
)


def repo_ok(name: str) -> bool:
    """True for a single owner/name with no traversal."""

    if not isinstance(name, str) or not _REPO.fullmatch(name):
        return False
    owner, repo = name.split("/")
    if owner in {".", ".."} or repo in {".", ".."}:
        return False
    return True


def real_editor_profile(
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """The signed-in Visual Studio Code profile. This directory is never launched."""

    which = sys.platform if system is None else system
    root_home = Path.home() if home is None else home
    values = os.environ if env is None else env
    if which == "win32":
        roaming = values.get("APPDATA") or str(root_home / "AppData" / "Roaming")
        return Path(roaming) / "Code"
    return root_home / "Library/Application Support/Code"


def editor_profile_path(
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """User-data directory for activitysim's own Code window."""

    return _activitysim_dir("vscode", system, env, home)


def code_sandbox_path(
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Where shallow clones are stored. Not a project the user already had open."""

    return _activitysim_dir("code", system, env, home)


def _activitysim_dir(
    name: str,
    system: str | None,
    env: Mapping[str, str] | None,
    home: Path | None,
) -> Path:
    which = sys.platform if system is None else system
    root_home = Path.home() if home is None else home
    values = os.environ if env is None else env
    if which == "win32":
        local = values.get("LOCALAPPDATA") or str(root_home / "AppData" / "Local")
        return Path(local) / "activitysim" / name
    return root_home / "Library/Application Support/activitysim" / name


def assert_editor_profile(profile: Path, real: Path | None = None) -> Path:
    """Refuse the signed-in Code directory and any path that contains it."""

    chosen = profile.expanduser().resolve()
    signed_in = real_editor_profile() if real is None else real.expanduser().resolve()
    if chosen == signed_in or signed_in in chosen.parents or chosen in signed_in.parents:
        raise ValueError("refusing the signed-in Visual Studio Code profile")
    if chosen == Path(chosen.anchor):
        raise ValueError("refusing the signed-in Visual Studio Code profile")
    return chosen


def code_candidates(
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[Path, ...]:
    which = sys.platform if system is None else system
    root_home = Path.home() if home is None else home
    values = os.environ if env is None else env
    if which == "win32":
        program_files = values.get("PROGRAMFILES") or r"C:\Program Files"
        program_files_x86 = values.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)"
        local = values.get("LOCALAPPDATA") or str(root_home / "AppData" / "Local")
        return (
            Path(local) / "Programs" / "Microsoft VS Code" / "Code.exe",
            Path(program_files) / "Microsoft VS Code" / "Code.exe",
            Path(program_files_x86) / "Microsoft VS Code" / "Code.exe",
        )
    return (
        Path("/Applications/Visual Studio Code.app"),
        root_home / "Applications" / "Visual Studio Code.app",
    )


def editor_binary(app: Path) -> Path | None:
    """The executable inside an install. A .app is not launched via `open`."""

    if app.suffix.lower() == ".exe":
        return app if app.is_file() else None
    if app.suffix == ".app":
        for name in ("Code", "Electron"):
            binary = app / "Contents" / "MacOS" / name
            if binary.is_file():
                return binary
    return None


def find_code(
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path | None:
    for path in code_candidates(system, env, home):
        binary = editor_binary(path)
        if binary is not None:
            return binary
    return None


def launch_code_command(binary: Path, folder: Path, profile: Path, real: Path | None = None) -> list[str]:
    """Argv that reuses activitysim's one Code window. The signed-in profile is not named."""

    chosen = assert_editor_profile(profile, real)
    target = folder.expanduser().resolve()
    if not target.is_dir():
        raise ValueError("refusing to open a missing folder")
    if chosen == target or chosen in target.parents or target in chosen.parents:
        raise ValueError("refusing to open the editor profile as a folder")
    return [
        str(binary),
        "--reuse-window",
        "--disable-workspace-trust",
        f"--user-data-dir={chosen}",
        str(target),
    ]


def prepare_profile(profile: Path, real: Path | None = None) -> Path:
    """Create activitysim's editor profile and keep one quiet fullscreen window."""

    root = assert_editor_profile(profile, real)
    user = root / "User"
    user.mkdir(parents=True, exist_ok=True)
    settings = user / "settings.json"
    current: dict = {}
    if settings.exists():
        try:
            loaded = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return root
        if not isinstance(loaded, dict):
            return root
        current = loaded
    changed = not settings.is_file()
    for key, value in _SETTING_OVERRIDES.items():
        if current.get(key) != value:
            current[key] = value
            changed = True
    if changed:
        settings.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return root


def matches_editor_command(command: str, profile: Path) -> bool:
    """True for activitysim's Code process, not a helper and not the signed-in app."""

    if "--type=" in command:
        return False
    if not _command_has_profile(command, profile):
        return False
    from activitysim.winchrome import basename, executable_token

    name = basename(executable_token(command)).lower()
    if name in {"code", "code.exe"}:
        return True
    lowered = command.lower()
    if "google chrome" in lowered or "chrome.exe" in lowered:
        return False
    return "visual studio code.app/contents/macos/code" in lowered


def editor_pids_from_ps(text: str, profile: Path) -> list[int]:
    """Parse `ps` lines of `pid command`. Helpers and other profiles are skipped."""

    found: list[int] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        pid_text, _, command = raw.partition(" ")
        if not pid_text.isdigit():
            continue
        if matches_editor_command(command.strip(), profile):
            found.append(int(pid_text))
    return found


def editor_pids_from_rows(text: str, profile: Path) -> list[int]:
    """Parse Windows `pid<TAB>command` rows for Code.exe on this profile."""

    from activitysim.winchrome import iter_process_rows

    return [pid for pid, command in iter_process_rows(text) if matches_editor_command(command, profile)]


def code_process_argv() -> list[str]:
    """Argv for the Code.exe listing. It does not contain a profile path."""

    from activitysim.winchrome import powershell_path

    return [
        str(powershell_path()),
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        _CODE_LIST_SCRIPT,
    ]


def editor_pids(profile: Path) -> list[int]:
    """Main activitysim Code processes. The signed-in Code is not included."""

    chosen = assert_editor_profile(profile)
    if sys.platform == "win32":
        from activitysim.winchrome import decode_process_output

        exe_list = code_process_argv()
        exe = Path(exe_list[0])
        if not exe.is_file():
            return []
        kwargs: dict = {"capture_output": True, "check": False, "timeout": 5}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flags:
            kwargs["creationflags"] = flags
        try:
            result = subprocess.run(exe_list, **kwargs)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        return editor_pids_from_rows(decode_process_output(result.stdout or b""), chosen)
    result = subprocess.run(
        ["ps", "-axww", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    return editor_pids_from_ps(result.stdout, chosen)


def stop_editor(profile: Path) -> None:
    """Close activitysim's Code windows. The signed-in Code is not signaled.

    A save prompt keeps the process alive across SIGTERM. Don't Save is
    clicked first. A prompt that is still up is killed with the process.
    """

    chosen = assert_editor_profile(profile)
    windows = sys.platform == "win32"
    if windows:
        from activitysim.winchrome import clear_process_cache, request_close

        clear_process_cache()
    else:
        for pid in editor_pids(chosen):
            _dismiss_editor_save(pid)
    for pid in editor_pids(chosen):
        try:
            if windows:
                request_close(pid)
            else:
                os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
    deadline = time.time() + 3
    while time.time() < deadline and editor_pids(chosen):
        time.sleep(0.1)
    if windows:
        for pid in editor_pids(chosen):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                continue
        return
    for pid in editor_pids(chosen):
        _dismiss_editor_save(pid)
    deadline = time.time() + 1
    while time.time() < deadline and editor_pids(chosen):
        time.sleep(0.1)
    for pid in editor_pids(chosen):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            continue


def checkout_path(root: Path, repo: str) -> Path:
    """owner/name under the sandbox. `..` and absolute names are refused."""

    if not repo_ok(repo):
        raise ValueError(f"refusing to clone {repo!r}")
    owner, name = repo.split("/")
    base = root.expanduser().resolve()
    dest = (base / owner / name).resolve()
    if dest != base and base not in dest.parents:
        raise ValueError("refusing a checkout outside the sandbox")
    return dest


def clone_command(repo: str, dest: Path) -> list[str]:
    """Shallow clone of one public GitHub repository. No shell."""

    if not repo_ok(repo):
        raise ValueError(f"refusing to clone {repo!r}")
    return [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--quiet",
        "--",
        f"https://github.com/{repo}",
        str(dest),
    ]


def ensure_checkout(
    repo: str,
    root: Path,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    timeout: float = 45,
) -> Path | None:
    """Return an existing checkout, or clone it. None when git cannot do it.

    A directory that is already there without `.git` is left untouched.
    """

    if not repo_ok(repo):
        return None
    try:
        dest = checkout_path(root, repo)
    except ValueError:
        return None
    if (dest / ".git").is_dir():
        return dest
    if dest.exists():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = clone_command(repo, dest)
    caller = subprocess.run if run is None else run
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = caller(
            command,
            timeout=timeout,
            check=False,
            capture_output=True,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        _delete_checkout(root, dest)
        return None
    if getattr(result, "returncode", 1) != 0 or not (dest / ".git").is_dir():
        _delete_checkout(root, dest)
        return None
    return dest


def _delete_checkout(root: Path, dest: Path) -> None:
    base = root.expanduser().resolve()
    try:
        chosen = dest.resolve()
    except OSError:
        return
    if chosen == base or base not in chosen.parents:
        return
    if chosen.is_symlink():
        chosen.unlink()
        return
    if chosen.exists():
        shutil.rmtree(chosen)


def list_sources(root: Path, limit: int = 40) -> list[str]:
    """Relative source paths inside `root`, skipping dependencies and dot dirs."""

    base = root.expanduser().resolve()
    if not base.is_dir():
        return []
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        current = Path(dirpath).resolve()
        if current != base and base not in current.parents:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(
            name for name in dirnames if name not in _SKIP_DIRS and not name.startswith(".")
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            candidate = Path(dirpath) / name
            try:
                relative = candidate.resolve().relative_to(base).as_posix()
            except (ValueError, OSError):
                continue
            if ".." in relative.split("/") or not can_type(relative):
                continue
            found.append(relative)
            if len(found) >= limit:
                return found
    return found


def find_token(relative: str) -> str:
    """A short alphanumeric piece of the file name, safe to type into Find."""

    stem = relative.rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    token = "".join(ch for ch in stem if ch.isalnum())[:12]
    if len(token) < 2 or not can_type(token):
        return ""
    return token


def parse_origin_size(text: str) -> tuple[float, float, float, float] | None:
    """AppleScript `x y width height` into a left, top, right, bottom frame."""

    parts = text.replace(",", " ").split()
    if len(parts) != 4:
        return None
    try:
        x, y, width, height = (float(part) for part in parts)
    except ValueError:
        return None
    if width < 400 or height < 300:
        return None
    return (x, y, x + width, y + height)


def script_editor_fullscreen(pid: int) -> str:
    """Enter full screen for this pid. A window that is already full screen is left there.

    Visual Studio Code has no "Enter Full Screen" item. View > Appearance >
    Full Screen is a toggle, so it is clicked only when AXFullScreen is false.
    AppleScript does not send keystrokes.
    """

    return script_tell_pid(
        pid,
        """
set frontmost to true
delay 0.4
set bestIndex to 0
set bestArea to 0
set isFull to false
set readable to false
set nth to 0
repeat with w in windows
  set nth to nth + 1
  try
    set s to size of w
    set wide to item 1 of s
    set high to item 2 of s
    if high > 200 then
      set area to wide * high
      if area > bestArea then
        set bestArea to area
        set bestIndex to nth
      end if
      try
        if (value of attribute "AXFullScreen" of w) is true then
          set isFull to true
        else
          set readable to true
        end if
      end try
    end if
  end try
end repeat
if isFull then return "fullscreen"
if bestIndex is 0 then return "absent"
if readable is false then return "absent"
set best to window bestIndex
try
  set value of attribute "AXFullScreen" of best to true
end try
try
  if (value of attribute "AXFullScreen" of best) is true then return "fullscreen"
end try
set entered to false
repeat with mi in menu items of menu "View" of menu bar 1
  try
    set itemName to name of mi as text
  on error
    set itemName to ""
  end try
  if itemName is "Exit Full Screen" then return "fullscreen"
  if itemName is "Enter Full Screen" then
    click mi
    set entered to true
    exit repeat
  end if
end repeat
if entered then return "entered"
try
  click menu item "Full Screen" of menu 1 of menu item "Appearance" of menu "View" of menu bar 1
  set entered to true
end try
if entered then return "entered"
return "absent"
""",
    )


def script_dismiss_save(pid: int) -> str:
    """Click Don't Save on this pid. Save and Save Anyway are not clicked.

    The button name uses a straight apostrophe. macOS may show a curly one.
    """

    number = pid_literal(pid)
    curly = "Don\u2019t Save"
    return f"""tell application "System Events"
  set targetProc to missing value
  repeat with proc in processes
    try
      if (unix id of proc) is equal to {number} then
        set targetProc to proc
        exit repeat
      end if
    end try
  end repeat
  if targetProc is missing value then error "activitysim process is not open"
  tell targetProc
    set frontmost to true
    delay 0.4
    repeat with w in windows
      set hit to false
      tell me to set hit to clickDontSave(w)
      if hit then return "dismissed"
      try
        repeat with sh in sheets of w
          tell me to set hit to clickDontSave(sh)
          if hit then return "dismissed"
          try
            repeat with g in groups of sh
              tell me to set hit to clickDontSave(g)
              if hit then return "dismissed"
            end repeat
          end try
        end repeat
      end try
    end repeat
  end tell
end tell
return "clear"

on clickDontSave(el)
  tell application "System Events"
    try
      repeat with b in buttons of el
        try
          set label to name of b as text
        on error
          set label to ""
        end try
        if label starts with "Don't Save" or label starts with "{curly}" then
          click b
          return true
        end if
      end repeat
    end try
  end tell
  return false
end clickDontSave
"""


def _dismiss_editor_save(pid: int) -> bool:
    """True when Don't Save was clicked. A missing window is not an error."""

    if sys.platform == "win32":
        return False
    try:
        state = run_osascript(script_dismiss_save(pid), timeout=8).strip()
    except (OsaError, ValueError):
        return False
    return state == "dismissed"


def script_largest_window(pid: int) -> str:
    """Bounds of the largest window for this pid, not a small palette."""

    return script_tell_pid(
        pid,
        """
set best to ""
set bestArea to 0
repeat with w in windows
  try
    set s to size of w
    set area to (item 1 of s) * (item 2 of s)
    if area > bestArea then
      set bestArea to area
      set p to position of w
      set best to (item 1 of p as text) & " " & (item 2 of p as text) & " " & (item 1 of s as text) & " " & (item 2 of s as text)
    end if
  end try
end repeat
return best
""",
    )


class CodeDeck:
    """Repositories to check out, without repeating until the list is used."""

    def __init__(self, rng: random.Random, repos: tuple[str, ...] = CODE_REPOS) -> None:
        self.rng = rng
        self._repos = tuple(repo for repo in repos if repo_ok(repo))
        self._queue: list[str] = []
        self.seen: set[str] = set()
        self._last: str | None = None

    def next(self) -> str:
        for _ in range(2):
            while self._queue:
                repo = self._queue.pop()
                if repo.lower() not in self.seen:
                    self.seen.add(repo.lower())
                    self._last = repo.lower()
                    return repo
            self._refill()
        repo = self._repos[0]
        self.seen.add(repo.lower())
        self._last = repo.lower()
        return repo

    def _refill(self) -> None:
        pool = [repo for repo in self._repos if repo.lower() not in self.seen]
        if not pool:
            keep = self._last
            self.seen.clear()
            if keep:
                self.seen.add(keep)
            pool = [repo for repo in self._repos if repo.lower() not in self.seen]
        if not pool:
            self.seen.clear()
            pool = list(self._repos)
        self.rng.shuffle(pool)
        self._queue = pool


class GitWork:
    """Shallow clones into the sandbox. `run` is injectable in tests."""

    def __init__(
        self,
        rng: random.Random,
        root: Path | None = None,
        run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.rng = rng
        self.root = code_sandbox_path() if root is None else root
        self._run = run
        self.deck = CodeDeck(rng)

    def ready(self) -> bool:
        return shutil.which("git") is not None

    def next_repo(self) -> str:
        return self.deck.next()

    def checkout(self, repo: str) -> Path | None:
        return ensure_checkout(repo, self.root, self._run)

    def files(self, folder: Path | str) -> list[str]:
        return list_sources(Path(folder))


class CodeEditor:
    """One Visual Studio Code process on activitysim's profile."""

    def __init__(self) -> None:
        self.pid: int | None = None
        self.folder: str | None = None
        self._process: subprocess.Popen[str] | None = None

    def installed(self) -> bool:
        return find_code() is not None

    def open_folder(self, folder: str) -> None:
        binary = find_code()
        if binary is None:
            raise OsaError("Visual Studio Code is not installed")
        profile_path = editor_profile_path()
        running = self.pid is not None and self.pid in editor_pids(profile_path)
        if not running:
            # A previous run can leave several windows. Close those, then open one.
            # Settings are written after that exit so Code does not overwrite them.
            stop_editor(profile_path)
            self._process = None
            self.pid = None
        profile = prepare_profile(profile_path)
        command = launch_code_command(binary, Path(folder), profile)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._adopt(profile, process)
        time.sleep(0.4 if running else 1.0)
        self.folder = str(folder)
        # Reusing the window with a dirty file raises Save / Don't Save and
        # drops full screen. Discard the edit, then enter full screen.
        self.dismiss_save_prompt()
        self.enter_fullscreen(settle=True)
        if self.dismiss_save_prompt():
            self.enter_fullscreen()

    def _adopt(self, profile: Path, process: subprocess.Popen[str]) -> None:
        deadline = time.time() + 8
        while time.time() < deadline:
            found = editor_pids(profile)
            if process.pid in found:
                self._process = process
                self.pid = process.pid
                return
            if found:
                self.pid = found[-1]
                if self._process is None or self._process.poll() is not None:
                    self._process = process
                return
            if process.poll() is not None:
                break
            time.sleep(0.1)
        if self.pid is None:
            self._process = process
            self.pid = process.pid

    def dismiss_save_prompt(self) -> bool:
        """Click Don't Save when Code asks. Save is not clicked."""

        if not self.pid:
            return False
        return _dismiss_editor_save(self.pid)

    def enter_fullscreen(self, settle: bool = False) -> None:
        """Make activitysim's Code window full screen. A second call does not leave it.

        `settle` waits through the startup moment when Code leaves full screen
        again. Later calls only enter when the window is not already there.
        """

        if not self.pid:
            return
        if sys.platform == "win32":
            from activitysim.winchrome import maximize

            try:
                maximize(self.pid)
            except OSError:
                return
            return
        attempts = 4 if settle else 1
        stable = 0
        for _ in range(attempts):
            try:
                state = run_osascript(script_editor_fullscreen(self.pid), timeout=8).strip()
            except OsaError:
                return
            if state == "fullscreen":
                stable += 1
                if not settle or stable >= 2:
                    return
                time.sleep(0.4)
                continue
            stable = 0
            if settle:
                time.sleep(0.35)

    def activate(self) -> None:
        if not self.pid:
            raise OsaError("Visual Studio Code is not open")
        if sys.platform == "win32":
            from activitysim.winchrome import activate as activate_window

            try:
                activate_window(self.pid)
            except OSError as exc:
                raise OsaError(str(exc)) from exc
            return
        run_osascript(script_activate(self.pid))

    def is_front(self) -> bool:
        if not self.pid:
            return False
        if sys.platform == "win32":
            from activitysim.winchrome import is_front as window_is_front

            try:
                return window_is_front(self.pid)
            except OSError:
                return False
        try:
            front = run_osascript(script_front_pid()).strip()
        except OsaError:
            return False
        return front.isdigit() and int(front) == self.pid

    def bounds(self) -> tuple[float, float, float, float] | None:
        if not self.pid:
            return None
        if sys.platform == "win32":
            from activitysim.winchrome import window_bounds

            try:
                return window_bounds(self.pid)
            except OSError:
                return None
        try:
            text = run_osascript(script_largest_window(self.pid))
        except OsaError:
            return None
        return parse_origin_size(text)
