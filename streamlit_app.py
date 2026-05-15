"""
streamlit_app.py

This is the main file for our Chairie app. We run it with: streamlit run streamlit_app.py

Chairie helps students at the HSG library find and book a free seat.
This file handles all the UI and pages the user sees:

    - Login and signup page
    - Sidebar menu and the top bar (shown on every page after login)
    - Home page with the library stats and a forecast chart
    - Map page where you click a seat to reserve it and scan its QR code
    - Profile page (the code is in Account_page.py)
    - Settings page (currently simple placeholder)
    - Support section at the bottom of every page

The functions that actually link back to the database (in our case, Supabase), do the 
machine learning, and calculate the countdowns that are in seat_manager.py.
We just import them here and call them.

============================================================
ABOUT AI USE (Directory of Aids at bottom of the program) 
============================================================
While building this app we used AIs to help us and guide us 
mainly Claude as a tutor. Most of the time we asked Claude things
like "which library should we use for X" or "why is our QR
scanner not picking up a second photo", and Claude pointed us
in the right direction. We then mostly wrote the code ourselves.

There are a few places where the specific code solution is something
we would not have figured out on our own. Those places have a
comment that starts with "# AI HELP:" and a short note about
why we used Claude.

Other note: we did not keep careful notes while coding, so the
line between "we wrote it with AI's advice" and "AI basically gave 
us this" is not always clear. 

The AI HELP tags are our best memory of where Claude's help was the most specific.
============================================================
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import os
from datetime import datetime as dt, timezone

import streamlit as st
from streamlit_autorefresh import st_autorefresh

# Plotly is for the forecast bar chart on the home page.
import plotly.graph_objects as go

"""
─────────────────────────────────────────────────────────────
 IMPORTS FROM seat_manager.py
─────────────────────────────────────────────────────────────
seat_manager.py is the file with all the database storage and logic
"""

from seat_manager import (
    # Constants (numbers we use in many places)
    RESERVATION_MINUTES,
    RECHECK_HOURS,
    RECHECK_WINDOW_MINUTES,
    ZURICH_TZ,
    LUNCH_BREAK_START_HOUR,
    LUNCH_BREAK_END_HOUR,
    LUNCH_BREAK_MINUTES,
    FLOOR_META,
    SUPABASE_OK,
    # Time helper (gives us the current time in Zurich)
    _zurich_now,
    # Login and signup
    login_request,
    signup_request,
    # Seat actions
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
    # Countdown helpers (how many seconds left)
    seconds_left,
    countdown,
    # Floor helpers
    _seat_belongs_to_floor,
    _compute_floor_stats,
    # Machine learning for the forecast
    _ml_forecast_series,
    save_real_occupancy_snapshot,
    # Study time statistics for the profile page
    get_user_study_stats,
    # Decides what to do after the user scans a QR code
    _resolve_scanned_code,
)

"""
 ─────────────────────────────────────────────────────────────
 IMPORTS FROM OUR OTHER FILES
 ─────────────────────────────────────────────────────────────
"""

# Makes the clickable seat map
from interactive_map import (
    load_map_data as load_layout_data,
    render_interactive_map,
    clear_seat_selection,
)

# Reads QR codes from photos
from qr_code import decode_qr, extract_seat_code

# The support section at the bottom of every page
from Support_page import render_support_page

# The profile page
from Account_page import render_account_page

"""
─────────────────────────────────────────────────────────────
 STYLES AND JAVASCRIPT
