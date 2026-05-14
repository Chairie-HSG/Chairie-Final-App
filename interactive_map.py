"""
Interactive Map of the Bib with the use of Plotly

Makes each real life seat a clickable green dot.

Plotly draws the map, gives hover labels / zooming and detects the clicks
Map is made with an image of the floor + a JSON file that stores x/y positions of the seats.
"""

import json
import os
from typing import Dict, List, Optional, Tuple

import streamlit as st


# ---------------------------------------------------------------------------
# Config
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

#check status to assign color
STATUS_COLORS: Dict[str, str] = {
    "available":   "#1db954",
    "free":        "#1db954",
    "reserved":    "#ff9800",
    "occupied":    "#e53935",
    "maintenance": "#9ca3af",
}

DEFAULT_DOT_COLOR = "#9ca3af" #color if seat has unknown state


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

"""
    Reliability check to find correct file path 
    Checks through the valid filenames to avoid crashes if file extensions change

"""
def _find_file(candidates: List[str], custom_path: Optional[str] = None) -> Optional[str]:

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

""" 
Loads the JSON file containing the coordinates of the seat

Returns the Python dictionary
"""
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

""" 
Gives the colro for a given seat based n status
"""
def get_seat_color(status: Optional[str]) -> str:
    return STATUS_COLORS.get((status or "").lower(), DEFAULT_DOT_COLOR)


def get_image_dimensions(image_path: Optional[str] = None) -> Optional[Tuple[int, int]]:
    """Return (width, height) of the floor plan image"""
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
    show_diagnostics: bool = False,
    key: str = "library_map_chart",
) -> Optional[Dict]:
     """
    Draws the full map using Plotly.
    We used AI to help us utilize Plotly as we had to adopt a new library very quickly

    Plotly takes care of clickable seats, hovering, zooming, selecting seat etc.
    This function returns the seat dictionary that was clicked
    """
    #Checks if plotly / PIL is imported 
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

    # Figure out the coordinate space
    img_file = _find_file(_IMAGE_CANDIDATES, custom_path=image_path)
    img = Image.open(img_file) if img_file else None
    img_w, img_h = (img.size if img else (1300, 850))

    # Scaling to fix potential alignment issues.
    if layout_canvas_size:
        canvas_w, canvas_h = layout_canvas_size
    else:
        canvas_w, canvas_h = img_w, img_h
    scale_x = img_w / canvas_w
    scale_y = img_h / canvas_h

    if show_diagnostics:     #Info on if the map needs to be debugged
        max_jx = max((int(s.get("x", 0)) for s in seats_data), default=0)
        max_jy = max((int(s.get("y", 0)) for s in seats_data), default=0)
        st.caption(
            f"🔧 image natural: {img_w}×{img_h} px • "
            f"JSON max coords: {max_jx}×{max_jy} • "
            f"layout canvas: {canvas_w}×{canvas_h} • "
            f"scale: ×{scale_x:.3f}, ×{scale_y:.3f}"
        )

        if (max_jx < img_w * 0.85 or max_jy < img_h * 0.85) and layout_canvas_size is None:
            image_aspect = img_w / img_h
            json_aspect = (max_jx / max_jy) if max_jy else 0.0
            sug_w = max_jx + 20
            sug_h_aspect = int(round(sug_w / image_aspect))
            sug_h_pad = max_jy + 20
            aspect_match = abs(json_aspect - image_aspect) < 0.03
            sug_h = sug_h_aspect if aspect_match else sug_h_pad
            st.info(
                f"Heads up: JSON coords only span ≈{int(100 * max_jx / img_w)}% × "
                f"{int(100 * max_jy / img_h)}% of the image. "
                f"{'The JSON aspect ratio matches the image, so ' if aspect_match else ''}"
                f"try `layout_canvas_size=({sug_w}, {sug_h})`."
            )

    # Build per-seat arrays for the scatter trace 
    #Lists for storing seat info
    xs:       List[float] = []
    ys:       List[float] = []
    ids:      List[int]   = []
    statuses: List[str]   = []
    colors:   List[str]   = []
    #Loop through every seat
    for seat in seats_data:
        try:
            xs.append(int(seat["x"]) * scale_x)     #Align dots (here x axis, next y axis)
            ys.append(int(seat["y"]) * scale_y)
            ids.append(int(seat["id"]))
        except (KeyError, TypeError, ValueError):
            continue
        status = str(seat.get("status", "available")).lower()
        statuses.append(status.title())
        colors.append(get_seat_color(status)) #Display status color 

    # Build the Plotly figure 
    fig = go.Figure()

    #Display floor image behind the dots
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

    # Add all seats as clickable markers that store ID/Status/Color
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
        #Hovering Tool through Plotly
        hovertemplate=(
            "<b>Seat %{customdata[0]}</b><br>"
            "%{customdata[1]}"
            "<extra></extra>"  
        ),
        
        hoverlabel=dict(
            bgcolor=colors,
            bordercolor="white",
            font=dict(color="white", size=14),
        ),
        name="",
        showlegend=False,
    ))

    # Highlight Selected seat 
    if selected_seat_id is not None:
        try:
            sel_idx = ids.index(int(selected_seat_id))
            fig.update_traces(selectedpoints=[sel_idx]) 
        except ValueError:
            pass

    #Makes selected bigger / brighter
    fig.update_traces(
        selected=dict(marker=dict(size=22, opacity=1.0)),
        unselected=dict(marker=dict(opacity=0.85)),
    )

    #Hide Axes 
    fig.update_xaxes(
        visible=False,
        range=[0, img_w],
        constrain="domain",
    )
    fig.update_yaxes(
        visible=False,
        # Inverting Y axis because Plotly is bottom-up
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

    # Render the Plotly chart inside Streamlit.
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

    # Reads clicked seat
    if not event: #check if clicked
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
    """
    Clears currently selected seat.
    When the users logs out or the map resets
    
    """
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
            st.experimental_set_query_params()  
        except Exception:
            pass
