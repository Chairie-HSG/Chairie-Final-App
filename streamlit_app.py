"""
streamlit_app.py
================

Frontend / UI layer for Chairie (HSG Seat Finder).

This file is what Streamlit actually runs — i.e. the entry point you
launch with ``streamlit run streamlit_app.py``. It owns ONLY rendering
and routing. The pages it draws are:

    - Login + signup page (shown when the user is not logged in)
    - Sidebar navigation rail + shared top bar (shown on every
      post-login page)
    - "home" / landing page — hero card, KPI strip, per-floor stat
      cards, "Today's forecast" Plotly charts
    - "map" page — interactive Plotly seat map, QR-scan card, and a
      seat-detail / reservation action panel
    - "profile" page — delegates to ``Account_page.render_account_page``
    - "settings" page — currently demo / debug controls for the
      lunch-break and re-check-in features
    - Support footer rendered at the bottom of every page

Everything that is NOT rendering — Supabase access, authentication,
seat reservation, the lunch-break state machine, ML occupancy
forecasting, countdown math, and study statistics — lives in
``seat_manager.py`` and is imported below. The companion modules
``Account_page.py``, ``Support_page.py``, ``interactive_map.py``, and
``qr_code.py`` are imported with a graceful try/except so the app
still boots even if one of them (or its dependencies) is missing.

High-level control flow
-----------------------
``main()``                               ── streamlit entry point
   ├── ``st.set_page_config(...)``
   ├── ``init_auth_state()``             ── seed session_state keys
   └── if logged in → ``main_app()``     ── post-login router
       else        → ``login_page()``    ── login / signup screen

``main_app()`` injects the CSS, draws the sidebar, then dispatches
to ``landing_page`` / ``map_page`` / ``profile_page`` / ``settings_page``
based on ``st.session_state["current_page"]``. After the page body,
the support footer is rendered and the JS countdown ticker is
injected at the very bottom of the DOM.
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import os
from datetime import datetime as dt, timezone

import streamlit as st
from streamlit_autorefresh import st_autorefresh

# Plotly is used by the landing page's "Today's forecast" charts.
import plotly.graph_objects as go

# ─────────────────────────────────────────────────────────────
# BACKEND  (seat_manager.py — all business logic)
# ─────────────────────────────────────────────────────────────
from seat_manager import (
    # Constants
    RESERVATION_MINUTES,
    RECHECK_HOURS,
    RECHECK_WINDOW_MINUTES,
    ZURICH_TZ,
    LUNCH_BREAK_START_HOUR,
    LUNCH_BREAK_END_HOUR,
    LUNCH_BREAK_MINUTES,
    FLOOR_META,
    SUPABASE_OK,
    # Time helpers
    _zurich_now,
    # Auth requests
    login_request,
    signup_request,
    # Seat management
    get_seats,
    get_user_status,
    reserve_seat,
    cancel_reservation,
    recheck_in_from_qr,
    release_current_seat,
    _recheck_window_state,
    # Lunch break
    _lunch_break_state,
    start_lunch_break,
    # Demo
    demo_set_seat_expiry,
    # Countdown helpers
    seconds_left,
    countdown,
    # Floor helpers
    _seat_belongs_to_floor,
    _compute_floor_stats,
    # ML / snapshots
    _ml_forecast_series,
    save_real_occupancy_snapshot,
    # Study stats
    get_user_study_stats,
    # QR resolution
    _resolve_scanned_code,
)


# ─────────────────────────────────────────────────────────────
# INTERACTIVE MAP  (from interactive_map.py)
# Graceful fallback: if the module or its deps are missing, the app
# still runs with the legacy static-image + button-grid view.
#
# This try/except pattern is used by every optional helper module
# (interactive_map, qr_code, Support_page, Account_page). The trick
# is that we set a *_AVAILABLE flag based on whether the import
# succeeded, and every call site checks the flag before using the
# function. That means a single missing file or unmet pip dep never
# crashes the app — it just degrades gracefully to a less rich UI.
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
        """Fallback for ``interactive_map.clear_seat_selection`` when the
        interactive_map module failed to import. Tries to remove
        ``?seat=…`` from the URL but never raises — logout / page
        changes that depend on this helper must always succeed.
        """
        try:
            if "seat" in st.query_params:
                del st.query_params["seat"]
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# QR CHECK-IN  (utilities from qr_code.py)
# qr_code.py is NOT modified — we just import its pure decoder
# helpers (decode_qr + extract_seat_code) and drive the UI flow
# here in streamlit_app.py, since the new "scan any seat" behaviour
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
# SUPPORT FOOTER  (rendered at the bottom of every page)
# Support_page.py is imported as-is — its render_support_page()
# call is invoked by _render_support_footer() below. Same graceful
# fallback pattern as the other optional modules: if the file isn't
# present, the support section is silently skipped.
# ─────────────────────────────────────────────────────────────
try:
    from Support_page import render_support_page
    SUPPORT_PAGE_AVAILABLE = True
except Exception:
    SUPPORT_PAGE_AVAILABLE = False
    render_support_page = None


# ─────────────────────────────────────────────────────────────
# ACCOUNT PAGE  (rendered as the Profile tab)
# Account_page.py is imported as-is — its render_account_page(token)
# call is invoked from profile_page() below. Same graceful fallback
# pattern as Support_page: if the module isn't importable (e.g. it
# wasn't deployed alongside this file), the profile tab falls back
# to a minimal placeholder so a missing module never breaks the app.
# ─────────────────────────────────────────────────────────────
try:
    from Account_page import render_account_page
    ACCOUNT_PAGE_AVAILABLE = True
except Exception:
    ACCOUNT_PAGE_AVAILABLE = False
    render_account_page = None


# ─────────────────────────────────────────────────────────────
# VISUAL SHELL
# ─────────────────────────────────────────────────────────────
def _inject_app_styles():
    """Inject the CSS half of app_styles.html (everything ABOVE the
    `<!-- SCRIPT -->` marker). Called once at the top of main_app()
    so styles are present before any element renders.
    """
    css_part, _ = _read_app_shell_parts()
    if css_part:
        st.markdown(css_part, unsafe_allow_html=True)

    # ── Add-on styles introduced *after* app_styles.html was authored ──
    # Kept here so we don't have to touch the shared CSS file just for
    # the lunch-break note added to the My Seat panel.
    st.markdown(
        """
        <style>
          /* Lunch-break inline note (My Seat card) */
          .chairie-lunch-note {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 14px;
            border-radius: var(--radius-md);
            font-size: 13px;
            line-height: 1.45;
            margin: 6px 0 10px 0;
            border: 1px solid transparent;
          }
          .chairie-lunch-note.open {
            background: var(--chairie-honey-soft);
            border-color: var(--chairie-honey);
            color: #5c4100;
          }
          .chairie-lunch-note.used {
            background: var(--chairie-green-soft);
            border-color: var(--chairie-green);
            color: #1f3d12;
          }
          .chairie-lunch-note.closed {
            background: var(--bg-subtle);
            border-color: var(--border-soft);
            color: var(--text-secondary);
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _inject_app_script():
    """Inject the JS half (everything BELOW the marker) via
    st.components.v1.html. Streamlit's markdown sanitizer strips
    <script>, so the JS rides in a zero-height iframe and reaches
    the parent DOM via window.parent.document.

    Called at the *end* of main_app(): the iframe wrapper has a small
    default margin even at height=0, so placing it at the bottom of
    the page keeps it from pushing the sticky top bar downward.
    """
    _, js_part = _read_app_shell_parts()
    if js_part and js_part.strip():
        st.components.v1.html(js_part, height=0)


def _read_app_shell_parts():
    """Read app_styles.html and split it at the `<!-- SCRIPT -->`
    marker. Returns (css_str, js_str). If the file is missing, returns
    ('', '') so callers can simply no-op.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "app_styles.html")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return "", ""
    if "<!-- SCRIPT -->" in content:
        css_part, js_part = content.split("<!-- SCRIPT -->", 1)
        return css_part, js_part
    return content, ""


# ─────────────────────────────────────────────────────────────
# PER-FLOOR MAP CONFIGURATION  (UI-only: image paths + map keys)
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


# ─────────────────────────────────────────────────────────────
# AUTH SESSION STATE  (UI-side login/logout bookkeeping)
# ─────────────────────────────────────────────────────────────
def init_auth_state():
    """Seed ``st.session_state`` with the keys the rest of the app expects.

    Streamlit reruns the entire script on every interaction, so we cannot
    rely on module-level variables to remember anything. Anything that
    must survive between reruns (the logged-in user, which page they are
    on, which seat they clicked, etc.) lives in ``st.session_state``.

    This function is called once from ``main()`` BEFORE any page renders.
    It only writes a key if it is not already present, so a real value
    set by ``login_user()`` etc. is never overwritten on subsequent reruns.

    Keys seeded
    -----------
    logged_in           : bool   — True once the user has authenticated.
    username            : str    — e-mail address shown in the top bar.
    token               : str    — Supabase access token used by the backend.
    selected_seat_id    : int    — id of the seat currently clicked on the map.
    auth_mode           : str    — "login" or "signup" — which form is shown.
    current_page        : str    — "home" | "map" | "profile" | "settings".
                                   Defaults to "home" so users land on the
                                   landing/marketing page right after login
                                   and click through to the map themselves.
    """
    defaults = {
        "logged_in": False,
        "username": None,
        "token": None,
        "selected_seat_id": None,
        "auth_mode": "login",
        "current_page": "home",
    }
    # Only write keys that are not yet set — never clobber existing values.
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def is_logged_in():
    """Return ``True`` if the current session has an authenticated user.

    Used as a router gate in ``main()`` to decide whether to draw the
    login page or the post-login app shell.
    """
    return st.session_state.get("logged_in", False)


def login_user(username, token):
    """Mark the session as authenticated.

    Called by ``login_page()`` after ``login_request()`` (from
    ``seat_manager.py``) returns a successful result. The ``token`` is
    the Supabase access token; every backend call later in the session
    will pass it through so Supabase can identify the user.
    """
    st.session_state["logged_in"] = True
    st.session_state["username"] = username
    st.session_state["token"] = token


def logout_user():
    """Tear down all session state tied to the current user.

    Called by the red "Logout" button in ``_render_top_bar()``. Resets
    auth + selected seat + current page, drops any lunch-break or
    demo state left over from this session, and clears the ``?seat=…``
    query-string param so a fresh login starts on a clean URL.
    """
    st.session_state["logged_in"] = False
    st.session_state["username"] = None
    st.session_state["token"] = None
    st.session_state["selected_seat_id"] = None
    # Reset the visible page so the next login starts on the landing page.
    st.session_state["current_page"] = "home"
    # Drop any in-progress lunch-break state. We keep these out of the
    # `defaults` dict in init_auth_state() because they're optional /
    # transient — using .pop ensures a stale value from a previous
    # session doesn't leak into the next user on the same browser.
    st.session_state.pop("lunch_break_active_until", None)
    st.session_state.pop("lunch_break_claimed_date", None)
    # DEMO BLOCK: also clear any demo override left over on this browser.
    st.session_state.pop("_demo_lunch_window_force", None)
    # Drop ?seat=… from the URL so a fresh session starts clean.
    try:
        clear_seat_selection()
    except Exception:
        # clear_seat_selection() reads st.query_params which can raise
        # in some Streamlit versions / contexts — never crash logout
        # over a URL-cleanup failure.
        pass


def require_login():
    """Guard a page from anonymous access.

    Called at the top of ``main_app()``. If the user is not logged in we
    show a warning and ``st.stop()`` to halt the rest of the script —
    ``st.stop()`` raises an internal exception that Streamlit catches,
    so no further widgets render on this run.
    """
    if not is_logged_in():
        st.warning("Please log in first.")
        st.stop()


# ─────────────────────────────────────────────────────────────
# DISPLAY HELPERS  (UI-only formatting on top of countdown math)
# ─────────────────────────────────────────────────────────────
def live_countdown_html(iso_value):
    """HTML <span> that the JS ticker in app_styles.html refreshes every
    second on the client side — never freezes between Streamlit reruns.

    The span carries the ISO target on a data-attribute; the JS reads
    every `.chairie-countdown[data-target]` and updates its text.
    The initial text content avoids a "--:--" flash before JS kicks in.
    """
    if not iso_value:
        return '<span class="chairie-countdown">--:--</span>'
    return (
        f'<span class="chairie-countdown" data-target="{iso_value}">'
        f'{countdown(iso_value)}</span>'
    )


def seat_status_color(status):
    """Map a seat status string to a CSS hex color.

    Used by the LEGACY fallback view (static image + button grid) in
    ``map_page()`` — the interactive Plotly map uses its own colour
    palette defined in ``interactive_map.STATUS_COLORS``. Returns a
    grey fallback for any unknown status (e.g. ``"maintenance"``).
    """
    return {"free": "#1db954", "reserved": "#ff9800", "occupied": "#e53935"}.get(status, "#9ca3af")


# ─────────────────────────────────────────────────────────────
# AUTH PAGE  (login + signup — styled via inline CSS)
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
# MY SEAT PANEL  +  LUNCH BREAK BLOCK
# ─────────────────────────────────────────────────────────────
def _render_lunch_break_block(seat, token, key_prefix=""):
    """Render the lunch-break sub-section inside the My Seat card.

    Shows different states depending on whether:
      - the user is currently on a break (live countdown)
      - the user already claimed today's break (muted note)
      - the claim window is open right now (action button)
      - we're outside 11:00–14:00 (muted note)

    Only meaningful when the user is OCCUPYING (checked-in) the seat —
    reservations expire in 10 min so a break there makes no sense.
    `key_prefix` keeps Streamlit's button keys unique if this function
    is somehow rendered twice in a single rerun (e.g. two seat cards).
    """
    if not seat or seat.get("status") != "occupied" or not seat.get("occupied_by_me"):
        return

    state = _lunch_break_state()

    # ── Active break: live countdown + visual highlight ─────────────
    if state["active"]:
        st.markdown(
            f'<div class="chairie-alert chairie-alert-warning">'
            f'🍽️ <strong>On lunch break</strong> — your seat is held. '
            f'{live_countdown_html(state["ends_at_iso"])} left'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    # ── Already used today's break ──────────────────────────────────
    if state["claimed_today"]:
        st.markdown(
            '<div class="chairie-lunch-note used">'
            '✓ Lunch break already used today. Comes back tomorrow.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # ── Inside the claim window: actionable button ──────────────────
    if state["window_open"]:
        st.markdown(
            '<div class="chairie-lunch-note open">'
            f'🍽️ <strong>Lunch break available</strong> — claim a free '
            f'{LUNCH_BREAK_MINUTES}-min hold on your seat.'
            '</div>',
            unsafe_allow_html=True,
        )
        if st.button(
            "Take 1-hour lunch break",
            key=f"{key_prefix}lunch_break_{seat['id']}",
            use_container_width=True,
        ):
            result = start_lunch_break(token, seat["id"])
            if result.get("success"):
                st.success(result["message"])
                st.rerun()
            else:
                st.error(result.get("message", "Could not start lunch break."))
        return

    # ── Outside the window: muted info ──────────────────────────────
    st.markdown(
        f'<div class="chairie-lunch-note closed">'
        f'🍽️ Lunch break is available daily between '
        f'<strong>{LUNCH_BREAK_START_HOUR:02d}:00</strong> and '
        f'<strong>{LUNCH_BREAK_END_HOUR:02d}:00</strong>.'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_my_seat_panel(seat, token, key_prefix=""):
    """Render the pinned "Your seat" card shown automatically whenever
    the user has a reserved or occupied seat.

    Lives at the top of the Map page and just below the hero on the
    Home page. Re-uses the same `chairie-seat-detail` card styling
    as the click-driven detail panel so the two never look out of
    sync, but adds:
      - a contextual eyebrow ("Your seat · checked in" / "reserved")
      - the lunch-break sub-block (when checked-in)
      - the right primary action (Release / Cancel)
    `key_prefix` keeps Streamlit's widget keys unique when this panel
    appears on more than one page during a single rerun.
    """
    if not seat:
        return

    status = seat.get("status")
    is_occupied = (status == "occupied" and seat.get("occupied_by_me"))
    is_reserved = (status == "reserved" and seat.get("reserved_by_me"))

    if not (is_occupied or is_reserved):
        return

    eyebrow = "Your seat · checked in" if is_occupied else "Your seat · reserved"

    st.markdown(
        f'<div class="chairie-eyebrow">{eyebrow}</div>',
        unsafe_allow_html=True,
    )

    # Build the live-countdown row. For OCCUPIED seats it shows the
    # re-check-in deadline (occupied_until). For RESERVED it shows the
    # 10-min check-in window (reserved_until). When the user is on a
    # lunch break, occupied_until has been bumped forward by 60 min,
    # which would make the seat-level countdown duplicate the lunch
    # break block's countdown — so we suppress it in that case and
    # let the lunch break block carry the timer.
    countdown_row_html = ""
    on_lunch_break = is_occupied and _lunch_break_state()["active"]
    if not on_lunch_break:
        if is_occupied and seat.get("occupied_until"):
            countdown_row_html = (
                '<div class="chairie-seat-row">'
                f'<span class="chairie-seat-label">Re-check in within</span>'
                f'<span class="chairie-seat-value">'
                f'{live_countdown_html(seat["occupied_until"])}'
                f'</span>'
                '</div>'
            )
        elif is_reserved and seat.get("reserved_until"):
            countdown_row_html = (
                '<div class="chairie-seat-row">'
                f'<span class="chairie-seat-label">Check in within</span>'
                f'<span class="chairie-seat-value">'
                f'{live_countdown_html(seat["reserved_until"])}'
                f'</span>'
                '</div>'
            )

    status_class = (status or "maintenance").lower()
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
            <span class="chairie-status-pill {status_class}">{status}</span>
          </div>
          {countdown_row_html}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Lunch-break sub-section (only renders content when checked-in)
    _render_lunch_break_block(seat, token, key_prefix=key_prefix)

    # Re-check-in hint — shown only when the user is checked in, NOT
    # currently on a lunch break (the break already extends the seat),
    # and we're inside the last RECHECK_WINDOW_MINUTES of occupied_until.
    # Surfacing this in the My Seat panel pairs with the QR scan card
    # below: the user sees "scan to extend" right next to the camera
    # button, so the action is obvious.
    if is_occupied and not _lunch_break_state()["active"]:
        rs = _recheck_window_state(seat)
        if rs["window_open"]:
            st.markdown(
                f'<div class="chairie-lunch-note open">'
                f'⏰ <strong>Re-check in available</strong> — scan your seat\'s '
                f'QR code to extend for another {RECHECK_HOURS} hour(s).'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Primary action: release a checked-in seat, or cancel a reservation
    if is_occupied:
        if st.button(
            "Release seat",
            key=f"{key_prefix}myseat_release_{seat['id']}",
            type="secondary",
            use_container_width=True,
        ):
            result = release_current_seat(token)
            if result.get("success"):
                st.success(result["message"])
                # Clear lunch-break state too — they no longer have a seat.
                st.session_state.pop("lunch_break_active_until", None)
                st.rerun()
            else:
                st.error(result.get("message", "Could not release seat."))
    elif is_reserved:
        if st.button(
            "Cancel reservation",
            key=f"{key_prefix}myseat_cancel_{seat['id']}",
            type="secondary",
            use_container_width=True,
        ):
            result = cancel_reservation(token)
            if result.get("success"):
                st.success(result["message"])
                st.rerun()
            else:
                st.error(result.get("message", "Could not cancel reservation."))


def _render_seat_detail_panel(seats, token):
    """Render the seat detail panel (info + action buttons).

    Called from map_page() above the interactive map. Reads
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
            st.markdown(
                f'<div class="chairie-alert chairie-alert-warning">'
                f'This seat is reserved by you. Time left to check in: '
                f'{live_countdown_html(seat["reserved_until"])}'
                f'</div>',
                unsafe_allow_html=True,
            )
            if st.button(
                "Cancel Reservation",
                key=f"cancel_{seat['id']}",
                type="secondary",
                use_container_width=True,
            ):
                result = cancel_reservation(token)
                if result["success"]:
                    st.success(result["message"])
                    st.rerun()
                else:
                    st.error(result["message"])
        else:
            st.error("Someone else reserved this seat.")

    elif seat["status"] == "occupied":
        if seat["occupied_by_me"]:
            st.markdown(
                f'<div class="chairie-alert chairie-alert-success">'
                f'You are currently occupying this seat. Re-check '
                f'countdown: '
                f'{live_countdown_html(seat["occupied_until"])}'
                f'</div>',
                unsafe_allow_html=True,
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
#   4) Per-floor "Today's forecast" Plotly bar chart — predicted
#      occupancy from the ML model trained on historical snapshots.

def _render_forecast_chart(floor_choice, floor_stats):
    """Render the "Today's forecast" bar chart for a single floor.

    The bar for the current hour is recoloured to match the floor's
    current availability — green when there are free seats, honey
    when it's getting busy, red when it's full. The other bars are
    a calm dark green so the eye is pulled to "right now".
    """
    series = _ml_forecast_series(floor_choice)

    if series is None:
        st.info("Not enough historical data yet for this floor.")
        return
    hours      = [h for h, _ in series]
    occupancy  = [pct for _, pct in series]

    # Find "now" — clamp to the chart range so we always highlight
    # one bar even outside operating hours. Zurich-local hour, since
    # this is for display in a Swiss library.
    now_hour = _zurich_now().hour
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
        title_text="",
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
# QR SCAN CARD  (lives on the map page)
# ─────────────────────────────────────────────────────────────
# The camera/UI flow lives here; qr_code.py is only used for its pure
# decoder helpers (decode_qr + extract_seat_code), as written. The
# decision logic (what to do for a scanned code) lives in
# `_resolve_scanned_code` over in seat_manager.py.

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
    All numbers come from `get_seats(token)`; forecast bars use the
    ML model in seat_manager._ml_forecast_series().
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

    # ── "Your seat" pinned card (only if user has a seat) ───────────────
    # Sits just below the welcoming hero so logged-in users with an
    # active seat see their status immediately on landing — no need
    # to navigate to the map first. Includes the daily lunch-break
    # control when the 11–14h window is open.
    my_seat = next(
        (s for s in seats if s.get("occupied_by_me") or s.get("reserved_by_me")),
        None,
    )
    if my_seat:
        _render_my_seat_panel(my_seat, token, key_prefix="home_")

    # ── KPI strip ───────────────────────────────────────────────────────
    total_free = sum(1 for s in seats if s.get("status") == "free")
    floors_n   = len(FLOOR_META)
    now_str    = _zurich_now().strftime("%H:%M")

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
# PROFILE PAGE  (delegates to Account_page.render_account_page)
# ─────────────────────────────────────────────────────────────
# The actual account UI (avatar, profile form, study stats) lives in
# Account_page.py — same modular pattern Support_page uses. We just
# render the shared top bar here, then hand off to that module. If
# Account_page.py wasn't deployed alongside this file, we fall back
# to a minimal placeholder so the tab stays navigable.
def profile_page(token):
    """Render the Profile tab.

    All actual profile UI (avatar, full-name / gender form, study-stats
    metrics, current-seat readout) lives in ``Account_page.py`` —
    same modular pattern ``Support_page`` uses. This wrapper just
    paints the shared top bar and hands off to that module.

    If ``Account_page.py`` was not deployed alongside this file (the
    import at the top of the module raised), ``ACCOUNT_PAGE_AVAILABLE``
    is ``False`` and we render a minimal placeholder card so the tab
    stays navigable.
    """
    _render_top_bar("Profile")

    if not ACCOUNT_PAGE_AVAILABLE or render_account_page is None:
        st.markdown(
            """
            <div class="chairie-placeholder">
              <div class="chairie-placeholder-icon">👤</div>
              <div class="chairie-placeholder-title">Account</div>
              <div class="chairie-placeholder-sub">
                The Account module (Account_page.py) is not available.
                Place it next to this file to enable profile editing
                and study statistics.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    render_account_page(token)


# ─────────────────────────────────────────────────────────────
# SETTINGS PAGE  (currently DEMO CONTROLS — temporary)
# ─────────────────────────────────────────────────────────────
# This whole function body is part of the DEMO BLOCK. When demos
# are no longer needed, restore the placeholder shown at the very
# bottom (commented out) and delete everything above it inside
# settings_page(). Grep for "DEMO BLOCK" to find related code.
def settings_page(token):
    """DEMO controls — lets anyone trigger the lunch-break and
    re-check-in flows without waiting for real time windows.
    To be replaced with real settings (notifications, appearance,
    account) before launch.
    """
    _render_top_bar("Settings")

    # ── Top banner: makes the temporary status obvious ──────────────
    st.markdown(
        '<div class="chairie-alert chairie-alert-warning">'
        '🛠️ <strong>Demo controls</strong> — these settings exist so '
        'we can demonstrate the lunch-break and re-check-in features '
        'on demand. They\'re visible to every logged-in user and will '
        'be removed before launch.'
        '</div>',
        unsafe_allow_html=True,
    )

    # ─────────────────────────────────────────────────────────────
    # SECTION 1 · Lunch break window
    # ─────────────────────────────────────────────────────────────
    st.markdown(
        '<div class="chairie-section-title">Lunch break window '
        '<span class="hint">11:00–14:00 Zurich</span></div>',
        unsafe_allow_html=True,
    )

    # Snapshot of all the relevant state for the readout table.
    real_now         = dt.now(ZURICH_TZ)
    real_window_open = (LUNCH_BREAK_START_HOUR <= real_now.hour < LUNCH_BREAK_END_HOUR)
    forced           = st.session_state.get("_demo_lunch_window_force")
    state            = _lunch_break_state()

    if forced is None:
        override_label = "None (real time)"
    elif forced:
        override_label = "FORCE OPEN"
    else:
        override_label = "FORCE CLOSED"

    effective_pill = "free" if state["window_open"] else "occupied"
    effective_text = "OPEN" if state["window_open"] else "CLOSED"

    st.markdown(
        f"""
        <div class="chairie-seat-detail">
          <div class="chairie-seat-row">
            <span class="chairie-seat-label">Current Zurich time</span>
            <span class="chairie-seat-value">{real_now.strftime("%H:%M:%S")}</span>
          </div>
          <div class="chairie-seat-row">
            <span class="chairie-seat-label">Real window status</span>
            <span class="chairie-seat-value">
              {"OPEN" if real_window_open else "CLOSED"} (based on wall clock)
            </span>
          </div>
          <div class="chairie-seat-row">
            <span class="chairie-seat-label">Override</span>
            <span class="chairie-seat-value">{override_label}</span>
          </div>
          <div class="chairie-seat-row">
            <span class="chairie-seat-label">Effective state</span>
            <span class="chairie-status-pill {effective_pill}">{effective_text}</span>
          </div>
          <div class="chairie-seat-row">
            <span class="chairie-seat-label">Claimed today?</span>
            <span class="chairie-seat-value">
              {"Yes" if state["claimed_today"] else "No"}
            </span>
          </div>
          <div class="chairie-seat-row">
            <span class="chairie-seat-label">Break currently active?</span>
            <span class="chairie-seat-value">
              {"Yes" if state["active"] else "No"}
            </span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Override controls
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Force window OPEN", key="demo_lb_force_open",
                     use_container_width=True):
            st.session_state["_demo_lunch_window_force"] = True
            st.rerun()
    with c2:
        if st.button("Force window CLOSED", key="demo_lb_force_closed",
                     use_container_width=True):
            st.session_state["_demo_lunch_window_force"] = False
            st.rerun()
    with c3:
        if st.button("Use real time", key="demo_lb_use_real",
                     use_container_width=True, type="secondary"):
            st.session_state.pop("_demo_lunch_window_force", None)
            st.rerun()

    # Reset claim/active state so the same user can re-demo
    if st.button("Reset today's break (clear claim + active state)",
                 key="demo_lb_reset", use_container_width=True,
                 type="secondary"):
        st.session_state.pop("lunch_break_active_until", None)
        st.session_state.pop("lunch_break_claimed_date", None)
        st.success("Lunch break state cleared — you can claim again.")
        st.rerun()

    # ─────────────────────────────────────────────────────────────
    # SECTION 2 · Seat expiry (re-check-in window)
    # ─────────────────────────────────────────────────────────────
    st.markdown(
        '<div class="chairie-section-title">Seat expiry '
        f'<span class="hint">re-check window = last {RECHECK_WINDOW_MINUTES} min'
        f'</span></div>',
        unsafe_allow_html=True,
    )

    status_result = get_user_status(token)
    seat = None
    if status_result.get("success"):
        seat = status_result.get("checked_in_seat")

    if not seat:
        st.markdown(
            '<div class="chairie-empty-detail">'
            'You need to be <strong>checked in</strong> to a seat to use '
            'these controls. Head to the Map, reserve a seat, then '
            'check in via QR.'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        secs = seconds_left(seat["occupied_until"]) if seat.get("occupied_until") else 0
        window_open = (0 < secs <= RECHECK_WINDOW_MINUTES * 60)
        window_pill = "free" if window_open else "maintenance"
        window_text = "OPEN" if window_open else "Not yet"

        st.markdown(
            f"""
            <div class="chairie-seat-detail">
              <div class="chairie-seat-row">
                <span class="chairie-seat-label">Your seat</span>
                <span class="chairie-seat-value">{seat['code']}</span>
              </div>
              <div class="chairie-seat-row">
                <span class="chairie-seat-label">Currently expires in</span>
                <span class="chairie-seat-value">
                  {live_countdown_html(seat["occupied_until"])}
                </span>
              </div>
              <div class="chairie-seat-row">
                <span class="chairie-seat-label">Re-check window</span>
                <span class="chairie-status-pill {window_pill}">{window_text}</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("Expire in 20 min (opens re-check)",
                         key="demo_seat_20min", use_container_width=True):
                result = demo_set_seat_expiry(token, 20)
                if result.get("success"):
                    st.success(result["message"])
                    st.rerun()
                else:
                    st.error(result["message"])
        with b2:
            if st.button("Expire in 2 min (near expiry)",
                         key="demo_seat_2min", use_container_width=True):
                result = demo_set_seat_expiry(token, 2)
                if result.get("success"):
                    st.success(result["message"])
                    st.rerun()
                else:
                    st.error(result["message"])
        with b3:
            if st.button(f"Reset to fresh {RECHECK_HOURS}-hour timer",
                         key="demo_seat_reset", use_container_width=True,
                         type="secondary"):
                result = demo_set_seat_expiry(token, RECHECK_HOURS * 60)
                if result.get("success"):
                    st.success(result["message"])
                    st.rerun()
                else:
                    st.error(result["message"])

    # ─────────────────────────────────────────────────────────────
    # Future real settings would live below this divider
    # ─────────────────────────────────────────────────────────────
    st.markdown(
        '<div class="chairie-section-title">Coming soon</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="chairie-placeholder">
          <div class="chairie-placeholder-icon">⚙</div>
          <div class="chairie-placeholder-title">Real settings</div>
          <div class="chairie-placeholder-sub">
            Notification preferences, appearance, and account controls
            will live here once we ship. The demo controls above will
            be removed at that point.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ── To restore the original Settings placeholder later, replace the
#    entire settings_page body above with:
#
#        _render_top_bar("Settings")
#        st.markdown(
#            '<div class="chairie-placeholder">'
#            '  <div class="chairie-placeholder-icon">⚙</div>'
#            '  <div class="chairie-placeholder-title">Settings</div>'
#            '  <div class="chairie-placeholder-sub">'
#            '    Notification preferences, appearance and account '
#            '    controls will live here. Coming soon.'
#            '  </div>'
#            '</div>',
#            unsafe_allow_html=True,
#        )


# ─────────────────────────────────────────────────────────────
# MAP PAGE  (interactive seat-reservation map)
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

        # If the user is RESERVED but hasn't checked in yet, keep the
        # legacy honey-coloured countdown alert — they need a clear
        # nudge to scan within 10 min, and the action set in My Seat
        # is just "cancel" which doesn't carry that urgency.
        if reserved_seat and not checked_in_seat:
            st.markdown(
                f'<div class="chairie-alert chairie-alert-warning">'
                f'You reserved seat <strong>{reserved_seat["code"]}</strong>. '
                f'Scan the QR code within {RESERVATION_MINUTES} min. ⏱ '
                f'{live_countdown_html(reserved_seat["reserved_until"])} '
                f'remaining'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── "Your seat" pinned card (auto-shown when user has a seat) ────────
    # Sits at the top of the map page, between the status alert (if any)
    # and the QR card. Shows the seat detail plus the daily lunch-break
    # control without requiring the user to click on the map first.
    my_seat_on_map = checked_in_seat or reserved_seat
    if my_seat_on_map:
        _render_my_seat_panel(my_seat_on_map, token, key_prefix="map_")

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
        # The layout JSON gives us each seat's pixel position on the floor
        # plan image (x, y, size, id), but it has NO live status — that
        # lives in Supabase. We merge the two by id so the dot at (x, y)
        # gets coloured by the seat's current status.
        #
        # After renumbering Supabase so its `id` values match the layout's
        # (Ground 1..189, Floor 1 190..496), this straight id lookup is
        # all that's needed. We index against ALL Supabase rows (not just
        # this floor's): each floor's JSON uses a non-overlapping id
        # range, so a Supabase row can only match the floor it actually
        # belongs to.
        supabase_by_id = {int(s["id"]): s for s in seats}

        merged_seats = []
        for layout_seat in layout["seats"]:
            try:
                sid = int(layout_seat["id"])
            except (KeyError, TypeError, ValueError):
                # Malformed/missing id in the layout JSON → skip this dot.
                continue
            live = supabase_by_id.get(sid)
            merged_seats.append({
                "id":     sid,
                "x":      int(layout_seat.get("x", 0)),
                "y":      int(layout_seat.get("y", 0)),
                "size":   int(layout_seat.get("size", 13)),
                # Live status if Supabase knows this seat, else gray "maintenance".
                # A seat showing as "maintenance" on the map typically means the
                # row was deleted from Supabase but its layout entry still exists.
                "status": (live or {}).get("status", "maintenance"),
            })

        # ── Click handling ───────────────────────────────────────────────
        # Plotly stores the most recent selection event in st.session_state
        # under the chart's key. We read it BEFORE rendering anything so the
        # detail panel above the map can use the up-to-date selection on
        # the same rerun (no extra rerun needed). Per-floor key means each
        # floor's selection persists independently.
        #
        # Event shape from Streamlit's plotly_chart selection_mode="points":
        #   { "selection": { "points": [{"customdata": [seat_id], ...}], ... }, ... }
        # We stuff the seat id into customdata in interactive_map.py so we
        # don't have to reverse-lookup by (x, y) here.
        map_key = floor_cfg["map_key"]
        chart_event = st.session_state.get(map_key)
        if isinstance(chart_event, dict):
            sel = chart_event.get("selection")
            points = (sel.get("points") if isinstance(sel, dict) else None) or []
            if points:
                cd = points[0].get("customdata")
                if cd is not None:
                    try:
                        # customdata can come back as a 1-element list OR a
                        # bare value depending on Plotly version — handle both.
                        clicked_id = int(cd[0] if isinstance(cd, (list, tuple)) else cd)
                        # Only update state if the user actually clicked a
                        # different seat — avoids an unnecessary rerun
                        # cascade when the same event replays.
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


def _render_support_footer():
    """Render the Support & Contact section at the bottom of every page.

    Calls render_support_page() from Support_page.py — that file is
    used exactly as uploaded (no modifications). A thin divider is
    drawn first so the support block is visually separated from the
    page's main content.

    Silent no-op if the Support_page module isn't importable (e.g. it
    wasn't deployed alongside this file), so a missing module never
    breaks the rest of the app.
    """
    if not SUPPORT_PAGE_AVAILABLE:
        return

    # Thin divider — uses the same border color the rest of the app
    # cards use, so it blends with the design system.
    st.markdown(
        '<hr class="chairie-support-divider" />',
        unsafe_allow_html=True,
    )
    render_support_page()


def main_app():
    """Render the post-login app shell and dispatch to the current page.

    Called from ``main()`` when ``is_logged_in()`` returns True. Order
    of operations is important and intentional:

      1. ``require_login()``       — block the page if somehow not auth'd.
      2. ``st_autorefresh(60_000)`` — pick up server-side seat changes
                                      every minute (countdowns are NOT
                                      driven by this — they tick client-
                                      side every second via JS).
      3. ``_inject_app_styles()``  — CSS must land BEFORE any element
                                      renders, or the page flashes
                                      unstyled.
      4. ``_render_sidebar()``     — the left nav rail (visible on every
                                      page).
      5. occupancy snapshot        — every 30 min we persist a row in
                                      ``occupancy_snapshots`` so the ML
                                      forecast model has training data.
      6. dispatch                  — look up the current page key in
                                      ``PAGE_ROUTES`` and call its
                                      renderer. Each renderer draws
                                      its own top bar.
      7. support footer            — Support_page section at the very
                                      bottom of every page.
      8. ``_inject_app_script()``  — JS ticker for the live countdowns.
                                      Injected LAST so its zero-height
                                      iframe wrapper sits below the
                                      visible content rather than
                                      pushing the sticky top bar down.
    """
    require_login()

    # Auto-refresh every 60 seconds — only purpose now is to pick up
    # seat-status changes that happen on the server (someone else
    # reserved/released a seat). Countdowns no longer rely on this
    # refresh: they're driven by a 1-second JS interval in the parent
    # DOM, so the user doesn't see the page "freeze" on every tick.
    st_autorefresh(interval=60000, key="seat_refresh")

    # Inject CSS FIRST so styles are applied before any element paints.
    _inject_app_styles()

    # The left-hand navigation rail is always visible on every page.
    _render_sidebar()

    token = st.session_state["token"]

    # ── Persist an occupancy snapshot every 30 minutes ─────────────
    # The ML forecast needs historical data points. We record one row
    # per (floor, timestamp) into Supabase at most every 1800 s. Using
    # session_state to throttle means each browser tab only writes at
    # most twice an hour — many concurrent users still produce one
    # row per interval, which is plenty.
    last_snapshot = st.session_state.get("last_snapshot_time")
    now = dt.now(timezone.utc)
    if not last_snapshot or (now - last_snapshot).total_seconds() > 1800:
        save_real_occupancy_snapshot()
        st.session_state["last_snapshot_time"] = now

    # Dispatch to the right page based on session state.
    current      = st.session_state.get("current_page", "home")
    page_fn_name = PAGE_ROUTES.get(current, "landing_page")
    page_fn      = globals().get(page_fn_name)
    if not callable(page_fn):
        # Defensive fallback — should never trip unless PAGE_ROUTES
        # is misconfigured. Land the user back on home.
        st.session_state["current_page"] = "home"
        page_fn = landing_page

    page_fn(token)

    # Support & contact section at the bottom of EVERY page (Home, Map,
    # Profile, Settings). Uses Support_page.render_support_page() — that
    # file is imported as-is and untouched.
    _render_support_footer()

    # Inject the countdown JS LAST. It rides in a zero-height iframe
    # component; placing it at the bottom of the page means any leftover
    # wrapper spacing falls below the visible content instead of above
    # the sticky top bar.
    _inject_app_script()


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
def main():
    """Streamlit entry point — called by ``streamlit run streamlit_app.py``.

    Configures the page (title, wide layout, expanded sidebar), seeds
    the session-state defaults, checks that Supabase secrets were
    configured, and finally hands off to either ``main_app()`` (post-
    login app shell) or ``login_page()`` based on the auth flag.
    """
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
        # SUPABASE_OK is False when neither st.secrets nor .env supplied
        # SUPABASE_URL + SUPABASE_KEY. We still render whatever follows
        # (login page or main app) so the developer can SEE the error
        # in the running app rather than a blank screen.
        st.error(
            "⚠️ Supabase is not configured. "
            "Add SUPABASE_URL and SUPABASE_KEY to your Streamlit secrets or .env file."
        )

    if is_logged_in():
        main_app()
    else:
        login_page()


if __name__ == "__main__":
    # Streamlit imports this file as a module and runs the script
    # top-to-bottom, so this guard is only really hit if someone runs
    # `python streamlit_app.py` directly — in which case main() still
    # works (it just won't render anything outside a Streamlit server).
    main()
