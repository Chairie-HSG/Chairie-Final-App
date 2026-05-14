"""
streamlit_app.py

Main App File

Contains:
- Supabase login/signup
- live seat reservation and check-in logic
- interactive floor maps
- QR/manual check-in
- home dashboard with live statistics
- ML occupancy forecast
- profile study-time statistics
"""
# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import os
import datetime
from datetime import datetime as dt, timedelta, timezone
from zoneinfo import ZoneInfo   # Python 3.9+ stdlib — no extra dep needed

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
# API LAYER  (from api.py + supabase_client.py)
# ─────────────────────────────────────────────────────────────
RESERVATION_MINUTES = 10
RECHECK_HOURS = 2
# How early (before occupied_until expires) the re-check-in window opens.
# Scanning your seat's QR within this window extends `occupied_until` by
# another RECHECK_HOURS without starting a new study session. Scans
# outside the window just return a friendly "already checked in" note.
RECHECK_WINDOW_MINUTES = 30

# All wall-clock display is in Switzerland's local timezone. Storage in
# Supabase stays in UTC (good practice for ISO timestamps); we only
# convert when *showing* a time to the user.
ZURICH_TZ = ZoneInfo("Europe/Zurich")


def _now():
    """UTC datetime used by all storage / comparison logic."""
    return dt.now(timezone.utc)


