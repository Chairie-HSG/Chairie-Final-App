import streamlit as st
from qr_code import show_checkin

def fake_check_in(token, seat_id):
    return {"success": True, "message": f"Checked in to seat {seat_id}!"}

show_checkin(
    token="fake_token",
    expected_seat_id=42,
    expected_seat_code="A-14",
    check_in_fn=fake_check_in,
)