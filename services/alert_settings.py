"""Website Settings > Alerts: the same per-owner alert switches as the iOS app (push_prefs), so an owner can turn
alerts on or off from either place. The wording of every alert lives in api/alerts.py."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st

from api import alerts

ZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles",
         "America/Anchorage", "Pacific/Honolulu"]


def render_alert_settings(sb: Any, *, league_id: str, user_id: str) -> None:
    try:
        rows = sb.table("push_prefs").select("off,tz").eq("user_id", user_id).eq("league_id", league_id).limit(1).execute().data or []
    except Exception:
        st.info("Alerts aren't switched on yet. The commissioner needs to run the push alerts SQL.")
        return
    view = alerts.settings_view(rows[0] if rows else None)
    st.caption("Alerts go to the Legacy Dynasty iPhone app. These switches are the same ones in the app's Settings > Alerts.")
    with st.form("alert_settings"):
        picks = {}
        for k in view["kinds"]:
            picks[k["kind"]] = st.toggle(k["label"], value=k["on"], help=k["note"], key=f"alert_{k['kind']}")
        tz_now = view["tz"] or alerts.LEAGUE_TZ.key
        tz = st.selectbox("Your time zone (for quiet hours)", ZONES, index=ZONES.index(tz_now) if tz_now in ZONES else 2)
        st.caption(f"Quiet hours: nothing arrives {view['quiet_hours']} your time, except a starter ruled inactive right "
                   f"before kickoff. At most {view['weekly_limit']} alerts a week (over-the-cap warnings don't count).")
        if st.form_submit_button("Save alerts", type="primary"):
            row = {"user_id": user_id, "league_id": league_id, "off": alerts.clean_off(k for k, on in picks.items() if not on),
                   "tz": tz, "updated_at": datetime.now(timezone.utc).isoformat()}
            try:
                existing = sb.table("push_prefs").select("user_id").eq("user_id", user_id).eq("league_id", league_id).execute().data
                if existing:
                    sb.table("push_prefs").update(row).eq("user_id", user_id).eq("league_id", league_id).execute()
                else:
                    sb.table("push_prefs").insert(row).execute()
                st.success("Saved.")
            except Exception:
                st.error("Your alert settings weren't saved. Try again.")