def _zurich_now():
    """Zurich-local datetime used only for display (KPI strip, forecast)."""
    return dt.now(ZURICH_TZ)


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

        supabase.table("study_sessions").insert({
            "user_email": email,
            "seat_id": seat["id"],
            "seat_code": seat["code"],
            "floor": seat["floor"],
            "started_at": _to_iso(_now()),
        }).execute()

        return {
            "success": True,
            "message": f"Checked in to seat {seat['code']}.",
            "occupied_until": occupied_until,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


def recheck_in_from_qr(token, seat_id):
    """Extend the user's current check-in by another `RECHECK_HOURS`
    via a QR re-scan of their own seat.

    Differs from `check_in_from_qr` in three important ways:
      1. Only works on a seat the user *already* occupies.
      2. Only works inside the last `RECHECK_WINDOW_MINUTES` of the
         current `occupied_until` — too-early scans are rejected with
         a friendly "available in N min" message.
      3. Does NOT create a new `study_sessions` row — the original
         row's `started_at` stays put, so total study time is computed
         correctly when the seat is eventually released.
    """
    if not SUPABASE_OK:
        return {"success": False, "message": "Supabase not configured."}
    try:
        _expire_seats()
        email = _email_from_token(token)
        if not email:
            return {"success": False, "message": "Unauthorized"}

        seat_res = supabase.table("seats").select("*").eq("id", seat_id).limit(1).execute()
        if not seat_res.data:
            return {"success": False, "message": "Seat not found."}
        seat = seat_res.data[0]

        if seat.get("status") != "occupied" or seat.get("occupied_by") != email:
            return {
                "success": False,
                "message": "You're not currently checked in to this seat.",
            }

        current_until = seat.get("occupied_until")
        if not current_until:
            return {"success": False, "message": "Seat has no expiration set."}

        # Only allow extension inside the last RECHECK_WINDOW_MINUTES.
        secs_remaining = seconds_left(current_until)
        if secs_remaining > RECHECK_WINDOW_MINUTES * 60:
            mins_until_open = (secs_remaining - RECHECK_WINDOW_MINUTES * 60) // 60
            return {
                "success": False,
                "kind": "too_early",
                "message": (
                    f"Re-check in opens {RECHECK_WINDOW_MINUTES} min before your "
                    f"time runs out. Available in {mins_until_open} min."
                ),
            }

        new_until = _to_iso(_now() + timedelta(hours=RECHECK_HOURS))
        supabase.table("seats").update(
            {"occupied_until": new_until}
        ).eq("id", seat_id).execute()

        return {
            "success": True,
            "message": (
                f"Re-checked in to seat {seat['code']} — your seat is "
                f"yours for another {RECHECK_HOURS} hour(s)."
            ),
            "occupied_until": new_until,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


def _recheck_window_state(seat):
    """Return whether the user's occupied seat is currently inside the
    re-check-in window. Used by the UI to surface a contextual hint
    in the My Seat panel and by `_resolve_scanned_code` to decide
    whether a self-scan should extend or just acknowledge.

    Returns a dict with:
      - is_occupied_by_me:  bool
      - window_open:        bool  — True if we're in the last
                                    RECHECK_WINDOW_MINUTES of the
                                    current `occupied_until`
      - minutes_to_open:    int|None — minutes until window opens
                                    (None once it's already open)
    """
    if not seat or seat.get("status") != "occupied" or not seat.get("occupied_by_me"):
        return {"is_occupied_by_me": False, "window_open": False, "minutes_to_open": None}

    until = seat.get("occupied_until")
    if not until:
        return {"is_occupied_by_me": True, "window_open": False, "minutes_to_open": None}

    secs = seconds_left(until)
    window_open = (0 < secs <= RECHECK_WINDOW_MINUTES * 60)
    return {
        "is_occupied_by_me": True,
        "window_open":        window_open,
        "minutes_to_open":    None if window_open else max(0, (secs - RECHECK_WINDOW_MINUTES * 60) // 60),
    }


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
            session_res = (
                supabase.table("study_sessions")
                .select("*")
                .eq("user_email", email)
                .is_("ended_at", "null")
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            )

            if session_res.data:
                session = session_res.data[0]

                started = dt.fromisoformat(session["started_at"])
                ended = _now()

                duration_minutes = int((ended - started).total_seconds() / 60)

                supabase.table("study_sessions").update({
                    "ended_at": _to_iso(ended),
                    "duration_minutes": duration_minutes,
                }).eq("id", session["id"]).execute()

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
# LUNCH BREAK  (daily 1-hour grace window, 11:00–14:00 Zurich)
# ─────────────────────────────────────────────────────────────
# Every checked-in user gets one 1-hour lunch break per day, claimable
# at any moment in the 11:00–14:00 Zurich window. Once started, the
# break runs for 60 min and is tracked in session state — both because
# we don't want to add a new Supabase column for this, AND because the
# break is primarily a UX construct: the only persistent effect is
# bumping the seat's `occupied_until` forward by an hour so the
# auto-expire job doesn't release the seat while the user is at lunch.
LUNCH_BREAK_START_HOUR = 11   # inclusive (Zurich local)
LUNCH_BREAK_END_HOUR   = 14   # exclusive — last claim at 13:59
LUNCH_BREAK_MINUTES    = 60


def _lunch_break_window_open():
    """True if the wall clock (Zurich) is currently in the claim window."""
    # ── DEMO OVERRIDE (Settings page) ───────────────────────────────
    # `_demo_lunch_window_force` is set by the Settings demo controls
    # to True/False to force the window open or closed regardless of
    # the actual wall clock. None (the default) falls through to the
    # real time check. REMOVE THIS BLOCK WHEN DEMO PAGE IS REMOVED.
    override = st.session_state.get("_demo_lunch_window_force")
    if override is not None:
        return bool(override)
    # ── END DEMO OVERRIDE ───────────────────────────────────────────
    now = _zurich_now()
    return LUNCH_BREAK_START_HOUR <= now.hour < LUNCH_BREAK_END_HOUR


def _lunch_break_state():
    """Return the current lunch-break state for the logged-in user.

    Returns a dict with:
      - window_open:    bool  — is it 11:00–14:00 right now?
      - active:         bool  — is a break currently running?
      - ends_at_iso:    str|None — when the break ends (UTC ISO)
      - claimed_today:  bool  — has the user already used today's break?
    """
    window_open = _lunch_break_window_open()
    today_str   = _zurich_now().strftime("%Y-%m-%d")

    # Persisted in session state. If the date stamp doesn't match
    # today's Zurich date, the user is free to claim a fresh break.
    claimed_date  = st.session_state.get("lunch_break_claimed_date")
    claimed_today = (claimed_date == today_str)

    active_until_iso = st.session_state.get("lunch_break_active_until")
    active = False
    if active_until_iso:
        try:
            ends = dt.fromisoformat(active_until_iso)
            active = ends > _now()
        except Exception:
            active = False

    return {
        "window_open":   window_open,
        "active":        active,
        "ends_at_iso":   active_until_iso if active else None,
        "claimed_today": claimed_today,
    }


def start_lunch_break(token, seat_id):
    """Claim today's 1-hour lunch break on the user's occupied seat.

    Extends the seat's `occupied_until` to at least (now + 60 min)
    so the auto-expire job doesn't release the seat while the user is
    away. Records the claim in session state so the same user can't
    claim it again until tomorrow.
    """
    if not SUPABASE_OK:
        return {"success": False, "message": "Supabase not configured."}

    state = _lunch_break_state()
    if not state["window_open"]:
        return {
            "success": False,
            "message": f"Lunch break is only available between "
                       f"{LUNCH_BREAK_START_HOUR:02d}:00 and "
                       f"{LUNCH_BREAK_END_HOUR:02d}:00.",
        }
    if state["active"]:
        return {"success": False, "message": "You are already on a lunch break."}
    if state["claimed_today"]:
        return {"success": False, "message": "You already used your lunch break today."}

    try:
        email = _email_from_token(token)
        if not email:
            return {"success": False, "message": "Unauthorized"}

        seat_res = supabase.table("seats").select("*").eq("id", seat_id).limit(1).execute()
        if not seat_res.data:
            return {"success": False, "message": "Seat not found."}
        seat = seat_res.data[0]
        if seat.get("occupied_by") != email or seat.get("status") != "occupied":
            return {"success": False, "message": "You must be checked in to take a lunch break."}

        break_ends_iso = _to_iso(_now() + timedelta(minutes=LUNCH_BREAK_MINUTES))

        # Don't shorten an existing longer occupied_until — only extend it.
        current_until = seat.get("occupied_until")
        new_until = break_ends_iso
        if current_until and current_until > break_ends_iso:
            new_until = current_until

        supabase.table("seats").update(
            {"occupied_until": new_until}
        ).eq("id", seat_id).execute()

        today_str = _zurich_now().strftime("%Y-%m-%d")
        st.session_state["lunch_break_claimed_date"] = today_str
        st.session_state["lunch_break_active_until"] = break_ends_iso

        return {
            "success": True,
            "message": "Enjoy your break — your seat is held for 1 hour.",
            "ends_at_iso": break_ends_iso,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


# ═════════════════════════════════════════════════════════════
# ███  DEMO BLOCK START  ███████████████████████████████████████
# ═════════════════════════════════════════════════════════════
# TEMPORARY demo controls used to exercise the lunch-break and
# re-check-in features without waiting for the real time windows.
# Visible to every logged-in user on the Settings page. Everything
# inside this block (plus the demo override in _lunch_break_window_open
# and the entire `settings_page()` body that's marked DEMO) should be
# deleted before launch. Grep for "DEMO BLOCK" to find all the pieces.

def demo_set_seat_expiry(token, minutes_from_now):
    """Force the user's currently-occupied seat to expire in
    `minutes_from_now` minutes. Used by the Settings demo page to
    pop the seat into the re-check-in window (≤30 min remaining)
    on demand, or to reset it back to a full 2-hour timer.
    """
    if not SUPABASE_OK:
        return {"success": False, "message": "Supabase not configured."}
    try:
        email = _email_from_token(token)
        if not email:
            return {"success": False, "message": "Unauthorized"}

        occupied = (
            supabase.table("seats")
            .select("*")
            .eq("occupied_by", email)
            .eq("status", "occupied")
            .limit(1)
            .execute()
        )
        if not occupied.data:
            return {
                "success": False,
                "message": "You have no occupied seat to advance. "
                           "Check into a seat first.",
            }
        seat = occupied.data[0]

        new_until = _to_iso(_now() + timedelta(minutes=int(minutes_from_now)))
        supabase.table("seats").update(
            {"occupied_until": new_until}
        ).eq("id", seat["id"]).execute()

        return {
            "success": True,
            "message": f"Seat {seat['code']} now expires in "
                       f"{int(minutes_from_now)} min.",
            "occupied_until": new_until,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}

# ═════════════════════════════════════════════════════════════
# ███  DEMO BLOCK END  █████████████████████████████████████████
# ═════════════════════════════════════════════════════════════


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
    # Drop any in-progress lunch-break state. We keep these out of the
    # `defaults` dict above because they're optional/transient — using
    # .pop ensures a stale value from a previous session doesn't leak
    # into the next user on the same browser.
    st.session_state.pop("lunch_break_active_until", None)
    st.session_state.pop("lunch_break_claimed_date", None)
    # DEMO BLOCK: also clear any demo override left over on this browser.
    st.session_state.pop("_demo_lunch_window_force", None)
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
    """Static MM:SS string. Kept for places where a one-shot snapshot is
    fine (e.g. log messages, the QR scan result on first display)."""
    secs = seconds_left(iso_value)
    return f"{secs // 60:02d}:{secs % 60:02d}"


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
            st.markdown(
                f'<div class="chairie-alert chairie-alert-warning">'
                f'This seat is reserved by you. Time left to check in: '
                f'{live_countdown_html(seat["reserved_until"])}'
                f'</div>',
                unsafe_allow_html=True,
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
        "capacity": 189,   # total physical seats; ALWAYS the denominator
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
        "capacity": 307,
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

def _ml_forecast_series(floor_choice):
    try:
        import pandas as pd
        from sklearn.ensemble import RandomForestRegressor

        response = (
            supabase.table("occupancy_snapshots")
            .select("*")
            .eq("floor", floor_choice)
            .order("created_at")
            .execute()
        )

        rows = response.data

        if not rows or len(rows) < 20:
            return None

        df = pd.DataFrame(rows)
        df["created_at"] = pd.to_datetime(df["created_at"])
        df["hour"] = df["created_at"].dt.hour
        df["day_of_week"] = df["created_at"].dt.dayofweek

        X = df[["hour", "day_of_week"]]
        y = df["occupied_percent"]

        model = RandomForestRegressor(n_estimators=100, random_state=42)
        model.fit(X, y)

        hours = list(range(8, 22))
        current_day = dt.now(timezone.utc).weekday()

        future_hours = pd.DataFrame({
            "hour": hours,
            "day_of_week": [current_day] * len(hours)
        })

        predictions = model.predict(future_hours)

        return [
            (hour, max(0, min(float(prediction) / 100, 1)))
            for hour, prediction in zip(hours, predictions)
        ]

    except Exception as e:
        st.warning(f"Could not generate ML forecast: {e}")
        return None

def save_real_occupancy_snapshot():
    """
    Saves the current real seat occupancy into occupancy_snapshots.
    This is what gives the ML model real historical data over time.
    """
    if not SUPABASE_OK:
        return

    try:
        response = supabase.table("seats").select("*").execute()
        seats = response.data

        for floor_choice in ["Ground Floor", "Floor 1"]:
            floor_seats = [
                seat for seat in seats
                if _seat_belongs_to_floor(seat, floor_choice)
            ]

            total_count = len(floor_seats)
            reserved_count = sum(1 for s in floor_seats if s["status"] == "reserved")
            occupied_count = sum(1 for s in floor_seats if s["status"] == "occupied")
            free_count = sum(1 for s in floor_seats if s["status"] == "free")

            if total_count == 0:
                occupied_percent = 0
            else:
                occupied_percent = round(((reserved_count + occupied_count) / total_count) * 100, 2)

            supabase.table("occupancy_snapshots").insert({
                "floor": floor_choice,
                "free_count": free_count,
                "reserved_count": reserved_count,
                "occupied_count": occupied_count,
                "total_count": total_count,
                "occupied_percent": occupied_percent,
            }).execute()

    except Exception:
        pass

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

    # Self-scan: either re-check in (if inside the window) or just
    # acknowledge with a helpful "comes back in N min" note.
    if seat.get("occupied_by_me"):
        rs = _recheck_window_state(seat)
        if rs["window_open"]:
            recheck = recheck_in_from_qr(token, seat["id"])
            if recheck.get("success"):
                return {
                    "kind": "ok",
                    "message": (
                        f"✅ Re-checked in at seat {seat['code']} — extended "
                        f"for another {RECHECK_HOURS} hour(s)."
                    ),
                }
            # Re-check failed (e.g. raced past the window edge) — fall
            # through to a soft acknowledgement so the user isn't shown
            # a scary error for what is, essentially, a no-op.
            return {
                "kind": "ok",
                "message": recheck.get(
                    "message",
                    f"You're already checked in at seat {seat['code']}.",
                ),
            }
        mins = rs.get("minutes_to_open")
        if mins and mins > 0:
            return {
                "kind": "ok",
                "message": (
                    f"You're already checked in at seat {seat['code']}. "
                    f"Re-check in opens in {mins} min."
                ),
            }
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

def get_user_study_stats(token):
    """
    Returns study statistics for the logged-in user.
    """
    try:
        email = _email_from_token(token)

        if not email:
            return None

        response = (
            supabase.table("study_sessions")
            .select("*")
            .eq("user_email", email)
            .not_.is_("duration_minutes", "null")
            .execute()
        )

        sessions = response.data

        if not sessions:
            return {
                "weekly_hours": 0,
                "total_hours": 0,
                "sessions": 0,
            }

        now = dt.now(timezone.utc)

        weekly_minutes = 0
        total_minutes = 0

        for s in sessions:
            total_minutes += s["duration_minutes"]

            started = dt.fromisoformat(s["started_at"])

            if (now - started).days <= 7:
                weekly_minutes += s["duration_minutes"]

        return {
            "weekly_hours": round(weekly_minutes / 60, 1),
            "total_hours": round(total_minutes / 60, 1),
            "sessions": len(sessions),
        }

    except Exception as e:
        st.error(f"Stats error: {e}")
        return None
# ─────────────────────────────────────────────────────────────
# PROFILE PAGE  (delegates to Account_page.render_account_page)
# ─────────────────────────────────────────────────────────────
# The actual account UI (avatar, profile form, study stats) lives in
# Account_page.py — same modular pattern Support_page uses. We just
# render the shared top bar here, then hand off to that module. If
# Account_page.py wasn't deployed alongside this file, we fall back
# to a minimal placeholder so the tab stays navigable.
def profile_page(token):
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
        # Merge layout coordinates with live Supabase status by seat id.
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
