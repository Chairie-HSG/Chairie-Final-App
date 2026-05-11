"""
interactive_map.py

Interactive library floor map using Plotly.

Each seat is a clickable, hoverable point overlaid on top of the floor
plan image. Plotly handles all the UI features for us:

    - Hover tooltips on individual dots (showing seat number + status,
      tinted with the seat's status colour).
    - Mouse-wheel zoom on desktop, pinch-zoom on mobile.
    - Drag-to-pan.
    - Click events delivered back to Streamlit via on_select="rerun".

Required dependencies (add to requirements.txt):

    plotly
    pillow

Usage:

    from interactive_map import load_map_data, render_interactive_map

    data = load_map_data()
    seats = data["seats"] if data else []

    clicked = render_interactive_map(
        seats,
        selected_seat_id=st.session_state.get("selected_seat_id"),
        image_path="Library_GFloor.jpg",
        layout_canvas_size=(1300, 848),
    )
    if clicked and clicked["id"] != st.session_state.get("selected_seat_id"):
        st.session_state["selected_seat_id"] = clicked["id"]
"""

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

# Both "available" (static JSON export) and "free" (Supabase status name)
# count as the green/bookable state.
STATUS_COLORS: Dict[str, str] = {
    "available":   "#1db954",
    "free":        "#1db954",
    "reserved":    "#ff9800",
    "occupied":    "#e53935",
    "maintenance": "#9ca3af",
}

DEFAULT_DOT_COLOR = "#9ca3af"


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _find_file(candidates: List[str], custom_path: Optional[str] = None) -> Optional[str]:
    """Return the first existing path: explicit > script dir > cwd."""
    if custom_path and os.path.exists(custom_path):
        return os.path.abspath(custom_path)
    here = os.path.dirname(os.path.abspath(__file__))
    for name in candidates:
        p = os.path.join(here, name)
        if os.path.exists(p):
            return p
    for name in candidates:
        p = os.path.join(os.getcwd(), name)
        if os.path.exists(p):
            return p
    return None


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_map_data(json_path: Optional[str] = None, silent: bool = False) -> Optional[Dict]:
    """Load the seat-layout JSON. Returns the parsed dict or None."""
    if json_path is not None:
        if not os.path.exists(json_path):
            if not silent:
                st.error(f"Map data not found: {json_path}")
            return None
        path = json_path
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
    return STATUS_COLORS.get((status or "").lower(), DEFAULT_DOT_COLOR)


