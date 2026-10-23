"""Locate the GitHub search box and result links through Chrome's debugger.

The script collects href, title, and a rectangle. accept_result in search.py
is what decides a link is safe to click. There is no URL blocklist in the script.
"""

from __future__ import annotations

from activitysim.search import Rect, accept_result

SEARCH_EXPRESSION = r"""
(() => {
  var nodes = document.querySelectorAll("input, textarea");
  var fields = [];
  var limit = nodes.length < 80 ? nodes.length : 80;
  for (var i = 0; i < limit; i++) {
    var el = nodes[i];
    var name = el.getAttribute("name") || "";
    var id = el.id || "";
    var placeholder = el.getAttribute("placeholder") || "";
    var label = el.getAttribute("aria-label") || "";
    var blob = (name + " " + id + " " + placeholder + " " + label).toLowerCase();
    if (name === "q" || blob.indexOf("search") !== -1) {
      var r = el.getBoundingClientRect();
      fields.push({left: r.left, top: r.top, width: r.width, height: r.height});
      if (fields.length >= 8) break;
    }
  }
  return {
    screenX: window.screenX,
    screenY: window.screenY,
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio || 1,
    fields: fields
  };
})()
"""

RESULTS_EXPRESSION = r"""
(() => {
  var anchors = document.querySelectorAll("a[href]");
  var links = [];
  var limit = anchors.length < 400 ? anchors.length : 400;
  for (var i = 0; i < limit; i++) {
    var el = anchors[i];
    var href = el.href || "";
    var parsed;
    try {
      parsed = new URL(href, window.location.href);
    } catch (err) {
      continue;
    }
    var host = (parsed.hostname || "").toLowerCase();
    if (host !== "github.com" && host.slice(-11) !== ".github.com") continue;
    var parts = parsed.pathname.split("/").filter(function (part) { return part.length > 0; });
    if (parts.length !== 2) continue;
    var rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    var title = (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 200);
    links.push({
      href: parsed.href,
      title: title,
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height
    });
    if (links.length >= 40) break;
  }
  return {
    screenX: window.screenX,
    screenY: window.screenY,
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio || 1,
    links: links
  };
})()
"""

FOCUSED_EXPRESSION = r"""
(() => {
  var el = document.activeElement;
  if (!el || !el.tagName) return {tag: "", editable: false};
  var type = "";
  if (String(el.tagName).toLowerCase() === "input") {
    type = String(el.type || "text").toLowerCase();
  }
  return {
    tag: String(el.tagName).toLowerCase(),
    type: type,
    editable: el.isContentEditable === true
  };
})()
"""


_TEXT_INPUTS = frozenset({"", "text", "search", "url", "email"})


def css_center_to_screen(
    screen_x: float,
    screen_y: float,
    outer_width: float,
    outer_height: float,
    inner_width: float,
    inner_height: float,
    device_pixel_ratio: float,
    left: float,
    top: float,
    width: float,
    height: float,
) -> tuple[float, float]:
    """Uniform-scale helper. Clicks use css_point_in_window instead."""

    border = (outer_width - inner_width) / 2
    top_inset = (outer_height - inner_height) - border
    x = (screen_x + border + left + width / 2) * device_pixel_ratio
    y = (screen_y + top_inset + top + height / 2) * device_pixel_ratio
    return (x, y)


def css_point_in_window(
    bounds: Rect,
    outer_width: float,
    outer_height: float,
    inner_width: float,
    inner_height: float,
    left: float,
    top: float,
    width: float,
    height: float,
) -> tuple[float, float]:
    """Map a viewport rectangle onto the DPI-aware outer window rect.

    border and top_inset are fractions of the CSS outer size. The fraction is
    then applied to GetWindowRect, so mixed-DPI screenX is not used.
    """

    if outer_width <= 0 or outer_height <= 0:
        raise ValueError("bad outer size")
    border = (outer_width - inner_width) / 2
    top_inset = (outer_height - inner_height) - border
    frac_x = (border + left + width / 2) / outer_width
    frac_y = (top_inset + top + height / 2) / outer_height
    win_left, win_top, win_right, win_bottom = bounds
    return (
        win_left + frac_x * (win_right - win_left),
        win_top + frac_y * (win_bottom - win_top),
    )


def pixel_scale_for(
    bounds: Rect,
    outer_width: float,
    outer_height: float,
    device_pixel_ratio: float | None = None,
) -> float:
    """Physical window height divided by CSS outer height.

    That is the scale the zone insets need. devicePixelRatio is only a
    fallback when the page did not report an outer height.
    """

    height = float(bounds[3]) - float(bounds[1])
    if outer_height > 0 and height > 0:
        return height / float(outer_height)
    try:
        ratio = float(device_pixel_ratio) if device_pixel_ratio is not None else 1.0
    except (TypeError, ValueError):
        return 1.0
    if not ratio > 0:
        return 1.0
    return ratio


