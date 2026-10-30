"""Owner-to-owner trade offers: the builder, sending and answering offers, and the transaction center.

Offers live in public.trade_offers and only move through two database calls
(supabase/migrations/20261205_owner_trade_offers.sql): propose_trade_offer_authenticated and
respond_trade_offer_authenticated. Accepting runs the same canonical trade engine the commissioner
uses, so a trade made in the app moves contracts and picks exactly like one entered by the commissioner.
Everything stays off until the league's switch (league_app_transactions.enabled) is turned on after
the league leaves Sleeper; until then the app's Trade button opens Sleeper.

The builder's read is free (no model call): each side's share of today's dynasty market value.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

RESULT_DAYS = 7
FAIR_BAND = 0.05  # within 5 points of 50/50 reads as fair

# Database errors (raised by the SQL functions) in owner words.
ERRORS = {
    "transactions_not_enabled": "Trades in the app turn on after the league leaves Sleeper.",
    "not_a_team_owner": "Your account isn't linked to a team in this league.",
    "counter_not_allowed": "That offer can't be countered anymore.",
    "offer_team_invalid": "That team isn't in your league.",
    "offer_side_invalid": "Each piece has to go from one team to the other.",
    "offer_player_moved": "A player in this offer isn't on that roster anymore.",
    "offer_pick_moved": "A pick in this offer has already moved.",
    "offer_player_locked": "A player in this offer has already played this week. Try again after Monday night.",
    "offer_empty": "Add at least one player or pick.",
    "offer_one_sided": "Both teams have to get something.",
    "offer_not_open": "That offer was already answered.",
    "not_your_offer": "That offer isn't yours to answer.",
    "offer_action_invalid": "Unknown action.",
    "trade_pick_lifecycle_not_tradeable": "That pick's draft isn't open for trades.",
}


def friendly(exc: Exception) -> str:
    text = str(exc)
    for code, message in ERRORS.items():
        if code in text:
            return message
    return "The trade wasn't saved. Try again."


def _f(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def enabled(client: Any, league_id: str) -> bool:
    try:
        rows = client.table("league_app_transactions").select("enabled").eq("league_id", league_id).limit(1).execute().data or []
    except Exception:  # table not created yet
        return False
    return bool(rows and rows[0].get("enabled"))


def pick_label(row: Mapping[str, Any], owner_of: Mapping[str, str]) -> str:
    year, rnd = int(row.get("draft_year") or 0), int(row.get("round_number") or 0)
    original = owner_of.get(str(row.get("original_league_team_id"))) or str(row.get("original_owner_name") or "")
    first = original.split(" ")[0] if original else ""
    return f"{year} R{rnd}" + (f" ({first}'s)" if first and str(row.get("original_league_team_id")) != str(row.get("current_owner_league_team_id")) else "")


def pick_pricer(picks: Mapping[tuple[int, int], float]):
    """(year, round) -> market value; years without a price use that round's average."""
    def price(year: int, rnd: int) -> float:
        if (year, rnd) in picks:
            return picks[(year, rnd)]
        same = [v for (_, r), v in picks.items() if r == rnd]
        return sum(same) / len(same) if same else 0.0
    return price


def side(state: Mapping[str, Any], team_id: str, info: Mapping[str, Mapping[str, Any]], ppg: Mapping[str, float],
         values: Mapping[str, float], picks_value: Any, owner_of: Mapping[str, str]) -> dict[str, Any]:
    """One team's tradeable players and picks, best first."""
    players = []
    for r in state.get("roster", []):
        if str(r.get("league_team_id")) != team_id or not r.get("contract_agreement_id"):
            continue
        pid = str(r.get("sleeper_player_id"))
        meta = info.get(pid) or {}
        players.append({
            "player_id": pid, "contract_id": str(r["contract_agreement_id"]),
            "name": meta.get("full_name") or r.get("player_name") or "Player", "pos": meta.get("position") or r.get("position") or "",
            "nfl_team": meta.get("team") or "FA", "salary": _f(r.get("contract_cap_hit") or r.get("cap_hit")),
            "years": int(r.get("contract_years_left") or 1), "ppg": ppg.get(pid), "value": round(values.get(pid, 0.0)),
        })
    picks = []
    for r in state.get("draft_picks", []):
        if str(r.get("current_owner_league_team_id")) != team_id or str(r.get("asset_status") or "tradable") != "tradable" or not r.get("stable_pick_id"):
            continue
        picks.append({"pick_id": str(r["stable_pick_id"]), "label": pick_label(r, owner_of),
                      "year": int(r.get("draft_year") or 0), "round": int(r.get("round_number") or 0),
                      "value": round(picks_value(int(r.get("draft_year") or 0), int(r.get("round_number") or 0)))})
    players.sort(key=lambda p: (-p["value"], -(p["ppg"] or 0)))
    picks.sort(key=lambda p: (p["year"], p["round"]))
    return {"players": players, "picks": picks}


