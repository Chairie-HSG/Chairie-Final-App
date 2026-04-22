
from datetime import datetime, timezone
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from auth import init_auth_state, login_page, logout_button, is_logged_in, require_login
from api import (
    RESERVATION_MINUTES,
    RECHECK_HOURS,
    get_seats,
    get_user_status,
    reserve_seat,
    cancel_reservation,
    check_in_from_qr,
    release_current_seat,
)

st.set_page_config(page_title="ChairY", layout="wide")


def seconds_left(iso_value):
    if not iso_value: #check if time is left
        return 0
    target = datetime.fromisoformat(iso_value)
    now = datetime.now(timezone.utc)  #current time
    diff = int((target - now).total_seconds())
    return max(diff, 0) #returns the difference between the time the reservation is over, and shows 0 if its negative.


def countdown(iso_value):
    secs = seconds_left(iso_value)
    minutes = secs // 60
    seconds = secs % 60
    return f"{minutes:02d}:{seconds:02d}"


def seat_status_color(status): # Colors the seat depending on condition
    colors = {
        "free": "#22c55e",
        "reserved": "#f59e0b",
        "occupied": "#ef4444",
    }
    return colors.get(status, "#9ca3af")


def render_top_bar():
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("Chairi")
        st.caption("Chai Demo")
    with col2:
        st.write("")
        st.write(f"Logged in as: **{st.session_state['username']}**")
        logout_button()


def render_user_status(token):
    result = get_user_status(token)
    if not result["success"]:
        st.error(result["message"])
        return   #error check

    reserved_seat = result["reserved_seat"]
    checked_in_seat = result["checked_in_seat"] #state of the seats

    if reserved_seat:
        st.warning(
            f"You reserved seat **{reserved_seat['code']}**. "
            f"You must scan the QR code within {RESERVATION_MINUTES} minutes."
        )
        st.info(
            f"Reservation countdown: **{countdown(reserved_seat['reserved_until'])}** remaining"
        )

    if checked_in_seat:
        st.success(
            f"You are checked in at seat **{checked_in_seat['code']}**. "
            f"You must rescan after {RECHECK_HOURS} hour(s)."
        )
        st.info(
            f"Check-in expires in: **{countdown(checked_in_seat['occupied_until'])}**"
        )


def render_seat_grid(token):
    seats_result = get_seats(token)
    if not seats_result["success"]:
        st.error(seats_result["message"])
        return [] #error check

    seats = seats_result["seats"] 

    st.subheader("Available Seats")

    cols_per_row = 4
    for i in range(0, len(seats), cols_per_row):
        row = seats[i:i + cols_per_row]
        cols = st.columns(cols_per_row)

        for col, seat in zip(cols, row):
            with col:
                color = seat_status_color(seat["status"])
                owner_note = ""
                if seat["reserved_by_me"]:
                    owner_note = "Reserved by you"
                elif seat["occupied_by_me"]:
                    owner_note = "Occupied by you"
                #state of the seat 
                st.markdown(
                    f"""
                    <div style="
                        border:1px solid #374151;
                        border-radius:12px;
                        padding:14px;
                        margin-bottom:8px;
                        background:#111827;
                    ">
                        <h4 style="margin:0 0 8px 0;">{seat['code']}</h4>
                        <p style="margin:0; font-weight:700; color:{color};">
                            {seat['status'].upper()}
                        </p>
                        <p style="margin:6px 0 0 0; color:#9ca3af;">
                            Floor {seat['floor']} • {seat['building']}
                        </p>
                        
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                if st.button(f"Select {seat['code']}", key=f"select_{seat['id']}"):
                    st.session_state["selected_seat_id"] = seat["id"]

    return seats


def render_seat_details(token, seats):
    st.subheader("Seat Details")

    selected_id = st.session_state.get("selected_seat_id")
    if not selected_id:
        st.info("Select a seat to see its details.")
        return

    seat = next((s for s in seats if s["id"] == selected_id), None)
    if not seat:
        st.warning("Selected seat not found.")
        return

    st.write(f"**Seat:** {seat['code']}")
    st.write(f"**Building:** {seat['building']}")
    st.write(f"**Floor:** {seat['floor']}")
    st.write(f"**Status:** {seat['status'].title()}")

    if seat["status"] == "free":
        st.success("This seat is free.")
        if st.button("Reserve this seat", key=f"reserve_{seat['id']}"):
            result = reserve_seat(token, seat["id"])
            if result["success"]:
                st.success(result["message"])
                st.info("You must scan the QR code within 10 minutes.")
                st.rerun()
            else:
                st.error(result["message"])

    elif seat["status"] == "reserved":
        if seat["reserved_by_me"]:
            st.warning("This seat is reserved by you.")
            st.info(f"Time left to scan QR: {countdown(seat['reserved_until'])}")

            col1, col2 = st.columns(2)

            with col1:
                if st.button("Simulate QR Scan / Check In", key=f"checkin_{seat['id']}"):
                    result = check_in_from_qr(token, seat["id"])
                    if result["success"]:
                        st.success(result["message"])
                        st.rerun()
                    else:
                        st.error(result["message"])

            with col2:
                if st.button("Cancel Reservation", key=f"cancel_{seat['id']}"):
                    result = cancel_reservation(token)
                    if result["success"]:
                        st.success(result["message"])
                        st.rerun()
                    else:
                        st.error(result["message"])

            st.caption("In the real app, this button is replaced by the real QR flow.")

        else:
            st.error("Someone else reserved it first.")

    elif seat["status"] == "occupied":
        if seat["occupied_by_me"]:
            st.success("You are currently occupying this seat.")
            st.info(f"Recheck countdown: {countdown(seat['occupied_until'])}")

            if st.button("Release Seat", key=f"release_{seat['id']}"):
                result = release_current_seat(token)
                if result["success"]:
                    st.success(result["message"])
                    st.rerun()
                else:
                    st.error(result["message"])
        else:
            st.error("This seat is already occupied.")


def main_app():
    require_login()

    # Auto-refresh every second so countdowns update
    st_autorefresh(interval=1000, key="seat_refresh")

    token = st.session_state["token"]

    render_top_bar()
    render_user_status(token)

    left, right = st.columns([2, 1])

    with left:
        seats = render_seat_grid(token)

    with right:
        render_seat_details(token, seats)


def main():
    init_auth_state()

    if is_logged_in():
        main_app()
    else:
        login_page()


if __name__ == "__main__":
    main()

    