def _frame(payload: dict) -> tuple[float, float, float, float] | None:
    try:
        return (
            float(payload["outerWidth"]),
            float(payload["outerHeight"]),
            float(payload["innerWidth"]),
            float(payload["innerHeight"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _center(payload: dict, rect: dict, bounds: Rect) -> tuple[float, float] | None:
    frame = _frame(payload)
    if frame is None:
        return None
    try:
        left = float(rect["left"])
        top = float(rect["top"])
        width = float(rect["width"])
        height = float(rect["height"])
    except (KeyError, TypeError, ValueError):
        return None
    try:
        return css_point_in_window(bounds, *frame, left, top, width, height)
    except (TypeError, ValueError):
        return None


def _remember_scale(browser, bounds: Rect, payload: object) -> float:
    if not isinstance(payload, dict):
        return 1.0
    frame = _frame(payload)
    if frame is None:
        return 1.0
    scale = pixel_scale_for(bounds, frame[0], frame[1], payload.get("devicePixelRatio"))
    remember = getattr(browser, "remember_pixel_scale", None)
    if callable(remember):
        remember(scale)
    return scale


def search_point(payload: object, bounds: Rect) -> tuple[float, float] | None:
    """Center of the largest search field, in the same pixels as bounds."""

    if not isinstance(payload, dict):
        return None
    fields = payload.get("fields")
    if not isinstance(fields, list):
        return None
    best: tuple[float, tuple[float, float]] | None = None
    for field in fields:
        if not isinstance(field, dict):
            continue
        point = _center(payload, field, bounds)
        if point is None:
            continue
        try:
            area = float(field.get("width") or 0) * float(field.get("height") or 0)
        except (TypeError, ValueError):
            area = 0
        if best is None or area > best[0]:
            best = (area, point)
    if best is None:
        return None
    return best[1]


def result_points_from_payload(payload: object, bounds: Rect) -> list[tuple[float, float]]:
    """Window points for links accept_result keeps. Order is top to bottom."""

    if not isinstance(payload, dict):
        return []
    links = payload.get("links")
    if not isinstance(links, list):
        return []
    frame = _frame(payload)
    scale = 1.0
    if frame is not None:
        scale = pixel_scale_for(bounds, frame[0], frame[1], payload.get("devicePixelRatio"))
    points: list[tuple[float, float]] = []
    seen: set[str] = set()
    for item in links:
        if not isinstance(item, dict):
            continue
        href = item.get("href")
        title = item.get("title")
        if not isinstance(href, str):
            continue
        if href in seen:
            continue
        point = _center(payload, item, bounds)
        if point is None:
            continue
        label = title if isinstance(title, str) else ""
        if not accept_result(href, label, point, bounds, scale):
            continue
        seen.add(href)
        points.append(point)
    points.sort(key=lambda point: (point[1], point[0]))
    return points[:8]


def role_from_active(payload: object) -> str:
    """Map document.activeElement onto the roles the runner already checks.

    Only a text-like input, a textarea, or a contenteditable control is
    AXTextField. Checkbox, radio, submit, and other input types are buttons.
    A missing input type is text. An empty tag with no edit flag is empty.
    """

    if not isinstance(payload, dict):
        return ""
    tag = str(payload.get("tag") or "").lower()
    editable = payload.get("editable")
    if isinstance(editable, str):
        editable = editable.lower() == "true"
    if editable is True or tag == "textarea":
        return "AXTextField"
    if tag == "input":
        kind = payload.get("type")
        if kind is None or kind == "":
            kind = "text"
        if str(kind).lower() in _TEXT_INPUTS:
            return "AXTextField"
        return "button"
    if not tag:
        return ""
    return "button"


def _evaluate(browser, expression: str):
    try:
        return browser.evaluate(expression)
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def search_field_from(browser, bounds: Rect) -> tuple[float, float] | None:
    payload = _evaluate(browser, SEARCH_EXPRESSION)
    _remember_scale(browser, bounds, payload)
    return search_point(payload, bounds)


def result_clicks_from(browser, bounds: Rect) -> list[tuple[float, float]]:
    payload = _evaluate(browser, RESULTS_EXPRESSION)
    _remember_scale(browser, bounds, payload)
    return result_points_from_payload(payload, bounds)


def focused_role_from(browser) -> str:
    """Page text role, unless a dialog of this Chrome is the OS focus.

    document.activeElement stays on the page input while a permission bubble
    or file dialog is in front. Those windows are not the main window, so
    this returns an empty role and the runner does not type.
    """

    pid = getattr(browser, "pid", None)
    if pid:
        try:
            from activitysim.winchrome import main_window_is_front

            if not main_window_is_front(int(pid)):
                return ""
        except (OSError, AttributeError, TypeError, ValueError):
            return ""
    return role_from_active(_evaluate(browser, FOCUSED_EXPRESSION))
