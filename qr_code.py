import streamlit as st
from PIL import Image


def decode_qr(image: Image.Image):
    """Decode a QR code from a PIL image. Returns the string or None. Uses zxing-cpp library based on Google's ZXing engine"""
    try:
        import zxingcpp
        import numpy as np
        arr = np.array(image) 
        #Convert PIL image to NumPy array so zxingcpp can process it

        results = zxingcpp.read_barcodes(arr)
        #Scan the image for any barcodes/QR codes

        if results:
            return results[0].text
        #Return the text content of the first detected QR code

        return None
    #no QR code found in the image

    except Exception:
        return None
    #If zxingcpp is not installed or fails for any reason return none

def extract_seat_code(qr_string: str):
    """Parses the seat code out of a decoded QR string. 
    Accepted formats are "SEAT:A2" (standard)
    and "A2" (bare code)"""
    qr_string = qr_string.strip()
    #remove accidental whitespace

    if qr_string.upper().startswith("SEAT:"):
        return qr_string.split(":", 1)[1].strip() or None
        #Standard format: "Seat:<code>", split on colon and take the right side

    return qr_string or None
    #Fallback: accept bare code directly


def _do_checkin(token, expected_seat_id, expected_seat_code, entered_code, check_in_fn):
    """Shared check-in logic used by both the QR scan and manual code input methods. Validates that entered code matches the reservation, then calls check_in_function."""

    if entered_code.lower() != expected_seat_code.lower():
        #Compare codes case-insensitively so "a2" and "A2" both work
        st.error(f"Wrong code! You entered '{entered_code}' but your seat code is '{expected_seat_code}'.")
        return
    

    st.success(f"Code matches your reservation for seat {expected_seat_code}!")
    #Code matches, shows green success message

    if st.button("Confirm Check-In", key="confirm_checkin_btn"):
        #Show confirm button so user consciously completes check-in

        result = check_in_fn(token, expected_seat_id)
        #Call the supabase function to mark the seat as occupied

        if result["success"]:
            st.success(result["message"])
            st.rerun()
            #Refresh app to show updated seat as occupied

        else:
            st.error(result["message"])


def show_checkin(token, expected_seat_id, expected_seat_code, check_in_fn):
    """Main check-in UI with QR camera scan and manual code fallback."""
    st.markdown("### Check In")
    st.caption("Scan the QR code on your seat, or type the number code printed under it.")

    # Option 1: Camera QR scan
    st.markdown("**Option 1: Scan QR code**")
    photo = st.camera_input("Point your camera at the QR code on the seat")
    #Camera_input opens device camera

    if photo is not None:
        image = Image.open(photo).convert("RGB")
        #User took a photo, open it as PIL image for processing

        qr_string = decode_qr(image)
        #Try to decode Qr code from the photo

        if not qr_string:
            st.warning("No QR code detected. Try again or use the code below.")
            #No QR deteced, warn user and let them use option 2

        else:
            scanned_code = extract_seat_code(qr_string)
            #QR detected, extract the seat code from it

            if scanned_code:
                _do_checkin(token, expected_seat_id, expected_seat_code, scanned_code, check_in_fn)
                #Validate check in using the scanned code

                return
                #Stop here so option 2 doesn't also appear

    # Option 2: Manual code input
    st.markdown("---")
    st.markdown("**Option 2: Type the code printed under the QR**")
    entered_code = st.text_input("Seat code", placeholder="e.g. A-14", key="checkin_code_input").strip()
    #Text box for user to type their seat code manually

    if not entered_code:
        return
        #Nothing typed yet, wait for input

    _do_checkin(token, expected_seat_id, expected_seat_code, entered_code, check_in_fn)
    #Validate and check in using the manually entered code
