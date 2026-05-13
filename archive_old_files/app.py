
from datetime import datetime, timezone  #Imports tools for date/time and streamlit
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
from interactive_map import load_map_data, render_interactive_map

from auth import init_auth_state, login_page, logout_button, is_logged_in, require_login  #Imports login data from auth.py
from api import (  #Imports Seat data from api.py
    RESERVATION_MINUTES,
    RECHECK_HOURS,
    get_seats,
    get_user_status,
    reserve_seat,
    cancel_reservation,
    check_in_from_qr,
    release_current_seat,
    get_dashboard_stats,
    get_occupancy_prediction,
)

st.set_page_config(page_title="ChairY", layout="wide")


def seconds_left(iso_value):
    if not iso_value: #check if time is left from expiry
        return 0
    target = datetime.fromisoformat(iso_value) 
    now = datetime.now(timezone.utc)  #current time
    diff = int((target - now).total_seconds())
    return max(diff, 0) #returns the difference between the time the reservation is over, and shows 0 if its negative.


def countdown(iso_value):  #Time formatting
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
        st.caption("Chari Demo")
    with col2:
        st.write("")
        st.write(f"Logged in as: **{st.session_state['username']}**")
        logout_button()


def render_user_status(token): #Checks with api.py of user status
    result = get_user_status(token)
    if not result["success"]:
        st.error(result["message"])
        return   #error check

    reserved_seat = result["reserved_seat"]
    checked_in_seat = result["checked_in_seat"] #User seat status

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

def merge_map_with_supabase(map_seats, supabase_seats):
    # Supabase IDs may not match the visual map IDs.
    # So we mainly match seats using the seat code like A1, A2, A3.

    supabase_by_code = {
        str(seat["code"]).upper(): seat
        for seat in supabase_seats
        if seat.get("code") is not None
    }

    merged = []

    for map_seat in map_seats:
        db_seat = None

        # Try direct code fields first.
        possible_code = (
            map_seat.get("code")
            or map_seat.get("seat")
            or map_seat.get("seat_code")
            or map_seat.get("label")
        )

        if possible_code:
            db_seat = supabase_by_code.get(str(possible_code).upper())

        # If the map only has numeric id=1, convert that into A1.
        if not db_seat:
            try:
                possible_code = "A" + str(int(map_seat.get("id")))
                db_seat = supabase_by_code.get(possible_code.upper())
            except Exception:
                pass

        if db_seat:
            merged.append({
                **map_seat,

                # Use the real Supabase database ID for actions/reservations.
                "id": db_seat["id"],

                # Use the real database information.
                "code": db_seat["code"],
                "building": db_seat["building"],
                "floor": db_seat["floor"],
                "status": db_seat["status"],
                "reserved_until": db_seat.get("reserved_until"),
                "occupied_until": db_seat.get("occupied_until"),
                "reserved_by_me": db_seat.get("reserved_by_me", False),
                "occupied_by_me": db_seat.get("occupied_by_me", False),

                # Keep visual coordinates from the map JSON.
                "x": map_seat.get("x"),
                "y": map_seat.get("y"),
            })

    return merged
    # This combines the visual map coordinates with the real Supabase statuses.
    # map_seats contains x/y positions.
    # supabase_seats contains live reservation status.
    
    supabase_by_code = {
        seat["code"]: seat
        for seat in supabase_seats
    }

    merged = []

    for map_seat in map_seats:
        seat_code = str(map_seat.get("code") or map_seat.get("seat") or map_seat.get("id"))

        db_seat = supabase_by_code.get(seat_code)

        if db_seat:
            merged.append({
                **map_seat,
                "id": db_seat["id"],
                "code": db_seat["code"],
                "building": db_seat["building"],
                "floor": db_seat["floor"],
                "status": db_seat["status"],
                "reserved_until": db_seat.get("reserved_until"),
                "occupied_until": db_seat.get("occupied_until"),
                "reserved_by_me": db_seat.get("reserved_by_me", False),
                "occupied_by_me": db_seat.get("occupied_by_me", False),
            })

    return merged

