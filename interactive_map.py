"""
interactive_map.py
Interactive library floor map with clickable seat dots positioned at exact
x,y coordinates from the JSON export.

Designed to be imported from a main Streamlit file:

    import streamlit as st
    from interactive_map import (
        load_map_data,
        render_interactive_map,
        handle_seat_selection,
    )

    data = load_map_data()                  # auto-finds the JSON
    seats = data["seats"] if data else []

    selected = handle_seat_selection(seats) # reads ?seat=… from URL
    render_interactive_map(seats, selected_seat_id=selected["id"] if selected else None)

    if selected:
        st.success(f"Selected seat #{selected['id']} ({selected['status']})")

How clicks reach Streamlit
--------------------------
The map is rendered as an SVG inside `st.components.v1.html`. When a dot is
clicked, JS updates the *parent* page's URL with `?seat=<id>`, which causes
Streamlit to rerun. `handle_seat_selection` then reads that query param.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Dict, List, Optional

import streamlit as st
import streamlit.components.v1 as components

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# JSON filenames we'll try, in order. First match wins.
_JSON_CANDIDATES = [
    "library_map_data (1).json",
    "library_map_data__1_.json",
    "library_map_data_1.json",
    "library_map_data.json",
]

# Background floor-plan image filenames we'll try, in order.
_IMAGE_CANDIDATES = [
    "Library_GFloor.jpg",
    "Library_GFloor.jpeg",
    "Library_GFloor.png",
    "library_gfloor.jpg",
    "library_gfloor.png",
]

# Status -> hex color
# Both "available" (used by the static JSON export) and "free" (used by the
# Supabase backend) are accepted as the green/bookable state.
STATUS_COLORS: Dict[str, str] = {
    "available":   "#1db954",  # green
    "free":        "#1db954",  # green (Supabase status name)
    "reserved":    "#ff9800",  # orange
    "occupied":    "#e53935",  # red
    "maintenance": "#9ca3af",  # gray
}

DEFAULT_DOT_COLOR = "#9ca3af"


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _base_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _find_file(candidates: List[str], custom_path: Optional[str] = None) -> Optional[str]:
    """Return the first existing path: explicit > script dir > cwd."""
    if custom_path and os.path.exists(custom_path):
        return os.path.abspath(custom_path)

    base = _base_dir()
    for name in candidates:
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p

    cwd = os.getcwd()
    for name in candidates:
        p = os.path.join(cwd, name)
        if os.path.exists(p):
            return p

    return None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_map_data(
    json_path: Optional[str] = None,
    silent: bool = False,
) -> Optional[Dict]:
    """Load the library map data JSON.

    Args:
        json_path: optional explicit path. If given and the file does not
            exist, returns ``None`` (the candidate filenames are NOT tried
            as a fallback when an explicit path was passed).
        silent: if True, suppress error messages and just return ``None``
            when the file isn't found / readable. Useful when probing for
            an optional per-floor map JSON.

    Returns:
        The parsed dict (with keys like ``mapName``, ``totalSeats``,
        ``seats``), or ``None`` if nothing was found / readable.
    """
    if json_path is not None:
        # Caller is being explicit — only try this exact path.
        if not os.path.exists(json_path):
            if not silent:
                st.error(f"Map data not found: {json_path}")
            return None
        path: Optional[str] = json_path
    else:
        path = _find_file(_JSON_CANDIDATES)
        if not path:
            if not silent:
                st.error(
                    "Could not find library map data JSON. Looked for: "
                    + ", ".join(_JSON_CANDIDATES)
                )
            return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        if not silent:
            st.error(f"Error loading map data from {path}: {e}")
        return None


def get_seat_color(status: Optional[str]) -> str:
    """Return the hex color for a seat status (case-insensitive)."""
    return STATUS_COLORS.get((status or "").lower(), DEFAULT_DOT_COLOR)


def _load_background_image_b64(image_path: Optional[str] = None) -> Optional[str]:
    """Return the floor plan as a `data:` URL, or None if not found."""
    path = _find_file(_IMAGE_CANDIDATES, custom_path=image_path)
    if not path:
        return None
    try:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = "jpeg" if ext in ("jpg", "jpeg") else ext
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/{mime};base64,{b64}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_interactive_map(
    seats_data: List[Dict],
    selected_seat_id: Optional[int] = None,
    image_path: Optional[str] = None,
    height: Optional[int] = None,
    show_legend: bool = True,
    dot_radius: Optional[int] = None,
) -> None:
    """Render the interactive map as an SVG inside an iframe component.

    Args:
        seats_data: list of seat dicts, each with ``id``, ``x``, ``y``,
            ``status`` (and optionally ``size``).
        selected_seat_id: if given, that seat is rendered as currently
            selected (highlighted ring).
        image_path: optional explicit path to the floor-plan image. If the
            file is found it will be embedded as a faded background.
        height: pixel height for the iframe. Auto-computed from the map's
            aspect ratio if omitted.
        show_legend: whether to draw the colour legend overlay.
        dot_radius: override dot radius in map coordinates. Defaults to
            ``size / 2 + 1`` per seat (so dots stay readable).
    """
    if not seats_data:
        st.warning("No seat data available")
        return

    # Coordinate space — derive from the data so it always fits.
    xs = [int(s.get("x", 0)) for s in seats_data]
    ys = [int(s.get("y", 0)) for s in seats_data]
    pad = 40
    map_width  = max(xs) + pad
    map_height = max(ys) + pad

    # Build the seat payload for JS.
    seats_payload = []
    for s in seats_data:
        size = int(s.get("size", 13))
        r = dot_radius if dot_radius is not None else max(6, size // 2 + 1)
        seats_payload.append({
            "id":     int(s["id"]),
            "x":      int(s.get("x", 0)),
            "y":      int(s.get("y", 0)),
            "r":      int(r),
            "status": str(s.get("status", "available")),
            "color":  get_seat_color(s.get("status", "available")),
        })

    seats_json   = json.dumps(seats_payload)
    selected_js  = "null" if selected_seat_id is None else json.dumps(int(selected_seat_id))
    bg_data_url  = _load_background_image_b64(image_path)

    bg_svg = ""
    if bg_data_url:
        bg_svg = (
            f'<image href="{bg_data_url}" xlink:href="{bg_data_url}" '
            f'x="0" y="0" width="{map_width}" height="{map_height}" '
            f'preserveAspectRatio="xMidYMid meet" opacity="0.55"/>'
        )

    # Iframe height: keep the natural aspect ratio. Assume the component
    # gets ~700px of width inside a typical Streamlit column.
    if height is None:
        assumed_width = 720
        height = int(assumed_width * map_height / map_width) + 24
        height = max(380, min(height, 820))

    legend_html = ""
    if show_legend:
        legend_html = """
        <div class="legend">
          <div class="legend-title">Legend</div>
          <div class="legend-row"><span class="ldot" style="background:#1db954"></span>Available</div>
          <div class="legend-row"><span class="ldot" style="background:#ff9800"></span>Reserved</div>
          <div class="legend-row"><span class="ldot" style="background:#e53935"></span>Occupied</div>
          <div class="legend-row"><span class="ldot" style="background:#9ca3af"></span>Maintenance</div>
        </div>
        """

    html_code = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  html, body {{
    margin: 0; padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    background: transparent;
  }}
  #wrap {{
    position: relative;
    width: 100%;
    background: #f8f9fa;
    border: 2px solid #1f4c66;
    border-radius: 8px;
    overflow: hidden;
    box-sizing: border-box;
  }}
  svg#map {{
    display: block;
    width: 100%;
    height: auto;
    background: #ffffff;
  }}
  .seat {{
    cursor: pointer;
    transition: transform 0.12s ease, filter 0.12s ease;
    transform-box: fill-box;
    transform-origin: center;
  }}
  .seat:hover {{
    transform: scale(1.5);
    filter: drop-shadow(0 2px 3px rgba(0,0,0,0.45));
  }}
  .seat.selected {{
    stroke: #1a73e8;
    stroke-width: 3;
    transform: scale(1.6);
    filter: drop-shadow(0 0 6px rgba(26,115,232,0.75));
  }}
  .legend {{
    position: absolute;
    bottom: 10px; left: 10px;
    background: rgba(255,255,255,0.95);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 12px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    pointer-events: none;
  }}
  .legend-title {{ font-weight: 600; margin-bottom: 4px; }}
  .legend-row   {{ display: flex; align-items: center; gap: 8px; margin-bottom: 2px; }}
  .ldot         {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  #tooltip {{
    position: absolute;
    background: #1f4c66; color: #fff;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 12px;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.12s ease;
    transform: translate(-50%, -130%);
    white-space: nowrap;
    z-index: 1000;
  }}
</style>
</head>
<body>
<div id="wrap">
  <svg id="map"
       viewBox="0 0 {map_width} {map_height}"
       preserveAspectRatio="xMidYMid meet"
       xmlns="http://www.w3.org/2000/svg"
       xmlns:xlink="http://www.w3.org/1999/xlink">
    {bg_svg}
    <g id="seats-layer"></g>
  </svg>
  {legend_html}
  <div id="tooltip"></div>
</div>

<script>
(function() {{
  const SEATS       = {seats_json};
  const SELECTED_ID = {selected_js};
  const SVG_NS      = "http://www.w3.org/2000/svg";

  const layer   = document.getElementById("seats-layer");
  const wrap    = document.getElementById("wrap");
  const tooltip = document.getElementById("tooltip");

  function statusLabel(s) {{
    if (!s) return "";
    return s.charAt(0).toUpperCase() + s.slice(1);
  }}

  SEATS.forEach(function(seat) {{
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute("cx", seat.x);
    c.setAttribute("cy", seat.y);
    c.setAttribute("r",  seat.r);
    c.setAttribute("fill",   seat.color);
    c.setAttribute("stroke", "white");
    c.setAttribute("stroke-width", "1.5");
    c.classList.add("seat");
    if (seat.id === SELECTED_ID) c.classList.add("selected");
    c.dataset.seatId = seat.id;

    c.addEventListener("mouseenter", function() {{
      const wRect = wrap.getBoundingClientRect();
      const cRect = c.getBoundingClientRect();
      tooltip.style.left = (cRect.left - wRect.left + cRect.width / 2) + "px";
      tooltip.style.top  = (cRect.top  - wRect.top) + "px";
      tooltip.textContent = "Seat " + seat.id + " — " + statusLabel(seat.status);
      tooltip.style.opacity = "1";
    }});
    c.addEventListener("mouseleave", function() {{
      tooltip.style.opacity = "0";
    }});
    c.addEventListener("click", function(ev) {{
      ev.stopPropagation();
      selectSeat(seat.id);
    }});

    layer.appendChild(c);
  }});

  function selectSeat(seatId) {{
    // Visual feedback before navigation.
    document.querySelectorAll(".seat").forEach(function(d) {{ d.classList.remove("selected"); }});
    const target = document.querySelector('.seat[data-seat-id="' + seatId + '"]');
    if (target) target.classList.add("selected");

    // Push ?seat=<id> onto the parent (top) frame so Streamlit reruns
    // and st.query_params picks up the value.
    try {{
      const parentLoc = window.parent.location;
      const url = new URL(parentLoc.href);
      url.searchParams.set("seat", String(seatId));
      window.parent.location.href = url.toString();
      return;
    }} catch (e) {{ /* fall through */ }}

    try {{
      const url = new URL(window.top.location.href);
      url.searchParams.set("seat", String(seatId));
      window.top.location.href = url.toString();
      return;
    }} catch (e) {{ /* fall through */ }}

    // Last resort: same-frame nav (won't update Streamlit, but at least
    // surfaces the click).
    const url = new URL(window.location.href);
    url.searchParams.set("seat", String(seatId));
    window.location.href = url.toString();
  }}

  // Bring the currently-selected dot into view if it would be off-screen.
  if (SELECTED_ID !== null) {{
    const sel = document.querySelector('.seat[data-seat-id="' + SELECTED_ID + '"]');
    if (sel && sel.scrollIntoView) {{
      try {{ sel.scrollIntoView({{ behavior: "smooth", block: "center" }}); }} catch (e) {{}}
    }}
  }}
}})();
</script>
</body>
</html>
"""

    components.html(html_code, height=height, scrolling=False)


