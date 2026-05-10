"""
interactive_map.py

Interactive library floor map with clickable seat dots.

Why a custom Streamlit component (streamlit-image-coordinates) instead of
plain components.html: components.html iframes are sandboxed without
allow-top-navigation, so window.parent.location.href = ... is silently
blocked by the browser. Real bidirectional click events require a registered
Streamlit component with the proper message-passing plumbing.

Required dependencies (add to requirements.txt):
    streamlit-image-coordinates
    pillow

Usage from your main Streamlit file:

    from interactive_map import (
        load_map_data,
        render_interactive_map,
        clear_seat_selection,
    )

    layout = load_map_data()                      # auto-finds the JSON
    seats_layout = layout["seats"] if layout else []

    # Merge layout coords with live status (e.g. from Supabase) by seat id,
    # then pass to render_interactive_map. The function returns the seat dict
    # the user clicked on this rerun (or None).
    clicked = render_interactive_map(
        merged_seats,
        selected_seat_id=st.session_state.get("selected_seat_id"),
        image_path="Library_GFloor.jpg",
    )
    if clicked and clicked["id"] != st.session_state.get("selected_seat_id"):
        st.session_state["selected_seat_id"] = clicked["id"]
        st.rerun()
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_JSON_CANDIDATES = [
    "library_map_data (1).json",
    "library_map_data__1_.json",
    "library_map_data_1.json",
    "library_map_data.json",
]

_IMAGE_CANDIDATES = [
    "Library_GFloor.jpg",
    "Library_GFloor.jpeg",
    "Library_GFloor.png",
    "library_gfloor.jpg",
    "library_gfloor.png",
]

# Both "available" (static JSON export) and "free" (Supabase backend)
# are accepted as the green/bookable state.
STATUS_COLORS: Dict[str, str] = {
    "available":   "#1db954",
    "free":        "#1db954",
    "reserved":    "#ff9800",
    "occupied":    "#e53935",
    "maintenance": "#9ca3af",
}

DEFAULT_DOT_COLOR = "#9ca3af"
_DEFAULT_KEY = "library_map_click"


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

    If ``json_path`` is given and the file does not exist, returns None
    (the candidate filenames are NOT tried as a fallback).
    """
    if json_path is not None:
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


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _draw_dots_on_image(
    base_img,                       # PIL.Image in RGBA
    seats_data: List[Dict],
    selected_seat_id: Optional[int],
    dot_radius_override: Optional[int],
):
    """Composite anti-aliased seat dots onto a copy of the base image.

    Dots are rendered on a 2× super-sampled overlay and downscaled with
    LANCZOS so the circles have smooth edges instead of the pixelated steps
    you get from a direct ImageDraw.ellipse call.
    """
    from PIL import Image, ImageDraw

    SCALE = 2
    base_w, base_h = base_img.size
    overlay = Image.new("RGBA", (base_w * SCALE, base_h * SCALE), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    for seat in seats_data:
        try:
            sid = int(seat["id"])
            x = int(seat.get("x", 0))
            y = int(seat.get("y", 0))
        except (KeyError, TypeError, ValueError):
            continue

        size = int(seat.get("size", 13))
        r_base = (
            dot_radius_override if dot_radius_override is not None
            else max(7, size // 2 + 2)
        )
        r = r_base * SCALE
        cx = x * SCALE
        cy = y * SCALE
        rgb = _hex_to_rgb(get_seat_color(seat.get("status", "available")))

        # Selection ring (drawn first, sits behind the dot)
        if sid == selected_seat_id:
            ring_r = r + 6 * SCALE
            od.ellipse(
                [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
                outline=(26, 115, 232, 255),
                width=3 * SCALE,
            )

        # White halo so the dot reads against any background
        halo_r = r + 1 * SCALE
        od.ellipse(
            [cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r],
            fill=(255, 255, 255, 255),
        )

        # Coloured inner dot
        od.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(rgb[0], rgb[1], rgb[2], 255),
        )

    overlay = overlay.resize((base_w, base_h), Image.LANCZOS)
    return Image.alpha_composite(base_img, overlay)


def _build_blank_canvas(seats_data: List[Dict]):
    """Fallback canvas when no floor plan image is available."""
    from PIL import Image, ImageDraw

    xs = [int(s.get("x", 0)) for s in seats_data] or [800]
    ys = [int(s.get("y", 0)) for s in seats_data] or [600]
    pad = 60
    w, h = max(xs) + pad, max(ys) + pad

    img = Image.new("RGBA", (w, h), (248, 249, 250, 255))
    d = ImageDraw.Draw(img)
    for gx in range(0, w, 100):
        d.line([(gx, 0), (gx, h)], fill=(220, 220, 220, 255), width=1)
    for gy in range(0, h, 100):
        d.line([(0, gy), (w, gy)], fill=(220, 220, 220, 255), width=1)
    return img


def render_interactive_map(
    seats_data: List[Dict],
    selected_seat_id: Optional[int] = None,
    image_path: Optional[str] = None,
    click_tolerance: int = 25,
    dot_radius: Optional[int] = None,
    key: str = _DEFAULT_KEY,
) -> Optional[Dict]:
    """Render the interactive floor map and return any clicked seat.

    The floor plan is shown at full natural resolution (responsively scaled
    to the column width). Dots are drawn into the image bitmap at exact
    JSON pixel coordinates, so they always line up with the seats in the
    plan — no SVG aspect-ratio drift.

    Args:
        seats_data: list of seat dicts with at least ``id``, ``x``, ``y``,
            ``status``. ``size`` is honoured if present.
        selected_seat_id: optional id of a seat to render with a blue ring.
        image_path: explicit path to the floor plan image. If omitted,
            common filenames in the script dir / cwd are tried.
        click_tolerance: max pixel distance (in image-natural pixels) from
            a click to a dot center for that dot to count as clicked.
        dot_radius: override dot radius (in image pixels). Default
            ``size / 2 + 2`` per seat.
        key: Streamlit session_state key for the click component. Use
            different keys when rendering multiple maps in one app.

    Returns:
        The seat dict the user clicked on (persists across reruns until
        they click somewhere else), or None if no click has landed on a
        seat yet, or the click was outside ``click_tolerance`` of any dot.
    """
    if not seats_data:
        st.warning("No seat data available.")
        return None

    # ---- Dependency probes ------------------------------------------------
    try:
        from streamlit_image_coordinates import streamlit_image_coordinates
    except ImportError:
        st.error(
            "**Missing dependency**: `streamlit-image-coordinates`.\n\n"
            "Install with `pip install streamlit-image-coordinates` and add "
            "it to your `requirements.txt`."
        )
        return None

    try:
        from PIL import Image
    except ImportError:
        st.error(
            "**Missing dependency**: `pillow`.\n\n"
            "Install with `pip install pillow`."
        )
        return None

    # ---- Build the image with dots overlaid -------------------------------
    img_file = _find_file(_IMAGE_CANDIDATES, custom_path=image_path)
    if img_file:
        try:
            base_img = Image.open(img_file).convert("RGBA")
        except Exception as e:
            st.warning(f"Could not open floor plan image: {e}")
            base_img = _build_blank_canvas(seats_data)
    else:
        base_img = _build_blank_canvas(seats_data)

    rendered = _draw_dots_on_image(
        base_img,
        seats_data,
        selected_seat_id=selected_seat_id,
        dot_radius_override=dot_radius,
    )

    # ---- Show + capture clicks --------------------------------------------
    # streamlit-image-coordinates returns click coords in the image's
    # natural pixel space regardless of the displayed size, so our
    # click→seat lookup needs no scaling.
    coords = streamlit_image_coordinates(
        rendered,
        key=key,
        use_column_width="always",
    )

    if not coords:
        return None

    cx = coords.get("x")
    cy = coords.get("y")
    if cx is None or cy is None:
        return None

    # Find nearest seat
    nearest: Optional[Dict] = None
    min_d2 = float("inf")
    for seat in seats_data:
        try:
            dx = int(seat.get("x", 0)) - cx
            dy = int(seat.get("y", 0)) - cy
        except (TypeError, ValueError):
            continue
        d2 = dx * dx + dy * dy
        if d2 < min_d2:
            min_d2 = d2
            nearest = seat

    if nearest is not None and min_d2 <= click_tolerance * click_tolerance:
        return nearest
    return None


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------

def clear_seat_selection(key: str = _DEFAULT_KEY) -> None:
    """Forget any persisted click + URL ``?seat=…`` param.

    Call on logout or whenever the next map render should start with no
    seat selected.
    """
    try:
        if key in st.session_state:
            del st.session_state[key]
    except Exception:
        pass
    try:
        if "seat" in st.query_params:
            del st.query_params["seat"]
    except Exception:
        try:
            st.experimental_set_query_params()  # type: ignore[attr-defined]
        except Exception:
            pass
