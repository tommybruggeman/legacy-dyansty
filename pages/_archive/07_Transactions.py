# pages/07_Transactions.py
import os
from pathlib import Path
from dotenv import load_dotenv
import streamlit as st
from supabase import create_client, Client
import pandas as pd

st.set_page_config(page_title="Fantasy GM — Transactions", layout="wide")

# ---- Env + client
DOTENV_PATH = Path(__file__).with_name("fantasy_env")
load_dotenv(dotenv_path=DOTENV_PATH)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.title("Transactions")
st.caption("Auto-synced from Sleeper (waivers Wed/Sun ~1:00 AM local).")

# ---- Filters
cols = st.columns([1,1,1,1,1])
with cols[0]:
    week = st.number_input("NFL Week", min_value=1, value=1, step=1)
with cols[1]:
    tx_types = st.multiselect("Type", ["waiver", "free_agent", "drop", "trade"])
with cols[2]:
    teams = [r["team_name"] for r in sb.table("teams").select("team_name").order("team_name").execute().data or []]
    team = st.selectbox("Team (any role)", [""] + teams)
with cols[3]:
    limit = st.selectbox("Rows", [50,100,250,500], index=1)
with cols[4]:
    refresh = st.button("Refresh")

# ---- Query view
q = sb.table("transactions_enriched").select(
    "id,tx_id,executed_at,nfl_week,tx_type,player_name,nfl_team,from_team,to_team,waiver_bid,direction"
)

if week:
    q = q.eq("nfl_week", int(week))
if tx_types:
    ors = ",".join([f"tx_type.eq.{t}" for t in tx_types])
    q = q.or_(ors)
if team:
    q = q.or_(f"from_team.eq.{team},to_team.eq.{team}")

data = q.order("executed_at", desc=True).limit(int(limit)).execute().data or []
df = pd.DataFrame(data)

# ---- Pretty table
if not df.empty:
    # Order + formatting
    cols_order = ["executed_at","nfl_week","tx_type","direction","player_name","nfl_team","from_team","to_team","waiver_bid","tx_id","id"]
    df = df[[c for c in cols_order if c in df.columns]]
    df.rename(columns={
        "executed_at":"When (UTC)",
        "nfl_week":"Week",
        "tx_type":"Type",
        "direction":"Dir",
        "player_name":"Player",
        "nfl_team":"NFL",
        "from_team":"From",
        "to_team":"To",
        "waiver_bid":"Bid",
    }, inplace=True)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No transactions found for the current filters.")
