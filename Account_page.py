"""
account_page.py
Account tab for Chairie – shows profile info, study statistics, and
current seat status.
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


def _get_study_stats(token):
    """Pull weekly hours / total hours / session count from the
    `study_sessions` table via streamlit_app.get_user_study_stats().

    Note: the previous version of this file pulled study time from
    `profiles.total_hours_studied`, but nothing in the codebase ever
    writes to that column — hours always read as 0. The
    `study_sessions` table is the actual source of truth, populated
    by check_in_from_qr / release_current_seat in streamlit_app.py.
    """
    try:
        from streamlit_app import get_user_study_stats
        return get_user_study_stats(token)
    except Exception:
        return None


def render_account_page(token):
    st.title("My Account")

    profile = _get_profile(token)
    status  = _get_status(token)
    stats   = _get_study_stats(token)

    full_name = (profile or {}).get("full_name", "")
    gender    = (profile or {}).get("gender", "Prefer not to say")
    email     = st.session_state.get("username", "")

    # ── Avatar + name ─────────────────────────────────────────
    avatar = {"Female": "👩", "Male": "👨"}.get(gender, "🧑")
    st.markdown(f"## {avatar} {full_name or email}")
    st.caption(email)
    st.divider()

    # ── Study statistics ──────────────────────────────────────
    # Three cards: weekly hours, all-time hours, and session count.
    # If get_user_study_stats fails or returns None (e.g. Supabase
    # is unreachable), the cards render with zeros — same fallback
    # as the original "Hours Studied" tile.
    weekly_hours = (stats or {}).get("weekly_hours", 0)
    total_hours  = (stats or {}).get("total_hours",  0)
    sessions_n   = (stats or {}).get("sessions",     0)

    col1, col2, col3 = st.columns(3)
    col1.metric("Hours this week",     f"{weekly_hours}h")
    col2.metric("Total hours studied", f"{total_hours}h")
    col3.metric("Study sessions",      sessions_n)

    if not stats:
        st.caption("No study sessions yet — check into a seat to "
                   "start tracking your hours.")

    # ── Current seat ──────────────────────────────────────────
    current_seat = "None"
    if status.get("success"):
        if status.get("checked_in_seat"):
            current_seat = status["checked_in_seat"]["code"] + " (checked in)"
        elif status.get("reserved_seat"):
            current_seat = status["reserved_seat"]["code"] + " (reserved)"
    st.markdown(f"**Current seat:** {current_seat}")
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
