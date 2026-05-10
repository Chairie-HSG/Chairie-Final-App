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

def _get_label_font(size_px: int):
    """Load a bold font for seat labels, with cross-platform fallbacks."""
    from PIL import ImageFont

    candidates = [
        # Linux (incl. Streamlit Cloud)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        # macOS
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        # Windows
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size_px)
        except (OSError, IOError):
            continue
    # Last-resort fallback: PIL's bundled bitmap font.
    try:
        return ImageFont.load_default(size=size_px)  # Pillow ≥ 10
    except TypeError:
        return ImageFont.load_default()


def _draw_dots_on_image(
    base_img,                       # PIL.Image in RGBA
    seats_data: List[Dict],
    selected_seat_id: Optional[int],
    dot_radius_override: Optional[int],
    coord_scale: Tuple[float, float] = (1.0, 1.0),
    show_seat_label: bool = True,
):
    """Composite anti-aliased seat dots onto a copy of the base image.

    ``coord_scale`` maps JSON layout coordinates to natural image pixels.
    A value of (1.0, 1.0) means JSON coords are already in natural-image
    pixel space; >1.0 means the JSON canvas was smaller than the image.

    When ``show_seat_label`` is True and a seat is selected, its id is
    rendered in a small floating label above the dot — useful as visual
    confirmation of which seat the user just clicked.

    Dots are rendered on a 2× super-sampled overlay and downscaled with
    LANCZOS so the circles have smooth edges instead of the pixelated steps
    you get from a direct ImageDraw.ellipse call.
    """
    from PIL import Image, ImageDraw

    SCALE = 2
    base_w, base_h = base_img.size
    overlay = Image.new("RGBA", (base_w * SCALE, base_h * SCALE), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    cs_x, cs_y = coord_scale

    # Defer label rendering until after every dot is drawn so the label sits
    # on top of any neighbouring dots (z-order matters when seats are dense).
    selected_label: Optional[Tuple[int, int, int, str, Tuple[int, int, int]]] = None

    for seat in seats_data:
        try:
            sid = int(seat["id"])
            json_x = int(seat.get("x", 0))
            json_y = int(seat.get("y", 0))
        except (KeyError, TypeError, ValueError):
            continue

        x_img = int(json_x * cs_x)
        y_img = int(json_y * cs_y)

        size = int(seat.get("size", 13))
        r_base = (
            dot_radius_override if dot_radius_override is not None
            else max(7, size // 2 + 2)
        )
        r_base = int(r_base * max(cs_x, cs_y))
        r = r_base * SCALE
        cx = x_img * SCALE
        cy = y_img * SCALE
        rgb = _hex_to_rgb(get_seat_color(seat.get("status", "available")))

        if sid == selected_seat_id:
            ring_r = r + 6 * SCALE
            od.ellipse(
                [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
                outline=(26, 115, 232, 255),
                width=3 * SCALE,
            )
            # Stash label coords; render later.
            selected_label = (cx, cy, r, str(sid), rgb)

        halo_r = r + 1 * SCALE
        od.ellipse(
            [cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r],
            fill=(255, 255, 255, 255),
        )

        od.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(rgb[0], rgb[1], rgb[2], 255),
        )

    # ── Seat label for the selected dot (drawn last → always on top) ──────
    if selected_label is not None and show_seat_label:
        cx, cy, r, label_text, _rgb = selected_label
        # Font sized in the 2× overlay space; ~28 px in the final image.
        font_px = int(56 * max(cs_x, cs_y))
        font = _get_label_font(font_px)

        # Measure text
        try:
            l, t, rgt, btm = od.textbbox((0, 0), label_text, font=font)
            tw, th = rgt - l, btm - t
        except AttributeError:
            tw, th = font.getsize(label_text)  # pre-Pillow-10 fallback
            l = t = 0

        pad = 12 * SCALE
        gap = 14 * SCALE
        bg_w = tw + 2 * pad
        bg_h = th + 2 * pad

        # Place the label centered horizontally above the dot, with a small
        # gap. If that would clip the top of the canvas, place it below.
        bg_x = cx - bg_w // 2
        bg_y = cy - r - gap - bg_h
        if bg_y < 8 * SCALE:
            bg_y = cy + r + gap

        # Pill background
        try:
            od.rounded_rectangle(
                [bg_x, bg_y, bg_x + bg_w, bg_y + bg_h],
                radius=12 * SCALE,
                fill=(255, 255, 255, 240),
                outline=(26, 115, 232, 255),
                width=int(2 * SCALE),
            )
        except AttributeError:
            # Very old Pillow without rounded_rectangle
            od.rectangle(
                [bg_x, bg_y, bg_x + bg_w, bg_y + bg_h],
                fill=(255, 255, 255, 240),
                outline=(26, 115, 232, 255),
                width=int(2 * SCALE),
            )

        # Text (Streamlit-blue, matches the selection ring)
        od.text(
            (bg_x + pad - l, bg_y + pad - t),
            label_text,
            fill=(26, 115, 232, 255),
            font=font,
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
    layout_canvas_size: Optional[Tuple[int, int]] = None,
    click_tolerance: int = 25,
    dot_radius: Optional[int] = None,
    zoom_level: float = 1.0,
    show_seat_label: bool = True,
    show_diagnostics: bool = False,
    key: str = _DEFAULT_KEY,
) -> Optional[Dict]:
    """Render the interactive floor map and return any clicked seat.

    Args:
        seats_data: list of seat dicts with at least ``id``, ``x``, ``y``,
            ``status``. ``size`` is honoured if present.
        selected_seat_id: optional id of a seat to render with a blue ring
            and a small floating seat-number label.
        image_path: explicit path to the floor plan image. If omitted,
            common filenames in the script dir / cwd are tried.
        layout_canvas_size: ``(width, height)`` of the canvas the JSON
            coordinates were authored against.
        click_tolerance: max pixel distance (in JSON layout pixels) from
            a click to a seat's center for that seat to count as clicked.
        dot_radius: override dot radius (in JSON layout pixels).
        zoom_level: 1.0 = no zoom (full image visible). Values > 1 zoom in
            by cropping a window around the currently-selected seat (or
            the image center if no seat is selected). Click coordinates
            are translated back to the full image, so clicking on a dot
            in the zoomed view selects it normally.
        show_seat_label: whether to draw the seat-id label next to the
            currently-selected dot.
        show_diagnostics: if True, render a small caption showing the
            image dimensions, JSON max coords, and the scale being applied
            — handy when calibrating ``layout_canvas_size``.
        key: Streamlit session_state key for the click component.

    Returns:
        The seat dict the user clicked on (persists across reruns until
        they click somewhere else), or None if no click has landed within
        ``click_tolerance`` of any dot yet.
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

    # ---- Load floor plan --------------------------------------------------
    img_file = _find_file(_IMAGE_CANDIDATES, custom_path=image_path)
    if img_file:
        try:
            base_img = Image.open(img_file).convert("RGBA")
        except Exception as e:
            st.warning(f"Could not open floor plan image: {e}")
            base_img = _build_blank_canvas(seats_data)
    else:
        base_img = _build_blank_canvas(seats_data)

    natural_w, natural_h = base_img.size

    # ---- Resolve coordinate spaces ---------------------------------------
    if layout_canvas_size is not None:
        layout_w, layout_h = int(layout_canvas_size[0]), int(layout_canvas_size[1])
    else:
        layout_w, layout_h = natural_w, natural_h

    layout_w = max(1, layout_w)
    layout_h = max(1, layout_h)
    coord_scale_x = natural_w / layout_w
    coord_scale_y = natural_h / layout_h

    if show_diagnostics:
        max_jx = max(int(s.get("x", 0)) for s in seats_data)
        max_jy = max(int(s.get("y", 0)) for s in seats_data)
        st.caption(
            f"🔧 image natural: {natural_w}×{natural_h} px • "
            f"JSON max coords: {max_jx}×{max_jy} • "
            f"layout canvas: {layout_w}×{layout_h} • "
            f"scale: ×{coord_scale_x:.3f}, ×{coord_scale_y:.3f}"
        )
        if (max_jx < natural_w * 0.85 or max_jy < natural_h * 0.85) and layout_canvas_size is None:
            json_aspect = (max_jx / max_jy) if max_jy else 0.0
            image_aspect = natural_w / natural_h
            sug_w = max_jx + 20
            sug_h_aspect = int(round(sug_w / image_aspect))
            sug_h_pad = max_jy + 20
            aspect_match = abs(json_aspect - image_aspect) < 0.03
            sug_h = sug_h_aspect if aspect_match else sug_h_pad
            st.info(
                f"Heads up: JSON coords only span the upper-left "
                f"≈{int(100 * max_jx / natural_w)}% × "
                f"{int(100 * max_jy / natural_h)}% of the image. "
                f"{'The JSON aspect ratio matches the image, so the editor preserved aspect — ' if aspect_match else ''}"
                f"try `layout_canvas_size=({sug_w}, {sug_h})`."
            )

    # ---- Draw dots --------------------------------------------------------
    rendered = _draw_dots_on_image(
        base_img,
        seats_data,
        selected_seat_id=selected_seat_id,
        dot_radius_override=dot_radius,
        coord_scale=(coord_scale_x, coord_scale_y),
        show_seat_label=show_seat_label,
    )

    # ---- Apply zoom (crop around the selected seat or image center) ------
    crop_offset_x = 0
    crop_offset_y = 0
    zoom = max(1.0, float(zoom_level or 1.0))
    if zoom > 1.001:
        # Determine the natural-image pixel to keep centered.
        center_nx, center_ny = natural_w / 2, natural_h / 2
        if selected_seat_id is not None:
            for s in seats_data:
                try:
                    if int(s.get("id", -1)) == int(selected_seat_id):
                        center_nx = int(s.get("x", 0)) * coord_scale_x
                        center_ny = int(s.get("y", 0)) * coord_scale_y
                        break
                except (TypeError, ValueError):
                    pass

        # Cropped window dimensions in natural image pixels
        crop_w = max(1, int(natural_w / zoom))
        crop_h = max(1, int(natural_h / zoom))

        # Position crop, clamped to image bounds
        left = int(round(center_nx - crop_w / 2))
        top = int(round(center_ny - crop_h / 2))
        left = max(0, min(left, natural_w - crop_w))
        top = max(0, min(top, natural_h - crop_h))

        rendered = rendered.crop((left, top, left + crop_w, top + crop_h))
        crop_offset_x = left
        crop_offset_y = top

    # ---- Show + capture clicks --------------------------------------------
    coords = streamlit_image_coordinates(
        rendered,
        key=key,
        use_column_width="always",
    )

    if not coords:
        return None

    cx_disp = coords.get("x")
    cy_disp = coords.get("y")
    disp_w = coords.get("width")
    disp_h = coords.get("height")
    if cx_disp is None or cy_disp is None or not disp_w or not disp_h:
        return None

    # Display → cropped natural → original natural → JSON layout space.
    cropped_w, cropped_h = rendered.size
    cropped_nat_x = cx_disp * (cropped_w / disp_w)
    cropped_nat_y = cy_disp * (cropped_h / disp_h)
    natural_click_x = cropped_nat_x + crop_offset_x
    natural_click_y = cropped_nat_y + crop_offset_y
    json_click_x = natural_click_x / coord_scale_x
    json_click_y = natural_click_y / coord_scale_y

    # ---- Find nearest seat ------------------------------------------------
    nearest: Optional[Dict] = None
    min_d2 = float("inf")
    for seat in seats_data:
        try:
            dx = int(seat.get("x", 0)) - json_click_x
            dy = int(seat.get("y", 0)) - json_click_y
        except (TypeError, ValueError):
            continue
        d2 = dx * dx + dy * dy
        if d2 < min_d2:
            min_d2 = d2
            nearest = seat

    if nearest is not None and min_d2 <= click_tolerance * click_tolerance:
        return nearest
    return None


def get_image_dimensions(image_path: Optional[str] = None) -> Optional[Tuple[int, int]]:
    """Return ``(width, height)`` of the floor plan image, or None if missing.

    Useful for figuring out what to pass as ``layout_canvas_size``: from
    a Streamlit cell, run

        from interactive_map import get_image_dimensions
        st.write(get_image_dimensions())

    and compare to the ``JSON max coords`` line in the diagnostics caption.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    path = _find_file(_IMAGE_CANDIDATES, custom_path=image_path)
    if not path:
        return None
    try:
        with Image.open(path) as img:
            return img.size
    except Exception:
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
