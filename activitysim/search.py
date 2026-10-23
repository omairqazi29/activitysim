"""GitHub advanced search: queries, click zones, and which links are safe to open.

Result clicks stay out of the right-hand column, where Star, Fork, and Watch
live. The search box is a separate band under the browser toolbar.
"""

from __future__ import annotations

import random
import urllib.parse
from urllib.parse import urlsplit

from activitysim.input import can_type

Rect = tuple[float, float, float, float]
Point = tuple[float, float]

ADVANCED_URL = "https://github.com/search/advanced"

QUERIES: tuple[str, ...] = (
    "language:python stars:>1000",
    "language:rust stars:>200",
    "language:go stars:>500",
    "language:typescript stars:>300",
    "language:javascript stars:>1000",
    "language:python asyncio",
    "language:go kubernetes",
    "topic:cli stars:>100",
    "topic:macos stars:>50",
    "org:python",
)

_SKIP_OWNERS = frozenset({
    "search",
    "topics",
    "settings",
    "login",
    "signup",
    "features",
    "marketplace",
    "explore",
    "orgs",
    "sponsors",
    "about",
    "pricing",
    "customer-stories",
    "readme",
})

_BAD_PHRASES = (
    "star this",
    "fork this",
    "sponsor",
    "unfollow",
    "follow",
    "watch",
    "sign in",
    "sign up",
    "unsubscribe",
    "delete",
)


def results_url(query: str) -> str:
    quoted = urllib.parse.quote_plus(query)
    return f"https://github.com/search?q={quoted}&type=repositories"


def is_advanced_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.netloc.endswith("github.com") and parts.path.rstrip("/") == "/search/advanced"


def is_results_url(url: str) -> bool:
    parts = urlsplit(url)
    if not parts.netloc.endswith("github.com"):
        return False
    return parts.path.rstrip("/") == "/search" and parts.query != ""


def is_repo_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc.endswith("github.com"):
        return False
    pieces = [part for part in parts.path.split("/") if part]
    if len(pieces) < 2:
        return False
    return pieces[0] not in _SKIP_OWNERS


def is_result_href(url: str) -> bool:
    """A repository home link from the search list, not a button or a filter."""

    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc.endswith("github.com"):
        return False
    pieces = [part for part in parts.path.split("/") if part]
    if len(pieces) != 2:
        return False
    owner, name = pieces
    if owner in _SKIP_OWNERS or name in _SKIP_OWNERS:
        return False
    return True


def bad_label(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _BAD_PHRASES)


def _width(bounds: Rect) -> float:
    return bounds[2] - bounds[0]


def _height(bounds: Rect) -> float:
    return bounds[3] - bounds[1]


def in_search_zone(point: Point, bounds: Rect, scale: float = 1.0) -> bool:
    """Search band under the toolbar. scale is physical pixels per CSS pixel."""

    x, y = point
    left, top, right, _bottom = bounds
    return (
        (left + 60 * scale) <= x <= (right - 60 * scale)
        and (top + 110 * scale) <= y <= (top + 280 * scale)
    )


def in_result_zone(point: Point, bounds: Rect, scale: float = 1.0) -> bool:
    """Repository titles. Stars sit further right, past 62% of the window."""

    x, y = point
    left, top, _right, bottom = bounds
    width = _width(bounds)
    return (
        (left + max(120.0 * scale, width * 0.18)) <= x <= (left + width * 0.62)
        and (top + 160 * scale) <= y <= (bottom - 40 * scale)
    )


def content_frame(windows: list[Rect]) -> Rect | None:
    """The page window. Menu-bar strips and popovers are too small to use."""

    best: Rect | None = None
    best_area = 0.0
    for left, top, right, bottom in windows:
        width = right - left
        height = bottom - top
        if width < 400 or height < 300:
            continue
        area = width * height
        if area > best_area:
            best_area = area
            best = (left, top, right, bottom)
    return best


def in_readme_zone(point: Point, bounds: Rect, scale: float = 1.0) -> bool:
    x, y = point
    left, top, right, bottom = bounds
    width = _width(bounds)
    height = _height(bounds)
    return (
        (left + 80 * scale) <= x <= (left + width * 0.62)
        and (top + height * 0.38) <= y <= (bottom - 70 * scale)
    )


def search_box_point(bounds: Rect, scale: float = 1.0) -> Point | None:
    left, top, right, _bottom = bounds
    point = (left + _width(bounds) * 0.42, top + 175 * scale)
    if in_search_zone(point, bounds, scale):
        return point
    return None


