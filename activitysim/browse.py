"""Turn a page link into a screen point the pointer can click.

The signed-out page reports the link's box. The box is mapped onto the
window the same way the GitHub result list is. A missing link is not a guess.
"""

from __future__ import annotations

import json

from activitysim.sites import allowed_url
from activitysim.winpage import css_point_in_window

Rect = tuple[float, float, float, float]
Point = tuple[float, float]


def locate_link(browser, url: str) -> Point | None:
    try:
        bounds = browser.bounds()
    except (OSError, RuntimeError, ValueError, TypeError):
        return None
    return link_point(browser, bounds, url)


def link_point(browser, bounds: Rect, url: str) -> Point | None:
    if not allowed_url(url):
        return None
    payload = _evaluate(browser, _expression(url))
    if not isinstance(payload, dict):
        return None
    try:
        frame = (
            float(payload["outerWidth"]),
            float(payload["outerHeight"]),
            float(payload["innerWidth"]),
            float(payload["innerHeight"]),
        )
        left = float(payload["left"])
        top = float(payload["top"])
        width = float(payload["width"])
        height = float(payload["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width < 2 or height < 2 or frame[0] <= 0 or frame[1] <= 0:
        return None
    try:
        point = css_point_in_window(bounds, *frame, left, top, width, height)
    except (TypeError, ValueError):
        return None
    return point


def _evaluate(browser, expression: str):
    try:
        return browser.evaluate(expression)
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def _expression(url: str) -> str:
    wanted = json.dumps(url)
    return f"""(() => {{
  var want = {wanted};
  function norm(value) {{
    try {{
      var parsed = new URL(value, location.href);
      var path = parsed.pathname.replace(/\\/$/, "");
      return parsed.origin + path + parsed.search;
    }} catch (err) {{
      return "";
    }}
  }}
  var target = norm(want);
  if (!target) return null;
  var nodes = document.querySelectorAll("a[href]");
  var best = null;
  var limit = nodes.length < 500 ? nodes.length : 500;
  for (var i = 0; i < limit; i++) {{
    var el = nodes[i];
    if (norm(el.href) !== target) continue;
    var rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
    var nav = el.closest("header, nav, [role=navigation]");
    var score = (nav ? 0 : 1000) + Math.max(0, rect.top);
    if (best === null || score < best.score) {{
      best = {{
        score: score,
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height
      }};
    }}
  }}
  if (!best) return null;
  return {{
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio || 1,
    left: best.left,
    top: best.top,
    width: best.width,
    height: best.height
  }};
}})()"""
