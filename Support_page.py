"""
support_page.py
Support tab for Chairie – contact info and FAQ.
"""

import streamlit as st


def render_support_page():
    st.title("Support & Contact")

    # ── Contact info ──────────────────────────────────────────
    st.subheader("Contact Us")
    st.write("📧 General:  support@chairie.app")
    st.write("🐛 Bugs:     bugs@chairie.app")
    st.write("🏛️ Library:  library@unisg.ch  |  +41 71 224 22 96")
    st.write("📍 Address:  Dufourstrasse 50, 9000 St. Gallen")
    st.divider()

    # ── Opening hours ─────────────────────────────────────────
    st.subheader("Library Opening Hours")
    st.write("Monday – Friday:  08:00 – 22:00")
    st.write("Saturday:         09:00 – 18:00")
    st.write("Sunday:           10:00 – 18:00")
    st.divider()

    # ── FAQ ───────────────────────────────────────────────────
    st.subheader("FAQ")

    with st.expander("How long can I reserve a seat?"):
        st.write("10 minutes. Scan the QR code at the seat before time runs out or the seat is released.")

    with st.expander("How long can I stay once checked in?"):
        st.write("2 hours. You can re-check in afterwards to extend your session.")

    with st.expander("Can I reserve multiple seats?"):
        st.write("No, each account can hold one reservation or check-in at a time.")

    with st.expander("What if the QR scanner doesn't work?"):
        st.write("Make sure you're logged in and camera permission is granted. If it still fails, email bugs@chairie.app.")

    with st.expander("How do I reset my password?"):
        st.write("Use the 'Forgot password?' link on the login page and check your email for a reset link.")