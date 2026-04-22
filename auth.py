import streamlit as st
from api import login_request, signup_request


def init_auth_state():
    defaults = {
        "logged_in": False,
        "username": None,          # for now this stores the user's email
        "token": None,
        "selected_seat_id": None,
        "auth_mode": "login",      # can be "login" or "signup"
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


def show_auth_switcher():
    col1, col2 = st.columns(2)

    with col1:
        if st.button("Login", use_container_width=True):
            st.session_state["auth_mode"] = "login"
            st.rerun()

    with col2:
        if st.button("Sign Up", use_container_width=True):
            st.session_state["auth_mode"] = "signup"
            st.rerun()


def login_page():
    st.title("Seat Booking System")
    show_auth_switcher()

    mode = st.session_state.get("auth_mode", "login")

    if mode == "login":
        st.subheader("Login")

        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")

        if submitted:
            if not email or not password:
                st.warning("Please enter both email and password.")
                return

            result = login_request(email, password)

            if result["success"]:
                login_user(result["username"], result["token"])
                st.success("Login successful.")
                st.rerun()
            else:
                st.error(result["message"])

    else:
        st.subheader("Sign Up")

        with st.form("signup_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            submitted = st.form_submit_button("Create Account")

        if submitted:
            if not email or not password or not confirm_password:
                st.warning("Please fill in all fields.")
                return

            if password != confirm_password:
                st.warning("Passwords do not match.")
                return

            if len(password) < 6:
                st.warning("Password should be at least 6 characters.")
                return

            result = signup_request(email, password)

            if result["success"]:
                st.success(result["message"])
                st.info("Go back to Login and sign in with your new account.")
                st.session_state["auth_mode"] = "login"
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