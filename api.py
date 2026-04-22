from datetime import datetime, timedelta, timezone
from supabase_client import supabase

RESERVATION_MINUTES = 10 #Reservation rules 
RECHECK_HOURS = 2


def _now(): #current time
    return datetime.now(timezone.utc)


def _to_iso(dt): #format time
    return dt.isoformat()


def _user_from_token(token):
    """
    Uses the real Supabase access token to get the current logged-in user.
    Returns the user object, or None if token is invalid / missing.
    """
    if not token:
        return None #Error check

    try:
        response = supabase.auth.get_user(token)  #gets user details
        return response.user
    except Exception:
        return None


def _email_from_token(token):
    """
    Extracts the logged-in user's email from the real Supabase token.
    """
    user = _user_from_token(token)
    if not user:
        return None
    return user.email


def login_request(email, password):
    """
    Real login using Supabase Auth.
    Expects email + password.
    """
    try:
        response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })

        if response.user and response.session:
            return {
                "success": True,
                "username": response.user.email,   # keep key name same so app/auth need minimal changes
                "token": response.session.access_token
            }

        return {
            "success": False,
            "message": "Login failed."
        }

    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }


def _expire_seats():
    
    now_iso = _to_iso(_now()) #current time in iso

    reserved = (
        supabase.table("seats")
        .select("*")
        .eq("status", "reserved")
        .not_.is_("reserved_until", "null")
        .execute()
    ) #defines reserved seats as seats with the reserved status whose time hasn't expired (reached 0)

    for seat in reserved.data:
        if seat["reserved_until"] and seat["reserved_until"] <= now_iso:
            (
                supabase.table("seats")
                .update({
                    "status": "free",
                    "reserved_by": None,
                    "reserved_until": None
                })
                .eq("id", seat["id"])
                .execute()
            ) #if the seats time of expriation is passed (aka smaller than the current time), we release the seat

    # expire occupied seats
    occupied = (
        supabase.table("seats")
        .select("*")
        .eq("status", "occupied")
        .not_.is_("occupied_until", "null")
        .execute()
    ) #defines occupied seats as seats with the occupied status whose time hasn't expired (reached 0)

    for seat in occupied.data:
        if seat["occupied_until"] and seat["occupied_until"] <= now_iso:
            (
                supabase.table("seats")
                .update({
                    "status": "free",
                    "occupied_by": None,
                    "occupied_until": None
                })
                .eq("id", seat["id"])
                .execute()
            ) #if the seats time of expriation is passed (aka smaller than the current time), we release the seat


def get_seats(token=None):

    try:
        _expire_seats()
        email = _email_from_token(token)

        response = supabase.table("seats").select("*").order("id").execute()

        seats = []
        for seat in response.data:
            seats.append({
                "id": seat["id"],
                "code": seat["code"],
                "building": seat["building"],
                "floor": seat["floor"],
                "status": seat["status"],
                "reserved_until": seat.get("reserved_until"),
                "occupied_until": seat.get("occupied_until"),
                "reserved_by_me": seat.get("reserved_by") == email,
                "occupied_by_me": seat.get("occupied_by") == email,
            })

        return {
            "success": True,
            "seats": seats
        }

    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }


def get_user_status(token=None):
    """
    Returns the currently logged-in user's active reservation / occupied seat.
    """
    try:
        _expire_seats()
        email = _email_from_token(token)

        if not email:
            return {
                "success": False,
                "message": "Unauthorized"
            }

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

        reserved_seat = None
        checked_in_seat = None

        if reserved.data:
            seat = reserved.data[0]
            reserved_seat = {
                "id": seat["id"],
                "code": seat["code"],
                "building": seat["building"],
                "floor": seat["floor"],
                "status": seat["status"],
                "reserved_until": seat.get("reserved_until"),
                "occupied_until": seat.get("occupied_until"),
                "reserved_by_me": True,
                "occupied_by_me": False,
            }

        if occupied.data:
            seat = occupied.data[0]
            checked_in_seat = {
                "id": seat["id"],
                "code": seat["code"],
                "building": seat["building"],
                "floor": seat["floor"],
                "status": seat["status"],
                "reserved_until": seat.get("reserved_until"),
                "occupied_until": seat.get("occupied_until"),
                "reserved_by_me": False,
                "occupied_by_me": True,
            }

        return {
            "success": True,
            "username": email,
            "reserved_seat": reserved_seat,
            "checked_in_seat": checked_in_seat,
        }

    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }


def reserve_seat(token, seat_id):
    """
    Reserve a free seat for 10 minutes.
    """
    try:
        _expire_seats()
        email = _email_from_token(token)

        if not email:
            return {"success": False, "message": "Unauthorized"}

        # user cannot already occupy a seat
        occupied = (
            supabase.table("seats")
            .select("*")
            .eq("occupied_by", email)
            .eq("status", "occupied")
            .execute()
        )

        if occupied.data:
            return {
                "success": False,
                "message": "You are currently checked in somewhere else."
            }

        # user cannot already reserve another seat
        existing_res = (
            supabase.table("seats")
            .select("*")
            .eq("reserved_by", email)
            .eq("status", "reserved")
            .execute()
        )

        if existing_res.data and existing_res.data[0]["id"] != seat_id:
            return {
                "success": False,
                "message": "User already has another seat reserved."
            }

        seat_res = (
            supabase.table("seats")
            .select("*")
            .eq("id", seat_id)
            .limit(1)
            .execute()
        )

        if not seat_res.data:
            return {"success": False, "message": "Seat not found."}

        seat = seat_res.data[0]

        if seat["status"] == "occupied":
            return {"success": False, "message": "That seat is already occupied."}

        if seat["status"] == "reserved" and seat.get("reserved_by") != email:
            return {"success": False, "message": "Someone else reserved it first."}

        expires_at = _to_iso(_now() + timedelta(minutes=RESERVATION_MINUTES))

        (
            supabase.table("seats")
            .update({
                "status": "reserved",
                "reserved_by": email,
                "reserved_until": expires_at
            })
            .eq("id", seat_id)
            .execute()
        )

        return {
            "success": True,
            "message": f"Seat {seat['code']} reserved successfully.",
            "reservation_expires_at": expires_at
        }

    except Exception as e:
        return {"success": False, "message": str(e)}


def cancel_reservation(token):
    """
    Cancel the logged-in user's active reservation.
    """
    try:
        email = _email_from_token(token)
        if not email:
            return {"success": False, "message": "Unauthorized"}

        res = (
            supabase.table("seats")
            .select("*")
            .eq("reserved_by", email)
            .eq("status", "reserved")
            .limit(1)
            .execute()
        )

        if not res.data:
            return {"success": False, "message": "No active reservation."}

        seat_id = res.data[0]["id"]

        (
            supabase.table("seats")
            .update({
                "status": "free",
                "reserved_by": None,
                "reserved_until": None
            })
            .eq("id", seat_id)
            .execute()
        )

        return {"success": True, "message": "Reservation cancelled."}

    except Exception as e:
        return {"success": False, "message": str(e)}


def check_in_from_qr(token, seat_id):
    """
    Converts a reservation into an occupied seat, or directly occupies a free seat.
    """
    try:
        _expire_seats()
        email = _email_from_token(token)

        if not email:
            return {"success": False, "message": "Unauthorized"}

        # cannot occupy two seats
        occupied = (
            supabase.table("seats")
            .select("*")
            .eq("occupied_by", email)
            .eq("status", "occupied")
            .execute()
        )

        if occupied.data and occupied.data[0]["id"] != seat_id:
            return {"success": False, "message": "User cannot occupy two seats."}

        seat_res = (
            supabase.table("seats")
            .select("*")
            .eq("id", seat_id)
            .limit(1)
            .execute()
        )

        if not seat_res.data:
            return {"success": False, "message": "Seat not found."}

        seat = seat_res.data[0]

        if seat["status"] == "occupied" and seat.get("occupied_by") != email:
            return {"success": False, "message": "That seat is already occupied."}

        if seat["status"] == "reserved" and seat.get("reserved_by") != email:
            return {"success": False, "message": "This reservation belongs to another user."}

        occupied_until = _to_iso(_now() + timedelta(hours=RECHECK_HOURS))

        (
            supabase.table("seats")
            .update({
                "status": "occupied",
                "reserved_by": None,
                "reserved_until": None,
                "occupied_by": email,
                "occupied_until": occupied_until
            })
            .eq("id", seat_id)
            .execute()
        )

        return {
            "success": True,
            "message": f"Checked in to seat {seat['code']}.",
            "occupied_until": occupied_until
        }

    except Exception as e:
        return {"success": False, "message": str(e)}


def release_current_seat(token):
    """
    Releases the logged-in user's occupied seat, or cancels their reservation.
    """
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

        if occupied.data:
            seat_id = occupied.data[0]["id"]
            (
                supabase.table("seats")
                .update({
                    "status": "free",
                    "occupied_by": None,
                    "occupied_until": None
                })
                .eq("id", seat_id)
                .execute()
            )

            return {"success": True, "message": "Seat released."}

        reserved = (
            supabase.table("seats")
            .select("*")
            .eq("reserved_by", email)
            .eq("status", "reserved")
            .limit(1)
            .execute()
        )

        if reserved.data:
            seat_id = reserved.data[0]["id"]
            (
                supabase.table("seats")
                .update({
                    "status": "free",
                    "reserved_by": None,
                    "reserved_until": None
                })
                .eq("id", seat_id)
                .execute()
            )

            return {"success": True, "message": "Reservation cancelled."}

        return {"success": False, "message": "No active seat to release."}

    except Exception as e:
        return {"success": False, "message": str(e)}
    
def signup_request(email, password):
    """
    This creates a new user in Supabase Auth.
    """
    try:
        response = supabase.auth.sign_up({
            "email": email,
            "password": password
        })

        if response.user:
            return {
                "success": True,
                "message": "Account created successfully. If email confirmation is enabled, check your inbox before logging in."
            }

        return {
            "success": False,
            "message": "Signup failed."
        }

    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }