# pages/02_Import_Contracts.py
from __future__ import annotations

import pandas as pd
import streamlit as st
import time

from auth import require_login, current_user, _sb, _sb_service
from services.import_resolver import normalize_name, resolve_row
from services.sleeper_sync import refresh_sleeper_players

st.set_page_config(page_title="Import Contracts", page_icon="📄", layout="centered")

require_login("home.py")
u = current_user()

access = st.session_state.get("sb_access_token")
refresh = st.session_state.get("sb_refresh_token")

if not access:
    st.error("No access token. Please sign in again.")
    st.stop()

league_id = st.session_state.get("active_league_id")

if not league_id:
    st.error("No active league selected. Go back to League Setup.")
    st.stop()

sb = _sb(access)

try:
    sb.auth.set_session(access, refresh)
except Exception:
    pass

svc = _sb_service()

st.markdown(
    """
    <style>
        [data-testid="stSidebar"] {display: none;}
        [data-testid="collapsedControl"] {display: none;}

        .block-container {
            max-width: 920px;
            padding-top: 4rem;
            padding-bottom: 5rem;
        }

        h1, h2, h3, p {
            color: #F5EBD7;
        }

        label, .stCaption {
            color: #CFC6B4 !important;
        }

        .import-shell {
            margin-top: 1.5rem;
            background: rgba(13, 36, 26, 0.72);
            border: 1px solid rgba(200, 155, 74, 0.22);
            border-radius: 24px;
            padding: 34px 36px;
        }

        .import-title {
            font-size: 1.45rem;
            font-weight: 900;
            color: #F5EBD7;
            margin-bottom: 8px;
        }

        .import-copy {
            color: #CFC6B4;
            font-size: .95rem;
            margin-bottom: 22px;
        }

        .status-pill {
            display: inline-block;
            padding: 6px 12px;
            border-radius: 999px;
            background: rgba(200, 155, 74, 0.18);
            color: #E2BC5B;
            font-size: .78rem;
            font-weight: 800;
            margin-bottom: 1rem;
        }

        .stButton button,
        .stDownloadButton button {
            background: linear-gradient(135deg, #C89B4A, #B88735);
            color: #071A12;
            border: none;
            border-radius: 12px;
            font-weight: 800;
            min-height: 48px;
        }

        .stButton button:hover,
        .stDownloadButton button:hover {
            transform: translateY(-1px);
            opacity: 0.95;
        }

        div[data-testid="stFileUploader"] section {
            border-radius: 18px;
            border: 1px dashed rgba(200,155,74,0.35);
            background: rgba(17,43,32,0.55);
        }

        div[data-testid="stDataFrame"] {
            border-radius: 16px;
            overflow: hidden;
        }

        .verify-shell {
            margin-top: 2rem;
            background: rgba(13, 36, 26, 0.72);
            border: 1px solid rgba(200, 155, 74, 0.22);
            border-radius: 24px;
            padding: 48px 36px;
            text-align: center;
            min-height: 310px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }

        .verify-spinner {
            width: 54px;
            height: 54px;
            border: 4px solid rgba(245,235,215,0.16);
            border-top: 4px solid #C89B4A;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-bottom: 22px;
        }

        .verify-check {
            width: 58px;
            height: 58px;
            border-radius: 50%;
            border: 2px solid rgba(200,155,74,0.7);
            color: #E2BC5B;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 2rem;
            font-weight: 900;
            margin-bottom: 22px;
        }

        .verify-title {
            font-size: 1.55rem;
            font-weight: 900;
            color: #F5EBD7;
            margin-bottom: 8px;
        }

        .verify-subtitle {
            color: #CFC6B4;
            font-size: .95rem;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("Import Contracts")
st.caption(
    "Upload your league contract CSV. We’ll match players to Sleeper automatically before final review."
)

st.markdown(
    """
    <div class="import-shell">
        <span class="status-pill">Contract Import</span>
        <div class="import-title">Upload your league contracts</div>
        <p class="import-copy">
            Start with the template, add your league’s contracts, then upload the completed CSV.
            The app will remove blank rows, normalize salary values, and match players to Sleeper.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)
