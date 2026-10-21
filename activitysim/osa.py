"""Run AppleScript. Used for Chrome and for reading the frontmost app."""

from __future__ import annotations

import subprocess


class OsaError(RuntimeError):
    """osascript failed, timed out, or returned an error."""


def run_osascript(source: str, timeout: float = 20) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-"],
            input=source,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OsaError("osascript timed out") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "osascript failed").strip()
        raise OsaError(detail)
    return result.stdout.strip()


def as_text(value: str) -> str:
    """Quote a string for an AppleScript literal.

    Backslashes are escaped first so a quote escaped as \\" stays a quote.
    """

    if "\n" in value or "\r" in value:
        raise ValueError("newlines are not allowed in AppleScript strings")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


FRONTMOST_SCRIPT = """
tell application "System Events"
  return name of first application process whose frontmost is true
end tell
"""


def frontmost_app() -> str | None:
    try:
        name = run_osascript(FRONTMOST_SCRIPT).strip()
    except OsaError:
        return None
    return name or None
