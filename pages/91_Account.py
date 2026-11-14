# pages/91_Account.py
# ------------------------------------------------------------
# Account: sign out, privacy policy, help, and delete my account.
# Same delete as the app's Settings > Delete account (api/account.py).
# ------------------------------------------------------------
from pathlib import Path

import streamlit as st

from auth import current_user, require_login, sign_out
from components.sidebar_nav import render_nav
from services.account_web import delete_account

ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"
SITE = "https://tommybruggeman.github.io/legacy-dyansty"

st.set_page_config(page_title="Legacy Dynasty — Account", page_icon=str(ICON), layout="centered")
render_nav()
require_login("home.py")

user = current_user() or {}
st.title("Account")
st.caption(f"Signed in as {user.get('email', '')}")

st.markdown(f"[Help and support]({SITE}/support.html) · [Privacy policy]({SITE}/privacy.html)")

if st.button("Sign out", use_container_width=True):
    sign_out()
    st.rerun()

st.divider()
st.subheader("Delete account")
st.write(
    "This deletes your sign-in, your Front Office chats, saved targets, GM settings, alert settings and your link to your team. "
    "League records such as past contracts, trades and lineups stay in the league history. This can't be undone."
)
typed = st.text_input("Type DELETE to confirm", key="delete_confirm")
if st.button("Delete my account", type="primary", disabled=typed.strip().upper() != "DELETE"):
    problem = delete_account()
    if problem:
        st.error(problem)
    else:
        sign_out()
        st.success("Your account is deleted.")
        st.stop()
