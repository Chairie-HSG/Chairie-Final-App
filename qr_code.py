import streamlit as st
import streamlit.components.v1 as components
from PIL import Image


def prepare_back_camera():
    """Bias `st.camera_input` toward the device's BACK (rear-facing)
    camera instead of the front-facing selfie camera.

    Background:
    -----------
    `st.camera_input` does NOT expose a `facingMode` parameter — the
    browser picks the camera using the OS default, which on most
    phones is the front camera. That's wrong for QR scanning: the QR
    sticker is on the seat, in front of the user, not behind them.

    Workaround:
    -----------
    We patch `navigator.mediaDevices.getUserMedia` on the parent
    document before `st.camera_input` mounts. When `camera_input`
    calls `getUserMedia({ video: true })` (or with any other video
    constraint), the patched function rewrites the request to
    include `facingMode: 'environment'`. The browser then prefers
    the rear camera if one exists.

    We use `facingMode: 'environment'` (a *preference*) rather than
    `facingMode: { exact: 'environment' }` (a *strict requirement*)
    so the camera still works on desktops / laptops that only have a
    front-facing webcam — there, the browser ignores the preference
    and uses the only camera available, instead of failing outright.

    Idempotency:
    ------------
    Streamlit reruns the script on every interaction, so this
    function may be called many times per session. The patch flag
    `md._chairieBackCameraPatched` lives on the parent's
    `navigator.mediaDevices` object (which is NOT reset across
    reruns), so the override is applied exactly once per page load.

    Call this BEFORE `st.camera_input(...)` in any flow that opens
    the camera for QR scanning.
    """
    components.html(
        """
        <script>
        (function () {
          // The Streamlit script runs inside an iframe; the actual
          // page (where camera_input lives) is the parent document.
          const parent = window.parent;
          if (!parent || !parent.navigator || !parent.navigator.mediaDevices) {
            return;
          }
          const md = parent.navigator.mediaDevices;

          // Already patched on this page? Bail out — don't stack
          // wrappers, which would build up after each Streamlit rerun.
          if (md._chairieBackCameraPatched) {
            return;
          }
          md._chairieBackCameraPatched = true;

          const orig = md.getUserMedia.bind(md);
          md.getUserMedia = function (constraints) {
            constraints = constraints || {};
            // Three shapes camera_input might hand us:
            //   1. { video: true }              → upgrade to object form
            //   2. { video: undefined / null }  → same as (1)
            //   3. { video: { ... } }           → respect existing keys
            //                                     but inject facingMode
            if (constraints.video === true || constraints.video == null) {
              constraints.video = { facingMode: 'environment' };
            } else if (typeof constraints.video === 'object') {
              if (!constraints.video.facingMode) {
                constraints.video.facingMode = 'environment';
              }
            }
            return orig(constraints);
          };
        })();
        </script>
        """,
        height=0,
    )


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
    # Force the back/photo camera instead of the front/selfie camera
    # — must run before the camera_input widget mounts.
    prepare_back_camera()
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