def render_seat_grid(token):
    seats_result = get_seats(token)

    if not seats_result["success"]:
        st.error(seats_result["message"])
        return []

    supabase_seats = seats_result["seats"]

    st.subheader("Find a Seat")

    selected_floor = st.selectbox(
        "Choose floor",
        ["Ground Floor", "Floor 1"],
        key="floor_selector"
    )

    if selected_floor == "Ground Floor":
        image_path = "Library_GFloor.jpg"
        json_path = "library_map_data (1).json"
        layout_canvas_size = (1300, 848)
    else:
        image_path = "Library_1Floor.jpg"
        json_path = "library_map_data_floor1.json"
        layout_canvas_size = (1300, 848)

    map_data = load_map_data(json_path=json_path)

    if not map_data:
        st.error(f"Map data could not be loaded for {selected_floor}.")
        return supabase_seats

    map_seats = map_data.get("seats", [])

    floor_supabase_seats = [
        seat for seat in supabase_seats
        if seat["floor"] == selected_floor
    ]

    merged_seats = merge_map_with_supabase(map_seats, floor_supabase_seats)

    clicked = render_interactive_map(
        merged_seats,
        selected_seat_id=st.session_state.get("selected_seat_id"),
        image_path=image_path,
        layout_canvas_size=layout_canvas_size,
        height=650,
        key=f"library_map_{selected_floor}",
    )

    if clicked:
        st.session_state["selected_seat_id"] = clicked["id"]
        st.rerun()

    return supabase_seats
    # Get real live seat data from Supabase.
    seats_result = get_seats(token)

    if not seats_result["success"]:
        st.error(seats_result["message"])
        return []

    supabase_seats = seats_result["seats"]

    # Load visual seat coordinates from JSON.
    map_data = load_map_data()

    if not map_data:
        st.error("Map data could not be loaded.")
        return supabase_seats

    map_seats = map_data.get("seats", [])


    # Combine the visual coordinates with the real Supabase status.
    merged_seats = merge_map_with_supabase(map_seats, supabase_seats)
    

    st.subheader("Find a Seat")

    clicked = render_interactive_map(
        merged_seats,
        selected_seat_id=st.session_state.get("selected_seat_id"),
        image_path="Library_GFloor.jpg",
        layout_canvas_size=(1300, 848),
        height=650,
    )

    if clicked:
        st.session_state["selected_seat_id"] = clicked["id"]
        st.rerun()

    return supabase_seats
    seats_result = get_seats(token) #get all seats from the database
    if not seats_result["success"]:
        st.error(seats_result["message"])
        return [] #error check

    seats = seats_result["seats"] #List of seats

    st.subheader("Available Seats")

    cols_per_row = 4
    for i in range(0, len(seats), cols_per_row):   
        row = seats[i:i + cols_per_row]
        cols = st.columns(cols_per_row)

        for col, seat in zip(cols, row):     #display 1 seat per column
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
                    """,   #HTML visual of the seats
                    unsafe_allow_html=True
                )
                 # When user clicks Select, store the chosen seat in session_state.
                if st.button(f"Select {seat['code']}", key=f"select_{seat['id']}"):
                    st.session_state["selected_seat_id"] = seat["id"]

    return seats

def render_seat_details(token, seats):
    st.subheader("Seat Details")

    selected_id = st.session_state.get("selected_seat_id") #Reads which is the selected seat
    if not selected_id:
        st.info("Select a seat to see its details.")
        return

    seat = next((s for s in seats if s["id"] == selected_id), None) #Finds it in the seat list
    if not seat:
        st.warning("Selected seat not found.")
        return
    #display seat info
    st.write(f"**Seat:** {seat['code']}")
    st.write(f"**Building:** {seat['building']}")
    st.write(f"**Floor:** {seat['floor']}")
    st.write(f"**Status:** {seat['status'].title()}")

    if seat["status"] == "free": #Check status -> if free can reserve
        st.success("This seat is free.")
        if st.button("Reserve this seat", key=f"reserve_{seat['id']}"):
            result = reserve_seat(token, seat["id"]) 
            if result["success"]:
                st.success(result["message"])
                st.info("You must scan the QR code within 10 minutes.")
                st.rerun()
            else:
                st.error(result["message"])

    elif seat["status"] == "reserved": #Check status -> if reserved can't reserve
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
                if st.button("Cancel Reservation", key=f"cancel_{seat['id']}"): #cancel button
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

def render_dashboard_page(token):
    st.title("ChairY Dashboard")
    st.caption("Overview of current and predicted seat usage")

    stats = get_dashboard_stats()

    if not stats["success"]:
        st.error(stats["message"])
        return

    st.metric(
        label="Seats taken right now",
        value=f"{stats['percent_taken']}%",
        delta=f"{stats['taken']} out of {stats['total']} seats"
    )

    st.divider()

    st.subheader("Predicted busy times today")

    prediction = get_occupancy_prediction()

    if not prediction["success"]:
        st.info(prediction["message"])
    else:
        df = pd.DataFrame(prediction["predictions"])
        df = df.set_index("hour")
        st.line_chart(df["predicted_occupied_percent"])

    st.divider()

    if st.button("Find Seat"):
        st.session_state["page"] = "seats"
        st.rerun()

def main_app():
    require_login()

    st_autorefresh(interval=1000, key="seat_refresh")

    token = st.session_state["token"]

    render_top_bar()

    page = st.session_state.get("page", "dashboard")

    if page == "dashboard":
        render_dashboard_page(token)

    elif page == "seats":
        if st.button("Back to Dashboard"):
            st.session_state["page"] = "dashboard"
            st.rerun()

        render_user_status(token)

        left, right = st.columns([2, 1])

        with left:
            seats = render_seat_grid(token)

        with right:
            render_seat_details(token, seats)

def main(): 
    init_auth_state()

    if is_logged_in(): #check login
        main_app()
    else:
        login_page()


if __name__ == "__main__": 
    main()

