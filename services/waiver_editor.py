"""Website pickups and drops: the same bids, Sunday adds and drops the iOS app makes, through the same database calls.

Only shows while the league's switch (league_app_transactions.enabled) is on. Wording and rules come from
api.waivers so the website and the app say the same thing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import streamlit as st

from api import waivers


def _error(exc: Exception) -> str:
    body = getattr(getattr(exc, "response", None), "text", "")  # PostgREST puts the database's error code in the body
    return waivers.friendly(Exception(f"{exc} {body}"))


def render_waiver_card(client: Any, *, league_id: str, team_id: str, season: int, players: Iterable[Mapping[str, Any]]) -> None:
    """players: the Market list (api.market.all_players rows): free agents have no team_id, mine have is_mine."""
    try:
        on = client.table("league_app_transactions").select("enabled").eq("league_id", league_id).limit(1).execute().data or []
    except Exception:
        on = []
    if not (on and on[0].get("enabled")) or not team_id:
        return
    players = list(players)
    free = [p for p in players if not p.get("team_id")]
    mine = [p for p in players if p.get("is_mine") and not p.get("designation")]
    label = lambda p: f"{p['name']} ({p.get('pos') or '?'}, {p.get('nfl_team') or 'FA'})"
    now = datetime.now(timezone.utc)
    ledger = client.table("faab_ledger").select("amount").eq("league_id", league_id).eq("league_team_id", team_id).eq("season", season).execute().data or []
    left = round(sum(float(r["amount"]) for r in ledger), 2)
    open_now = waivers.free_agency_open(now)

    with st.container(border=True, key="fa_waiver_card"):
        st.markdown("### Pickups")
        st.caption(f"${left:g} FAAB left. " + ("Sunday free agency is open: adds go through now at $1."
                                                if open_now else f"Bids run {waivers.run_label(waivers.next_run(now))}. Highest bid wins; a tie goes to the earlier bid."))
        target = st.selectbox("Free agent", [None] + free, format_func=lambda p: "Choose a player" if p is None else label(p), key="waiver_target")
        drop = st.selectbox("Drop (optional)", [None] + mine, format_func=lambda p: "Nobody" if p is None else label(p), key="waiver_drop")
        bid = 1 if open_now else st.number_input("Bid ($)", min_value=0, max_value=int(left) if left >= 0 else 0, value=min(1, int(left)), step=1, key="waiver_bid")
        if st.button("Add now for $1" if open_now else "Place bid", key="waiver_send", use_container_width=True, disabled=target is None):
            request = {"league_id": league_id, "player_id": target["player_id"], "drop_player_id": drop["player_id"] if drop else None}
            try:
                if open_now:
                    client.rpc("add_free_agent_authenticated", {"p_request": request}).execute()
                    st.success(f"{target['name']} signed for $1 this season.")
                else:
                    client.rpc("place_waiver_claim_authenticated", {"p_request": {**request, "bid": int(bid)}}).execute()
                    st.success(f"${int(bid)} bid placed on {target['name']}.")
            except Exception as exc:
                st.error(_error(exc))

        claims = (client.table("waiver_claims").select("id,player_id,bid,status").eq("league_team_id", team_id)
                  .eq("status", "pending").execute().data or [])
        names = {p["player_id"]: p["name"] for p in players}
        for c in claims:
            col, button = st.columns([3, 1])
            col.write(f"{names.get(str(c['player_id']), 'Player')}: ${float(c['bid']):g}")
            if button.button("Cancel", key=f"waiver_cancel_{c['id']}"):
                try:
                    client.rpc("cancel_waiver_claim_authenticated", {"p_request": {"claim_id": c["id"]}}).execute()
                    st.rerun()
                except Exception as exc:
                    st.error(_error(exc))

        st.markdown("#### Drop a player")
        gone = st.selectbox("Player", [None] + mine, format_func=lambda p: "Choose a player" if p is None else label(p), key="waiver_drop_only")
        if gone is not None and st.button(f"Drop {gone['name']}", key="waiver_drop_send"):
            try:
                out = client.rpc("drop_player_authenticated", {"p_request": {"league_id": league_id, "player_id": gone["player_id"]}}).execute().data or {}
                dead = float((out or {}).get("dead_cap") or 0)
                st.success(f"{gone['name']} dropped. " + (f"${dead:g} dead cap in total." if dead else "No dead cap."))
            except Exception as exc:
                st.error(_error(exc))
