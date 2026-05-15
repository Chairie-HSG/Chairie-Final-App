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

"""
    Releases the user's current occupied or reserved seat.

    If the user was checked in, this also closes their active study session
    and saves the session duration.
"""
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


# LUNCH BREAK 

LUNCH_BREAK_START_HOUR = 11   
LUNCH_BREAK_END_HOUR   = 14  
LUNCH_BREAK_MINUTES    = 60
"""
    Checks whether the current Zurich time is inside the lunch break window.
    Every user gets a 1 hour period between 11am and 2 pm for lunch which then can claim at any time in this perido
"""

def _lunch_break_window_open():

    override = st.session_state.get("_demo_lunch_window_force")
    if override is not None:
        return bool(override)
    now = _zurich_now()
    return LUNCH_BREAK_START_HOUR <= now.hour < LUNCH_BREAK_END_HOUR

"""
    Returns the user's current lunch-break status.
"""
def _lunch_break_state():
    
    window_open = _lunch_break_window_open()
    today_str   = _zurich_now().strftime("%Y-%m-%d")


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


# Demo section for us testing the lunch break function

def demo_set_seat_expiry(token, minutes_from_now):
   
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

# FLOOR DATA + STATS, this is used to show stats of how many seats are taken per floor etc 

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

"""
    Checks whether a seat belongs to a selected floor.
"""
def _seat_belongs_to_floor(seat, floor_choice):
  
    meta = FLOOR_META.get(floor_choice)
    if not meta:
        return False
    raw = seat.get("floor")
    if raw is None:
        return False
    return str(raw).strip().lower() in meta["matches"]


def _compute_floor_stats(seats, floor_choice):

    rows     = [s for s in seats if _seat_belongs_to_floor(s, floor_choice)]
    capacity = FLOOR_META[floor_choice]["capacity"]
    matched  = len(rows)

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


# Machine Learning Forecast, for this part we got a bit of help from Claude AI, as this was a bit difficult for us to wrap our head around
"""
    Predicts occupancy by hour for one floor.
"""
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
    Saves the current seat occupancy into the historical snapshot table.

    These snapshots become the training data for the ML forecast. So at first it will use fake data and overtime start using real user data.
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


# Personal Profile Data stats, that are displayed in the profile section
 """
    Calculates study statistics for the logged-in user.

    It reads completed study sessions and returns weekly hours,
    total hours, and number of sessions.
"""
def get_user_study_stats(token):

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


# QR-SCAN RESOLUTION 
 """
    Decides what should happen when a QR code is scanned.
    It receives the scanned seat code and decides whether the user
    can check in, re-check in, or should see an error message.
"""
def _resolve_scanned_code(token, seats, scanned_code):

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