def grade(gives: float, gets: float) -> dict[str, Any]:
    """Your share of the value that moves. 'you' = you come out ahead, 'them' = they do."""
    total = gives + gets
    if total <= 0:
        return {"share": None, "lean": "fair", "pct": 0}
    share = gets / total
    lean = "fair" if abs(share - 0.5) <= FAIR_BAND else "you" if share > 0.5 else "them"
    return {"share": round(share, 3), "lean": lean, "pct": round(abs(share - 0.5) * 200)}


def read_text(g: Mapping[str, Any], partner: str) -> str:
    first = partner.split(" ")[0] if partner else "They"
    if g["share"] is None:
        return "Pick what each side gets."
    if g["lean"] == "fair":
        return f"About even on today's dynasty value. {first} should see it as fair."
    if g["lean"] == "you":
        return f"About {g['pct']}% in your favor on today's dynasty value. {first} may counter."
    return f"About {g['pct']}% in {first}'s favor on today's dynasty value. You're paying a premium."


def offer_items(me: str, them: str, give: Iterable[Mapping[str, Any]], get: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Builder picks -> the stored offer rows (contract or pick ids plus who gives and gets)."""
    out = []
    for item, frm, to in [*((i, me, them) for i in give), *((i, them, me) for i in get)]:
        row = {"from_league_team_id": frm, "to_league_team_id": to}
        if item.get("contract_id"):
            row.update(contract_id=item["contract_id"], player_id=item.get("player_id"), name=item.get("name"))
        else:
            row.update(draft_pick_id=item["pick_id"], label=item.get("label"))
        out.append(row)
    return out


def _when(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def center(rows: Iterable[Mapping[str, Any]], me: str, owner_of: Mapping[str, str], info: Mapping[str, Mapping[str, Any]],
           now: datetime | None = None) -> dict[str, Any]:
    """The transaction center: offers to answer, trades you sent, and the last week's results."""
    now = now or datetime.now(timezone.utc)
    needs, sent, results = [], [], []
    for r in sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True):
        mine_from = str(r.get("from_league_team_id")) == me
        partner_id = str(r.get("to_league_team_id") if mine_from else r.get("from_league_team_id"))
        you_get, you_give = [], []
        for item in [*(r.get("players") or []), *(r.get("picks") or [])]:
            meta = info.get(str(item.get("player_id"))) or {}
            shown = {"kind": "player" if item.get("contract_id") else "pick", "player_id": item.get("player_id"),
                     "contract_id": item.get("contract_id"), "pick_id": item.get("draft_pick_id"),
                     "name": meta.get("full_name") or item.get("name") or item.get("label") or "Pick",
                     "pos": meta.get("position") or "", "nfl_team": meta.get("team") or ""}
            (you_get if str(item.get("to_league_team_id")) == me else you_give).append(shown)
        offer = {"id": str(r["id"]), "partner": owner_of.get(partner_id, "Another team"), "partner_team_id": partner_id,
                 "you_get": you_get, "you_give": you_give, "note": r.get("note"), "created_at": r.get("created_at"),
                 "status": r.get("status"), "decided_at": r.get("decided_at"), "is_counter": bool(r.get("parent_offer_id"))}
        if r.get("status") == "pending":
            (sent if mine_from else needs).append(offer)
            continue
        decided = _when(r.get("decided_at"))
        if decided and now - decided <= timedelta(days=RESULT_DAYS) and r.get("status") in {"accepted", "declined", "void"}:
            if r.get("status") == "declined" and not mine_from:
                continue  # you declined it yourself: nothing to report
            results.append({**offer, "kind": "trade", "sent_by_you": mine_from})
    return {"needs_answer": needs, "sent": sent, "results": results, "pending": len(needs) + len(sent)}


def result_line(r: Mapping[str, Any]) -> str:
    """'Grady M declined your trade' and friends, for the Results list."""
    who = r.get("partner") or "Another team"
    if r.get("status") == "accepted":
        return f"{who} accepted your trade" if r.get("sent_by_you") else f"You accepted {who}'s trade"
    if r.get("status") == "declined":
        return f"{who} declined your trade"
    return f"Trade with {who} fell through (a player moved)"


_UUID = re.compile(r"^[0-9a-f-]{36}$")


def is_uuid(value: str) -> bool:
    return bool(_UUID.match(str(value or "")))
