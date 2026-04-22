
import streamlit as st
from api import login_request


def init_auth_state():
    defaults = {
        "logged_in": False,
        "username": None,
        "token": None,
        "selected_seat_id": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def is_logged_in():
    return st.session_state.get("logged_in", False)


def login_user(username, token):
    st.session_state["logged_in"] = True
    st.session_state["username"] = username
    st.session_state["token"] = token


def logout_user():
    st.session_state["logged_in"] = False
    st.session_state["username"] = None
    st.session_state["token"] = None
    st.session_state["selected_seat_id"] = None


def login_page():
    st.title("Login")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

    if submitted:
        if not username or not password:
            st.warning("Please enter both username and password.")
            return

        result = login_request(username, password)

        if result["success"]:
            login_user(result["username"], result["token"])
            st.success("Login successful.")
            st.rerun()
        else:
            st.error(result["message"])


def logout_button():
    if st.button("Logout"):
        logout_user()
        st.rerun()


def require_login():
    if not is_logged_in():
        st.warning("Please log in first.")
        st.stop()
