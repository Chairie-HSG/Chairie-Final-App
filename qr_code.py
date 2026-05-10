import streamlit as st
from PIL import Image



def decode_qr(image: Image.Image):
    """Decode a QR code from a PIL image. Returns the string or None."""
    try:
        import zxingcpp
        import numpy as np
        arr = np.array(image)
        results = zxingcpp.read_barcodes(arr)
        if results:
            return results[0].text
        return None
    except Exception:
        return None

def extract_seat_code(qr_string: str):
    """Extract seat code from QR string. 'SEAT:A-14' or 'A-14' -> 'A-14'."""
    qr_string = qr_string.strip()
    if qr_string.upper().startswith("SEAT:"):
        return qr_string.split(":", 1)[1].strip() or None
    return qr_string or None


def _do_checkin(token, expected_seat_id, expected_seat_code, entered_code, check_in_fn):
    """Validate code and check in."""
    if entered_code.lower() != expected_seat_code.lower():
        st.error(f"Wrong code! You entered '{entered_code}' but your seat code is '{expected_seat_code}'.")
        return

    st.success(f"Code matches your reservation for seat {expected_seat_code}!")

    if st.button("Confirm Check-In", key="confirm_checkin_btn"):
        result = check_in_fn(token, expected_seat_id)
        if result["success"]:
            st.success(result["message"])
            st.rerun()
        else:
            st.error(result["message"])


def show_checkin(token, expected_seat_id, expected_seat_code, check_in_fn):
    """Main check-in UI with QR camera scan and manual code fallback."""
    st.markdown("### Check In")
    st.caption("Scan the QR code on your seat, or type the number code printed under it.")

    # Option 1: Camera QR scan
    st.markdown("**Option 1: Scan QR code**")
    photo = st.camera_input("Point your camera at the QR code on the seat")

    if photo is not None:
        image = Image.open(photo).convert("RGB")
        qr_string = decode_qr(image)

        if not qr_string:
            st.warning("No QR code detected. Try again or use the code below.")
        else:
            scanned_code = extract_seat_code(qr_string)
            if scanned_code:
                _do_checkin(token, expected_seat_id, expected_seat_code, scanned_code, check_in_fn)
                return

    # Option 2: Manual code input
    st.markdown("---")
    st.markdown("**Option 2: Type the code printed under the QR**")
    entered_code = st.text_input("Seat code", placeholder="e.g. A-14", key="checkin_code_input").strip()

    if not entered_code:
        return

    _do_checkin(token, expected_seat_id, expected_seat_code, entered_code, check_in_fn)