─────────────────────────────────────────────────────────────
 The CSS and JavaScript for the app are saved in app_styles html file.
 
 We split that file in two parts at the "<!-- SCRIPT -->" line:
   - the top part is CSS
   - the bottom part is JavaScript (for the live countdowns)

 AI HELP: Claude told us that st.markdown does not allow script tags, so we have to use 
 st.components.v1.html for the JavaScript. It also guided us to put the JS at the bottom of 
 the page so it doesn't push the top bar down. Without Claude we probably would have used 
 st_autorefresh every second, which made the page blink.
 
 """

def _inject_app_styles():
    """Add the CSS to the page. We call this at the top of every page so the styles are 
    ready before anything is drawn."""
    css_part, _ = _read_app_shell_parts()
    if css_part:
        st.markdown(css_part, unsafe_allow_html=True)

    # Extra CSS for the lunch-break note. We added this later, so
    # instead of changing app_styles.html we just put it here.
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
    """Add the JavaScript to the page. We call this at the end of every page (not at the top) 
    so the page does not get pushed down.

    The JavaScript makes the countdown numbers update every second without reloading the whole page. 
    It is a solution suggested by Claude that we decided to implement"""
    _, js_part = _read_app_shell_parts()
    if js_part and js_part.strip():
        st.components.v1.html(js_part, height=0)


def _read_app_shell_parts():
    """Read app_styles html file and split it into CSS and JavaScript. Returns two strings: css, js. 
    If the file is missing, then it will returns two empty strings."""
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

"""
─────────────────────────────────────────────────────────────
CONFIGURATION FOR EACH FLOOR
─────────────────────────────────────────────────────────────
For each floor we save:

    - json_path:          the file with the seat positions
                         (None means we use the default file)                            
    - image_filename:     the floor plan picture
    - layout_canvas_size: the size (width, height) of the picture we used when we made the JSON file. 
                          We need this so the dots match the chairs.
    - show_diagnostics:   we set this to True only when we are placing the dots, normally it is False.
    - map_key:            a unique name for each floor's map. Streamlit needs this so the two floors
                          don't get mixed up.
"""

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

"""
 ─────────────────────────────────────────────────────────────
 LOGIN STATUS (saved in session state)
 ─────────────────────────────────────────────────────────────
 Streamlit runs the whole script again every time the user clicks something. 
 So normal variables forget their values. We use st.session_state to remember things between clicks.
"""

def init_auth_state():
    """Create the keys in session_state that we use everywhere.
    We only set a key if it is not already there, so we don't
    overwrite the values when the user clicks a button."""
    defaults = {
        "logged_in": False,         # True after login
        "username": None,           # the email address
        "token": None,              # token from Supabase
        "selected_seat_id": None,   # which seat the user clicked
        "auth_mode": "login",       # "login" or "signup"
        "current_page": "home",     # which page is open right now
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def is_logged_in():
    """Returns True if the user is logged in, otherwise False."""
    return st.session_state.get("logged_in", False)


def login_user(username, token):
    """Save the username and token in session_state so the rest of
    the app knows the user is logged in. We call this after the
    backend confirms that the login worked."""
    st.session_state["logged_in"] = True
    st.session_state["username"] = username
    st.session_state["token"] = token


def logout_user():
    """Log the user out and reset everything for the next user."""
    st.session_state["logged_in"] = False
    st.session_state["username"] = None
    st.session_state["token"] = None
    st.session_state["selected_seat_id"] = None
    # Go back to the home page for next use.
    st.session_state["current_page"] = "home"
    # Also clear the lunch break info from this session so it does not show up for the next user on the same browser.
    st.session_state.pop("lunch_break_active_until", None)
    st.session_state.pop("lunch_break_claimed_date", None)
    # Remove ?seat=X from the URL so the next user starts clean.
    try:
        clear_seat_selection()
    except Exception:
        # If clearing the URL fails, just ignore it.
        # The logout should still work.
        pass


def require_login():
    """If the user is not logged in, show a warning and stop the page
    so nothing else gets drawn."""
    if not is_logged_in():
        st.warning("Please log in first.")
        st.stop()

"""
 ─────────────────────────────────────────────────────────────
 SMALL HELPER FUNCTIONS FOR DISPLAY
 ─────────────────────────────────────────────────────────────

 AI HELP: this function works with the JavaScript in app_styles.html. We make an HTML span tag with the end time inside. 
 The JavaScript finds these spans every second and updates the number. This way the countdown does not freeze 
 when Streamlit reruns the page. Our first version used st_autorefresh(1000) which made the whole page blink
 every second. With Claude we used this approach instead.
