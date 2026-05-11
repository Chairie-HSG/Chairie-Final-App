"""
streamlit_app.py

All-in-one Streamlit seat booking app for HSG Library.
Combines:
  - Among-US-Group core logic (seat_manager.py, timer.py, storage.py)
  - indexnew.html layout and design (HTML-only, no separate CSS files)
  - Supabase backend (api.py, supabase_client.py, auth.py)
  - Library floor-plan images (Library_GFloor.jpg, Library_1Floor.jpg)
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import os
import datetime
from datetime import datetime as dt, timedelta, timezone

import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ─────────────────────────────────────────────────────────────
# INTERACTIVE MAP  (from interactive_map.py)
# Graceful fallback: if the module or its deps are missing, the app
# still runs with the legacy static-image + button-grid view.
# ─────────────────────────────────────────────────────────────
try:
    from interactive_map import (
        load_map_data as load_layout_data,
        render_interactive_map,
        clear_seat_selection,
    )
    INTERACTIVE_MAP_AVAILABLE = True
except Exception:
    INTERACTIVE_MAP_AVAILABLE = False

    def clear_seat_selection(key=None):  # noqa: E306  (fallback no-op)
        try:
            if "seat" in st.query_params:
                del st.query_params["seat"]
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# VISUAL SHELL
# ─────────────────────────────────────────────────────────────
def _inject_app_shell():
    """Load the global CSS shell (app_styles.html) into the page.

    Called once at the top of main_app(). The login page has its own
    self-contained Google-style theme and intentionally does not load
    this shell.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "app_styles.html")
    try:
        with open(path, "r", encoding="utf-8") as f:
            st.markdown(f.read(), unsafe_allow_html=True)
    except FileNotFoundError:
        # Shell missing → fall back to Streamlit's default look.
        pass


# ─────────────────────────────────────────────────────────────
# PER-FLOOR MAP CONFIGURATION
# ─────────────────────────────────────────────────────────────
# Each entry tells the map renderer what to load for that floor:
#   - json_path:          seat-layout file (None = use default candidates)
#   - image_filename:     floor plan image to overlay seats onto
#   - layout_canvas_size: (W, H) the JSON coords were authored against.
#                         Calibrated per floor so dots line up with chairs.
#   - show_diagnostics:   True while still calibrating; False in production.
#   - map_key:            unique Streamlit widget key per floor (so each
#                         floor's selection persists independently).
FLOOR_CONFIG = {
    "Ground Floor": {
        "json_path":          None,
        "image_filename":     "Library_GFloor.jpg",
        "layout_canvas_size": (1300, 848),
        "show_diagnostics":   False,
        "map_key":            "library_map_chart_ground",
    },
    "Floor 1": {
        "json_path":          "library_map_data_floor1.json",
        "image_filename":     "Library_1Floor.jpg",
        "layout_canvas_size": (1365, 1015),
        "show_diagnostics":   False,
        "map_key":            "library_map_chart_floor1",
    },
}

# Supabase
import os
from supabase import create_client

# ─────────────────────────────────────────────────────────────
# SUPABASE CLIENT  (from supabase_client.py)
# ─────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY", ""))

supabase = None
SUPABASE_OK = False
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        SUPABASE_OK = True
    except Exception:
        SUPABASE_OK = False

# ─────────────────────────────────────────────────────────────
# TIMER HELPERS  (from Among-US-Group/timer.py)
# ─────────────────────────────────────────────────────────────
def has_expired(check_in_time: dt) -> bool:
    """Returns True if 2 hours have passed since check_in_time."""
    expiry_time = check_in_time + timedelta(hours=2)
    current_time = dt.now()
    return current_time > expiry_time


def free_expired_seats(seats: list) -> None:
    """Goes through all seats and sets occupied=False if their 2 hours are up."""
    for seat in seats:
        if seat["occupied"]:
            check_in_time = dt.fromisoformat(seat["check_in_time"])
            if has_expired(check_in_time):
                seat["occupied"] = False
                seat["check_in_time"] = None


# ─────────────────────────────────────────────────────────────
# API LAYER  (from api.py + supabase_client.py)
# ─────────────────────────────────────────────────────────────
RESERVATION_MINUTES = 10
RECHECK_HOURS = 2


def _now():
    return dt.now(timezone.utc)


def _to_iso(d):
    return d.isoformat()


