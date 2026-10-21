"""Public pages the simulator is allowed to open.

Every built-in URL is https. Extra URLs from a file have to be http(s) with no
username or password. javascript:, file:, and other schemes are dropped.
"""

from __future__ import annotations

import random
import urllib.parse
from pathlib import Path

REPOS: tuple[str, ...] = (
    "torvalds/linux",
    "python/cpython",
    "golang/go",
    "kubernetes/kubernetes",
    "microsoft/vscode",
    "facebook/react",
    "tensorflow/tensorflow",
    "rust-lang/rust",
    "django/django",
    "vercel/next.js",
    "neovim/neovim",
    "nodejs/node",
    "git/git",
    "apple/swift",
    "hashicorp/terraform",
    "prometheus/prometheus",
    "ansible/ansible",
    "home-assistant/core",
    "astral-sh/uv",
    "oven-sh/bun",
    "pallets/flask",
    "encode/httpx",
    "psf/requests",
    "numpy/numpy",
    "pandas-dev/pandas",
)

# These paths exist on every GitHub repo page, including empty ones.
REPO_SUBPATHS: tuple[str, ...] = (
    "",
    "/issues",
    "/pulls",
    "/commits",
    "/releases",
    "/graphs/contributors",
)

TOPICS: tuple[str, ...] = (
    "python",
    "rust",
    "macos",
    "kubernetes",
    "typescript",
    "go",
    "machine-learning",
    "cli",
    "sqlite",
)

QUERIES: tuple[str, ...] = (
    "python asyncio tutorial",
    "git rebase explained",
    "how github actions cache works",
    "rust borrow checker examples",
    "kubernetes deployment vs statefulset",
    "sqlite wal mode",
    "macos accessibility permission terminal",
    "http caching headers",
    "regex lookaround examples",
    "cpython garbage collection",
)

WANDER: tuple[str, ...] = (
    "https://github.com/trending",
    "https://github.com/trending/python",
    "https://github.com/trending/rust",
    "https://github.com/explore",
    "https://news.ycombinator.com",
    "https://news.ycombinator.com/newest",
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://docs.python.org/3/tutorial/index.html",
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide",
    "https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview",
)


def safe_url(url: str) -> bool:
    if any(ch in url for ch in "\n\r\t "):
        return False
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return False
    if not parts.netloc or parts.username or parts.password:
        return False
    return True


def load_url_file(path: Path) -> list[str]:
    found: list[str] = []
    text = path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if safe_url(line):
            found.append(line)
    return found


def _search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)


class Places:
    """A short trail of related pages, then a new subject."""

    def __init__(self, rng: random.Random, extra_urls: list[str] | None = None) -> None:
        self.rng = rng
        self.extra = [url for url in (extra_urls or []) if safe_url(url)]
        self._queue: list[str] = []

    def next(self) -> str:
        if not self._queue:
            self._queue = self._burst()
        url = self._queue.pop(0)
        if not safe_url(url):
            raise RuntimeError(f"refusing to open {url!r}")
        return url

    def _burst(self) -> list[str]:
        kinds = ["repo", "repo", "repo", "topic", "search", "wander"]
        if self.extra:
            kinds.append("extra")
        kind = self.rng.choice(kinds)
        if kind == "repo":
            return self._repo_burst()
        if kind == "topic":
            topic = self.rng.choice(TOPICS)
            return [f"https://github.com/topics/{topic}"]
        if kind == "search":
            return [_search_url(self.rng.choice(QUERIES))]
        if kind == "extra":
            return [self.rng.choice(self.extra)]
        return [self.rng.choice(WANDER)]

    def _repo_burst(self) -> list[str]:
        repo = self.rng.choice(REPOS)
        extras = list(REPO_SUBPATHS[1:])
        self.rng.shuffle(extras)
        paths = [""] + extras[: self.rng.randint(1, 3)]
        return [f"https://github.com/{repo}{path}" for path in paths]


