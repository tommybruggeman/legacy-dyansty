"""Website Settings > My Team: the same team profile and Front Office preferences as the app's Settings
(api/owner_settings.py), so both halves show the same names and pictures and the GM answers the same way."""
from __future__ import annotations

import io
import time
from typing import Any

import streamlit as st

from api import owner_settings

BUCKET = "owner-pictures"
SIZE = 400


def _my_team(client: Any, league_id: str, user_id: str) -> dict[str, Any] | None:
    rows = (client.table("league_memberships").select("league_team_id,role").eq("league_id", league_id)
            .eq("user_id", user_id).limit(1).execute().data or [])
    if not rows or not rows[0].get("league_team_id"):
        return None
    team_id = rows[0]["league_team_id"]
    team = (client.table("league_teams").select("owner_name,team_name").eq("id", team_id).limit(1).execute().data or [{}])[0]
    return {"league_id": league_id, "team_id": team_id, "role": rows[0].get("role"), **team}


def _square_jpeg(raw: bytes) -> bytes:
    from PIL import Image, ImageOps
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    image = ImageOps.fit(image, (SIZE, SIZE))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=80)
    return out.getvalue()


def render_my_team_settings(client: Any, league_id: str, user_id: str) -> None:
    team = _my_team(client, league_id, user_id)
    if not team:
        st.info("Your account isn't linked to a team yet.")
        return
    profile = owner_settings.profile_view(client, team)

    st.markdown("#### Your team")
    left, right = st.columns([1, 3])
    with left:
        if profile.get("picture_url"):
            st.image(profile["picture_url"], width=110)
        upload = st.file_uploader("Picture", type=["jpg", "jpeg", "png"], key="profile_picture")
        remove = st.checkbox("Remove picture", value=False, disabled=not profile.get("picture_url"))
    with right:
        with st.form("my_team_profile", border=False):
            team_name = st.text_input("Team name", value=profile["team_name"], max_chars=40)
            abbr = st.text_input("Short name (2-4 letters, shows where space is tight)", value=profile["team_abbr"], max_chars=4)
            name = st.text_input("Your name", value=profile["display_name"], max_chars=40)
            saved = st.form_submit_button("Save", type="primary")
    if saved:
        body: dict[str, Any] = {"display_name": name, "team_name": team_name, "team_abbr": abbr}
        try:
            if upload is not None:
                path = f"{team['team_id']}/{int(time.time() * 1000)}.jpg"
                client.storage.from_(BUCKET).upload(path, _square_jpeg(upload.getvalue()), {"content-type": "image/jpeg", "upsert": "true"})
                body["picture_url"] = client.storage.from_(BUCKET).get_public_url(path).rstrip("?")
            elif remove:
                body["picture_url"] = None
            owner_settings.save_profile(client, team, body)
            st.cache_data.clear()
            st.success("Saved. Your team shows this everywhere in the app and on the website.")
        except Exception as exc:
            st.error(str(exc) if isinstance(exc, ValueError) else "That didn't save. Try again.")

    st.markdown("#### Front Office")
    prefs = owner_settings.gm_prefs(client, user_id)
    c1, c2 = st.columns(2)
    length = c1.radio("Answers", ["short", "detailed"], index=["short", "detailed"].index(prefs["answer_length"]),
                      format_func=str.title, horizontal=True)
    plan = c2.radio("Your plan this year", ["auto", "contend", "rebuild"], index=["auto", "contend", "rebuild"].index(prefs["team_plan"]),
                    format_func=str.title, horizontal=True)
    if (length, plan) != (prefs["answer_length"], prefs["team_plan"]):
        try:
            owner_settings.save_gm_prefs(client, user_id, {"answer_length": length, "team_plan": plan})
            st.success("Saved. The GM uses this on the website and in the app.")
        except ValueError as exc:
            st.error(str(exc))
