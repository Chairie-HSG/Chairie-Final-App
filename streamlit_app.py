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

# Plotly is used by both the interactive map AND the landing page's
# "Today's forecast" charts (shell data for now — wired to mock series
# until we have real time-series occupancy data in Supabase).
import plotly.graph_objects as go

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
# QR CHECK-IN  (utilities from qr_code.py)
# qr_code.py is NOT modified — we just import its pure decoder
# helpers (decode_qr + extract_seat_code) and drive the UI flow
# from streamlit_app.py, since the new "scan any seat" behaviour
# doesn't fit qr_code.show_checkin's reservation-validation flow.
# Graceful fallback if zxing-cpp / numpy / Pillow are missing.
# ─────────────────────────────────────────────────────────────
try:
    from qr_code import decode_qr, extract_seat_code
    QR_CHECKIN_AVAILABLE = True
except Exception:
    QR_CHECKIN_AVAILABLE = False
    decode_qr = None
    extract_seat_code = None


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
        # Which page of the post-login app is currently visible.
        # Possible values: "home", "map", "profile", "settings".
        # Defaults to "home" so users land on the marketing/landing
        # page right after login and click through to the map.
        "current_page": "home",
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
    # Reset the visible page so the next login starts on the landing page.
    st.session_state["current_page"] = "home"
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
    """
    Defining the login page for the website.
    This function creates a login and signup interface. It adds CSS for styling, displays app logo (top middle), switches between login and signup mode, validates the user input and calls the backend functions for logging in or creating a new account.
    """
    # Now we are switching to CSS, to start with the design of the website.
    st.markdown(
        """
        <style>
        /* Hide default Streamlit elements so the page looks cleaner */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}

        /* Making the app background white */
        .stApp {
            background-color: #ffffff;
        }

        /* Giving space at top and bottom and limits the page width so the login form does not stretch across the whole width */
        .block-container {
            padding-top: 3rem;
            padding-bottom: 3rem;
            max-width: 500px;
        }

        /* Styling the main title */
        .login-title {
            font-family: 'Roboto', 'Segoe UI', Arial, sans-serif;
            font-size: 24px;
            font-weight: 400;
            color: #202124;
            text-align: center;
            margin-top: 8px;
            margin-bottom: 8px;
        }
        /* Styling subtitle below the title*/
        .login-subtitle {
            font-family: 'Roboto', 'Segoe UI', Arial, sans-serif;
            font-size: 16px;
            font-weight: 400;
            color: #202124;
            text-align: center;
            margin-bottom: 28px;
        }

        /* Styling the streamlit form so it looks like a card */
        div[data-testid="stForm"] {
            border: 1px solid #dadce0;
            border-radius: 8px;
            padding: 32px 36px 28px 36px;
            background: #ffffff;
        }

        /* Styling email and password input fields */
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
        /*Styling the labels of the input fields*/
        .stTextInput label {
            font-size: 13px !important;
            color: #5f6368 !important;
            font-family: 'Roboto', 'Segoe UI', Arial, sans-serif !important;
        }

        /* Styling small tagline below the input fields */
        .tagline {
            font-family: 'Roboto', 'Segoe UI', Arial, sans-serif;
            font-size: 14px;
            color: #5f6368;
            line-height: 1.5;
            margin-top: 20px;
            margin-bottom: 8px;
            text-align: left;
        }
        /*Making word "Chairie" in the tagline slightly darker so it stands out*/
        .tagline strong {
            color: #202124;
            font-weight: 500;
        }

        /* Styling the main submit button inside the form */
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
        /*Changing the main button design when the mouse hovers over it*/
        .stFormSubmitButton button:hover {
            background-color: #1765cc !important;
            color: #ffffff !important;
            box-shadow: 0 1px 2px rgba(60,64,67,0.3), 0 1px 3px 1px rgba(60,64,67,0.15) !important;
        }

        /* Styling the button that switches between login and signup */
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
        /*Changing the switch button design, when the mouse hovers over it*/
        .switch-row .stButton button:hover {
            background: #f8f9fa !important;
            border-color: #d2d5d9 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Folder path of the current Python file
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    #Creating the full path to the logo
    logo_path = os.path.join(BASE_DIR, "full_size_logo.png")

    #Checking whether image of logo exists in the folder
    if os.path.exists(logo_path):
        c1, c2, c3 = st.columns([1, 1, 1]) #Creating three columsn so that the logo can be placed in the middle column
        with c2: #Placing the logo in the middle
            st.image(logo_path, width=110)
    else: #In case the logo would have been missing, show the app name as a text
        st.markdown(
            "<div style='text-align:center; font-size:32px; font-weight:600; "
            "color:#0a8f4d; margin-bottom:8px;'>Chairie</div>",
            unsafe_allow_html=True,
        )

    # Getting the current authentication mode from Streamlit's session state and if no mode exists yet, the page starts in login mode
    mode = st.session_state.get("auth_mode", "login")

    # If the current mode is login, show the login title and subtitle
    if mode == "login":
        st.markdown('<div class="login-title">Sign in</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="login-subtitle">Use your Seat Booking account</div>',
            unsafe_allow_html=True,
        )
    else: #Otherwise, show the signup title and subtitle
        st.markdown('<div class="login-title">Create your account</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="login-subtitle">to continue to Seat Booking</div>',
            unsafe_allow_html=True,
        )

    # If current mode is login, display the login form
    if mode == "login":
        with st.form("login_form", clear_on_submit=False): #Create a streamlit form for login input
            email = st.text_input("Email", key="login_email") #Create an input field where the user enters their email
            password = st.text_input("Password", type="password", key="login_password") #Create a password input field where the typed text is hidden

            st.markdown( #Display Chairie tagline inside the form
                '<div class="tagline">'
                "<strong>Chairie</strong>, Made by Students, for Students."
                "</div>",
                unsafe_allow_html=True,
            )

            submitted = st.form_submit_button("Next") #Create the submit button for the login form

        # Start styling a row for the button that switches to signup mode
        st.markdown('<div class="switch-row">', unsafe_allow_html=True)
        if st.button("Create account", key="go_signup"): #IF user clicks this button, switch to signup mode
            st.session_state["auth_mode"] = "signup" #Save the new mode in session state
            st.rerun() #Reload the page so the signup form appears immediately
        st.markdown("</div>", unsafe_allow_html=True) #Close the styled switch-row div

        if submitted: #Check whether the login form was submitted
            if not email or not password: #If not, show warning text
                st.warning("Please enter both email and password.")
            else: #If both fields are filled, try logging user in
                result = login_request(email, password) #Send email password to login backend function
                if result["success"]: #If backend says login was successful
                    login_user(result["username"], result["token"]) #Save logged-in user's data in the app session
                    st.success("Login successful.") 
                    st.rerun() #Reloading app so the user sees the logged-in area
                else: #Show error message if it did not work
                    st.error(result["message"])

    else:  # If current mode is login, display signup form
        with st.form("signup_form", clear_on_submit=False): #Creating streamlit form for signup input
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password", type="password", key="signup_password")
            confirm = st.text_input("Confirm password", type="password", key="signup_confirm")

            st.markdown( #Displaying Chairie tagline inside the form
                '<div class="tagline">'
                "<strong>Chairie</strong>, Made by Students, for Students."
                "</div>",
                unsafe_allow_html=True,
            )

            submitted = st.form_submit_button("Create") #Create submit button for signup form

        st.markdown('<div class="switch-row">', unsafe_allow_html=True) #Styled row for button that switches back to login mode
        if st.button("Sign in instead", key="go_login"): #If users click this button, switch back to login mode
            st.session_state["auth_mode"] = "login" #Save new mode in session state
            st.rerun() #Reloading the page so the login form appears immediately
        st.markdown("</div>", unsafe_allow_html=True) #Close the styled switch-row div

        if submitted: #Check whether the signup form was submitted
            if not email or not password or not confirm: #If any field is empty, show a warning text
                st.warning("Please fill in all fields.")
            elif password != confirm: #If two passwords fields are different, show warning text
                st.warning("Passwords do not match.")
            elif len(password) < 6:
                st.warning("Password must be at least 6 characters.")
            else: #Creating new account, if everything filled out correctly
                result = signup_request(email, password) #Send email and password to backend function
                if result["success"]:
                    st.success(result["message"])
                    st.info("Go back to Sign in and use your new account.")
                    st.session_state["auth_mode"] = "login" #Automatically switch the page back to login mode
                else: #If signup failed, show error message from backend
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


# ─────────────────────────────────────────────────────────────
# FLOOR HELPERS  (used by both map page and landing stats)
# ─────────────────────────────────────────────────────────────
# Floor metadata used by every page that needs to show per-floor
# numbers (landing-page stat cards, map-page filter, etc.). The
# "matches" set is what `_seat_belongs_to_floor` checks against,
# so Supabase rows can store floor as int 0/1, string "0"/"1",
# "Ground", "Ground Floor", or "Floor 1" — anything reasonable.
# NOTE on the floor convention used in Supabase
# ─────────────────────────────────────────────
# The `seats.floor` column stores an integer:
#     1  →  Library Ground Floor   (street level)
#     2  →  Library Upper Floor    (one above)
# This is the "elevator-button" convention — floor 1 is the lobby.
# `_seat_belongs_to_floor` lowercases and string-coerces the raw value
# before checking it against the `matches` set below, so int 1, str "1",
# and any human-friendly variant ("ground", "Ground Floor", …) all work.
FLOOR_META = {
    "Ground Floor": {
        "display":  "Library Ground Floor",
        "matches":  {
            "1",                                          # ← actual DB value
            "0", "g", "gf", "eg",                         # alt numeric/code
            "ground", "ground floor", "groundfloor",      # English
            "erdgeschoss",                                # German (HSG is in St. Gallen)
            "library ground floor",                       # display-name fallback
        },
        "capacity": 207,   # total physical seats; ALWAYS the denominator
    },
    "Floor 1": {
        "display":  "Library Upper Floor",
        "matches":  {
            "2",                                          # ← actual DB value
            "u",                                          # alt code
            "first", "first floor", "floor 1",            # English
            "upper", "upper floor", "level 1",
            "obergeschoss", "1. og", "og1",               # German
            "library upper floor",                        # display-name fallback
        },
        "capacity": 296,
    },
}


def _seat_belongs_to_floor(seat, floor_choice):
    """Return True if `seat` lives on the floor named by `floor_choice`.

    Robust to whatever the Supabase row stores in `floor`:
      - int 0 / 1
      - str "0" / "1"
      - str "Ground" / "Ground Floor" / "Floor 1" / "Upper"
    Falls back to False on missing / unrecognized floor values.

    NOTE: this replaces the previous brittle comparison
        str(s.get("floor", "")) == ("0" if floor_choice == "Ground Floor" else "1")
    which failed to filter when Supabase stored the floor as anything
    other than the bare digits "0" / "1" — and was the root of the
    "ground-floor count shows up on the upper-floor map" bug.
    """
    meta = FLOOR_META.get(floor_choice)
    if not meta:
        return False
    raw = seat.get("floor")
    if raw is None:
        return False
    return str(raw).strip().lower() in meta["matches"]


def _compute_floor_stats(seats, floor_choice):
    """Aggregate counts for one floor: total/free/reserved/occupied.

    Returns a dict the landing page + map page can both use:
        {
          "total":    <int>,
          "free":     <int>,
          "reserved": <int>,
          "occupied": <int>,
          "taken":    <int>,    # reserved + occupied
          "pct_taken": <float>,  # 0.0–1.0
          "availability": "open" | "busy" | "full",
        }
    If Supabase has no rows for this floor we fall back to
    FLOOR_META[...]["capacity"] for `total` so the progress bar
    still has a sensible denominator (and `free` is treated as 0).
    """
    rows     = [s for s in seats if _seat_belongs_to_floor(s, floor_choice)]
    capacity = FLOOR_META[floor_choice]["capacity"]
    matched  = len(rows)
    # ALWAYS use the configured capacity as the denominator, even if we
    # matched more rows than expected (e.g. seeded test data). Otherwise
    # the upper floor was showing "2 / 2" when only 2 test seats existed,
    # instead of "2 / 296" against its real capacity.
    total    = max(capacity, matched)
    free     = sum(1 for s in rows if s.get("status") == "free")
    reserved = sum(1 for s in rows if s.get("status") == "reserved")
    occupied = sum(1 for s in rows if s.get("status") == "occupied")
    taken    = reserved + occupied
    pct      = (taken / total) if total else 0.0

    if pct >= 0.85:
        availability = "full"
    elif pct >= 0.60:
        availability = "busy"
    else:
        availability = "open"

    return {
        "total":        total,
        "free":         free,
        "reserved":     reserved,
        "occupied":     occupied,
        "taken":        taken,
        "pct_taken":    pct,
        "availability": availability,
    }


# ─────────────────────────────────────────────────────────────
# SHARED CHROME  (sidebar + top bar — visible on every page)
# ─────────────────────────────────────────────────────────────
def _go_to(page):
    """Helper used by sidebar buttons + top-bar email click."""
    st.session_state["current_page"] = page
    # Clear any per-page selection state that shouldn't survive a
    # page change (e.g. don't keep a previously-clicked seat
    # selected when the user comes back to the map later).
    if page != "map":
        st.session_state["selected_seat_id"] = None


def _render_sidebar():
    """Render the left-hand navigation rail (Home / Map / Profile / Settings).

    The active page button is rendered with type="primary" so the
    CSS in app_styles.html can highlight it in Chairie green.
    """
    current = st.session_state.get("current_page", "home")

    with st.sidebar:
        # Brand block at the top
        st.markdown(
            """
            <div class="chairie-sidebar-brand">
              <span class="chairie-sidebar-brand-mark">C</span>
              <div class="chairie-sidebar-brand-text">
                <span class="chairie-sidebar-brand-name">Chairie</span>
                <span class="chairie-sidebar-brand-sub">Seat Finder</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            '<div class="chairie-nav-label">Menu</div>',
            unsafe_allow_html=True,
        )

        # (label, page_key) pairs — order is the visible order in the rail.
        nav_items = [
            ("🏠   Home",      "home"),
            ("🗺️   Map",       "map"),
            ("👤   Profile",   "profile"),
            ("⚙️   Settings",  "settings"),
        ]
        for label, page_key in nav_items:
            is_active = (current == page_key)
            if st.button(
                label,
                key=f"nav_{page_key}",
                # type="primary" → CSS paints it green; otherwise transparent
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                _go_to(page_key)
                st.rerun()


def _render_top_bar(page_label):
    """Render the shared top bar on every post-login page.

    Layout (single row):
        [logo + name + page label]      [email chip]  [logout]
    The email chip is clickable — clicking it routes to the
    Profile page (matching the requirement that "clicking on your
    email will land you to the profile page as well").
    """
    # The whole bar is wrapped in a keyed container so the CSS rule
    # `.st-key-chairie_topbar` can paint it as a single white card.
    with st.container(key="chairie_topbar"):
        col_left, col_right = st.columns([6, 4])

        with col_left:
            st.markdown(
                f"""
                <div style="display: flex; align-items: center; gap: 14px;">
                  <div class="chairie-brand">
                    <span class="chairie-brand-mark">C</span>
                    <span>Chairie</span>
                  </div>
                  <div class="chairie-page-label">{page_label}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col_right:
            # Keyed sub-container so the CSS scope
            # `.st-key-chairie_topbar_right` only restyles these two
            # buttons (the email chip + the red logout pill).
            with st.container(key="chairie_topbar_right"):
                bcol_email, bcol_logout = st.columns([3, 2])
                with bcol_email:
                    email_label = st.session_state.get("username") or "Account"
                    if st.button(
                        email_label,
                        key="email_topbar_btn",
                        use_container_width=True,
                        help="Open your profile",
                    ):
                        _go_to("profile")
                        st.rerun()
                with bcol_logout:
                    if st.button(
                        "Logout",
                        key="logout_topbar_btn",
                        type="secondary",
                        use_container_width=True,
                    ):
                        logout_user()
                        st.rerun()


# ─────────────────────────────────────────────────────────────
# LANDING PAGE  (the "home" route — users land here after login)
# ─────────────────────────────────────────────────────────────
#
# Layout, top to bottom:
#   1) Green hero card with the brand slogan + "Find a Seat" CTA
#      → the CTA routes to the map page.
#   2) KPI strip (3 small cards): total free seats, floors monitored,
#      last-updated stamp. Light, glanceable summary.
#   3) Per-floor stat cards: name, "free / total" big number,
#      progress bar (green→honey→red as occupancy rises), and an
#      Open/Busy/Full pill in the top-right.
#   4) Per-floor "Today's forecast" Plotly bar chart — shell data
#      for now (mocked hourly occupancy). When real time-series
#      occupancy lands in Supabase, swap _mock_forecast_series()
#      for a real fetch and the rest of the page keeps working.

def _mock_forecast_series(floor_choice):
    """Generate a stable, plausible hourly occupancy curve (8h–21h).

    Returns a list of 14 floats in 0..1 indexed by hour 8..21.
    The curve peaks around 10–11am and again 14–15pm to match
    typical library traffic. Different floors get slightly
    different shapes so the two charts don't look identical.

    Pure-Python (no numpy) so we don't add a dependency just for
    placeholder data. Replace this with a Supabase query when real
    history is available.
    """
    hours = list(range(8, 22))
    if floor_choice == "Ground Floor":
        # Wider peak, busier overall
        base = [0.30, 0.62, 0.78, 0.72, 0.58, 0.70, 0.74, 0.72,
                0.66, 0.58, 0.40, 0.22, 0.12, 0.06]
    else:
        # Quieter mornings, peak 1–2pm
        base = [0.20, 0.55, 0.68, 0.66, 0.52, 0.74, 0.72, 0.66,
                0.60, 0.50, 0.32, 0.18, 0.10, 0.05]
    return list(zip(hours, base[: len(hours)]))


def _render_forecast_chart(floor_choice, floor_stats):
    """Render the "Today's forecast" bar chart for a single floor.

    The bar for the current hour is recoloured to match the floor's
    current availability — green when there are free seats, honey
    when it's getting busy, red when it's full. The other bars are
    a calm dark green so the eye is pulled to "right now".
    """
    series = _mock_forecast_series(floor_choice)
    hours      = [h for h, _ in series]
    occupancy  = [pct for _, pct in series]

    # Find "now" — clamp to the chart range so we always highlight
    # one bar even outside operating hours.
    now_hour = dt.now().hour
    if now_hour < hours[0]:
        now_hour = hours[0]
    elif now_hour > hours[-1]:
        now_hour = hours[-1]

    # Per-bar colour: highlight the current hour with the floor's
    # availability colour, mute all the others.
    availability = floor_stats["availability"]
    highlight_color = {
        "open":  "#4A7C2D",
        "busy":  "#F2C46D",
        "full":  "#E30613",
    }.get(availability, "#4A7C2D")
    base_color = "#cfe0bf"   # very soft green tint for muted bars

    colors = [
        highlight_color if h == now_hour else base_color
        for h in hours
    ]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=hours,
        y=occupancy,
        marker_color=colors,
        hovertemplate="<b>%{x}:00</b><br>%{y:.0%} taken<extra></extra>",
        showlegend=False,
    ))
    fig.update_layout(
        title=None,
        margin=dict(l=10, r=10, t=10, b=10),
        height=220,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            tickmode="array",
            tickvals=hours,
            ticktext=[str(h) for h in hours],
            showgrid=False,
            zeroline=False,
            tickfont=dict(size=11, color="#9ca3af"),
        ),
        yaxis=dict(
            tickformat=".0%",
            range=[0, 1],
            showgrid=True,
            gridcolor="#f1eee2",
            zeroline=False,
            tickfont=dict(size=11, color="#9ca3af"),
        ),
        bargap=0.30,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_floor_stat_card(floor_choice, floor_stats):
    """Render the per-floor stat card for the landing page."""
    pct      = floor_stats["pct_taken"]
    free     = floor_stats["free"]
    total    = floor_stats["total"]
    avail    = floor_stats["availability"]
    display  = FLOOR_META[floor_choice]["display"]

    # Progress bar colour matches availability tier
    bar_class = {"open": "low", "busy": "mid", "full": "high"}[avail]
    pill_text = {"open": "Open", "busy": "Busy", "full": "Full"}[avail]

    st.markdown(
        f"""
        <div class="chairie-stat-card">
          <div class="chairie-stat-card-header">
            <div>
              <div class="chairie-stat-card-title">{display}</div>
              <div class="chairie-stat-card-sub">Live availability</div>
            </div>
            <span class="chairie-availability-pill {avail}">{pill_text}</span>
          </div>

          <div>
            <span class="chairie-stat-bignum">{free}</span>
            <span class="chairie-stat-bignum-suffix">/ {total}</span>
          </div>
          <div class="chairie-stat-bignum-label">seats free right now</div>

          <div class="chairie-progress">
            <div class="chairie-progress-fill {bar_class}"
                 style="width: {pct * 100:.1f}%;"></div>
          </div>
          <div class="chairie-progress-meta">
            <span>{floor_stats['taken']} taken</span>
            <span>{pct * 100:.0f}% full</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────
# QR SCAN CARD  (lives on the map page, not in qr_code.py)
# ─────────────────────────────────────────────────────────────
# The original qr_code.show_checkin validates a scanned code against
# an EXPECTED reservation. The new flow doesn't do that — the user
# can scan any seat at any time, and we decide what happens based on
# the scanned seat's live status. So the camera/UI flow lives here
# in streamlit_app.py; qr_code.py is only used for its pure decoder
# helpers (decode_qr + extract_seat_code), as it was written.
def _resolve_scanned_code(token, seats, scanned_code):
    """Look up `scanned_code` in `seats` and decide what to do.

    Returns a small dict the UI can render directly:
        {"kind": "ok" | "occupied" | "reserved" | "error",
         "message": str}

    Outcomes:
      • Free seat            → check user in, return "ok"
      • The user's own seat  → "ok" ("Successfully checked in" or
                                "Already checked in")
      • Occupied by someone  → "occupied" with remaining time
      • Reserved by someone  → "reserved" with remaining time
      • Code not in DB       → "error"
    """
    code_norm = (scanned_code or "").strip().lower()
    if not code_norm:
        return {"kind": "error", "message": "No seat code found in that QR."}

    seat = next(
        (s for s in seats if str(s.get("code", "")).strip().lower() == code_norm),
        None,
    )
    if not seat:
        return {
            "kind": "error",
            "message": f"Seat code '{scanned_code}' is not in our database.",
        }

    # Already checked in here.
    if seat.get("occupied_by_me"):
        return {
            "kind": "ok",
            "message": f"You're already checked in at seat {seat['code']}.",
        }

    # Occupied by someone else → show remaining time.
    if seat["status"] == "occupied":
        until = seat.get("occupied_until")
        remaining = countdown(until) if until else "an unknown amount of time"
        return {
            "kind": "occupied",
            "message": f"Seat {seat['code']} is occupied. ⏱ {remaining} remaining.",
        }

    # Reserved by someone else → show remaining time.
    if seat["status"] == "reserved" and not seat.get("reserved_by_me"):
        until = seat.get("reserved_until")
        remaining = countdown(until) if until else "an unknown amount of time"
        return {
            "kind": "reserved",
            "message": f"Seat {seat['code']} is reserved by another user. ⏱ {remaining}",
        }

    # Free, or reserved by the current user → attempt check-in.
    result = check_in_from_qr(token, seat["id"])
    if result.get("success"):
        return {
            "kind": "ok",
            "message": f"✅ Successfully checked in to seat {seat['code']}.",
        }
    return {
        "kind": "error",
        "message": result.get("message", "Check-in failed."),
    }


def _render_qr_scan_card(token, seats, reserved_seat):
    """Persistent 'Scan QR' card. The camera is NOT live until the
    user clicks the 'Scan QR code' button — gated by session state.

    Manual code entry is offered as a fallback for desktop users
    or when the camera is unavailable.
    """
    if not QR_CHECKIN_AVAILABLE:
        st.info(
            "QR check-in is unavailable — install `zxing-cpp`, `numpy`, "
            "and `Pillow`, and keep `qr_code.py` next to this app."
        )
        return

    ss = st.session_state
    # Init session state once.
    ss.setdefault("qr_scanner_open", False)   # camera widget visible?
    ss.setdefault("qr_camera_id",    0)       # bumped each scan → fresh widget
    ss.setdefault("qr_last_result",  None)    # last scan outcome to display

    # Section header — small contextual hint if user already has a reservation.
    if reserved_seat:
        hint = f"your reservation is seat {reserved_seat['code']}"
    else:
        hint = "scan any seat's QR code to check in"
    st.markdown(
        f"<div class='chairie-section-title'>Quick check-in "
        f"<span class='hint'>{hint}</span></div>",
        unsafe_allow_html=True,
    )

    # Show last scan result (if any) as a Streamlit alert.
    if ss["qr_last_result"]:
        res  = ss["qr_last_result"]
        kind = res.get("kind", "info")
        msg  = res.get("message", "")
        if   kind == "ok":               st.success(msg)
        elif kind in ("occupied",
                      "reserved"):       st.warning(msg)
        else:                            st.error(msg)
        if st.button("Dismiss", key="qr_dismiss_btn"):
            ss["qr_last_result"] = None
            st.rerun()

    # ─── Scanner CLOSED state ──────────────────────────────────────────
    if not ss["qr_scanner_open"]:
        col_scan, col_manual = st.columns([1, 2])
        with col_scan:
            if st.button(
                "📷 Scan QR code",
                type="primary",
                key="qr_open_btn",
                use_container_width=True,
            ):
                ss["qr_scanner_open"] = True
                ss["qr_camera_id"]  += 1   # force a fresh camera widget
                ss["qr_last_result"] = None
                st.rerun()
        with col_manual:
            with st.expander("Or type the seat code printed under the QR",
                             expanded=False):
                code = st.text_input(
                    "Seat code",
                    placeholder="e.g. A2",
                    key="qr_manual_code_input",
                ).strip()
                if code and st.button("Submit code", key="qr_manual_submit_btn"):
                    ss["qr_last_result"] = _resolve_scanned_code(token, seats, code)
                    st.rerun()
        return

    # ─── Scanner OPEN state ────────────────────────────────────────────
    # The camera widget only renders here, so it never goes live until
    # the user explicitly clicks the button above.
    st.caption("Point your camera at the QR code on the seat.")
    photo = st.camera_input(
        "QR scanner",
        key=f"qr_camera_{ss['qr_camera_id']}",  # unique key per scan session
        label_visibility="collapsed",
    )

    if photo is not None:
        from PIL import Image  # local import to avoid import errors when
                               # qr deps aren't installed and this branch
                               # is unreachable.
        image     = Image.open(photo).convert("RGB")
        qr_string = decode_qr(image)
        if not qr_string:
            st.warning(
                "No QR code detected. Try a clearer angle, or use "
                "the manual code option."
            )
        else:
            scanned_code = extract_seat_code(qr_string)
            ss["qr_last_result"]  = _resolve_scanned_code(token, seats, scanned_code)
            ss["qr_scanner_open"] = False
            st.rerun()

    if st.button("Cancel", key="qr_cancel_btn"):
        ss["qr_scanner_open"] = False
        st.rerun()


def landing_page(token):
    """Render the landing / home page that users see after login.

    Hero + KPI strip + per-floor stat cards + per-floor forecast charts.
    All numbers come from `get_seats(token)`; forecast bars use a
    mock series until real time-series occupancy lands in Supabase.
    """
    _render_top_bar("Home")

    # ── Hero card with slogan + CTA ─────────────────────────────────────
    st.markdown(
        """
        <div class="chairie-hero">
          <div class="chairie-hero-eyebrow">Chairie · HSG Seat Finder</div>
          <div class="chairie-hero-slogan">No Wandering,<br>Just Studying.</div>
          <div class="chairie-hero-sub">
            See live seat availability across every floor of the HSG library
            and reserve your spot in seconds. No more loops around the
            study halls.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # The CTA is a real Streamlit button so it can route. We host it in
    # a keyed container so the CSS rule `.st-key-chairie_hero_cta` can
    # paint just THIS button in honey-yellow (a wrapper-div approach
    # doesn't work — Streamlit renders each widget in its own DOM
    # container, so an open-then-close div in two markdown calls leaves
    # the button as a sibling, not a child).
    with st.container(key="chairie_hero_cta"):
        cta_col, _ = st.columns([2, 6])
        with cta_col:
            if st.button("Find a Seat  →", key="hero_find_seat_btn", use_container_width=True):
                _go_to("map")
                st.rerun()

    # ── Pull fresh data from Supabase ───────────────────────────────────
    seats_result = get_seats(token)
    if not seats_result.get("success"):
        st.error(seats_result.get("message", "Could not fetch seats."))
        return
    seats = seats_result["seats"]

    # ── KPI strip ───────────────────────────────────────────────────────
    total_free = sum(1 for s in seats if s.get("status") == "free")
    floors_n   = len(FLOOR_META)
    now_str    = dt.now().strftime("%H:%M")

    st.markdown(
        '<div class="chairie-section-title">At a glance</div>',
        unsafe_allow_html=True,
    )
    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown(
            f"""
            <div class="chairie-kpi">
              <div class="chairie-kpi-icon green">✓</div>
              <div class="chairie-kpi-text">
                <span class="chairie-kpi-num">{total_free}</span>
                <span class="chairie-kpi-label">Seats free right now</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with k2:
        st.markdown(
            f"""
            <div class="chairie-kpi">
              <div class="chairie-kpi-icon honey">▦</div>
              <div class="chairie-kpi-text">
                <span class="chairie-kpi-num">{floors_n}</span>
                <span class="chairie-kpi-label">Floors monitored</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with k3:
        st.markdown(
            f"""
            <div class="chairie-kpi">
              <div class="chairie-kpi-icon red">⟳</div>
              <div class="chairie-kpi-text">
                <span class="chairie-kpi-num">{now_str}</span>
                <span class="chairie-kpi-label">Last updated · auto-refresh</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── Per-floor stat cards ────────────────────────────────────────────
    st.markdown(
        '<div class="chairie-section-title">Library by floor '
        '<span class="hint">live numbers from Supabase</span></div>',
        unsafe_allow_html=True,
    )

    floor_choices = list(FLOOR_META.keys())
    stat_cols = st.columns(len(floor_choices))
    floor_stats_cache = {}
    for col, floor_choice in zip(stat_cols, floor_choices):
        stats = _compute_floor_stats(seats, floor_choice)
        floor_stats_cache[floor_choice] = stats
        with col:
            _render_floor_stat_card(floor_choice, stats)

    # ── Per-floor "Today's forecast" charts ─────────────────────────────
    st.markdown(
        '<div class="chairie-section-title">Today\'s forecast '
        '<span class="hint">typical occupancy by hour</span></div>',
        unsafe_allow_html=True,
    )
    fc_cols = st.columns(len(floor_choices))
    for col, floor_choice in zip(fc_cols, floor_choices):
        with col:
            st.markdown(
                f"<div class='chairie-eyebrow'>{FLOOR_META[floor_choice]['display']}</div>",
                unsafe_allow_html=True,
            )
            _render_forecast_chart(floor_choice, floor_stats_cache[floor_choice])


# ─────────────────────────────────────────────────────────────
# PROFILE PAGE  (placeholder for now)
# ─────────────────────────────────────────────────────────────
def profile_page(token):
    """Empty placeholder per the spec — to be fleshed out later."""
    _render_top_bar("Profile")
    st.markdown(
        f"""
        <div class="chairie-placeholder">
          <div class="chairie-placeholder-icon">👤</div>
          <div class="chairie-placeholder-title">Your profile</div>
          <div class="chairie-placeholder-sub">
            Signed in as <strong>{st.session_state.get("username", "anonymous")}</strong>.
            Profile details, reservation history and preferences will live
            here. Coming soon.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────
# SETTINGS PAGE  (placeholder for now)
# ─────────────────────────────────────────────────────────────
def settings_page(token):
    """Empty placeholder per the spec — to be fleshed out later."""
    _render_top_bar("Settings")
    st.markdown(
        """
        <div class="chairie-placeholder">
          <div class="chairie-placeholder-icon">⚙</div>
          <div class="chairie-placeholder-title">Settings</div>
          <div class="chairie-placeholder-sub">
            Notification preferences, appearance and account controls will
            live here. Coming soon.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────
# MAP PAGE  (the original main_app body — now scoped to "map")
# ─────────────────────────────────────────────────────────────
def map_page(token):
    """Interactive seat-reservation map (one floor at a time).

    This is the actual product: pick a floor, click a seat, reserve
    or check in. The top bar + sidebar come from the router; this
    function only renders the page body.
    """
    _render_top_bar("Library Map")

    # ── User status banner ───────────────────────────────────────────────
    # Hoisted here (not nested inside the if-success branch) so the QR
    # check-in section at the bottom of this page can reuse the same
    # reservation info without re-querying Supabase.
    reserved_seat   = None
    checked_in_seat = None
    status_result   = get_user_status(token)
    if status_result.get("success"):
        reserved_seat   = status_result.get("reserved_seat")
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

    # ── QR scan card  (always visible, near the reservation info) ────────
    # Placed here — between the status banner and the map toolbar — so the
    # check-in entry point is right next to the user's reservation
    # information (when they have one), and still visible to everyone
    # else. The camera is OFF until the user clicks "Scan QR code".
    _render_qr_scan_card(token, seats, reserved_seat)

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

    # FIX for the per-floor count bug:
    # The previous code did a brittle exact-string match against "0"/"1",
    # which silently filtered out rows whose `floor` was stored as
    # an int 0/1 or as a name ("Ground"). When the filter returned
    # an empty list, the map fell back to rendering nothing meaningful
    # but the badge above could still read like it was reporting
    # numbers from the wrong floor.
    # _seat_belongs_to_floor() handles every reasonable representation,
    # so the "free on <floor>" badge below now always matches the
    # floor the user selected.
    floor_seats = [s for s in seats if _seat_belongs_to_floor(s, floor_choice)]
    free_count  = sum(1 for s in floor_seats if s["status"] == "free")

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
        # Merge layout coordinates with live Supabase status.
        #
        # The layout JSON uses sequential integer IDs starting at 1 per
        # floor file, but Supabase auto-increment may give the same seats
        # entirely different IDs (e.g. 505+) and the layout JSON has no
        # `code` field, so neither id-matching nor code-matching can
        # bridge the two sides.
        #
        # FIX (simplest, no data migration required): match by POSITION.
        # Layout seats sorted by id and Supabase seats (filtered to this
        # floor) sorted by id describe the same physical seat sequence
        # (A1 is layout position 1, A2 is position 2, ...). Zipping them
        # in order gives the correct status for each dot. This will keep
        # working even if Supabase is re-seeded with brand new IDs again.
        floor_supabase_sorted = sorted(
            floor_seats,
            key=lambda s: int(s.get("id") or 0),
        )
        layout_sorted = sorted(
            layout["seats"],
            key=lambda ls: (
                int(ls["id"])
                if str(ls.get("id", "")).lstrip("-").isdigit()
                else 0
            ),
        )

        merged_seats = []
        matched      = 0
        for i, layout_seat in enumerate(layout_sorted):
            try:
                layout_id = int(layout_seat["id"])
            except (KeyError, TypeError, ValueError):
                continue

            live = floor_supabase_sorted[i] if i < len(floor_supabase_sorted) else None
            if live:
                matched += 1

            # Use the SUPABASE id (when paired) as the dot's click id so
            # the detail panel can look up the right row via
            # `s["id"] == selected_id`. Fall back to layout id otherwise.
            try:
                click_id = int(live["id"]) if live else layout_id
            except (TypeError, ValueError):
                click_id = layout_id

            merged_seats.append({
                "id":     click_id,
                "x":      int(layout_seat.get("x", 0)),
                "y":      int(layout_seat.get("y", 0)),
                "size":   int(layout_seat.get("size", 13)),
                "status": (live or {}).get("status", "maintenance"),
            })

        # ── Diagnostic expander  (auto-opens if the merge is broken) ─────
        total_layout = len(layout_sorted)
        unmatched    = total_layout - matched
        _broken      = matched == 0 and total_layout > 0
        with st.expander(
            f"🔧 Map merge — {matched}/{total_layout} layout seats paired "
            f"with Supabase (position-based)",
            expanded=_broken,
        ):
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("Supabase total",         len(seats))
            mc2.metric("Supabase on this floor", len(floor_seats))
            mc3.metric("Layout seats",           total_layout)
            mc4.metric("Paired",                 matched)

            if _broken:
                st.warning(
                    "**No seats paired on this floor.** Position-based "
                    "matching needs at least one Supabase row whose "
                    "`floor` column matches the current view. Verify "
                    "that the Supabase `floor` values map to "
                    "`FLOOR_META` (currently expects `Ground Floor` / "
                    "`Floor 1` / `1` / `2` / etc.)."
                )
            elif unmatched > 0:
                st.info(
                    f"{unmatched} layout seat(s) on this floor have no "
                    f"Supabase pair — they'll render as gray "
                    f"'maintenance'. Usually means the layout has more "
                    f"seats than Supabase does for this floor."
                )

            if floor_supabase_sorted:
                st.markdown(
                    "**Supabase on this floor, sorted by id** (first 5):"
                )
                st.table([
                    {
                        "id":     s.get("id"),
                        "code":   s.get("code"),
                        "floor":  s.get("floor"),
                        "status": s.get("status"),
                    }
                    for s in floor_supabase_sorted[:5]
                ])

            if layout_sorted:
                st.markdown("**Layout JSON, sorted by id** (first 5):")
                st.table([
                    {k: v for k, v in ls.items()
                     if k in ("id", "code", "label", "name", "x", "y")}
                    for ls in layout_sorted[:5]
                ])

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
# PAGE ROUTER
# ─────────────────────────────────────────────────────────────
#
# main_app() is the single entry point for every post-login screen.
# It does the shared work once (auth check, auto-refresh, CSS inject,
# sidebar) and then dispatches to whichever page function the user
# is currently viewing. Each page function renders its own top bar
# via _render_top_bar(...) so the email + logout pair is present
# everywhere (the requirement: "logout button should be next to the
# email placement top right all time (for all pages)").

# Map of page key → renderer function. Edit here when adding pages.
PAGE_ROUTES = {
    "home":     "landing_page",
    "map":      "map_page",
    "profile":  "profile_page",
    "settings": "settings_page",
}


def main_app():
    require_login()

    # Auto-refresh every 30s so live countdowns + seat statuses stay
    # fresh on every page (not just the map).
    st_autorefresh(interval=30000, key="seat_refresh")

    # Inject the global CSS shell once per page render.
    _inject_app_shell()

    # The left-hand navigation rail is always visible on every page.
    _render_sidebar()

    token = st.session_state["token"]

    # Dispatch to the right page based on session state.
    current = st.session_state.get("current_page", "home")
    page_fn_name = PAGE_ROUTES.get(current, "landing_page")
    page_fn = globals().get(page_fn_name)
    if not callable(page_fn):
        # Defensive fallback — should never trip unless PAGE_ROUTES
        # is misconfigured. Land the user back on home.
        st.session_state["current_page"] = "home"
        landing_page(token)
        return

    page_fn(token)


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="HSG Study Spots",
        layout="wide",
        # Make the new navigation rail visible by default — otherwise
        # users on first visit have to click the tiny chevron to find
        # the Home / Map / Profile / Settings tabs.
        initial_sidebar_state="expanded",
    )
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
