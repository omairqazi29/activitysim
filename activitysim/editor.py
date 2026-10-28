"""Check out a public repository and open it in a separate Visual Studio Code.

The checkout lives under activitysim's own directory. The editor is a second
instance with its own user-data directory, not the signed-in Code profile.
Quick Open and Find type only paths and tokens that came from that checkout.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Mapping

from activitysim.chrome import pid_literal, script_activate, script_front_pid
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

# Written only inside activitysim's editor profile, before the first launch.
_SETTINGS = """{
  "workbench.startupEditor": "none",
  "workbench.welcomePage.walkthroughs.openOnInstall": false,
  "security.workspace.trust.enabled": false,
  "security.workspace.trust.startupPrompt": "never",
  "update.mode": "none",
  "telemetry.telemetryLevel": "off",
  "window.restoreWindows": "none",
  "extensions.autoUpdate": false
}
"""


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
    """Argv for one new window on `folder`, using activitysim's profile only."""

    chosen = assert_editor_profile(profile, real)
    target = folder.expanduser().resolve()
    if not target.is_dir():
        raise ValueError("refusing to open a missing folder")
    if chosen == target or chosen in target.parents or target in chosen.parents:
        raise ValueError("refusing to open the editor profile as a folder")
    return [
        str(binary),
        "--new-window",
        "--disable-workspace-trust",
        f"--user-data-dir={chosen}",
        str(target),
    ]


def prepare_profile(profile: Path, real: Path | None = None) -> Path:
    """Create activitysim's editor profile and a quiet settings file, nowhere else."""

    root = assert_editor_profile(profile, real)
    user = root / "User"
    user.mkdir(parents=True, exist_ok=True)
    settings = user / "settings.json"
    if not settings.exists():
        settings.write_text(_SETTINGS, encoding="utf-8")
    return root


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


def script_largest_window(pid: int) -> str:
    """Bounds of the largest window for this pid, not a small palette."""

    number = pid_literal(pid)
    return f"""
tell application "System Events"
  tell (first process whose unix id is {number})
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
  end tell
end tell
"""


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
        profile = prepare_profile(editor_profile_path())
        command = launch_code_command(binary, Path(folder), profile)
        previous = self._process if self._process is not None and self._process.poll() is None else None
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if previous is None:
            self._process = process
            self.pid = process.pid
            time.sleep(1.0)
        else:
            # A second launch on the same profile usually hands the folder to the
            # running instance and exits. Keep the pid that is still alive.
            time.sleep(0.4)
            if previous.poll() is not None and process.poll() is None:
                self._process = process
                self.pid = process.pid
        self.folder = str(folder)

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