def _user_from_token(token):
    if not token or not SUPABASE_OK:
        return None
    try:
        response = supabase.auth.get_user(token)
        return response.user
    except Exception:
        return None


def _email_from_token(token):
    user = _user_from_token(token)
    if not user:
        return None
    return user.email


def login_request(email, password):
    if not SUPABASE_OK:
        return {"success": False, "message": "Supabase not configured."}
    try:
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if response.user and response.session:
            return {
                "success": True,
                "username": response.user.email,
                "token": response.session.access_token,
            }
        return {"success": False, "message": "Login failed."}
    except Exception as e:
        return {"success": False, "message": str(e)}


def signup_request(email, password):
    if not SUPABASE_OK:
        return {"success": False, "message": "Supabase not configured."}
    try:
        response = supabase.auth.sign_up({"email": email, "password": password})
        if response.user:
            return {"success": True, "message": "Account created successfully."}
        return {"success": False, "message": "Signup failed."}
    except Exception as e:
        return {"success": False, "message": str(e)}


def _expire_seats():
    if not SUPABASE_OK:
        return
    now_iso = _to_iso(_now())
    try:
        reserved = (
            supabase.table("seats")
            .select("*")
            .eq("status", "reserved")
            .not_.is_("reserved_until", "null")
            .execute()
        )
        for seat in reserved.data:
            if seat["reserved_until"] and seat["reserved_until"] <= now_iso:
                supabase.table("seats").update(
                    {"status": "free", "reserved_by": None, "reserved_until": None}
                ).eq("id", seat["id"]).execute()

        occupied = (
            supabase.table("seats")
            .select("*")
            .eq("status", "occupied")
            .not_.is_("occupied_until", "null")
            .execute()
        )
        for seat in occupied.data:
            if seat["occupied_until"] and seat["occupied_until"] <= now_iso:
                supabase.table("seats").update(
                    {"status": "free", "occupied_by": None, "occupied_until": None}
                ).eq("id", seat["id"]).execute()
    except Exception:
        pass


def get_seats(token=None):
    if not SUPABASE_OK:
        return {"success": False, "message": "Supabase not configured."}
    try:
        _expire_seats()
        email = _email_from_token(token)
        response = supabase.table("seats").select("*").order("id").execute()
        seats = []
        for seat in response.data:
            seats.append(
                {
                    "id": seat["id"],
                    "code": seat["code"],
                    "building": seat["building"],
                    "floor": seat["floor"],
                    "status": seat["status"],
                    "reserved_until": seat.get("reserved_until"),
                    "occupied_until": seat.get("occupied_until"),
                    "reserved_by_me": seat.get("reserved_by") == email,
                    "occupied_by_me": seat.get("occupied_by") == email,
                }
            )
        return {"success": True, "seats": seats}
    except Exception as e:
        return {"success": False, "message": str(e)}


