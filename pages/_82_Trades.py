# -*- coding: utf-8 -*-
# pages/_82_Trades.py
# Legacy Dynasty — Trade Center
# Supports 2–4 team proposals with players, draft picks, and salary/cap cash.

from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
import requests
import streamlit as st

from components.sidebar_nav import render_nav
from auth import require_login, current_user, _sb

ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"
st.set_page_config(page_title="Legacy Dynasty — Trades", page_icon=str(ICON), layout="wide", initial_sidebar_state="expanded")
render_nav()

PAGES_DIR = Path(__file__).resolve().parent
ROOT_DIR = PAGES_DIR.parent
sys.path.append(str(ROOT_DIR))
sys.path.append(str(ROOT_DIR / "lib"))

require_login("home.py")
user = current_user()
access = st.session_state.get("sb_access_token")
refresh = st.session_state.get("sb_refresh_token")

if not access:
    st.error("No access token. Please sign in again.")
    st.stop()

sb = _sb(access)
try:
    sb.auth.set_session(access, refresh)
except Exception:
    pass

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Missing SUPABASE_URL or SUPABASE_KEY in environment.")
    st.stop()


def get_user_id() -> Optional[str]:
    if isinstance(user, dict):
        return user.get("id") or user.get("user_id")
    return getattr(user, "id", None)


def get_active_league_id() -> Optional[str]:
    if st.session_state.get("active_league_id"):
        return st.session_state["active_league_id"]
    if st.session_state.get("import_league_id"):
        return st.session_state["import_league_id"]

    uid = get_user_id()
    if not uid:
        return None

    try:
        rows = (
            sb.table("league_memberships")
            .select("league_id, role, team_id")
            .eq("user_id", uid)
            .execute()
            .data
            or []
        )
        if rows:
            st.session_state["active_league_id"] = rows[0]["league_id"]
            st.session_state["role"] = rows[0].get("role")
            st.session_state["active_team_id"] = rows[0].get("team_id")
            return rows[0]["league_id"]
    except Exception:
        return None
    return None


league_id = get_active_league_id()
from season_engine import SeasonResolver
canonical_season = SeasonResolver(sb).get_active_season(league_id).season if league_id else None
role = st.session_state.get("role")
is_commissioner = role in {"commissioner", "host", "admin"}

if not league_id:
    st.error("No active league found. Go back to League Setup or Settings.")
    st.stop()


def rest_request(method: str, table: str, params: dict[str, str] | None = None, json_body: Any = None, prefer: str = "return=representation"):
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": prefer,
    }
    r = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=30)
    r.raise_for_status()
    if not r.content:
        return None
    return r.json()


def insert_rows(table: str, rows: list[dict[str, Any]] | dict[str, Any]):
    return rest_request("POST", table, json_body=rows)


def patch_rows(table: str, filters: dict[str, str], payload: dict[str, Any]):
    params = {k: f"eq.{v}" for k, v in filters.items()}
    return rest_request("PATCH", table, params=params, json_body=payload)


def load_teams() -> list[str]:
    rows = (
        sb.table("league_teams")
        .select("owner_name, team_name")
        .order("owner_name")
        .execute()
        .data
        or []
    )
    names = []
    for r in rows:
        name = str(r.get("team_name") or r.get("owner_name") or "").strip()
        if name and name.lower() != "none":
            names.append(name)
    return sorted(set(names))


def load_contracts(owner_name: str | None = None) -> pd.DataFrame:
    q = (
        sb.table("contracts")
        .select("id, league_id, owner_name, player_name, player_position, contract_years_left, contract_total_years, salary, sleeper_player_id")
        .eq("league_id", league_id)
    )
    if owner_name:
        q = q.eq("owner_name", owner_name)
    rows = q.order("owner_name").order("player_position").order("player_name").execute().data or []
    return pd.DataFrame(rows)


def load_picks(owner_name: str | None = None) -> pd.DataFrame:
    q = sb.table("draft_picks").select("*").eq("league_id", league_id)
    if owner_name:
        q = q.eq("current_owner", owner_name)
    rows = q.order("season").order("round").execute().data or []
    return pd.DataFrame(rows)


def resolve_active_team() -> Optional[str]:
    for key in ["trade_from_team", "active_team_name", "team_name"]:
        if st.session_state.get(key):
            return st.session_state[key]
    team_id = st.session_state.get("active_team_id")
    if team_id:
        try:
            rows = sb.table("league_teams").select("team_name, owner_name").eq("id", team_id).execute().data or []
            if rows:
                return rows[0].get("team_name") or rows[0].get("owner_name")
        except Exception:
            pass
    return None