def result_points(bounds: Rect, count: int = 6, scale: float = 1.0) -> list[Point]:
    left, top, _right, bottom = bounds
    x = left + _width(bounds) * 0.50
    y = top + 310 * scale
    points: list[Point] = []
    limit = bottom - 40 * scale
    while len(points) < count and y <= limit:
        point = (x, y)
        if in_result_zone(point, bounds, scale):
            points.append(point)
        y += 96 * scale
    return points


LANGUAGES: tuple[str, ...] = (
    "python", "rust", "go", "typescript", "javascript", "java", "ruby",
    "kotlin", "swift", "scala", "haskell", "elixir", "zig", "lua", "php",
    "csharp", "dart", "ocaml", "clojure", "perl", "r", "julia", "nim",
    "erlang", "solidity", "shell", "css", "html", "c", "cpp",
)

# Narrow star bands on these languages often return an empty page.
_RARE_LANGUAGES = frozenset({
    "zig", "nim", "ocaml", "erlang", "solidity", "haskell", "elixir",
    "julia", "clojure", "perl",
})

_STAR_BANDS: tuple[str, ...] = (
    "stars:5..40",
    "stars:40..120",
    "stars:120..400",
    "stars:400..1500",
    "stars:1500..8000",
    "stars:>8000",
)

_WIDE_BANDS: tuple[str, ...] = (
    "stars:20..200",
    "stars:200..2000",
    "stars:>500",
)

_TOPICS: tuple[str, ...] = (
    "cli", "parser", "compiler", "database", "editor", "http", "crypto",
    "tui", "sqlite", "wasm", "testing", "async", "docker", "kubernetes",
    "game", "lsp", "neovim", "emacs",
)

_RECENCY: tuple[str, ...] = (
    "pushed:>2024-01-01",
    "pushed:>2025-01-01",
)


class QueryDeck:
    """Search text that is not drawn again until the rest of the deck is used.

    `language:python stars:>1000` returns the same front page every time.
    Smaller star bands, topics, and recency filters move that page.
    """

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.used: set[str] = set()
        self.queue: list[str] = []
        self.last = ""

    def next(self) -> str:
        if not self.queue:
            self._fill()
        query = self.queue.pop()
        if query == self.last and self.queue:
            self.queue.insert(0, query)
            query = self.queue.pop()
        self.last = query
        return query

    def _fill(self) -> None:
        batch = self._gather()
        if len(batch) < 8:
            self.used.clear()
            batch = self._gather()
        if not batch:
            batch = [query for query in QUERIES if can_type(query) and can_type(results_url(query))]
        self.used.update(batch)
        self.rng.shuffle(batch)
        self.queue = batch

    def _gather(self) -> list[str]:
        batch: list[str] = []
        seen = set(self.used)
        for _ in range(500):
            if len(batch) >= 24:
                break
            query = self._compose()
            if query in seen:
                continue
            if not can_type(query) or not can_type(results_url(query)):
                continue
            seen.add(query)
            batch.append(query)
        return batch

    def _compose(self) -> str:
        language = self.rng.choice(LANGUAGES)
        rare = language in _RARE_LANGUAGES
        band = self.rng.choice(_WIDE_BANDS if rare else _STAR_BANDS)
        parts = [f"language:{language}", band]
        if not rare and self.rng.random() < 0.55:
            parts.append(f"topic:{self.rng.choice(_TOPICS)}")
        if self.rng.random() < 0.45:
            parts.append("fork:false")
        if not rare and self.rng.random() < 0.3:
            parts.append(self.rng.choice(_RECENCY))
        return " ".join(parts)


def readme_point(bounds: Rect, rng: random.Random, scale: float = 1.0) -> Point | None:
    left, top, _right, bottom = bounds
    width = _width(bounds)
    height = _height(bounds)
    point = (
        left + width * rng.uniform(0.36, 0.55),
        top + height * rng.uniform(0.48, 0.72),
    )
    if in_readme_zone(point, bounds, scale) and point[1] < bottom - 70 * scale:
        return point
    return None


def accept_result(url: str, title: str, point: Point, bounds: Rect, scale: float = 1.0) -> bool:
    if bad_label(title) or bad_label(url):
        return False
    if not is_result_href(url):
        return False
    return in_result_zone(point, bounds, scale)