"""

def live_countdown_html(iso_value):
    """Returns an HTML span that shows a countdown. The JavaScript in
    app_styles.html updates the number every second."""
    if not iso_value:
        return '<span class="chairie-countdown">--:--</span>'
    return (
        f'<span class="chairie-countdown" data-target="{iso_value}">'
        f'{countdown(iso_value)}</span>'
    )


def seat_status_color(status):
    """Returns the color for a seat based on its status.
    Used by the old version of the map (the one with buttons).
    Green = free, orange = reserved, red = occupied, grey = anything else."""
    return {"free": "#1db954", "reserved": "#ff9800", "occupied": "#e53935"}.get(status, "#9ca3af")


# ─────────────────────────────────────────────────────────────
# LOGIN AND SIGNUP PAGE
# ─────────────────────────────────────────────────────────────

def login_page():
    """
    Defining the login page for the website.
    This function creates a login and signup interface. It adds CSS for styling, displays app logo (top middle), 
    switches between login and signup mode, validates the user input and calls the backend functions for logging in or creating a new account.
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
# MY SEAT PANEL AND the LUNCH BREAK BLOCK
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# MY SEAT CARD AND LUNCH BREAK
# ─────────────────────────────────────────────────────────────

def _render_lunch_break_block(seat, token, key_prefix=""):
    """
    Shows the lunch break section inside the "My Seat" card.

    There are 4 different things we can show:
      - the user is on a lunch break right now -> show countdown
      - the user already used the break today -> show a note
      - the user can take a break now -> show the button
      - it is too early or too late -> show when the break is open

    We only show this when the user is checked in (status = occupied).
    A reservation only lasts 10 minutes, so a break there does not make sense. 
    key_prefix is so the button gets a unique name.
    """
    
    if not seat or seat.get("status") != "occupied" or not seat.get("occupied_by_me"):
        return

    state = _lunch_break_state()

    # Case 1: the user is on a break right now
    if state["active"]:
        st.markdown(
            f'<div class="chairie-alert chairie-alert-warning">'
            f'🍽️ <strong>On lunch break</strong> — your seat is held. '
            f'{live_countdown_html(state["ends_at_iso"])} left'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    # Case 2: the user already used their break today
    if state["claimed_today"]:
        st.markdown(
            '<div class="chairie-lunch-note used">'
            '✓ Lunch break already used today. Comes back tomorrow.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # Case 3: the user can take a break now -> show the button
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

    # Case 4: it is too early or too late, show when it is open
    st.markdown(
        f'<div class="chairie-lunch-note closed">'
        f'🍽️ Lunch break is available daily between '
        f'<strong>{LUNCH_BREAK_START_HOUR:02d}:00</strong> and '
        f'<strong>{LUNCH_BREAK_END_HOUR:02d}:00</strong>.'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_my_seat_panel(seat, token, key_prefix=""):
    """
    Shows the "Your seat" details at the top of the Home and Map pages.
    We only show it when the user has a reserved or occupied seat.

    The card shows:
      - a small title ("Your seat · checked in" or "Your seat · reserved")
      - seat details (code, building, floor, status, countdown)
      - the lunch break section (only when checked in)
      - a button to release the seat or cancel the reservation

    key_prefix makes the button names unique in case this section appears on more than one page 
    at the same time.
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

    # Make the countdown row.
    # - If occupied: show the countdown until they have to re-check in.
    # - If reserved: show the 10-min countdown to check in.
    # When the user is on a lunch break we hide this countdown because otherwise the same number 
    # shows up twice on the screen (once here and once in the lunch break block).

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

    # Lunch break section (only shows something when checked in)
    _render_lunch_break_block(seat, token, key_prefix=key_prefix)

    # Re check in hint. It show this only when the user is checked in, is NOT on a lunch break, 
    # and is in the last 30 minutes of their 2-hour slot. We put it here next to the QR scan button so the 
    # user sees the connection with the "scan to extend my seat" button.
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
                # Clear lunch break state too, they no longer have a seat.
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
    """Shows the panel with the seat info and the action buttons above the map. 
    We use st.session_state["selected_seat_id"] to know which seat the user clicked on."""
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
# SIDEBAR AND TOP BAR (shown on every page after login)
# ─────────────────────────────────────────────────────────────
def _go_to(page):
    """Helper to change to a different page when the user clicks a sidebar button or the email at the top."""
    st.session_state["current_page"] = page
    # If we are leaving the map page, forget the seat the user clicked.
    # Otherwise it would still be selected when they come back.
    if page != "map":
        st.session_state["selected_seat_id"] = None


def _render_sidebar():
    """Shows the left sidebar with the four buttons: Home, Map, Profile, Settings. 
    The button for the current page is green as primary type, the other ones are grey."""
    current = st.session_state.get("current_page", "home")

    with st.sidebar:
        # Logo and name at the top
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

        # List of buttons in the order they appear
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
                # If this button is the current page, make it green
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                _go_to(page_key)
                st.rerun()


def _render_top_bar(page_label):
    """Shows the top bar on every page after login.
    
    - On the left: the logo + Chairie name + page name.
    - On the right: the user's email and a Logout button.
    
    Clicking the email opens the profile page."""
    # We put the bar in a container with a CSS class so we can style it.
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
            # Another container so we can style just the email and logout buttons.
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

"""
 ─────────────────────────────────────────────────────────────
 HOME PAGE (the page the user sees after login)
 ─────────────────────────────────────────────────────────────
 The home page has these parts from top to bottom:
 
   1) Green hero box with the slogan and a "Find a Seat" button that takes the user to the map.
   2) Three small boxes or cards: free seats now, floors we have, time of the last update.
   3) One card per floor with: how many seats are free out of the total, a progress bar (green/honey yellow/red depending
      on how full it is), and a tag (Open/Busy/Full).
   4) One forecast chart per floor that shows the predicted occupancy by hour, made with the machine learning model.