st.write("")

with st.expander("Advanced: Refresh Sleeper Player Database", expanded=False):
    st.caption("Only use this if player matching seems outdated or a player is missing.")

    if st.button("Refresh Sleeper Player IDs", use_container_width=True):
        with st.spinner("Downloading and refreshing Sleeper players…"):
            refreshed_count = refresh_sleeper_players(svc)

        st.cache_data.clear()

        count_check = (
            svc.table("sleeper_players")
            .select("sleeper_player_id", count="exact")
            .execute()
            .count
        )

        st.success(
            f"Sleeper player database refreshed. "
            f"Updated: {refreshed_count}. Total players: {count_check}"
        )

csv_template = """player_name,player_position,owner_name,original_contract_years,years_left,player_salary_current_year,sleeper_player_id
Josh Allen,QB,Matt Smith,4,3,48.0,
Breece Hall,RB,Jake Laughs,4,3,46.5,
Justin Jefferson,WR,Phil Sone,4,3,46.0,
Patrick Mahomes,QB,Jake Fields,4,3,45.0,
"""

st.write("")

with st.container(border=True):
    st.subheader("1. Download template")
    st.caption("Start here so your contract file has the right columns.")

    st.download_button(
        label="Download CSV Template",
        data=csv_template,
        file_name="league_contract_import.csv",
        mime="text/csv",
        use_container_width=True,
    )

st.write("")

with st.container(border=True):
    st.subheader("2. Upload completed CSV")
    st.caption("Sleeper Player ID is optional. Leave it blank unless you know the exact ID.")

    uploaded_file = st.file_uploader(
        "Upload completed CSV",
        type=["csv"],
        help="Use the downloaded template. You may leave sleeper_player_id blank.",
        label_visibility="collapsed",
    )

if uploaded_file is None:
    st.stop()

try:
    df = pd.read_csv(uploaded_file)
except Exception as e:
    st.error(f"Could not read CSV: {e}")
    st.stop()

required = [
    "player_name",
    "owner_name",
    "original_contract_years",
    "years_left",
    "player_salary_current_year",
]

missing = [c for c in required if c not in df.columns]

if missing:
    st.error(f"Missing required columns: {missing}")
    st.stop()

if "player_position" not in df.columns:
    df["player_position"] = None

if "owner_email" not in df.columns:
    df["owner_email"] = None

if "sleeper_player_id" not in df.columns:
    df["sleeper_player_id"] = None
# Remove fully blank / accidental spacer rows
df = df.dropna(how="all")

# Remove rows with no real player name
df["player_name"] = df["player_name"].astype(str).str.strip()
df = df[
    df["player_name"].notna()
    & (df["player_name"].str.lower() != "nan")
    & (df["player_name"] != "")
].copy()

# Clean optional text columns
for col in ["player_position", "owner_name", "owner_email", "sleeper_player_id"]:
    if col in df.columns:
        df[col] = df[col].apply(
            lambda x: None if pd.isna(x) or str(x).strip().lower() == "nan" else str(x).strip()
        )

st.success("CSV uploaded successfully.")

with st.container(border=True):
    st.subheader("Preview")
    st.caption("Showing the first 25 rows.")
    preview_df = df.copy().head(25)
    st.dataframe(preview_df, use_container_width=True)

st.write("")

with st.container(border=True):
    st.subheader("3. Match players and continue")
    st.caption("The app will match players against Sleeper and send you to review.")

    run_import = st.button(
        "Run Auto-Match + Continue",
        type="primary",
        use_container_width=True,
    )

def clean_money(value):
    if pd.isna(value):
        return None

    s = str(value).strip()

    if not s:
        return None

    s = (
        s.replace("$", "")
         .replace(",", "")
         .replace(" ", "")
    )

    return float(s)

