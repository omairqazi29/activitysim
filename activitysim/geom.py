"""Pointer geometry that does not post events.

Clicks are restricted to the tab strip of the simulator's own window, clear of
the traffic lights and of the screen corners that trigger hot corners.
"""

from __future__ import annotations

import math
import random

Rect = tuple[float, float, float, float]
Point = tuple[float, float]


def pointer_moved(previous: Point | None, current: Point | None, tolerance: float = 36) -> bool:
    if previous is None or current is None:
        return False
    return math.hypot(current[0] - previous[0], current[1] - previous[1]) > tolerance


def user_left_chrome(last_front: str | None, front: str | None) -> bool:
    """True when Chrome was front at the end of our last action and no longer is."""

    return last_front == "Google Chrome" and front not in (None, "Google Chrome")


def away_from_hot_corners(point: Point, displays: list[Rect], margin: float = 80) -> bool:
    x, y = point
    for left, top, right, bottom in displays:
        corners = ((left, top), (right, top), (left, bottom), (right, bottom))
        for cx, cy in corners:
            if abs(x - cx) < margin and abs(y - cy) < margin:
                return False
    return True


def in_tab_strip(point: Point, bounds: Rect) -> bool:
    """Tab bodies only: right of the traffic lights, left of the new-tab button."""

    x, y = point
    left, top, right, _bottom = bounds
    return (left + 100) <= x <= (right - 90) and (top + 12) <= y <= (top + 30)


def tab_strip_point(bounds: Rect, displays: list[Rect], rng: random.Random) -> Point | None:
    left, top, right, bottom = bounds
    if right - left < 220 or bottom - top < 160:
        return None
    for _ in range(8):
        point = (
            rng.uniform(left + 100, right - 90),
            rng.uniform(top + 12, top + 30),
        )
        if in_tab_strip(point, bounds) and away_from_hot_corners(point, displays):
            return point
    return None


def hover_point(bounds: Rect, displays: list[Rect], rng: random.Random) -> Point | None:
    """A point over page content, below the toolbar, for moving and scrolling.

    This point is never clicked. Scrolling uses the wheel while the pointer
    hovers here, so a link is not activated.
    """

    left, top, right, bottom = bounds
    x0, y0 = left + 100, top + 160
    x1, y1 = right - 70, bottom - 100
    if x1 <= x0 or y1 <= y0:
        return None
    for _ in range(8):
        point = (rng.uniform(x0, x1), rng.uniform(y0, y1))
        if away_from_hot_corners(point, displays):
            return point
    return None


def wander_target(
    current: Point,
    screen: Rect,
    rng: random.Random,
    reach: float = 280,
) -> Point | None:
    left, top, right, bottom = screen
    for _ in range(8):
        x = min(max(current[0] + rng.uniform(-reach, reach), left + 80), right - 80)
        y = min(max(current[1] + rng.uniform(-reach, reach), top + 80), bottom - 80)
        point = (x, y)
        if away_from_hot_corners(point, [screen]):
            return point
    return None


def bezier_path(start: Point, end: Point, rng: random.Random) -> list[Point]:
    """A short eased curve from start to end. The last point is exactly end."""

    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = math.hypot(dx, dy)
    if distance < 1:
        return [end]
    px, py = -dy / distance, dx / distance
    bend = rng.uniform(-0.25, 0.25) * distance
    bend2 = rng.uniform(-0.25, 0.25) * distance
    c1 = (start[0] + dx * 0.3 + px * bend, start[1] + dy * 0.3 + py * bend)
    c2 = (start[0] + dx * 0.7 + px * bend2, start[1] + dy * 0.7 + py * bend2)
    steps = max(8, min(60, int(distance / 12)))
    points: list[Point] = []
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)
        if i == steps:
            points.append(end)
            break
        points.append(_cubic(start, c1, c2, end, t))
    return points


def _cubic(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    u = 1 - t
    x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
    return (x, y)


def next_gap(rng: random.Random, min_gap: float, max_gap: float) -> float:
    """Most pauses sit between the two gaps. A few run longer, like reading."""

    if rng.random() < 0.15:
        return rng.uniform(max_gap, max_gap * 3)
    return rng.uniform(min_gap, max_gap)