"""

def _render_forecast_chart(floor_choice, floor_stats):
    """Shows the forecast bar chart for one floor. The bar for the current hour is in a stronger colour 
    (green/yellow/red, depending on how full the floor is) so the user knows where they are right now. 
    We use Plotly for the chart suggested by Claude after being stuck on which library to choose and work with."""
    series = _ml_forecast_series(floor_choice)

    if series is None:
        st.info("Not enough historical data yet for this floor.")
        return
    hours      = [h for h, _ in series]
    occupancy  = [pct for _, pct in series]

    # Find what hour it is now in Zurich. If it is before or after
    # opening hours we still highlight the first or last bar so the
    # chart never has zero highlighted bars.
    now_hour = _zurich_now().hour
    if now_hour < hours[0]:
        now_hour = hours[0]
    elif now_hour > hours[-1]:
        now_hour = hours[-1]

    # Pick the colour for the current hour based on how full the floor is.
    availability = floor_stats["availability"]
    highlight_color = {
        "open":  "#4A7C2D",
        "busy":  "#F2C46D",
        "full":  "#E30613",
    }.get(availability, "#4A7C2D")
    base_color = "#cfe0bf"   # soft green for the other bars

    # Make a list of colours, one for each bar.
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
    """Shows one stat card for one floor on the home page."""
    pct      = floor_stats["pct_taken"]
    free     = floor_stats["free"]
    total    = floor_stats["total"]
    avail    = floor_stats["availability"]
    display  = FLOOR_META[floor_choice]["display"]

    # The progress bar colour depends on how full the floor is
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

"""
 ─────────────────────────────────────────────────────────────
 QR SCAN CARD (on the map page)
 ─────────────────────────────────────────────────────────────
 This card lets the user scan a QR code with their camera, or type the seat code by hand. 
 The function decode_qr() comes from qr code file, and the function resolve_scanned_code 
 (which decides what to do with the code) is in seat manager file.
 """

def _render_qr_scan_card(token, seats, reserved_seat):
    """ 
    Shows the "Scan QR" card. The camera does not turn on until the user clicks the button, 
    so it does not run all the time.
    
     AI HELP: Claude told us two things for this function:
     
     1) We have to keep st.camera_input hidden behind a button. Otherwise the camera light stays on.
     2) We have to change the key of the camera every time the user takes a new picture. Without this, Streamlit keeps
        the old photo and the user cannot scan a second time. 
    """
    
    ss = st.session_state
    # Set the default values once.
    ss.setdefault("qr_scanner_open", False)   # is the camera shown?
    ss.setdefault("qr_camera_id",    0)       # camera key, we change it each scan
    ss.setdefault("qr_last_result",  None)    # the result of the last scan

    # Section title. If the user already reserved a seat, mention it.
    if reserved_seat:
        hint = f"your reservation is seat {reserved_seat['code']}"
    else:
        hint = "scan any seat's QR code to check in"
    st.markdown(
        f"<div class='chairie-section-title'>Quick check-in "
        f"<span class='hint'>{hint}</span></div>",
        unsafe_allow_html=True,
    )

    # Show the last result (success or error message) at the top.
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

    # If the camera is closed, show the sxan button and the manual option
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
                ss["qr_camera_id"]  += 1   # change the key so the camera is fresh
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

    # If the camera is open, show it and wait for a photo
    st.caption("Point your camera at the QR code on the seat.")
    photo = st.camera_input(
        "QR scanner",
        key=f"qr_camera_{ss['qr_camera_id']}",  # unique name per scan
        label_visibility="collapsed",
    )

    if photo is not None:
        from PIL import Image  # we import this here so the app does not crash if PIL is not installed and the camera is never used
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
    """Shows the home page after login: hero card, three small cards,
    one stat card per floor, and one forecast chart per floor."""
    _render_top_bar("Home")

    # ── main loading hero card with slogan + CTA ─────────────────────────────────────
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

    # The "find a seat" button is a real Streamlit button so it can change the page. 
    # We put it inside a container with a special key so the CSS file can colour just this button honey-yellow.
    with st.container(key="chairie_hero_cta"):
        cta_col, _ = st.columns([2, 6])
        with cta_col:
            if st.button("Find a Seat  →", key="hero_find_seat_btn", use_container_width=True):
                _go_to("map")
                st.rerun()

    # Get the seats from Supabase
    seats_result = get_seats(token)
    if not seats_result.get("success"):
        st.error(seats_result.get("message", "Could not fetch seats."))
        return
    seats = seats_result["seats"]

    # If the user has a seat (reserved or occupied), show the "your seat" card here.
    my_seat = next(
        (s for s in seats if s.get("occupied_by_me") or s.get("reserved_by_me")),
        None,
    )
    if my_seat:
        _render_my_seat_panel(my_seat, token, key_prefix="home_")

    # The three small cards at the top: free seats, floors, time.
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

    # One card per floor with the live numbers.
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

    # One forecast chart per floor.
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

"""
 ─────────────────────────────────────────────────────────────
 PROFILE PAGE
 ─────────────────────────────────────────────────────────────
 The real profile page (avatar, name, study stats) is in account page file. This function just draws the top bar and then
 calls render_account_page function from that file.