# ---------------------------------------------------------------------------
# Selection helper
# ---------------------------------------------------------------------------

def handle_seat_selection(all_seats: List[Dict]) -> Optional[Dict]:
    """Read ``?seat=<id>`` from the URL and return the matching seat dict.

    Returns ``None`` if the param is missing, malformed, or doesn't match
    any seat in ``all_seats``.
    """
    raw: Optional[str] = None

    # Modern API (Streamlit ≥ 1.30)
    try:
        raw = st.query_params.get("seat")
    except Exception:
        # Legacy fallback
        try:
            qp = st.experimental_get_query_params()  # type: ignore[attr-defined]
            val = qp.get("seat")
            if isinstance(val, list):
                raw = val[0] if val else None
            else:
                raw = val
        except Exception:
            raw = None

    if raw is None or raw == "":
        return None

    try:
        seat_id = int(raw)
    except (ValueError, TypeError):
        return None

    return next((s for s in all_seats if int(s.get("id", -1)) == seat_id), None)


def clear_seat_selection() -> None:
    """Remove the ``seat`` query param (e.g. after the user closes details)."""
    try:
        if "seat" in st.query_params:
            del st.query_params["seat"]
    except Exception:
        try:
            st.experimental_set_query_params()  # type: ignore[attr-defined]
        except Exception:
            pass