def get_image_dimensions(image_path: Optional[str] = None) -> Optional[Tuple[int, int]]:
    """Return (width, height) of the floor plan image, or None if missing."""
    try:
        from PIL import Image
    except ImportError:
        return None
    p = _find_file(_IMAGE_CANDIDATES, custom_path=image_path)
    if not p:
        return None
    try:
        with Image.open(p) as img:
            return img.size
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_interactive_map(
    seats_data: List[Dict],
    selected_seat_id: Optional[int] = None,
    image_path: Optional[str] = None,
    layout_canvas_size: Optional[Tuple[int, int]] = None,
    height: int = 580,
    key: str = "library_map_chart",
) -> Optional[Dict]:
    """Render the interactive floor map.

    Returns the seat dict the user just clicked on this rerun, or None.

    Args:
        seats_data: list of seat dicts with at least ``id``, ``x``, ``y``, ``status``.
        selected_seat_id: optional id to render with a highlight ring.
        image_path: path to the floor plan image (auto-detected if None).
        layout_canvas_size: ``(W, H)`` the JSON coords were authored against.
            Defaults to the image's own size (no scaling).
        height: chart height in pixels.
        key: Streamlit widget key.
    """
    # Lazy imports so the app keeps running with clear errors if a dep is missing
    try:
        import plotly.graph_objects as go
    except ImportError:
        st.error(
            "**Missing dependency**: `plotly`.\n\n"
            "Install with `pip install plotly` and add it to requirements.txt."
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

    if not seats_data:
        st.warning("No seat data available.")
        return None

    # ── 1. Figure out the coordinate space ────────────────────────────────
    img_file = _find_file(_IMAGE_CANDIDATES, custom_path=image_path)
    img = Image.open(img_file) if img_file else None
    img_w, img_h = (img.size if img else (1300, 850))

    # If the JSON was authored on a smaller canvas than the actual image,
    # scale seat coordinates up so dots line up with the chairs.
    if layout_canvas_size:
        canvas_w, canvas_h = layout_canvas_size
    else:
        canvas_w, canvas_h = img_w, img_h
    scale_x = img_w / canvas_w
    scale_y = img_h / canvas_h

    # ── 2. Build per-seat arrays for the scatter trace ────────────────────
    xs:       List[float] = []
    ys:       List[float] = []
    ids:      List[int]   = []
    statuses: List[str]   = []
    colors:   List[str]   = []
    for seat in seats_data:
        try:
            xs.append(int(seat["x"]) * scale_x)
            ys.append(int(seat["y"]) * scale_y)
            ids.append(int(seat["id"]))
        except (KeyError, TypeError, ValueError):
            continue
        status = str(seat.get("status", "available")).lower()
        statuses.append(status.title())
        colors.append(get_seat_color(status))

    # ── 3. Build the Plotly figure ────────────────────────────────────────
    fig = go.Figure()

    # Floor plan as a background image (drawn below the dots)
    if img is not None:
        fig.add_layout_image(
            dict(
                source=img,
                xref="x", yref="y",
                x=0, y=0,
                sizex=img_w, sizey=img_h,
                xanchor="left", yanchor="top",
                sizing="stretch",
                layer="below",
                opacity=1.0,
            )
        )

    # One scatter trace holds every seat. We pass id+status+color per point
    # in customdata so the hover template and click handler can use them.
    customdata = list(zip(ids, statuses, colors))
    fig.add_trace(go.Scatter(
        x=xs, y=ys,
        mode="markers",
        marker=dict(
            size=14,
            color=colors,
            line=dict(color="white", width=2),
        ),
        customdata=customdata,
        # Hover tooltip — bold seat number on its own line, then the status
        hovertemplate=(
            "<b>Seat %{customdata[0]}</b><br>"
            "%{customdata[1]}"
            "<extra></extra>"   # hides Plotly's default trace-name footer
        ),
        # Tint the tooltip background with each seat's status colour
        hoverlabel=dict(
            bgcolor=colors,
            bordercolor="white",
            font=dict(color="white", size=14),
        ),
        name="",
        showlegend=False,
    ))

    # Pre-mark the currently-selected seat so Plotly's selected/unselected
    # styling kicks in across reruns (otherwise selection state would reset
    # every time Streamlit redraws).
    if selected_seat_id is not None:
        try:
            sel_idx = ids.index(int(selected_seat_id))
            fig.update_traces(selectedpoints=[sel_idx])
        except ValueError:
            pass

    # Visual difference between selected and unselected dots
    fig.update_traces(
        selected=dict(marker=dict(size=22, opacity=1.0)),
        unselected=dict(marker=dict(opacity=0.85)),
    )

    # ── 4. Axes: hide them, set the image's coordinate space ──────────────
    fig.update_xaxes(
        visible=False,
        range=[0, img_w],
        constrain="domain",
    )
    fig.update_yaxes(
        visible=False,
        # Image y goes top-down; Plotly's default is bottom-up. Inverting
        # the range makes seat (x=10, y=10) appear in the upper-left.
        range=[img_h, 0],
        scaleanchor="x",
        scaleratio=1,
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=height,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        dragmode="pan",
    )

    # ── 5. Render. on_select="rerun" delivers click events to Python ──────
    event = st.plotly_chart(
        fig,
        key=key,
        on_select="rerun",
        selection_mode=["points"],
        use_container_width=True,
        config={
            "displayModeBar": False,
            "scrollZoom": True,          # mouse wheel zoom AND mobile pinch
            "doubleClick": "reset",      # double-click resets the view
            "displaylogo": False,
        },
    )

    # ── 6. Read which seat (if any) the user just clicked ─────────────────
    if not event:
        return None
    selection = event.get("selection") if isinstance(event, dict) else None
    if not selection:
        return None
    points = selection.get("points") or []
    if not points:
        return None

    cd = points[0].get("customdata")
    if cd is None:
        return None
    clicked_id = int(cd[0]) if isinstance(cd, (list, tuple)) else int(cd)

    for s in seats_data:
        try:
            if int(s.get("id", -1)) == clicked_id:
                return s
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------

def clear_seat_selection(key: str = "library_map_chart") -> None:
    """Forget any stored selection (e.g. on logout)."""
    if key in st.session_state:
        try:
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