def get_user_status(token=None):
    if not SUPABASE_OK:
        return {"success": False, "message": "Supabase not configured."}
    try:
        _expire_seats()
        email = _email_from_token(token)
        if not email:
            return {"success": False, "message": "Unauthorized"}

        reserved = (
            supabase.table("seats")
            .select("*")
            .eq("reserved_by", email)
            .eq("status", "reserved")
            .limit(1)
            .execute()
        )
        occupied = (
            supabase.table("seats")
            .select("*")
            .eq("occupied_by", email)
            .eq("status", "occupied")
            .limit(1)
            .execute()
        )

        def _fmt(seat, by_me_key):
            return {
                "id": seat["id"],
                "code": seat["code"],
                "building": seat["building"],
                "floor": seat["floor"],
                "status": seat["status"],
                "reserved_until": seat.get("reserved_until"),
                "occupied_until": seat.get("occupied_until"),
                "reserved_by_me": by_me_key == "reserved",
                "occupied_by_me": by_me_key == "occupied",
            }

        return {
            "success": True,
            "username": email,
            "reserved_seat": _fmt(reserved.data[0], "reserved") if reserved.data else None,
            "checked_in_seat": _fmt(occupied.data[0], "occupied") if occupied.data else None,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


def reserve_seat(token, seat_id):
    if not SUPABASE_OK:
        return {"success": False, "message": "Supabase not configured."}
    try:
        _expire_seats()
        email = _email_from_token(token)
        if not email:
            return {"success": False, "message": "Unauthorized"}

        occupied = (
            supabase.table("seats").select("*").eq("occupied_by", email).eq("status", "occupied").execute()
        )
        if occupied.data:
            return {"success": False, "message": "You are currently checked in somewhere else."}

        existing_res = (
            supabase.table("seats").select("*").eq("reserved_by", email).eq("status", "reserved").execute()
        )
        if existing_res.data and existing_res.data[0]["id"] != seat_id:
            return {"success": False, "message": "You already have another seat reserved."}

        seat_res = supabase.table("seats").select("*").eq("id", seat_id).limit(1).execute()
        if not seat_res.data:
            return {"success": False, "message": "Seat not found."}
        seat = seat_res.data[0]

        if seat["status"] == "occupied":
            return {"success": False, "message": "That seat is already occupied."}
        if seat["status"] == "reserved" and seat.get("reserved_by") != email:
            return {"success": False, "message": "Someone else reserved it first."}

        expires_at = _to_iso(_now() + timedelta(minutes=RESERVATION_MINUTES))
        supabase.table("seats").update(
            {"status": "reserved", "reserved_by": email, "reserved_until": expires_at}
        ).eq("id", seat_id).execute()

        return {
            "success": True,
            "message": f"Seat {seat['code']} reserved successfully.",
            "reservation_expires_at": expires_at,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


def cancel_reservation(token):
    if not SUPABASE_OK:
        return {"success": False, "message": "Supabase not configured."}
    try:
        email = _email_from_token(token)
        if not email:
            return {"success": False, "message": "Unauthorized"}

        res = (
            supabase.table("seats").select("*").eq("reserved_by", email).eq("status", "reserved").limit(1).execute()
        )
        if not res.data:
            return {"success": False, "message": "No active reservation."}

        supabase.table("seats").update(
            {"status": "free", "reserved_by": None, "reserved_until": None}
        ).eq("id", res.data[0]["id"]).execute()

        return {"success": True, "message": "Reservation cancelled."}
    except Exception as e:
        return {"success": False, "message": str(e)}


def check_in_from_qr(token, seat_id):
    if not SUPABASE_OK:
        return {"success": False, "message": "Supabase not configured."}
    try:
        _expire_seats()
        email = _email_from_token(token)
        if not email:
            return {"success": False, "message": "Unauthorized"}

        occupied = (
            supabase.table("seats").select("*").eq("occupied_by", email).eq("status", "occupied").execute()
        )
        if occupied.data and occupied.data[0]["id"] != seat_id:
            return {"success": False, "message": "You cannot occupy two seats."}

        seat_res = supabase.table("seats").select("*").eq("id", seat_id).limit(1).execute()
        if not seat_res.data:
            return {"success": False, "message": "Seat not found."}
        seat = seat_res.data[0]

        if seat["status"] == "occupied" and seat.get("occupied_by") != email:
            return {"success": False, "message": "That seat is already occupied."}
        if seat["status"] == "reserved" and seat.get("reserved_by") != email:
            return {"success": False, "message": "This reservation belongs to another user."}

        occupied_until = _to_iso(_now() + timedelta(hours=RECHECK_HOURS))
        supabase.table("seats").update(
            {
                "status": "occupied",
                "reserved_by": None,
                "reserved_until": None,
                "occupied_by": email,
                "occupied_until": occupied_until,
            }
        ).eq("id", seat_id).execute()

        return {
            "success": True,
            "message": f"Checked in to seat {seat['code']}.",
            "occupied_until": occupied_until,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


def release_current_seat(token):
    if not SUPABASE_OK:
        return {"success": False, "message": "Supabase not configured."}
    try:
        email = _email_from_token(token)
        if not email:
            return {"success": False, "message": "Unauthorized"}

        occupied = (
            supabase.table("seats").select("*").eq("occupied_by", email).eq("status", "occupied").limit(1).execute()
        )
        if occupied.data:
            supabase.table("seats").update(
                {"status": "free", "occupied_by": None, "occupied_until": None}
            ).eq("id", occupied.data[0]["id"]).execute()
            return {"success": True, "message": "Seat released."}

        reserved = (
            supabase.table("seats").select("*").eq("reserved_by", email).eq("status", "reserved").limit(1).execute()
        )
        if reserved.data:
            supabase.table("seats").update(
                {"status": "free", "reserved_by": None, "reserved_until": None}
            ).eq("id", reserved.data[0]["id"]).execute()
            return {"success": True, "message": "Reservation cancelled."}

        return {"success": False, "message": "No active seat to release."}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ─────────────────────────────────────────────────────────────
# AUTH STATE  (from auth.py)
# ─────────────────────────────────────────────────────────────
def init_auth_state():
    defaults = {
        "logged_in": False,
        "username": None,
        "token": None,
        "selected_seat_id": None,
        "auth_mode": "login",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def is_logged_in():
    return st.session_state.get("logged_in", False)


def login_user(username, token):
    st.session_state["logged_in"] = True
    st.session_state["username"] = username
    st.session_state["token"] = token


def logout_user():
    st.session_state["logged_in"] = False
    st.session_state["username"] = None
    st.session_state["token"] = None
    st.session_state["selected_seat_id"] = None
    # Drop ?seat=… from the URL so a fresh session starts clean.
    try:
        clear_seat_selection()
    except Exception:
        pass


def require_login():
    if not is_logged_in():
        st.warning("Please log in first.")
        st.stop()


# ─────────────────────────────────────────────────────────────
# COUNTDOWN HELPERS  (from app.py)
# ─────────────────────────────────────────────────────────────
def seconds_left(iso_value):
    if not iso_value:
        return 0
    target = dt.fromisoformat(iso_value)
    now = dt.now(timezone.utc)
    diff = int((target - now).total_seconds())
    return max(diff, 0)


def countdown(iso_value):
    secs = seconds_left(iso_value)
    return f"{secs // 60:02d}:{secs % 60:02d}"


def seat_status_color(status):
    return {"free": "#1db954", "reserved": "#ff9800", "occupied": "#e53935"}.get(status, "#9ca3af")


# ─────────────────────────────────────────────────────────────
# AUTH PAGE  (login + signup — from auth.py, styled via indexnew.html HTML)
# ─────────────────────────────────────────────────────────────

def login_page():
    # ── Google-style CSS ──
    st.markdown(
        """
        <style>
        /* Hide default Streamlit chrome for a cleaner look */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}

        /* Page background */
        .stApp {
            background-color: #ffffff;
        }

        /* Center column width */
        .block-container {
            padding-top: 3rem;
            padding-bottom: 3rem;
            max-width: 500px;
        }

        /* Title and subtitle */
        .login-title {
            font-family: 'Roboto', 'Segoe UI', Arial, sans-serif;
            font-size: 24px;
            font-weight: 400;
            color: #202124;
            text-align: center;
            margin-top: 8px;
            margin-bottom: 8px;
        }
        .login-subtitle {
            font-family: 'Roboto', 'Segoe UI', Arial, sans-serif;
            font-size: 16px;
            font-weight: 400;
            color: #202124;
            text-align: center;
            margin-bottom: 28px;
        }

        /* The form itself acts as the card */
        div[data-testid="stForm"] {
            border: 1px solid #dadce0;
            border-radius: 8px;
            padding: 32px 36px 28px 36px;
            background: #ffffff;
        }

        /* Inputs — Google's outlined style */
        .stTextInput > div > div > input {
            height: 52px !important;
            border-radius: 4px !important;
            border: 1px solid #dadce0 !important;
            font-size: 15px !important;
            padding: 12px 15px !important;
            color: #202124 !important;
        }
        .stTextInput > div > div > input:focus {
            border: 2px solid #1a73e8 !important;
            box-shadow: none !important;
            outline: none !important;
        }
        .stTextInput label {
            font-size: 13px !important;
            color: #5f6368 !important;
            font-family: 'Roboto', 'Segoe UI', Arial, sans-serif !important;
        }

        /* Tagline (replaces the guest-mode block) */
        .tagline {
            font-family: 'Roboto', 'Segoe UI', Arial, sans-serif;
            font-size: 14px;
            color: #5f6368;
            line-height: 1.5;
            margin-top: 20px;
            margin-bottom: 8px;
            text-align: left;
        }
        .tagline strong {
            color: #202124;
            font-weight: 500;
        }

        /* Primary submit button (Next) */
        .stFormSubmitButton button {
            background-color: #1a73e8 !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 4px !important;
            height: 38px !important;
            font-weight: 500 !important;
            font-size: 14px !important;
            padding: 0 24px !important;
            float: right !important;
            font-family: 'Roboto', 'Segoe UI', Arial, sans-serif !important;
        }
        .stFormSubmitButton button:hover {
            background-color: #1765cc !important;
            color: #ffffff !important;
            box-shadow: 0 1px 2px rgba(60,64,67,0.3), 0 1px 3px 1px rgba(60,64,67,0.15) !important;
        }

        /* Secondary mode-switch button (Create account / Sign in instead) */
        .switch-row .stButton button {
            background: #ffffff !important;
            color: #202124 !important;
            border: 1px solid #dadce0 !important;
            border-radius: 4px !important;
            font-weight: 500 !important;
            font-size: 14px !important;
            height: 38px !important;
            padding: 0 16px !important;
        }
        .switch-row .stButton button:hover {
            background: #f8f9fa !important;
            border-color: #d2d5d9 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── Logo (centered, no outer wrapper card) ──
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    logo_path = os.path.join(BASE_DIR, "full_size_logo.png")

    if os.path.exists(logo_path):
        c1, c2, c3 = st.columns([1, 1, 1])
        with c2:
            st.image(logo_path, width=110)
    else:
        st.markdown(
            "<div style='text-align:center; font-size:32px; font-weight:600; "
            "color:#0a8f4d; margin-bottom:8px;'>Chairie</div>",
            unsafe_allow_html=True,
        )

    # ── Mode state ──
    mode = st.session_state.get("auth_mode", "login")

    # ── Title / subtitle ──
    if mode == "login":
        st.markdown('<div class="login-title">Sign in</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="login-subtitle">Use your Seat Booking account</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown('<div class="login-title">Create your account</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="login-subtitle">to continue to Seat Booking</div>',
            unsafe_allow_html=True,
        )

    # ── Forms ──
    if mode == "login":
        with st.form("login_form", clear_on_submit=False):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")

            st.markdown(
                '<div class="tagline">'
                "<strong>Chairie</strong>, Made by Students, for Students."
                "</div>",
                unsafe_allow_html=True,
            )

            submitted = st.form_submit_button("Next")

        # Switch-mode button rendered outside the form
        st.markdown('<div class="switch-row">', unsafe_allow_html=True)
        if st.button("Create account", key="go_signup"):
            st.session_state["auth_mode"] = "signup"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

        if submitted:
            if not email or not password:
                st.warning("Please enter both email and password.")
            else:
                result = login_request(email, password)
                if result["success"]:
                    login_user(result["username"], result["token"])
                    st.success("Login successful.")
                    st.rerun()
                else:
                    st.error(result["message"])

    else:  # signup
        with st.form("signup_form", clear_on_submit=False):
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password", type="password", key="signup_password")
            confirm = st.text_input("Confirm password", type="password", key="signup_confirm")

            st.markdown(
                '<div class="tagline">'
                "<strong>Chairie</strong>, Made by Students, for Students."
                "</div>",
                unsafe_allow_html=True,
            )

            submitted = st.form_submit_button("Create")

        st.markdown('<div class="switch-row">', unsafe_allow_html=True)
        if st.button("Sign in instead", key="go_login"):
            st.session_state["auth_mode"] = "login"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

        if submitted:
            if not email or not password or not confirm:
                st.warning("Please fill in all fields.")
            elif password != confirm:
                st.warning("Passwords do not match.")
            elif len(password) < 6:
                st.warning("Password must be at least 6 characters.")
            else:
                result = signup_request(email, password)
                if result["success"]:
                    st.success(result["message"])
                    st.info("Go back to Sign in and use your new account.")
                    st.session_state["auth_mode"] = "login"
                else:
                    st.error(result["message"])
# ─────────────────────────────────────────────────────────────
# MAIN APP  (combined indexnew.html layout + app.py logic)
# ─────────────────────────────────────────────────────────────
def _render_seat_detail_panel(seats, token):
    """Render the seat detail panel (info + action buttons).

    Called from main_app() above the interactive map. Reads
    st.session_state["selected_seat_id"] to decide what to show.
    """
    st.markdown(
        '<div class="chairie-eyebrow">Seat details</div>',
        unsafe_allow_html=True,
    )

    selected_id = st.session_state.get("selected_seat_id")
    if not selected_id:
        st.markdown(
            '<div class="chairie-empty-detail">'
            'Click a dot on the map below to see <strong>seat details</strong> '
            'and reserve it.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    seat = next((s for s in seats if s["id"] == selected_id), None)
    if not seat:
        st.markdown(
            '<div class="chairie-empty-detail">'
            'Selected seat is not on this floor or no longer available.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    status_class = (seat["status"] or "maintenance").lower()
    st.markdown(
        f"""
        <div class="chairie-seat-detail">
          <div class="chairie-seat-row">
            <span class="chairie-seat-label">Seat</span>
            <span class="chairie-seat-value">{seat['code']}</span>
          </div>
          <div class="chairie-seat-row">
            <span class="chairie-seat-label">Building</span>
            <span class="chairie-seat-value">{seat['building']}</span>
          </div>
          <div class="chairie-seat-row">
            <span class="chairie-seat-label">Floor</span>
            <span class="chairie-seat-value">{seat['floor']}</span>
          </div>
          <div class="chairie-seat-row">
            <span class="chairie-seat-label">Status</span>
            <span class="chairie-status-pill {status_class}">{seat['status']}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if seat["status"] == "free":
        if st.button("Reserve this seat (10 min)", key=f"reserve_{seat['id']}"):
            result = reserve_seat(token, seat["id"])
            if result["success"]:
                st.success(result["message"])
                st.info("You must scan / check in within 10 minutes.")
                st.rerun()
            else:
                st.error(result["message"])

    elif seat["status"] == "reserved":
        if seat["reserved_by_me"]:
            st.warning(
                f"This seat is reserved by you. "
                f"Time left to check in: {countdown(seat['reserved_until'])}"
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Simulate QR / Check In", key=f"checkin_{seat['id']}"):
                    result = check_in_from_qr(token, seat["id"])
                    if result["success"]:
                        st.success(result["message"])
                        st.rerun()
                    else:
                        st.error(result["message"])
            with c2:
                if st.button(
                    "Cancel Reservation",
                    key=f"cancel_{seat['id']}",
                    type="secondary",
                ):
                    result = cancel_reservation(token)
                    if result["success"]:
                        st.success(result["message"])
                        st.rerun()
                    else:
                        st.error(result["message"])
            st.caption("In the real app, 'Simulate QR / Check In' is replaced by a real QR scan.")
        else:
            st.error("Someone else reserved this seat.")

    elif seat["status"] == "occupied":
        if seat["occupied_by_me"]:
            st.success(
                f"You are currently occupying this seat. "
                f"Re-check countdown: {countdown(seat['occupied_until'])}"
            )
            if st.button("Release Seat", key=f"release_{seat['id']}"):
                result = release_current_seat(token)
                if result["success"]:
                    st.success(result["message"])
                    st.rerun()
                else:
                    st.error(result["message"])
        else:
            st.error("This seat is already occupied by someone else.")


def main_app():
    require_login()

    # Auto-refresh every 30s (countdowns and seat statuses update live)
    st_autorefresh(interval=30000, key="seat_refresh")

    # Inject the global CSS shell once per page render.
    _inject_app_shell()

    token = st.session_state["token"]

    # ── Top bar ──────────────────────────────────────────────────────────
    st.markdown(
        f"""
        <div class="chairie-topbar">
          <div class="chairie-brand">
            <span class="chairie-brand-mark">C</span>
            <span>Chairie</span>
          </div>
          <div class="chairie-page-label">Library Map</div>
          <div class="chairie-user">{st.session_state['username']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Logout (right-aligned via a thin column trick)
    _, _, c_logout = st.columns([6, 6, 2])
    with c_logout:
        if st.button("Logout", key="logout_btn", type="secondary"):
            logout_user()
            st.rerun()

    # ── User status banner ───────────────────────────────────────────────
    status_result = get_user_status(token)
    if status_result.get("success"):
        reserved_seat = status_result.get("reserved_seat")
        checked_in_seat = status_result.get("checked_in_seat")

        if reserved_seat:
            st.warning(
                f"You reserved seat **{reserved_seat['code']}**. "
                f"Scan the QR code within {RESERVATION_MINUTES} min. "
                f"⏱ {countdown(reserved_seat['reserved_until'])} remaining"
            )
        if checked_in_seat:
            st.success(
                f"Checked in at seat **{checked_in_seat['code']}**. "
                f"Re-check in after {RECHECK_HOURS} hour(s). "
                f"⏱ {countdown(checked_in_seat['occupied_until'])} left"
            )

    # ── Fetch seats from Supabase ────────────────────────────────────────
    seats_result = get_seats(token)
    if not seats_result.get("success"):
        st.error(seats_result.get("message", "Could not fetch seats."))
        return

    seats = seats_result["seats"]

    # ── Floor filter ─────────────────────────────────────────────────────
    # Use strict per-floor filtering when ANY seat has a floor field, so
    # selecting Floor 1 never falls back to Ground Floor's seats. Only fall
    # back to "show all" if the database has no floor information at all
    # (legacy case).
    has_floor_field = any(
        s.get("floor") is not None and s.get("floor") != "" for s in seats
    )

    # ── Toolbar row above the map: floor selector + free count + legend ──
    tcol1, tcol2, tcol3 = st.columns([2, 2, 5])
    with tcol1:
        floor_choice = st.selectbox(
            "Floor",
            options=list(FLOOR_CONFIG.keys()),
            index=0,
            key="floor_selector",
            label_visibility="visible",
        )

    floor_number = "0" if floor_choice == "Ground Floor" else "1"
    if has_floor_field:
        floor_seats = [s for s in seats if str(s.get("floor", "")) == floor_number]
    else:
        floor_seats = seats

    free_count = sum(1 for s in floor_seats if s["status"] == "free")

    with tcol2:
        st.markdown(
            f"""
            <div class="chairie-stat" style="margin-top: 26px;">
              <span class="chairie-stat-dot"></span>
              <div class="chairie-stat-text">
                <span class="chairie-stat-number">{free_count}</span>
                <span class="chairie-stat-label">free on {floor_choice}</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with tcol3:
        st.markdown(
            """
            <div class="chairie-legend" style="margin-top: 26px;">
              <span style="color: var(--text-tertiary); font-weight: 600;
                           text-transform: uppercase; letter-spacing: 0.06em;
                           font-size: 11px;">Legend</span>
              <span class="chairie-legend-item">
                <span class="chairie-legend-dot legend-free"></span>Free
              </span>
              <span class="chairie-legend-item">
                <span class="chairie-legend-dot legend-reserved"></span>Reserved
              </span>
              <span class="chairie-legend-item">
                <span class="chairie-legend-dot legend-occupied"></span>Occupied
              </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("<div style='margin-bottom: 14px;'></div>", unsafe_allow_html=True)

    # ── Map: interactive (clickable dots) if JSON layout exists, else legacy image+grid ──
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    floor_cfg = FLOOR_CONFIG.get(floor_choice)

    layout = None
    if INTERACTIVE_MAP_AVAILABLE and floor_cfg is not None:
        json_path = floor_cfg["json_path"]
        if json_path:
            full_path = os.path.join(BASE_DIR, json_path)
            layout = load_layout_data(full_path, silent=True)
        else:
            # Use the module's default JSON candidates (Ground Floor).
            layout = load_layout_data(silent=True)

    if layout and layout.get("seats"):
        # ── INTERACTIVE MAP PATH ────────────────────────────────────────────
        # Merge layout coordinates with live Supabase status (by seat id).
        supabase_by_id = {int(s["id"]): s for s in floor_seats}

        merged_seats = []
        for layout_seat in layout["seats"]:
            try:
                sid = int(layout_seat["id"])
            except (KeyError, TypeError, ValueError):
                continue
            live = supabase_by_id.get(sid)
            merged_seats.append({
                "id":     sid,
                "x":      int(layout_seat.get("x", 0)),
                "y":      int(layout_seat.get("y", 0)),
                "size":   int(layout_seat.get("size", 13)),
                # Live status if Supabase knows this seat, else gray "maintenance".
                "status": (live or {}).get("status", "maintenance"),
            })

        # ── Click handling ───────────────────────────────────────────────
        # Plotly stores the most recent selection event in st.session_state
        # under the chart's key. We read it BEFORE rendering anything so the
        # detail panel above the map can use the up-to-date selection on
        # the same rerun (no extra rerun needed). Per-floor key means each
        # floor's selection persists independently.
        map_key = floor_cfg["map_key"]
        chart_event = st.session_state.get(map_key)
        if isinstance(chart_event, dict):
            sel = chart_event.get("selection")
            points = (sel.get("points") if isinstance(sel, dict) else None) or []
            if points:
                cd = points[0].get("customdata")
                if cd is not None:
                    try:
                        clicked_id = int(cd[0] if isinstance(cd, (list, tuple)) else cd)
                        if clicked_id != st.session_state.get("selected_seat_id"):
                            st.session_state["selected_seat_id"] = clicked_id
                    except (TypeError, ValueError):
                        pass

        # ── Seat detail panel ABOVE the map ─────────────────────────────
        _render_seat_detail_panel(seats, token)

        # ── Map header + interactive map ────────────────────────────────
        st.markdown(
            f"""
            <div class="chairie-section-title">
              Map — {floor_choice}
              <span class="hint">hover a dot for info, click to select, scroll/pinch to zoom</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        render_interactive_map(
            merged_seats,
            selected_seat_id=st.session_state.get("selected_seat_id"),
            image_path=os.path.join(BASE_DIR, floor_cfg["image_filename"]),
            layout_canvas_size=floor_cfg["layout_canvas_size"],
            show_diagnostics=floor_cfg["show_diagnostics"],
            key=map_key,
        )

    else:
        # ── LEGACY FALLBACK: static image + button grid ─────────────────────
        img_map = {
            "Ground Floor": os.path.join(BASE_DIR, "Library_GFloor.jpg"),
            "Floor 1":      os.path.join(BASE_DIR, "Library_1Floor.jpg"),
        }
        img_file = img_map.get(floor_choice, "")

        st.markdown(
            f"""
            <div style="border:2px solid #1f4c66; border-radius:8px; padding:20px;
                        background:#fafafa; margin-bottom:24px;">
              <div style="margin-bottom:14px; font-size:20px; font-weight:600; color:#444;">
                Map — {floor_choice}
              </div>
            """,
            unsafe_allow_html=True,
        )

        if img_file and os.path.exists(img_file):
            st.image(img_file, use_container_width=True)
        else:
            st.info(
                f"Floor plan image '{img_file}' not found. "
                "Place it in the same directory as streamlit_app.py."
            )

        st.markdown("</div>", unsafe_allow_html=True)

        # Seat grid (legacy "Select" buttons)
        st.markdown(
            "<div style='font-size:20px; font-weight:600; margin-bottom:14px; color:#444;'>"
            "Available Seats</div>",
            unsafe_allow_html=True,
        )

        cols_per_row = 5
        for i in range(0, len(floor_seats), cols_per_row):
            row = floor_seats[i : i + cols_per_row]
            cols = st.columns(cols_per_row)
            for col, seat in zip(cols, row):
                with col:
                    color = seat_status_color(seat["status"])
                    owner_note = ""
                    if seat["reserved_by_me"]:
                        owner_note = "🟡 Your reservation"
                    elif seat["occupied_by_me"]:
                        owner_note = "🟢 Your seat"

                    st.markdown(
                        f"""
                        <div style="border:1px solid #2b6f95; border-radius:10px; padding:10px;
                                    margin-bottom:6px; background:#f5f9fc; text-align:center;">
                          <div style="width:22px; height:22px; border-radius:50%;
                                      background:{color}; border:2px solid white;
                                      margin:0 auto 6px auto;"></div>
                          <div style="font-size:13px; font-weight:600; color:#222;">
                            {seat['code']}
                          </div>
                          <div style="font-size:11px; color:#555;">
                            Floor {seat['floor']}
                          </div>
                          <div style="font-size:11px; color:{color}; font-weight:600;">
                            {seat['status'].upper()}
                          </div>
                          <div style="font-size:10px; color:#0a8f4d;">{owner_note}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if st.button("Select", key=f"sel_{seat['id']}"):
                        st.session_state["selected_seat_id"] = seat["id"]

        # For the legacy Floor 1 path, still show the seat details — but
        # at the bottom, since clicks come from the button grid above.
        _render_seat_detail_panel(seats, token)


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="HSG Study Spots", layout="wide")
    init_auth_state()

    if not SUPABASE_OK:
        st.error(
            "⚠️ Supabase is not configured. "
            "Add SUPABASE_URL and SUPABASE_KEY to your Streamlit secrets or .env file."
        )

    if is_logged_in():
        main_app()
    else:
        login_page()


if __name__ == "__main__":
    main()
