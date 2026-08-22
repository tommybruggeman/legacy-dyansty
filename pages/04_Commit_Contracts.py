# pages/04_Commit_Contracts.py
from __future__ import annotations

import pandas as pd
import streamlit as st

from auth import require_login, current_user, _sb

st.set_page_config(page_title="Commit Contracts", page_icon="🏈", layout="centered")

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

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 22px;
            border-color: rgba(245, 235, 215, 0.14);
            background: rgba(255,255,255,0.025);
        }

        div[data-testid="stButton"] button {
            border-radius: 14px;
            min-height: 46px;
            font-weight: 700;
        }

        div[data-testid="stMetric"] {
            background: rgba(255,255,255,0.025);
            border: 1px solid rgba(245,235,215,0.10);
            border-radius: 18px;
            padding: 18px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

require_login("home.py")

u = current_user()
access = st.session_state.get("sb_access_token")
refresh = st.session_state.get("sb_refresh_token")
league_id = st.session_state.get("import_league_id") or st.session_state.get("active_league_id")

if not access:
    st.error("No access token. Please sign in again.")
    st.stop()

if not league_id:
    st.error("No league selected. Go back to League Setup.")
    st.stop()

sb = _sb(access)

try:
    sb.auth.set_session(access, refresh)
except Exception:
    pass

# Advisory UX check. Database triggers remain authoritative and close the race
# between this preflight and every legacy contract write below.
try:
    cutover_locked = bool(
        sb.rpc("has_active_rollover_cutover_lock", {"p_league_id": league_id})
        .execute()
        .data
    )
except Exception as exc:
    st.error(f"Could not verify rollover contract-write availability: {exc}")
    st.stop()

if cutover_locked:
    st.warning(
        "Contract commits are temporarily disabled while the commissioner "
        "rollover cutover lock is active. No contract data was changed."
    )
    st.stop()

st.title("Finalize Contract Import")
st.caption("Review your contracts one last time before saving them to this league.")

st.write("")

staging = (
    sb.table("contract_import_staging")
    .select("*")
    .eq("league_id", league_id)
    .eq("uploaded_by", u["id"])
    .order("owner_name")
    .order("player_salary_current_year", desc=True)
    .execute()
    .data
    or []
)

if not staging:
    st.warning("No staged contract import found.")
    if st.button("Back to Import", use_container_width=True):
        st.switch_page("pages/02_Import_Contracts.py")
    st.stop()

unresolved = [r for r in staging if r.get("match_status") == "unresolved"]

if unresolved:
    st.error(f"{len(unresolved)} player(s) still need to be resolved before committing.")
    if st.button("Fix Players", use_container_width=True):
        st.switch_page("pages/03_Fix_Players.py")
    st.stop()

total_players = len(staging)
total_salary = sum(float(r.get("player_salary_current_year") or 0) for r in staging)
teams = sorted({r.get("owner_name") or "Unknown" for r in staging})
avg_salary = total_salary / total_players if total_players else 0

m1, m2, m3, m4 = st.columns(4)

m1.metric("Players", total_players)
m2.metric("Teams", len(teams))
m3.metric("Total Salary", f"${total_salary:,.1f}")
m4.metric("Avg Salary", f"${avg_salary:,.1f}")

st.write("")

with st.container(border=True):
    st.subheader("Team-by-Team Review")
    st.caption("Open each team to quickly check players, salaries, and contract years.")

    preview_rows = []

    for r in staging:
        preview_rows.append(
            {
                "Team": r.get("owner_name") or "Unknown",
                "Player": r.get("player_name"),
                "Position": r.get("player_position"),
                "Salary": float(r.get("player_salary_current_year") or 0),
                "Years Left": r.get("years_left"),
                "Original Years": r.get("original_contract_years"),
            }
        )

    df = pd.DataFrame(preview_rows)

    if not df.empty:
        df = df.sort_values(by=["Team", "Salary", "Player"], ascending=[True, False, True])

    for team in sorted(df["Team"].dropna().unique()):
        team_df = df[df["Team"] == team].copy()
        team_salary = team_df["Salary"].sum()
        team_players = len(team_df)

        with st.expander(f"{team} — {team_players} players — ${team_salary:,.1f}", expanded=False):
            st.dataframe(
                team_df[["Player", "Position", "Salary", "Years Left", "Original Years"]],
                use_container_width=True,
                hide_index=True,
            )

st.write("")

with st.container(border=True):
    st.subheader("Finalize")
    st.caption("This will save these contracts as the active contracts for this league.")

    replace_existing = st.checkbox(
        "Replace existing contracts for this league",
        value=True,
        help="Recommended during setup. This clears current contract rows for this league before inserting the new import.",
    )

    commit_clicked = st.button(
        "Finalize Contract Import",
        type="primary",
        use_container_width=True,
    )

if commit_clicked:
    try:
        with st.spinner("Committing contracts..."):
            if replace_existing:
                sb.table("contracts").delete().eq("league_id", league_id).execute()

            contract_rows = []

            for r in staging:
                contract_rows.append(
                    {
                        "league_id": league_id,
                        "sleeper_player_id": r.get("resolved_sleeper_player_id"),
                        "player_name": r.get("player_name"),
                        "player_position": r.get("player_position"),
                        "owner_name": r.get("owner_name"),
                        "contract_years_left": r.get("years_left"),
                        "contract_total_years": r.get("original_contract_years"),
                        "salary": r.get("player_salary_current_year"),
                    }
                )

            chunk_size = 200

            for i in range(0, len(contract_rows), chunk_size):
                sb.table("contracts").insert(
                    contract_rows[i : i + chunk_size]
                ).execute()

            team_rows = []

            for owner_name in teams:
                if owner_name and owner_name != "Unknown":
                    team_rows.append(
                        {
                            "league_id": league_id,
                            "owner_name": owner_name,
                            "team_name": owner_name,
                        }
                    )

            if team_rows:
                sb.table("league_teams").upsert(
                    team_rows,
                    on_conflict="league_id,owner_name",
                ).execute()

            (
                sb.table("contract_import_staging")
                .delete()
                .eq("league_id", league_id)
                .eq("uploaded_by", u["id"])
                .execute()
            )

        st.session_state["contracts_finalized"] = True
        st.session_state["contracts_finalized_count"] = len(contract_rows)
        st.rerun()

    except Exception as e:
        if "rollover_cutover_contract_writes_blocked" in str(e):
            st.error("Contract commits are temporarily disabled during rollover. No contract data was changed.")
        else:
            st.error(f"Could not commit contracts: {e}")

if st.session_state.get("contracts_finalized"):
    st.success(
        f"Finalized {st.session_state.get('contracts_finalized_count', 0)} contracts."
    )

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Continue to Invite League Members", use_container_width=True):
            st.session_state["contracts_finalized"] = False
            st.switch_page("pages/05_Invite_Members.py")

    with col2:
        if st.button("Skip for now", use_container_width=True):
            st.session_state["contracts_finalized"] = False
            st.switch_page("pages/03_Teams.py")
