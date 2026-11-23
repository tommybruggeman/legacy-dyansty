"""Settings > League tools > Owner activity on the website: the same console the app shows (api/console on our
server: charts, flows, funnels, retention, owners, session replays, saved reports and the AI analyst). It
loads inside the page with this account's sign-in; the server and the database only answer accounts in
app_activity_viewers (the league owner), so nobody else sees anything even with the link.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

import streamlit as st
import streamlit.components.v1 as components

API_URL = os.getenv("LEGACY_API_URL", "https://legacy-api-94901925561.us-central1.run.app").rstrip("/")
TZ = "America/Denver"


def is_viewer(client: Any, user_id: str | None) -> bool:
    if not user_id:
        return False
    try:
        return bool(client.table("app_activity_viewers").select("user_id").eq("user_id", user_id).limit(1).execute().data)
    except Exception:
        return False


def console_url(token: str, tz: str = TZ) -> str:
    # The sign-in rides in the #fragment, which browsers never send to a server; the page reads it and clears it.
    return f"{API_URL}/console#{urlencode({'t': token, 'tz': tz})}"


def render_owner_activity(client: Any, league_id: str, user_id: str | None) -> None:
    if not is_viewer(client, user_id):
        st.info("Owner activity is private to the league owner's account.")
        return
    from auth import ACCESS_KEY

    token = st.session_state.get(ACCESS_KEY)
    if not token:
        st.info("Sign in again to open Owner activity.")
        return
    st.caption("Only you see this. Every page, tap, session and replay from the app and the website, from our own database. "
               "If it says your sign-in expired, reload this page.")
    components.iframe(console_url(token), height=1100, scrolling=True)
