"""Website trade window: the same trade as the app's trade page, side by side, sent through the same checked
database call (propose_trade_offer_authenticated).

Uses api.trade_offers for the deadline lock, each team's tradeable players and picks, and the value read, so the
website and the app always agree. After the league's trade deadline it shows only the closed message.
"""
from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from api import teams, trade_feed, trade_offers


def _team_rows(sb: Any, league_id: str) -> list[dict[str, Any]]:
    try:
        return sb.table("league_teams").select("id,owner_name,team_name,sleeper_roster_id").eq("league_id", league_id).execute().data or []
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def _sides(_sb: Any, league_id: str, season: int, me: str, them: str) -> dict[str, Any]:
    state = teams.load_team_state(_sb, league_id, season)
    ids = sorted({str(r.get("sleeper_player_id")) for r in state.get("roster", []) if str(r.get("league_team_id")) in {me, them}})
    values = trade_feed.market_now(_sb, ids)
    price = trade_offers.pick_pricer(trade_feed.pick_values(_sb))
    owner_of = {str(t["id"]): str(t.get("owner_name") or "") for t in _team_rows(_sb, league_id)}
    info = teams.player_info(_sb, ids)
    return {side: trade_offers.side(state, tid, info, {}, values, price, owner_of) for side, tid in (("me", me), ("them", them))}


def _label(p: Mapping[str, Any], season: int) -> str:
    if "contract_id" in p:
        return f"{p['name']} · {p['pos']} · ${p['salary']:.1f} thru {season + max(1, int(p['years'])) - 1}"
    return str(p["label"])


def render_trade_box(sb: Any, *, league_id: str, season: int, my_team_id: str, sleeper_roster_id: Any) -> None:
    """Body of the Teams page "Send Trade Offer" window for the team being viewed (sleeper_roster_id)."""
    window = trade_offers.league_window(sb, league_id)
    if not window["open"]:
        st.markdown(
            f"<div style='text-align:center;padding:26px 8px'><div style='font-size:40px'>🔒</div>"
            f"<div style='font-size:24px;font-weight:900;margin:8px 0'>Trades are closed</div>"
            f"<div style='opacity:.75'>The trade deadline was Week {window['deadline_week']}. "
            f"Trading reopens the day after the championship (after Week {window['reopens_after_week']}).</div></div>",
            unsafe_allow_html=True)
        return
    rows = _team_rows(sb, league_id)
    them = next((t for t in rows if str(t.get("sleeper_roster_id")) == str(sleeper_roster_id)), None)
    mine = next((t for t in rows if str(t.get("id")) == str(my_team_id)), None)
    if not them or not mine or str(them["id"]) == str(my_team_id):
        st.info("Pick another owner's team to trade with.")
        return
    try:
        enabled = trade_offers.enabled(sb, league_id)
        sides = _sides(sb, league_id, season, str(my_team_id), str(them["id"]))
    except Exception:
        st.error("Couldn't load the rosters. Try again.")
        return
    me_first = str(mine.get("owner_name") or "You").split(" ")[0]
    them_first = str(them.get("owner_name") or "They").split(" ")[0]
    left, right = st.columns(2)
    pool = {}
    with left:
        st.markdown(f"**{me_first} Receives**")
        opts = sides["them"]["players"] + sides["them"]["picks"]
        pool.update({_label(p, season): p for p in opts})
        get = st.multiselect("From their team", [_label(p, season) for p in opts], key="trade_get", label_visibility="collapsed",
                             placeholder=f"Add from {them_first}")
    with right:
        st.markdown(f"**{them_first} Receives**")
        opts = sides["me"]["players"] + sides["me"]["picks"]
        pool.update({_label(p, season): p for p in opts})
        give = st.multiselect("From your team", [_label(p, season) for p in opts], key="trade_give", label_visibility="collapsed",
                              placeholder="Add from your team")
    gets, gives = [pool[k] for k in get], [pool[k] for k in give]
    g = trade_offers.grade(sum(p["value"] for p in gives), sum(p["value"] for p in gets))
    st.caption("GM READ · " + trade_offers.read_text(g, str(them.get("owner_name") or "")))
    if g["share"] is not None:
        st.progress(float(g["share"]))
    if not enabled:
        st.caption("Trades in the app turn on after the league leaves Sleeper. Until then, make the trade in Sleeper.")
    if st.button("Send offer", type="primary", use_container_width=True, disabled=not (enabled and gets and gives)):
        items = trade_offers.offer_items(str(my_team_id), str(them["id"]), gives, gets)
        request = {"league_id": league_id, "to_league_team_id": str(them["id"]),
                   "players": [i for i in items if i.get("contract_id")], "picks": [i for i in items if i.get("draft_pick_id")],
                   "note": None, "parent_offer_id": None}
        try:
            sb.rpc("propose_trade_offer_authenticated", {"p_request": request}).execute()
        except Exception as exc:
            st.error(trade_offers.friendly(exc))
            return
        st.success(f"Offer sent to {them_first}.")
