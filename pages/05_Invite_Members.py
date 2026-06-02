# pages/05_Invite_Members.py
from __future__ import annotations

import re
import pandas as pd
import streamlit as st

from auth import require_login, current_user, _sb

st.set_page_config(
    page_title="Invite Members",
    page_icon="✉️",
    layout="centered",
)

st.markdown(
    """
    <style>
        [data-testid="stSidebar"] {
            display: none;
        }

        [data-testid="collapsedControl"] {
            display: none;
        }

        .block-container {
            max-width: 950px;
            padding-top: 3rem;
            padding-bottom: 5rem;
        }

        div[data-testid="stButton"] button {
            border-radius: 14px;
            min-height: 46px;
            font-weight: 700;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 22px;
            border-color: rgba(245, 235, 215, 0.14);
            background: rgba(255,255,255,0.025);
        }
    </style>
    """,
    unsafe_allow_html=True,
)

require_login("home.py")

u = current_user()

access = st.session_state.get("sb_access_token")
refresh = st.session_state.get("sb_refresh_token")

league_id = (
    st.session_state.get("active_league_id")
    or st.session_state.get("import_league_id")
)

if not access:
    st.error("No access token. Please sign in again.")
    st.stop()

if not league_id:
    st.error("No league selected.")
    st.stop()

sb = _sb(access)

try:
    sb.auth.set_session(access, refresh)
except Exception:
    pass


def clean_email(value: str | None) -> str | None:
    if value is None:
        return None

    value = str(value).strip().lower()

    if not value:
        return None

    return value


def is_valid_email(value: str | None) -> bool:
    if not value:
        return True

    return bool(
        re.match(
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
            value,
        )
    )


def get_my_role() -> str:
    res = (
        sb.table("league_memberships")
        .select("role")
        .eq("league_id", league_id)
        .eq("user_id", u["id"])
        .execute()
    )

    rows = res.data or []

    if not rows:
        return "member"

    return rows[0].get("role", "member")


role = get_my_role()
is_commissioner = role == "commissioner"

league = (
    sb.table("leagues")
    .select("id, name")
    .eq("id", league_id)
    .single()
    .execute()
    .data
)

teams = (
    sb.table("league_teams")
    .select(
        "id, owner_name, team_name, owner_email, user_id"
    )
    .eq("league_id", league_id)
    .order("owner_name")
    .execute()
    .data
    or []
)

st.title("Invite League Members")

if league:
    st.caption(
        f"Assign emails to teams for **{league['name']}**."
    )

st.write("")

if not teams:
    st.warning(
        "No teams found yet. Finalize contracts first."
    )

    if st.button(
        "Go to Contract Import",
        use_container_width=True,
    ):
        st.switch_page("pages/02_Import_Contracts.py")

    st.stop()

with st.container(border=True):

    st.subheader("Team Assignments")

    st.caption(
        "Match each imported team to the email address "
        "that should control that team."
    )

    rows = []

    for team in teams:
        rows.append(
            {
                "Team Owner": team.get("owner_name") or "",
                "Team Name": (
                    team.get("team_name")
                    or team.get("owner_name")
                    or ""
                ),
                "Invite Email": (
                    team.get("owner_email") or ""
                ),
                "Connected": (
                    "Yes" if team.get("user_id") else "No"
                ),
                "_id": team.get("id"),
            }
        )

    df = pd.DataFrame(rows)

    edited_df = st.data_editor(
        df[
            [
                "Team Owner",
                "Team Name",
                "Invite Email",
                "Connected",
                "_id",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        disabled=(
            ["Team Owner", "Connected", "_id"]
            if is_commissioner
            else True
        ),
        column_config={
            "Team Owner": st.column_config.TextColumn(
                "Team Owner"
            ),
            "Team Name": st.column_config.TextColumn(
                "Team Name"
            ),
            "Invite Email": st.column_config.TextColumn(
                "Invite Email"
            ),
            "Connected": st.column_config.TextColumn(
                "Connected"
            ),
            "_id": None,
        },
    )

    st.write("")

    if not is_commissioner:
        st.info(
            "Only the commissioner can edit team assignments."
        )

    else:

        save_clicked = st.button(
            "Save Team Assignments",
            type="primary",
            use_container_width=True,
        )

        if save_clicked:

            errors = []

            for _, row in edited_df.iterrows():

                email = clean_email(
                    row.get("Invite Email")
                )

                team_name = str(
                    row.get("Team Name") or ""
                ).strip()

                team_id = row.get("_id")

                if not is_valid_email(email):
                    errors.append(
                        f"{row.get('Team Owner')}: invalid email address."
                    )
                    continue

                (
                    sb.table("league_teams")
                    .update(
                        {
                            "team_name": team_name,
                            "owner_email": email,
                        }
                    )
                    .eq("id", team_id)
                    .eq("league_id", league_id)
                    .execute()
                )

            if errors:

                st.error(
                    "Please fix these emails before continuing:"
                )

                for error in errors:
                    st.write(f"- {error}")

            else:
                st.success("Team assignments saved.")

st.write("")

col1, col2 = st.columns(2)

with col1:

    if st.button(
        "Go to Contracts Dashboard",
        use_container_width=True,
    ):
        st.switch_page("pages/_83_Contracts_Cap.py")

with col2:

    if st.button(
        "Go to Home",
        use_container_width=True,
    ):
        st.switch_page("home.py")