def load_open_proposals() -> list[dict[str, Any]]:
    try:
        return (
            sb.table("trade_proposals")
            .select("*")
            .eq("status", "OPEN")
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception:
        return []


def load_trade_items(trade_id: str) -> list[dict[str, Any]]:
    return sb.table("trade_items").select("*").eq("trade_id", trade_id).execute().data or []


def load_trade_participants(trade_id: str) -> list[dict[str, Any]]:
    return sb.table("trade_participants").select("*").eq("trade_id", trade_id).execute().data or []


def player_label(row: dict[str, Any]) -> str:
    name = row.get("player_name") or "Unknown Player"
    pos = row.get("player_position") or "—"
    salary = float(row.get("salary") or 0)
    years = row.get("contract_years_left") or "—"
    return f"{name} · {pos} · ${salary:.0f} · {years} yr"


def pick_label(row: dict[str, Any]) -> str:
    label = row.get("pick_label") or f"{row.get('round')}.{row.get('season')}"
    original = row.get("original_team") or "Original"
    return f"{label} · via {original}"


def asset_type(item: dict[str, Any]) -> str:
    return str(item.get("asset_type") or item.get("type") or "").upper().strip()


def execute_trade_direct(trade_id: str):
    items = load_trade_items(trade_id)
    if not items:
        raise RuntimeError("No trade items found.")

    for item in items:
        kind = asset_type(item)
        from_team = str(item.get("from_team") or item.get("from_owner") or "").strip()
        to_team = str(item.get("to_team") or item.get("to_owner") or "").strip()

        if not from_team or not to_team:
            raise RuntimeError(f"Trade item missing from/to team: {item}")

        if kind == "PLAYER":
            contract_id = item.get("contract_id")
            sleeper_id = item.get("sleeper_player_id") or item.get("sleeper_id")
            player_name = item.get("player_name")
            if contract_id:
                patch_rows("contracts", {"id": str(contract_id), "league_id": str(league_id)}, {"owner_name": to_team})
            elif sleeper_id:
                patch_rows("contracts", {"league_id": str(league_id), "owner_name": from_team, "sleeper_player_id": str(sleeper_id)}, {"owner_name": to_team})
            elif player_name:
                patch_rows("contracts", {"league_id": str(league_id), "owner_name": from_team, "player_name": player_name}, {"owner_name": to_team})
            else:
                raise RuntimeError(f"PLAYER item missing identity: {item}")

        elif kind == "PICK":
            pick_id = item.get("draft_pick_id") or item.get("pick_id")
            if pick_id:
                patch_rows("draft_picks", {"id": str(pick_id), "league_id": str(league_id)}, {"current_owner": to_team})
            else:
                filters = {
                    "league_id": str(league_id),
                    "current_owner": from_team,
                    "season": str(item.get("pick_season") or item.get("season")),
                    "round": str(item.get("pick_round") or item.get("round")),
                }
                if item.get("original_team"):
                    filters["original_team"] = str(item.get("original_team"))
                patch_rows("draft_picks", filters, {"current_owner": to_team})

        elif kind == "CASH":
            amount = float(item.get("cash_amount") or item.get("amount") or 0)
            years = item.get("cash_years") or item.get("seasons") or [canonical_season]
            if isinstance(years, str):
                years = [int(x.strip()) for x in years.split(",") if x.strip()]
            if isinstance(years, (int, float)):
                years = [int(years)]
            if amount > 0:
                rows = []
                for season in years:
                    rows.extend([
                        {
                            "league_id": league_id,
                            "owner_name": from_team,
                            "player_name": None,
                            "season": int(season),
                            "adjustment_type": "trade_carryover",
                            "amount": -amount,
                            "note": f"{from_team} sends ${amount:.0f} salary to {to_team} via trade {trade_id[:8]}",
                        },
                        {
                            "league_id": league_id,
                            "owner_name": to_team,
                            "player_name": None,
                            "season": int(season),
                            "adjustment_type": "trade_carryover",
                            "amount": amount,
                            "note": f"{to_team} receives ${amount:.0f} salary from {from_team} via trade {trade_id[:8]}",
                        },
                    ])
                insert_rows("cap_adjustments", rows)
        else:
            raise RuntimeError(f"Unsupported asset type: {kind}")

    patch_rows("trade_proposals", {"id": trade_id}, {"status": "COMPLETE", "completed_at": datetime.now(timezone.utc).isoformat()})
    try:
        insert_rows("transaction_ledger", {
            "league_id": league_id,
            "transaction_type": "trade",
            "status": "complete",
            "source": "trade_center",
            "reference_id": trade_id,
            "note": "Trade executed",
        })
    except Exception:
        pass


active_team = resolve_active_team()

st.markdown("## 🔁 Trade Center")
st.caption("Build 2–4 team trades with players, draft picks, and salary/cap cash. Trades become official only after execution.")

teams_all = load_teams()
if len(teams_all) < 2:
    st.info("Need at least two teams in this league before trades can be created.")
    st.stop()

if not is_commissioner and active_team:
    teams_all = [active_team] + [t for t in teams_all if t != active_team]

if "trade_basket" not in st.session_state:
    st.session_state["trade_basket"] = []

with st.container(border=True):
    st.markdown("### Create Trade Proposal")
    num_teams = st.number_input("Number of teams", min_value=2, max_value=4, value=2, step=1)

    team_cols = st.columns(int(num_teams))
    participating = []
    for i, col in enumerate(team_cols):
        with col:
            options = [t for t in teams_all if t not in participating] or teams_all
            default_idx = options.index(active_team) if i == 0 and active_team in options else 0
            disabled = bool(not is_commissioner and i == 0 and active_team in options)
            team = st.selectbox(f"Team {i + 1}", options=options, index=default_idx, key=f"trade_team_{i}", disabled=disabled)
            participating.append(team)

    participating = list(dict.fromkeys([t for t in participating if t]))

    st.markdown("#### Add Asset to Basket")
    a, b, c = st.columns(3)
    with a:
        from_team = st.selectbox("From", participating, key="asset_from")
    with b:
        to_team = st.selectbox("To", [t for t in participating if t != from_team], key="asset_to")
    with c:
        item_kind = st.selectbox("Asset Type", ["PLAYER", "PICK", "CASH"], key="asset_type")

    if item_kind == "PLAYER":
        roster_df = load_contracts(from_team)
        if roster_df.empty:
            st.info(f"No players found for {from_team}.")
        else:
            records = roster_df.to_dict("records")
            labels = [player_label(r) for r in records]
            selected = st.multiselect("Player(s)", labels, key="selected_players")
            if st.button("Add Player(s) to Basket", key="add_players"):
                for lab in selected:
                    row = records[labels.index(lab)]
                    st.session_state["trade_basket"].append({
                        "from_team": from_team,
                        "to_team": to_team,
                        "asset_type": "PLAYER",
                        "sleeper_id": str(row.get("sleeper_player_id") or ""),
                        "player_name": row.get("player_name"),
                        "pos": row.get("player_position"),
                        "nfl_team": None,
                    })
                st.rerun()

    elif item_kind == "PICK":
        picks_df = load_picks(from_team)
        if picks_df.empty:
            st.info(f"No draft picks found for {from_team}.")
        else:
            records = picks_df.to_dict("records")
            labels = [pick_label(r) for r in records]
            selected = st.multiselect("Draft pick(s)", labels, key="selected_picks")
            if st.button("Add Pick(s) to Basket", key="add_picks"):
                for lab in selected:
                    row = records[labels.index(lab)]
                    st.session_state["trade_basket"].append({
                        "from_team": from_team,
                        "to_team": to_team,
                        "asset_type": "PICK",
                        "pick_season": int(row.get("season")),
                        "pick_round": int(row.get("round")),
                        "pick_overall": int(row.get("original_pick_rank") or 0),
                    })
                st.rerun()

    else:
        d1, d2 = st.columns([1, 2])
        with d1:
            amount = st.number_input("Salary/cap dollars", min_value=0.0, value=0.0, step=1.0, key="cash_amount")
        with d2:
            season_options = [canonical_season + offset for offset in range(4)]
            seasons = st.multiselect("Applies to season(s)", season_options, default=[canonical_season], key="cash_seasons")
        if st.button("Add Cash to Basket", key="add_cash"):
            if amount <= 0:
                st.warning("Cash amount must be greater than 0.")
            elif not seasons:
                st.warning("Select at least one season.")
            else:
                st.session_state["trade_basket"].append({
                "from_team": from_team,
                "to_team": to_team,
                "asset_type": "CASH",
                "cash_amount": float(amount),
            })
                st.rerun()

    st.markdown("#### Basket")
    basket = st.session_state["trade_basket"]
    if basket:
        st.dataframe(pd.DataFrame(basket), use_container_width=True, hide_index=True)
        clear_col, create_col = st.columns([1, 2])
        with clear_col:
            if st.button("Clear Basket"):
                st.session_state["trade_basket"] = []
                st.rerun()
        with create_col:
            notes = st.text_input("Proposal notes", key="proposal_notes")
            if st.button("Create Proposal"):
                try:
                    prop = insert_rows("trade_proposals", {
                        "created_by": active_team or participating[0],
                        "teams": participating,
                        "status": "OPEN",
                        "notes": notes or "",
                    })[0]                   
 

                    trade_id = prop["id"]
                    trade_item_rows = []
                    for item in basket:
                        trade_item_rows.append({
                        "trade_id": trade_id,
                        "from_team": item.get("from_team"),
                        "to_team": item.get("to_team"),
                        "asset_type": item.get("asset_type"),
                        "sleeper_id": item.get("sleeper_id"),
                        "player_name": item.get("player_name"),
                        "pos": item.get("pos"),
                        "nfl_team": item.get("nfl_team"),
                        "pick_season": item.get("pick_season"),
                        "pick_round": item.get("pick_round"),
                        "pick_overall": item.get("pick_overall"),
                        "cash_amount": item.get("cash_amount"),
                     })

                    insert_rows("trade_items", trade_item_rows)
                    insert_rows("trade_participants", [
                        {"trade_id": trade_id, "team": t, "decision": "ACCEPT" if t == active_team else "PENDING"}
                        for t in participating
                    ])
                    st.session_state["trade_basket"] = []
                    st.success(f"Proposal created: {trade_id[:8]}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to create proposal: {e}")
    else:
        st.info("No assets in the basket yet.")

st.markdown("---")
st.markdown("### Open Proposals")
open_props = load_open_proposals()
if not open_props:
    st.info("No open proposals.")
    st.stop()

labels = []
for p in open_props:
    teams = p.get("teams") or []
    labels.append(f"{str(p.get('created_at') or '')[:19]} · {' ↔ '.join(teams)} · {p.get('id', '')[:8]}")

choice = st.selectbox("Select proposal", list(range(len(open_props))), format_func=lambda i: labels[i], key="open_trade_selector")
proposal = open_props[choice]
trade_id = proposal["id"]
teams = proposal.get("teams") or []
items = load_trade_items(trade_id)
participants = load_trade_participants(trade_id)
status_by_team = {p.get("team"): p.get("decision", "PENDING") for p in participants}

with st.container(border=True):
    st.markdown(f"#### Proposal {trade_id[:8]}")
    if proposal.get("notes"):
        st.caption(proposal["notes"])
    if items:
        st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)
    else:
        st.info("No items found for this proposal.")

    st.markdown("##### Team Decisions")
    decision_cols = st.columns(max(1, len(teams)))
    for i, team in enumerate(teams):
        with decision_cols[i]:
            current = status_by_team.get(team, "PENDING")
            st.metric(team, current)
            can_decide = is_commissioner or active_team == team
            if can_decide:
                c_accept, c_decline = st.columns(2)
                with c_accept:
                    if st.button("Accept", key=f"accept_{trade_id}_{team}"):
                        patch_rows(
                        "trade_participants",
                        {"trade_id": trade_id, "team": team},
                        {
                        
                            "decision": "ACCEPT",
                            "decided_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    st.rerun()
                with c_decline:
                    if st.button("Decline", key=f"decline_{trade_id}_{team}"):
                        patch_rows(
                            "trade_participants",
                            {"trade_id": trade_id, "team": team},
                            {
                                "decision": "DECLINE",
                                "decided_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )

                        patch_rows(
                            "trade_proposals",
                            {"id": trade_id},
                            {"status": "DECLINED"},
                        )

                        st.rerun()

    all_accepted = len(teams) > 0 and all(status_by_team.get(team) == "ACCEPT" for team in teams)
    st.markdown("---")
    if all_accepted:
        st.success("All teams have accepted. Commissioner can execute this trade.")
    else:
        st.info("Waiting for all teams to accept.")

    if is_commissioner:
        if st.button("Execute Trade", key=f"execute_{trade_id}", disabled=not all_accepted):
            try:
                execute_trade_direct(trade_id)
                st.success("Trade executed.")
                st.rerun()
            except Exception as e:
                st.error(f"Execution failed: {e}")
    else:
        st.caption("Only the commissioner can execute accepted trades.")
