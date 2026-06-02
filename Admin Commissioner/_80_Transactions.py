from __future__ import annotations

import os
import requests
import pandas as pd
import streamlit as st

from auth import require_login, current_user, _sb

# ==========================
# Config
# ==========================
TRANSACTIONS_TABLE = "transactions_enriched"  # Supabase VIEW
TIMESTAMP_COLUMN   = "ts"                     # Used for ordering
DEFAULT_SEASON     = 2025                    # Label only (no season col yet)

# ==========================
# Page setup
# ==========================
st.set_page_config(page_title="Fantasy GM — Transactions", layout="wide")
st.subheader("League Transactions")

require_login("home.py")

u = current_user()
access = st.session_state.get("sb_access_token")
refresh = st.session_state.get("sb_refresh_token")
league_id = st.session_state.get("active_league_id") or st.session_state.get("import_league_id")

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

try:
    league_row = (
        sb.table("leagues")
        .select("id,name,sleeper_league_id")
        .eq("id", league_id)
        .single()
        .execute()
        .data
    )
except Exception as e:
    st.error(f"Could not load active league: {e}")
    st.stop()

SLEEPER_LEAGUE_ID = str(league_row.get("sleeper_league_id") or "").strip()
SLEEPER_LEAGUE_ID = "".join(ch for ch in SLEEPER_LEAGUE_ID if ch.isdigit())

if not SLEEPER_LEAGUE_ID:
    st.error("This league does not have a Sleeper league connected yet.")
    st.stop()

# ==========================
# Helper — Manual league sync fallback
# ==========================
def run_sync(payload: dict, timeout_sec: int = 90):
    fn_url = os.getenv("SYNC_TX_URL", "").strip()

    if not fn_url:
        return {
            "ok": False,
            "error": "SYNC_TX_URL is not configured.",
        }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access}",
    }

    try:
        r = requests.post(fn_url, headers=headers, json=payload, timeout=timeout_sec)

        try:
            body = r.json()
        except Exception:
            body = {"raw_text": r.text[:10_000]}

        return {
            "ok": r.ok,
            "status": r.status_code,
            "body": body,
        }

    except requests.RequestException as e:
        return {
            "ok": False,
            "error": "Network error calling sync function.",
            "details": str(e),
        }

# ==========================
# Helpers — Supabase REST fetch
# ==========================
@st.cache_data(show_spinner=False)
def fetch_transactions(active_league_id: str) -> tuple[pd.DataFrame | None, str | None]:
    try:
        rows = (
            sb.table(TRANSACTIONS_TABLE)
            .select("*")
            .eq("league_id", active_league_id)
            .order(TIMESTAMP_COLUMN, desc=True)
            .execute()
            .data
            or []
        )

        if not rows:
            return pd.DataFrame(), None

        df = pd.DataFrame(rows)

        if "ts" in df.columns:
            df = df[df["ts"].notna()]
        if "tx_type" in df.columns:
            df = df[df["tx_type"].notna()]

        return df, None

    except Exception as e:
        return None, f"Could not load transactions: {e}"

