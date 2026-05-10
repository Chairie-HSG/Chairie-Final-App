import streamlit as st

def show_checkin(token: str, expected_seat_id, expected_seat_code:str, check_in_fn):
    """Render a simple text-based check-in UI inside a Streamlit page.
    The user types in the code printed on their physical seat.
    If it matches the reservation they can confirm check-in."""
    st.markdown ("### Check in")
    st.caption("Type the code printed on your seat to confirm you are there")

    #User types seat code
    entered_code = st.text_input(
        "Seat_code", 
        placeholder = "e.g. A-14",
         key="checkin_code_input",
         ).strip() #strip whitespaces so small typos don't matter
    
    if not entered_code:
        #Nothing typed so waiting
        return
    
    #Validate entered code matches reservation
    if entered_code.lower() != expected_seat_code.lower():
        st.error(
            f"Wrong code! You entered '{entered_code}'"
        )
        return
    
    #Confirm and check in
    st.success(f"Code matches your reservation for seat {expected_seat_code} !")

    #Confirm button so the user consciously completes check in
    if st.button("Confirm Check-in"):
        result = check_in_fn(token, expected_seat_id) #mark seat as occupied in Supabase

        if result["success"]:
            st.success(result["message"])
            st.rerun() #refresh app to show updated seat status
        else:
            st.error(result["message"])

#test run lol
def fake_check_in(token, seat_id):
    return {"success": True, "message": f"Checked in to seat {seat_id}!"}

show_checkin(
    token="fake_token",
    expected_seat_id=42,
    expected_seat_code="A-14",
    check_in_fn=fake_check_in,
)