"""

def profile_page(token):
    """Shows the Profile page. We just draw the top bar and then call the function from account page"""
    _render_top_bar("Profile")
    render_account_page(token)

"""
 ─────────────────────────────────────────────────────────────
 SETTINGS PAGE
 ─────────────────────────────────────────────────────────────
 Placeholder for now. deleting account, appearance and other controls. For future development. Was used
 as a demo setup to stimulate lunch break, recheck in simulation etc... 
""" 

def settings_page(token):
    """Shows the Settings page. Right now this is just a placeholder
    until we add the real settings (notifications, appearance, account)."""
    _render_top_bar("Settings")
    st.markdown(
        '<div class="chairie-placeholder">'
        '  <div class="chairie-placeholder-icon">⚙</div>'
        '  <div class="chairie-placeholder-title">Settings</div>'
        '  <div class="chairie-placeholder-sub">'
        '    Notification preferences, appearance and account '
        '    controls will live here. Coming soon.'
        '  </div>'
        '</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────
# MAP PAGE
# ─────────────────────────────────────────────────────────────
def map_page(token):
    """Shows the map page. The user picks a floor, clicks a seat,
    and reserves it or checks in with a QR code."""
    _render_top_bar("Library Map")

    # Get the user status (reserved seat? checked in seat?).
    # We do this here so the QR check-in section below can reuse it.
    reserved_seat   = None
    checked_in_seat = None
    status_result   = get_user_status(token)
    if status_result.get("success"):
        reserved_seat   = status_result.get("reserved_seat")
        checked_in_seat = status_result.get("checked_in_seat")

        # If the user reserved a seat but did NOT check in yet, show a yellow alert with the countdown. 
        # They have 10 minutes to scan the QR code.
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

    # If the user has a seat, show the "Your seat" card at the top.
    my_seat_on_map = checked_in_seat or reserved_seat
    if my_seat_on_map:
        _render_my_seat_panel(my_seat_on_map, token, key_prefix="map_")

    # Get all seats from Supabase.
    seats_result = get_seats(token)
    if not seats_result.get("success"):
        st.error(seats_result.get("message", "Could not fetch seats."))
        return

    seats = seats_result["seats"]

    # QR scan card. The camera is OFF until the user clicks "Scan QR code".
    _render_qr_scan_card(token, seats, reserved_seat)

    # Row above the map: floor picker, free seat count, legend.
    tcol1, tcol2, tcol3 = st.columns([2, 2, 5])
    with tcol1:
        floor_choice = st.selectbox(
            "Floor",
            options=list(FLOOR_CONFIG.keys()),
            index=0,
            key="floor_selector",
            label_visibility="visible",
        )

    # Count how many seats are free on the selected floor. We use _seat_belongs_to_floor() because the floor is stored differently
    # in different rows (sometimes as text, sometimes as a number), and this function handles all the cases.
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

    # Show the map. If we have a JSON file for the floor, we show the clickable Plotly map. 
    # If not, we show the old version (just a picture with a list of buttons).
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    floor_cfg = FLOOR_CONFIG.get(floor_choice)

    layout = None
    if floor_cfg is not None:
        json_path = floor_cfg["json_path"]
        if json_path:
            full_path = os.path.join(BASE_DIR, json_path)
            layout = load_layout_data(full_path, silent=True)
        else:
            # Use the default JSON file (for Ground Floor).
            layout = load_layout_data(silent=True)

    if layout and layout.get("seats"):
        """
         The JSON file tells us where each seat is on the picture (x, y, size, id), but the JSON does NOT have the live
         status (free, reserved, occupied). The live status comes from Supabase. So we have to combine the two by the seat
         id, so each dot has the right position AND the right colour.
        
         We give every seat in the database a unique id. Ground floor uses 1 to 189 and Floor 1 uses 190 to 496. 
         The ranges do not overlap, so we can just match by id without worrying about which floor we are on.
        """
        supabase_by_id = {int(s["id"]): s for s in seats}

        merged_seats = []
        for layout_seat in layout["seats"]:
            try:
                sid = int(layout_seat["id"])
            except (KeyError, TypeError, ValueError):
                # If the JSON has a missing or wrong id, we skip this dot
                continue
            live = supabase_by_id.get(sid)
            merged_seats.append({
                "id":     sid,
                "x":      int(layout_seat.get("x", 0)),
                "y":      int(layout_seat.get("y", 0)),
                "size":   int(layout_seat.get("size", 13)),
                # If Supabase knows the seat, use its status (free/reserved/occupied). If not, show maintenance as grey. 
                # This usually means the seat was deleted from Supabase but the JSON file still has it.
                "status": (live or {}).get("status", "maintenance"),
            })

        """
         Click handling.£
         
         AI HELP: we asked Claude how to know which dot the user clicked on the Plotly map. 
         It told us that Streamlit saves the click event in st.session_state under the same name as
         the chart, and that we have to read it before showing the chart, so the seat info above the map updates on the same
         click.
        """
        
        map_key = floor_cfg["map_key"]
        chart_event = st.session_state.get(map_key)
        if isinstance(chart_event, dict):
            sel = chart_event.get("selection")
            points = (sel.get("points") if isinstance(sel, dict) else None) or []
            if points:
                cd = points[0].get("customdata")
                if cd is not None:
                    try:
                        # customdata is sometimes a list and sometimes
                        # a single number, depending on the Plotly version
                        clicked_id = int(cd[0] if isinstance(cd, (list, tuple)) else cd)
                        # Only update if the user clicked a different seat,
                        # so the page does not reload for no reason.
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
        # Old version of the map, its a picture and a list of buttons. We keep this in case the JSON file is not working.
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

        # List of seats with a select button for each one.
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

        # In the old map version, we show the seat details at the bottom, because the user clicks a button from the list above.
        _render_seat_detail_panel(seats, token)


# ─────────────────────────────────────────────────────────────
# PAGE ROUTER
# ────────────────────────────────────────────────────────────
# This is how we know which page to open. The function main_ap below reads st.session_state["current_page"] and uses this dictionary to find the right function to call.

PAGE_ROUTES = {
    "home":     "landing_page",
    "map":      "map_page",
    "profile":  "profile_page",
    "settings": "settings_page",
}


def _render_support_footer():
    """Shows the support section at the bottom of every page. The actual support content is in support page file"""
    # A thin line to separate the support section from the page.
    st.markdown(
        '<hr class="chairie-support-divider" />',
        unsafe_allow_html=True,
    )
    render_support_page()


def main_app():
    """This is the main function we run after the user is logged in. It does the things every page needs (sidebar, styles, refresh)
    and then opens the page the user is currently on.

    The order is important:
      1. Check the user is logged in
      2. Refresh the page every 60 seconds so we see changes from other users
      3. Add the CSS at the top (so the styles work)
      4. Draw the sidebar
      5. Save a snapshot of the seats every 30 minutes (for ML)
      6. Draw the current page (home/map/profile/settings)
      7. Draw the support section at the bottom
      8. Add the JavaScript at the bottom (for the live countdowns)
    """
    require_login()

    # Refresh the page every 60 seconds so we see when other users reserve or release a seat.  The countdowns do NOT depend on this, they update every second by themselves with JavaScript.
    st_autorefresh(interval=60000, key="seat_refresh")

    # Add the CSS FIRST so all styles are ready before the page draws.
    _inject_app_styles()

    # Sidebar is on every page.
    _render_sidebar()

    token = st.session_state["token"]

    # Save a snapshot of the current seat occupancy to the database. We only do it if 30 minutes (1800 seconds) passed since the last one, 
    # otherwise we would save too many rows. The machine learning model uses these rows to make the forecast.
    last_snapshot = st.session_state.get("last_snapshot_time")
    now = dt.now(timezone.utc)
    if not last_snapshot or (now - last_snapshot).total_seconds() > 1800:
        save_real_occupancy_snapshot()
        st.session_state["last_snapshot_time"] = now

    # Open the right page based on session_state["current_page"].
    current      = st.session_state.get("current_page", "home")
    page_fn_name = PAGE_ROUTES.get(current, "landing_page")
    page_fn      = globals().get(page_fn_name)
    if not callable(page_fn):
        # Something is wrong with the page name, just go home.
        st.session_state["current_page"] = "home"
        page_fn = landing_page

    page_fn(token)

    # Support section at the bottom of every page.
    _render_support_footer()

    # Add the JavaScript LAST so it does not push the top bar down.
    _inject_app_script()


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
def main():
    """The first function that runs when we start the app. It sets the page title, prepares session state, and then
    shows the login page or the main app depending on the user."""
    st.set_page_config(
        page_title="HSG Study Spots",
        layout="wide",
        # We want the sidebar open by default. Otherwise the user has to click a small arrow to find the menu.
        initial_sidebar_state="expanded",
    )
    init_auth_state()

    # If Supabase is not set up correctly, show a red error message. The app still runs so we can see the error during testing.
    if not SUPABASE_OK:
        st.error(
            "⚠️ Supabase is not configured. "
            "Add SUPABASE_URL and SUPABASE_KEY to your Streamlit secrets or .env file."
        )

    if is_logged_in():
        main_app()
    else:
        login_page()


# This is how Python runs the file: only call main() if this file is the one we started with.
if __name__ == "__main__":
    main()