def build_display_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Create a clean display dataframe based on transactions_enriched:

    Time | Week | Player | Action | Owner(s) | Waiver Bid

    - Action is normalized from tx_type + acquisition.
    - Owner(s) always shows the owner(s) impacted by the move.
    """

    df = df.copy()
    # ---- Normalize Action (added / dropped / traded) ----
    def normalize_action(row):
        tx = str(row.get("tx_type", "") or "").lower()
        acq = str(row.get("acquisition", "") or "").lower()

        # 1) If Sleeper says "traded", that wins, even if tx_type is add/drop
        if acq in ("trade", "traded"):
            return "traded"

        # 2) Other acquisition-based hints
        if acq in ("add", "added", "waiver", "waivers", "claim", "claimed"):
            return "added"
        if acq in ("drop", "dropped", "release", "released"):
            return "dropped"

        # 3) Fallbacks from tx_type
        if tx == "trade":
            return "traded"
        if tx == "drop":
            return "dropped"
        if tx == "add":
            return "added"

        # 4) Last resort: whatever we have
        return acq or tx or ""

    df["Action"] = df.apply(normalize_action, axis=1)

    # ---- Rename core columns to friendly names ----
    col_map = {
        "ts": "Time",
        "nfl_week": "Week",
        "player_name": "Player",
        "from_owner_name": "From Owner",
        "from_team_name": "From Team",
        "to_owner_name": "To Owner",
        "to_team_name": "To Team",
        "waiver_bid": "Waiver Bid",
    }
    existing_col_map = {k: v for k, v in col_map.items() if k in df.columns}
    df = df.rename(columns=existing_col_map)

    # ---- Build single Owner(s) column ----
    def compute_owners(row):
        action = str(row.get("Action", "")).lower()
        from_owner = row.get("From Owner")
        to_owner = row.get("To Owner")

        # Normalize blanks
        from_owner = from_owner if from_owner not in (None, "", "None") else None
        to_owner = to_owner if to_owner not in (None, "", "None") else None

        # Trades: always show both sides if we have them
        if action == "traded":
            if from_owner and to_owner and from_owner != to_owner:
                return f"{from_owner} ↔ {to_owner}"
            return from_owner or to_owner

        # Adds: who ended up with the player
        if action == "added":
            return to_owner or from_owner

        # Drops: who dropped the player
        if action == "dropped":
            return from_owner or to_owner

        # Fallbacks
        if from_owner and to_owner and from_owner != to_owner:
            return f"{from_owner} ↔ {to_owner}"
        return from_owner or to_owner

    df["Owner(s)"] = df.apply(compute_owners, axis=1)

    # ---- Final column order ----
    desired_cols = [
        "Time",
        "Week",
        "Player",
        "Action",
        "Owner(s)",
        "Waiver Bid",
    ]
    cols_present = [c for c in desired_cols if c in df.columns]
    df = df[cols_present]

    return df
    
col1, col2 = st.columns([1, 3], gap="large")

with col1:
    st.markdown("### Sync Status")

    st.caption(
        "Transactions should sync automatically from Sleeper. "
        "Use this button only as a manual fallback if the latest add/drop/trade is missing."
    )

    if st.button("🔄 Sync this league now", use_container_width=True):
        payload = {
            "league_id": league_id,
            "sleeper_league_id": SLEEPER_LEAGUE_ID,
            "weeks": "1-18",
        }

        with st.spinner("Syncing this league from Sleeper…"):
            sync_out = run_sync(payload)

        fetch_transactions.clear()

        if sync_out.get("ok"):
            st.success("League transactions synced ✅")
        else:
            st.error("Sync failed ❌")

        st.json(sync_out)
# ==========================
# Right column — Transactions table
# ==========================
with col2:
    st.markdown("### Season Transactions")

    # Season input (label only for now)
    season = st.number_input(
        "Season",
        min_value=2000,
        max_value=2100,
        value=DEFAULT_SEASON,
        step=1,
        help="Season label only for now (no season column yet).",
    )

    with st.spinner("Loading transactions from Supabase…"):
        df_raw, err_tx = fetch_transactions(active_league_id=league_id)

    if err_tx:
        st.error(err_tx)

    elif df_raw is None or df_raw.empty:
        st.info("No transactions found.")

    else:
        df_display = build_display_df(df_raw)

        # Owner filter (based on Owner(s) column)
        owners = (
            df_display["Owner(s)"].dropna().astype(str).unique().tolist()
            if "Owner(s)" in df_display.columns
            else []
        )

        owners = sorted(o for o in owners if o.strip())

        selected_owner = None

        if owners:
            selected_owner = st.selectbox(
                "Filter by owner (optional)",
                options=["All owners"] + owners,
                index=0,
            )

            if selected_owner != "All owners":
                df_display = df_display[
                    df_display["Owner(s)"].astype(str) == selected_owner
                ]

        # Show total + latest timestamp
        latest_ts = df_raw["ts"].max() if "ts" in df_raw.columns else None

        if latest_ts is not None:
            st.caption(f"Latest transaction in DB: **{latest_ts}**")

        st.caption(f"Total transactions shown: **{len(df_display)}**")

        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
        )