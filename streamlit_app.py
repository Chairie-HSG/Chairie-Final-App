"""
streamlit_app.py

Test UI for the Chairy backend.
Lets you create seats, check in/out, view seat status, and auto-free expired seats.
"""

import streamlit as st
import sys
import os
import datetime

# Make sure Python can find seat_manager and timer in the same folder
sys.path.insert(0, os.path.dirname(__file__))
from seat_manager import SeatManager

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Chairy – Backend Tester",
    page_icon="🪑",
    layout="wide",
)

# ── Custom CSS ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'DM Sans', sans-serif;
        background-color: #0f0f0f;
        color: #f0f0f0;
    }
    .stApp { background-color: #0f0f0f; }

    h1, h2, h3 {
        font-family: 'Space Mono', monospace;
        color: #f0f0f0;
    }

    .seat-card {
        background: #1a1a1a;
        border: 1px solid #2a2a2a;
        border-radius: 10px;
        padding: 12px;
        text-align: center;
        font-family: 'Space Mono', monospace;
        font-size: 12px;
        transition: all 0.2s;
    }
    .seat-free   { border-color: #00c896; color: #00c896; }
    .seat-taken  { border-color: #ff4b6e; color: #ff4b6e; }

    .stat-box {
        background: #1a1a1a;
        border: 1px solid #2a2a2a;
        border-radius: 12px;
        padding: 20px;
        text-align: center;
    }
    .stat-number {
        font-family: 'Space Mono', monospace;
        font-size: 2.5rem;
        font-weight: 700;
        line-height: 1;
    }
    .stat-label {
        font-size: 0.8rem;
        color: #888;
        margin-top: 4px;
        text-transform: uppercase;
        letter-spacing: 1px;
    }

    .success-msg { color: #00c896; font-weight: 600; }
    .error-msg   { color: #ff4b6e; font-weight: 600; }
    .info-msg    { color: #7eb8ff; font-weight: 600; }

    div[data-testid="stButton"] > button {
        background: #1a1a1a;
        border: 1px solid #333;
        color: #f0f0f0;
        border-radius: 8px;
        font-family: 'Space Mono', monospace;
        font-size: 13px;
        padding: 10px 20px;
        width: 100%;
        transition: all 0.2s;
    }
    div[data-testid="stButton"] > button:hover {
        border-color: #00c896;
        color: #00c896;
    }

    div[data-testid="stNumberInput"] input,
    div[data-testid="stTextInput"] input {
        background: #1a1a1a;
        border: 1px solid #333;
        color: #f0f0f0;
        border-radius: 8px;
        font-family: 'Space Mono', monospace;
    }

    .qr-badge {
        display: inline-block;
        background: #111;
        border: 1px solid #333;
        border-radius: 6px;
        padding: 4px 10px;
        font-family: 'Space Mono', monospace;
        font-size: 12px;
        color: #aaa;
        letter-spacing: 2px;
    }

    hr { border-color: #222; }
    .section-title {
        font-family: 'Space Mono', monospace;
        font-size: 0.7rem;
        color: #555;
        text-transform: uppercase;
        letter-spacing: 3px;
        margin-bottom: 16px;
    }
</style>
""", unsafe_allow_html=True)


# ── Session state init ──────────────────────────────────────────────────────────
if "manager" not in st.session_state:
    st.session_state.manager = None
if "log" not in st.session_state:
    st.session_state.log = []

def log(msg, kind="info"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    st.session_state.log.insert(0, (ts, msg, kind))
    if len(st.session_state.log) > 50:
        st.session_state.log.pop()

def get_manager():
    return st.session_state.manager


# ── Header ──────────────────────────────────────────────────────────────────────
st.markdown("# 🪑 Chairy")
st.markdown("<p style='color:#555; font-family:Space Mono; font-size:12px;'>BACKEND TEST INTERFACE</p>", unsafe_allow_html=True)
st.markdown("---")


# ── Sidebar – Create / Load seats ──────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Setup")
    st.markdown("<p class='section-title'>Seat Manager</p>", unsafe_allow_html=True)

    num_seats = st.number_input("Number of seats", min_value=1, max_value=500, value=20, step=1)
    state_file = st.text_input("State file", value="seat_state.json")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🆕 Create"):
            m = SeatManager(num_seats=num_seats, state_file=state_file)
            m.create_seats()
            st.session_state.manager = m
            log(f"Created {num_seats} seats → {state_file}", "success")
            st.rerun()

    with col2:
        if st.button("📂 Load"):
            m = SeatManager(num_seats=num_seats, state_file=state_file)
            st.session_state.manager = m
            log(f"Loaded state from {state_file}", "info")
            st.rerun()

    st.markdown("---")
    st.markdown("### 🕒 Auto-Expire")
    st.caption("Frees seats whose 2h reservation expired.")
    if st.button("♻️ Free Expired Seats"):
        m = get_manager()
        if m:
            from timer import free_expired_seats
            free_expired_seats(m.seats)
            m.save_state()
            log("Freed all expired seats", "info")
            st.rerun()
        else:
            st.warning("Create or load seats first.")

    st.markdown("---")
    st.markdown("### 📋 Activity Log")
    if st.session_state.log:
        for ts, msg, kind in st.session_state.log[:10]:
            color = {"success": "#00c896", "error": "#ff4b6e", "info": "#7eb8ff"}.get(kind, "#aaa")
            st.markdown(f"<span style='color:#555;font-size:10px;'>{ts}</span> <span style='color:{color};font-size:12px;'>{msg}</span>", unsafe_allow_html=True)
    else:
        st.caption("No activity yet.")


# ── Main area ───────────────────────────────────────────────────────────────────
m = get_manager()

if m is None:
    st.info("👈 Use the sidebar to **Create** or **Load** seats to get started.")
    st.stop()


# ── Stats row ───────────────────────────────────────────────────────────────────
total    = len(m.seats)
occupied = sum(1 for s in m.seats if s["occupied"])
free     = total - occupied
pct      = int((occupied / total) * 100) if total else 0

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f"""<div class='stat-box'>
        <div class='stat-number' style='color:#7eb8ff'>{total}</div>
        <div class='stat-label'>Total Seats</div>
    </div>""", unsafe_allow_html=True)
with c2:
    st.markdown(f"""<div class='stat-box'>
        <div class='stat-number' style='color:#00c896'>{free}</div>
        <div class='stat-label'>Available</div>
    </div>""", unsafe_allow_html=True)
with c3:
    st.markdown(f"""<div class='stat-box'>
        <div class='stat-number' style='color:#ff4b6e'>{occupied}</div>
        <div class='stat-label'>Occupied</div>
    </div>""", unsafe_allow_html=True)
with c4:
    st.markdown(f"""<div class='stat-box'>
        <div class='stat-number' style='color:#f0c040'>{pct}%</div>
        <div class='stat-label'>Occupancy</div>
    </div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Tabs ────────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["🗺️ Seat Map", "✅ Check In / Out", "🔍 Seat Details"])


# ── Tab 1: Seat Map ─────────────────────────────────────────────────────────────
with tab1:
    st.markdown("<p class='section-title'>Live Seat Map</p>", unsafe_allow_html=True)
    st.caption("🟢 Available  🔴 Occupied")

    cols_per_row = st.slider("Columns", min_value=5, max_value=20, value=10)
    cols = st.columns(cols_per_row)

    for i, seat in enumerate(m.seats):
        col = cols[i % cols_per_row]
        with col:
            status_class = "seat-taken" if seat["occupied"] else "seat-free"
            icon = "🔴" if seat["occupied"] else "🟢"
            st.markdown(f"""
                <div class='seat-card {status_class}'>
                    {icon}<br><b>#{seat['id']}</b>
                </div><br>
            """, unsafe_allow_html=True)


# ── Tab 2: Check In / Out ───────────────────────────────────────────────────────
with tab2:
    left, right = st.columns(2)

    with left:
        st.markdown("### ✅ Check In")
        st.caption("Enter a QR code to occupy a seat.")
        qr_in = st.text_input("QR Code", key="qr_in", placeholder="e.g. AB12CD34").upper()
        if st.button("Check In", key="btn_in"):
            if qr_in:
                success, msg = m.check_in(qr_in)
                if success:
                    log(f"Check-in: {qr_in} – {msg}", "success")
                    st.success(msg)
                else:
                    log(f"Check-in FAILED: {qr_in} – {msg}", "error")
                    st.error(msg)
                st.rerun()
            else:
                st.warning("Enter a QR code.")

    with right:
        st.markdown("### 🚪 Check Out")
        st.caption("Enter a QR code to free a seat.")
        qr_out = st.text_input("QR Code", key="qr_out", placeholder="e.g. AB12CD34").upper()
        if st.button("Check Out", key="btn_out"):
            if qr_out:
                success, msg = m.check_out(qr_out)
                if success:
                    log(f"Check-out: {qr_out} – {msg}", "success")
                    st.success(msg)
                else:
                    log(f"Check-out FAILED: {qr_out} – {msg}", "error")
                    st.error(msg)
                st.rerun()
            else:
                st.warning("Enter a QR code.")

    st.markdown("---")
    st.markdown("### 🎲 Quick Test — Use a Real QR Code")
    st.caption("Pick any seat below to copy its QR code for testing.")

    # Show all QR codes in a table
    search = st.text_input("Filter by seat ID or QR", placeholder="Search...").upper()
    filtered = [s for s in m.seats if search in str(s["id"]) or search in s["qr_code"]] if search else m.seats

    header = st.columns([1, 3, 2])
    header[0].markdown("**Seat**")
    header[1].markdown("**QR Code**")
    header[2].markdown("**Status**")

    for seat in filtered[:30]:
        row = st.columns([1, 3, 2])
        row[0].write(f"#{seat['id']}")
        row[1].code(seat['qr_code'])
        status = "🔴 Occupied" if seat["occupied"] else "🟢 Free"
        row[2].write(status)
        if seat["occupied"] and seat.get("check_in_time"):
            checked_in = datetime.datetime.fromisoformat(seat["check_in_time"])
            elapsed = datetime.datetime.now() - checked_in
            mins = int(elapsed.total_seconds() // 60)
            row[2].caption(f"{mins}m ago")

    if len(filtered) > 30:
        st.caption(f"Showing 30 of {len(filtered)} results.")


# ── Tab 3: Seat Details ──────────────────────────────────────────────────────────
with tab3:
    st.markdown("### 🔍 Look Up a Seat")
    seat_id = st.number_input("Seat ID", min_value=1, max_value=total, value=1, step=1)

    match = next((s for s in m.seats if s["id"] == seat_id), None)
    if match:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"**Seat ID:** `{match['id']}`")
            st.markdown(f"**QR Code:** `{match['qr_code']}`")
            status = "🔴 Occupied" if match["occupied"] else "🟢 Available"
            st.markdown(f"**Status:** {status}")
        with col_b:
            if match["occupied"] and match.get("check_in_time"):
                checked_in = datetime.datetime.fromisoformat(match["check_in_time"])
                elapsed = datetime.datetime.now() - checked_in
                remaining = datetime.timedelta(hours=2) - elapsed
                mins_elapsed = int(elapsed.total_seconds() // 60)
                mins_remaining = max(0, int(remaining.total_seconds() // 60))
                st.markdown(f"**Checked in:** `{checked_in.strftime('%H:%M:%S')}`")
                st.markdown(f"**Elapsed:** `{mins_elapsed} min`")
                st.markdown(f"**Expires in:** `{mins_remaining} min`")
                progress = min(1.0, elapsed.total_seconds() / 7200)
                st.progress(progress, text="2h reservation")
            else:
                st.markdown("_Seat is not currently occupied._")
    else:
        st.warning("Seat not found.")
