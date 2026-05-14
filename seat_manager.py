"""

This file contains the parts of the app that actually change or read data:
login, signup, seat reservation, QR check-in, seat release, lunch break,
occupancy prediction, and study statistics.

The Streamlit page file should mostly handle what the user sees.
This file handles the backend.

"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import os
from datetime import datetime as dt, timedelta, timezone
from zoneinfo import ZoneInfo   # Python 3.9+ stdlib — no extra dep needed

import streamlit as st          # only used for st.secrets + st.session_state
from supabase import create_client



# SUPABASE CLIENT
# Connects our streamlit to Supabase through the keys in secrets 
# This lets the app work on streamlit cloud which allows it to be more than a local app
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



# CONSTANTS
# This establishes the main time rules for the app which are : 
#  Reservation lasts 10 minutes, checked-in seat lasts 2 hours before needing to recheck in
RESERVATION_MINUTES = 10
RECHECK_HOURS = 2
# Only allows recheck in in the last 30 minutes
RECHECK_WINDOW_MINUTES = 30

# We store times in UTC, but show times in Zurich time
ZURICH_TZ = ZoneInfo("Europe/Zurich")


# TIME HELPERS
 """
    Returns the current time in UTC to store it & use it for time calculations, and later convert it in Zurich timezone
"""
def _now(): 
    return dt.now(timezone.utc)


def _zurich_now():
    """Returns Zurich time, only for some user side things like lunch break"""
    return dt.now(ZURICH_TZ)


def _to_iso(d):
    return d.isoformat()


# AUTH HELPERS
"""
    Uses the Supabase token to get the currently logged-in user.

"""
def _user_from_token(token):
    if not token or not SUPABASE_OK:
        return None
    try:
        response = supabase.auth.get_user(token)
        return response.user
    except Exception: #case of error
        return None 
"""
    Gets the logged-in user's email from their Supabase token.

"""

def _email_from_token(token):
    user = _user_from_token(token)
    if not user:
        return None
    return user.email

 """
    Tries to log a user in with Supabase's authentification system

    Returns the user's email and access token if the account exists & the logins are correct
"""
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

 """
    Creates a new Supabase auth user using Supabase's authentification system
"""
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


# COUNTDOWN HELPERS  
 """
    Calculates how many seconds are left until the stored target time.
"""
def seconds_left(iso_value):
    if not iso_value:
        return 0
    target = dt.fromisoformat(iso_value)
    now = dt.now(timezone.utc)
    diff = int((target - now).total_seconds())
    return max(diff, 0)

"""
Format conversion
"""
def countdown(iso_value):
    secs = seconds_left(iso_value)
    return f"{secs // 60:02d}:{secs % 60:02d}"


# SEAT MANAGEMENT
"""
    Frees seats whose timers have expired.

    Reserved seats become free after the reservation deadline.
    Occupied seats become free after the check-in deadline.
"""
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
"""
    Loads all seats from Supabase
    Also indicates their status in relation to the current user so the Front end can display it
"""

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

 """
    Checks if the current user already has reserved or check in a seat, in which case we return it and the user cannot reserve another seat

"""
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

 """
    Reserves a free seat for the logged-in user.
    
"""
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
            return {"success": False, "message": "You are currently checked in somewhere else."}  # Do not allow users to hold a reservation while already checked in.

        existing_res = (
            supabase.table("seats").select("*").eq("reserved_by", email).eq("status", "reserved").execute()
        )
        if existing_res.data and existing_res.data[0]["id"] != seat_id: # Do not allow users to reserve multiple different seats.
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
        supabase.table("seats").update(  # Save the reservation owner and expiry time in Supabase.
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
 """
    Checks the user into a seat after scanning or entering its QR code.

"""

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
        st.write("DEBUG: study session inserted")
        return {
            "success": True,
            "message": f"Checked in to seat {seat['code']}.",
            "occupied_until": occupied_until,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}

 """
    Extends the user's current check-in timer by scanning their own seat again.

    Only works if the user already occupies a seat, hasnt released it yet (manually or automatically
"""
def recheck_in_from_qr(token, seat_id):
 
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
 """
    Checks whether the user's current seat is inside the re-check-in window.
"""

def _recheck_window_state(seat):

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
# FLOOR METADATA + AGGREGATE STATS
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
# ML OCCUPANCY FORECAST  +  HISTORICAL SNAPSHOTS
# ─────────────────────────────────────────────────────────────
def _ml_forecast_series(floor_choice):
    """Predict typical hourly occupancy (08:00–21:00) for the given
    floor using a RandomForestRegressor trained on past snapshots.

    Returns a list of (hour, occupied_fraction) tuples, where each
    fraction is clamped to [0.0, 1.0]. Returns None if there isn't
    enough history yet (< 20 snapshots) or if the ML stack isn't
    available — callers should render an "insufficient data" hint.
    """
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


# ─────────────────────────────────────────────────────────────
# USER STUDY STATISTICS
# ─────────────────────────────────────────────────────────────
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
# QR-SCAN RESOLUTION  (pure decision logic — UI calls this)
# ─────────────────────────────────────────────────────────────
# The original qr_code.show_checkin validates a scanned code against
# an EXPECTED reservation. The new flow doesn't do that — the user
# can scan any seat at any time, and we decide what happens based on
# the scanned seat's live status. The UI in streamlit_app.py drives
# the camera; this function decides what the result should mean.
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