if run_import:
    slot = st.empty()

    slot.markdown(
        """
        <div class="verify-shell">
            <div class="verify-spinner"></div>
            <div class="verify-title">Matching players</div>
            <p class="verify-subtitle">Checking your CSV against the Sleeper player database...</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    sleeper_count = (
        svc.table("sleeper_players")
        .select("sleeper_player_id", count="exact")
        .execute()
        .count
    )

    if not sleeper_count or sleeper_count == 0:
        st.error("Sleeper player database is empty. Open Advanced and refresh Sleeper Player IDs first.")
        st.stop()

    with st.spinner("Loading Sleeper player reference…"):
        sleeper_players = []
        page_size = 1000
        start = 0

        while True:
            batch = (
                svc.table("sleeper_players")
                .select("sleeper_player_id, full_name, position, team, status, search_name")
                .range(start, start + page_size - 1)
                .execute()
                .data
                or []
            )

            sleeper_players.extend(batch)

            if len(batch) < page_size:
                break

            start += page_size

    sleeper_by_name: dict[str, list[dict]] = {}

    for p in sleeper_players:
        player_record = {
            "sleeper_player_id": p["sleeper_player_id"],
            "full_name": p.get("full_name"),
            "position": p.get("position"),
            "team": p.get("team"),
            "status": p.get("status"),
        }

        keys = {
            normalize_name(p.get("full_name") or ""),
            normalize_name(p.get("search_name") or ""),
        }

        for key in keys:
            if key:
                sleeper_by_name.setdefault(key, []).append(player_record)

    sb.table("contract_import_staging").delete().eq("league_id", league_id).eq("uploaded_by", u["id"]).execute()

    rows = []

    for idx, r in df.iterrows():
        player_name = str(r.get("player_name") or "").strip()
        player_pos = str(r.get("player_position") or "").strip().upper() or None
        owner_name = str(r.get("owner_name") or "").strip()
        owner_email = str(r.get("owner_email") or "").strip() or None

        raw_pid = r.get("sleeper_player_id")

        if pd.isna(raw_pid):
            provided_pid = None
        else:
            provided_pid = str(raw_pid).strip() or None

        if provided_pid:
            match_status = "matched_manual"
            match_reason = "PROVIDED_SLEEPER_ID"
            resolved_id = provided_pid
            candidates = []
        else:
            res = resolve_row(sleeper_by_name, player_name, player_pos)
            match_status = res["match_status"]
            match_reason = res["match_reason"]
            resolved_id = res["resolved_sleeper_player_id"]
            candidates = res["candidates"]

        rows.append(
            {
                "league_id": league_id,
                "uploaded_by": u["id"],
                "row_num": int(idx) + 1,
                "player_name": player_name,
                "player_position": player_pos,
                "owner_name": owner_name,
                "owner_email": owner_email,
                "original_contract_years": int(r.get("original_contract_years"))
                if pd.notna(r.get("original_contract_years"))
                else None,
                "years_left": int(r.get("years_left"))
                if pd.notna(r.get("years_left"))
                else None,
                "player_salary_current_year": clean_money(r.get("player_salary_current_year")),
                "normalized_name": normalize_name(player_name),
                "resolved_sleeper_player_id": resolved_id,
                "match_status": match_status,
                "match_reason": match_reason,
                "match_candidates": candidates,
            }
        )

    chunk_size = 200

    for i in range(0, len(rows), chunk_size):
        sb.table("contract_import_staging").insert(rows[i : i + chunk_size]).execute()

    unresolved_count = (
        sb.table("contract_import_staging")
        .select("id", count="exact")
        .eq("league_id", league_id)
        .eq("uploaded_by", u["id"])
        .eq("match_status", "unresolved")
        .execute()
        .count
        or 0
    )

    st.session_state["import_league_id"] = league_id

    if unresolved_count > 0:
        slot.markdown(
            f"""
            <div class="verify-shell">
                <div class="verify-check">!</div>
                <div class="verify-title">Review needed</div>
                <p class="verify-subtitle">{unresolved_count} player(s) need manual confirmation.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        time.sleep(1.2)
        st.switch_page("pages/03_Fix_Players.py")

    else:
        slot.markdown(
            """
            <div class="verify-shell">
                <div class="verify-check">✓</div>
                <div class="verify-title">Players matched</div>
                <p class="verify-subtitle">Your contracts are ready for final review.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        time.sleep(1.2)
        st.switch_page("pages/04_Commit_Contracts.py")
