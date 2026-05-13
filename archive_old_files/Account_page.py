"""
account_page.py
Account tab for Chairie – shows profile info and current seat status.
"""

import streamlit as st


def _get_profile(token):
    try:
        from streamlit_app import supabase, SUPABASE_OK, _email_from_token
        if not SUPABASE_OK:
            return None
        email = _email_from_token(token)
        resp = supabase.table("profiles").select("*").eq("email", email).limit(1).execute()
        return resp.data[0] if resp.data else None
    except Exception:
        return None


def _save_profile(token, full_name, gender):
    try:
        from streamlit_app import supabase, SUPABASE_OK, _email_from_token
        if not SUPABASE_OK:
            return False
        email = _email_from_token(token)
        supabase.table("profiles").upsert(
            {"email": email, "full_name": full_name, "gender": gender},
            on_conflict="email"
        ).execute()
        return True
    except Exception:
        return False


def _get_status(token):
    try:
        from streamlit_app import get_user_status
        return get_user_status(token)
    except Exception:
        return {"success": False}


def render_account_page(token):
    st.title("My Account")

    profile = _get_profile(token)
    status  = _get_status(token)

    full_name = (profile or {}).get("full_name", "")
    gender    = (profile or {}).get("gender", "Prefer not to say")
    hours     = (profile or {}).get("total_hours_studied", 0) or 0
    email     = st.session_state.get("username", "")

    # ── Avatar + name ─────────────────────────────────────────
    avatar = {"Female": "👩", "Male": "👨"}.get(gender, "🧑")
    st.markdown(f"## {avatar} {full_name or email}")
    st.caption(email)
    st.divider()

    # ── Stats ─────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    col1.metric("Hours Studied", int(hours))

    current_seat = "None"
    if status.get("success"):
        if status.get("checked_in_seat"):
            current_seat = status["checked_in_seat"]["code"] + " (checked in)"
        elif status.get("reserved_seat"):
            current_seat = status["reserved_seat"]["code"] + " (reserved)"
    col2.metric("Current Seat", current_seat)
    st.divider()

    # ── Edit profile ──────────────────────────────────────────
    st.subheader("Personal Information")
    gender_options = ["Female", "Male", "Prefer not to say"]

    with st.form("profile_form"):
        new_name   = st.text_input("Full name", value=full_name)
        new_gender = st.selectbox("Gender", gender_options, index=gender_options.index(gender))
        if st.form_submit_button("Save"):
            if _save_profile(token, new_name, new_gender):
                st.success("Profile saved!")
                st.rerun()
            else:
                st.error("Could not save. Check your Supabase `profiles` table.")