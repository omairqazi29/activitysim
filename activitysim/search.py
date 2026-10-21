"""GitHub advanced search: queries, click zones, and which links are safe to open.

Result clicks stay out of the right-hand column, where Star, Fork, and Watch
live. The search box is a separate band under the browser toolbar.
"""

from __future__ import annotations

import random
import urllib.parse
from urllib.parse import urlsplit

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


def in_search_zone(point: Point, bounds: Rect) -> bool:
    x, y = point
    left, top, right, _bottom = bounds
    return (left + 60) <= x <= (right - 60) and (top + 110) <= y <= (top + 280)


def in_result_zone(point: Point, bounds: Rect) -> bool:
    """Repository titles. Stars sit further right, past 62% of the window."""

    x, y = point
    left, top, _right, bottom = bounds
    width = _width(bounds)
    return (
        (left + max(120.0, width * 0.18)) <= x <= (left + width * 0.62)
        and (top + 160) <= y <= (bottom - 40)
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


def in_readme_zone(point: Point, bounds: Rect) -> bool:
    x, y = point
    left, top, right, bottom = bounds
    width = _width(bounds)
    height = _height(bounds)
    return (
        (left + 80) <= x <= (left + width * 0.62)
        and (top + height * 0.38) <= y <= (bottom - 70)
    )


def search_box_point(bounds: Rect) -> Point | None:
    left, top, right, _bottom = bounds
    point = (left + _width(bounds) * 0.42, top + 175)
    if in_search_zone(point, bounds):
        return point
    return None


def result_points(bounds: Rect, count: int = 6) -> list[Point]:
    left, top, _right, bottom = bounds
    x = left + _width(bounds) * 0.50
    y = top + 310
    points: list[Point] = []
    while len(points) < count and y <= bottom - 40:
        point = (x, y)
        if in_result_zone(point, bounds):
            points.append(point)
        y += 96
    return points


def readme_point(bounds: Rect, rng: random.Random) -> Point | None:
    left, top, _right, bottom = bounds
    width = _width(bounds)
    height = _height(bounds)
    point = (
        left + width * rng.uniform(0.36, 0.55),
        top + height * rng.uniform(0.48, 0.72),
    )
    if in_readme_zone(point, bounds) and point[1] < bottom - 70:
        return point
    return None


def accept_result(url: str, title: str, point: Point, bounds: Rect) -> bool:
    if bad_label(title) or bad_label(url):
        return False
    if not is_result_href(url):
        return False
    return in_result_zone(point, bounds)
