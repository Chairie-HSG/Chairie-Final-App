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
    # ── Top bar (indexnew.html design) ──
    st.markdown(
        """
        <div style="display:flex; justify-content:space-between; align-items:center;
                    padding-bottom:18px; border-bottom:2px solid #2b6f95; margin-bottom:24px;">
          <div style="font-size:28px; font-weight:bold; color:#0a8f4d;">HSG</div>
          <div style="font-size:16px; font-weight:500; color:#333;">HSG Study Spots</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        "<div style='font-size:18px; margin-bottom:20px; color:#333;'>Seat Booking System</div>",
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Login", use_container_width=True):
            st.session_state["auth_mode"] = "login"
            st.rerun()
    with col2:
        if st.button("Sign Up", use_container_width=True):
            st.session_state["auth_mode"] = "signup"
            st.rerun()

    mode = st.session_state.get("auth_mode", "login")

    if mode == "login":
        st.subheader("Login")
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")
        if submitted:
            if not email or not password:
                st.warning("Please enter both email and password.")
                return
            result = login_request(email, password)
            if result["success"]:
                login_user(result["username"], result["token"])
                st.success("Login successful.")
                st.rerun()
            else:
                st.error(result["message"])
    else:
        st.subheader("Sign Up")
        with st.form("signup_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            confirm = st.text_input("Confirm Password", type="password")
            submitted = st.form_submit_button("Create Account")
        if submitted:
            if not email or not password or not confirm:
                st.warning("Please fill in all fields.")
                return
            if password != confirm:
                st.warning("Passwords do not match.")
                return
            if len(password) < 6:
                st.warning("Password must be at least 6 characters.")
                return
            result = signup_request(email, password)
            if result["success"]:
                st.success(result["message"])
                st.info("Go back to Login and sign in with your new account.")
                st.session_state["auth_mode"] = "login"
            else:
                st.error(result["message"])


# ─────────────────────────────────────────────────────────────
# MAIN APP  (combined indexnew.html layout + app.py logic)
# ─────────────────────────────────────────────────────────────
def main_app():
    require_login()

    # Auto-refresh every second (countdowns update live — from app.py)
    st_autorefresh(interval=1000, key="seat_refresh")

    token = st.session_state["token"]

    # ── Top bar (indexnew.html) ──
    st.markdown(
        f"""
        <div style="display:flex; justify-content:space-between; align-items:center;
                    padding-bottom:18px; border-bottom:2px solid #2b6f95; margin-bottom:24px;
                    background:#fff; border-radius:0;">
          <div style="font-size:28px; font-weight:bold; color:#0a8f4d;">HSG</div>
          <div style="font-size:15px; color:#333; font-weight:500;">
            Home / Main Map Page
          </div>
          <div style="font-size:16px; font-weight:500; color:#333;">
            👤 {st.session_state['username']}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Logout button (top-right area)
    if st.button("Logout", key="logout_btn"):
        logout_user()
        st.rerun()

    st.markdown("<div style='margin-bottom:6px;'></div>", unsafe_allow_html=True)

    # ── User status banner (from app.py render_user_status) ──
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

    # ── Fetch seats from Supabase ──
    seats_result = get_seats(token)
    if not seats_result.get("success"):
        st.error(seats_result.get("message", "Could not fetch seats."))
        return

    seats = seats_result["seats"]

    # ── Floor selector (indexnew.html) ──
    floor_choice = st.selectbox(
        "Select Floor",
        options=["Ground Floor", "Floor 1"],
        index=0,
        key="floor_selector",
    )

    # Filter seats by floor
    floor_number = "0" if floor_choice == "Ground Floor" else "1"
    floor_seats = [s for s in seats if str(s.get("floor", "")) == floor_number]
    if not floor_seats:
        floor_seats = seats  # fallback: show all if floor field not set

    # ── Availability dot + count (indexnew.html) ──
    free_count = sum(1 for s in floor_seats if s["status"] == "free")
    st.markdown(
        f"""
        <div style="display:flex; align-items:center; gap:10px; margin-bottom:14px;
                    font-size:18px; font-weight:600;">
          <span style="width:14px; height:14px; border-radius:50%; background:#1db954;
                       display:inline-block;"></span>
          <span>{free_count} free seats on {floor_choice}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Legend (indexnew.html) ──
    st.markdown(
        """
        <div style="display:flex; align-items:center; gap:20px; margin-bottom:20px;
                    font-size:15px; flex-wrap:wrap;">
          <span>Legend:</span>
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="width:14px; height:14px; border-radius:50%; background:#1db954;
                         display:inline-block;"></span><span>Free</span>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="width:14px; height:14px; border-radius:50%; background:#ff9800;
                         display:inline-block;"></span><span>Reserved</span>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="width:14px; height:14px; border-radius:50%; background:#e53935;
                         display:inline-block;"></span><span>Occupied</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Map box with library floor plan image (indexnew.html map-box) ──
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    img_map = {
        "Ground Floor": os.path.join(BASE_DIR, "Library_GFloor.jpg"),
        "Floor 1": os.path.join(BASE_DIR, "Library_1Floor.jpg"),
    }
    img_file = img_map[floor_choice]

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

    # Show library floor plan image (from Library_GFloor.jpg / Library_1Floor.jpg)
    if os.path.exists(img_file):
        st.image(img_file, use_container_width=True)
    else:
        st.info(f"Floor plan image '{img_file}' not found. Place it in the same directory as streamlit_app.py.")

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Seat grid (indexnew.html seat dots rendered as Streamlit columns) ──
    st.markdown(
        "<div style='font-size:20px; font-weight:600; margin-bottom:14px; color:#444;'>Available Seats</div>",
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

                # Seat card — styled after indexnew.html seat + map-box colours
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
                if st.button(f"Select", key=f"sel_{seat['id']}"):
                    st.session_state["selected_seat_id"] = seat["id"]

    # ── Seat detail panel (from app.py render_seat_details) ──
    st.markdown(
        """
        <div style="border-top:2px solid #2b6f95; margin-top:24px; padding-top:20px;">
          <div style="font-size:20px; font-weight:600; margin-bottom:14px; color:#444;">
            Seat Details
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    selected_id = st.session_state.get("selected_seat_id")
    if not selected_id:
        st.info("Click a 'Select' button above to see seat details.")
        return

    seat = next((s for s in seats if s["id"] == selected_id), None)
    if not seat:
        st.warning("Selected seat not found.")
        return

    st.markdown(
        f"""
        <div style="background:#eef5f8; border-radius:10px; padding:16px; font-size:15px;
                    margin-bottom:16px;">
          <strong>Seat:</strong> {seat['code']}<br>
          <strong>Building:</strong> {seat['building']}<br>
          <strong>Floor:</strong> {seat['floor']}<br>
          <strong>Status:</strong>
          <span style="color:{seat_status_color(seat['status'])}; font-weight:700;">
            {seat['status'].upper()}
          </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Actions depend on seat state (from app.py render_seat_details)
    if seat["status"] == "free":
        st.success("This seat is free.")
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
            st.warning("This seat is reserved by you.")
            st.info(f"Time left to check in: {countdown(seat['reserved_until'])}")
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
                if st.button("Cancel Reservation", key=f"cancel_{seat['id']}"):
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
            st.success("You are currently occupying this seat.")
            st.info(f"Re-check countdown: {countdown(seat['occupied_until'])}")
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
