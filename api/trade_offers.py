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
    "offer_cap_amount_invalid": "Cap space has to be more than $0, once per side for each season.",
    "offer_cap_season_invalid": "Cap space can be sent for this season or the next three.",
    "trade_deadline_passed": "The trade deadline has passed. Trading reopens the day after the championship.",
    "offer_one_sided": "Both teams have to get something.",
    "offer_not_open": "That offer was already answered.",
    "not_your_offer": "That offer isn't yours to answer.",
    "offer_action_invalid": "Unknown action.",
    "trade_pick_lifecycle_not_tradeable": "That pick's draft isn't open for trades.",
}


CLOSED = "Trades are closed until after the season."


def deadline_week(text: Any) -> int | None:
    """The league's trade deadline week from Settings ("week 11", "11", "Week 11 kickoff"), None when unset."""
    match = re.search(r"\d+", str(text or ""))
    return int(match.group()) if match else None


def window(deadline_text: Any, current_week: int, last_week: int) -> dict[str, Any]:
    """Whether trades are open this week. Closed after the deadline week until the season is over (the week after
    the championship), so the offseason is open again."""
    deadline = deadline_week(deadline_text)
    closed = deadline is not None and deadline < current_week <= last_week
    return {"open": not closed, "deadline_week": deadline, "reopens_after_week": last_week,
            "message": f"The trade deadline was Week {deadline}. Trading reopens the day after the championship." if closed else None}


def league_window(client: Any, league_id: str) -> dict[str, Any]:
    """window() for a league from its Settings deadline, this NFL week and its last playoff week (app and website)."""
    from api import league, playoffs
    from league_ai.settings_store import load_settings
    try:
        deadline = load_settings(client, league_id).trade_deadline
    except Exception:
        return {"open": True, "deadline_week": None, "reopens_after_week": None, "message": None}
    try:
        last = playoffs.last_week(league.playoff_rules(client, league_id))
    except Exception:
        last = 17
    return window(deadline, league.current_week(), last)


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


class ReadPricer:
    """Prices for the GM read, on the dynasty market scale, the way the trade engine (league_ai.trade_value) does it:

    - a player is his market value plus the contract's surplus or cost: how far his cap hit sits from what this league
      pays players of his value, over the years left (up to 3), at CONTRACT_WEIGHT. A cheap long deal adds value, an
      expensive one or one about to expire adds little.
    - a dollar of cap space (any season) is worth CAP_DOLLAR_SHARE of what a rostered dollar buys in this league.
    - a pick is priced by our own pick book (league_ai.pick_values: real drafts blended with the market's slot curve),
      with next year's pick placed by its original owner's record.
    """

    def __init__(self, roster: Iterable[Mapping[str, Any]], values: Mapping[str, float], season: int,
                 pick_rows: list[Mapping[str, Any]] | None = None, teams: int = 10,
                 records: Mapping[str, tuple[int, int]] | None = None):
        from league_ai.pick_values import PickBook
        from league_ai.trade_value import CAP_DOLLAR_SHARE, TOP_SHARE, TOP_WORTH

        rows = [r for r in roster if r.get("sleeper_player_id")]
        hit = lambda r: _f(r.get("contract_cap_hit") or r.get("cap_hit"))  # noqa: E731
        capped = [r for r in rows if hit(r) > 0]
        hits = sorted((hit(r) for r in capped), reverse=True)
        n_teams = len({str(r.get("league_team_id")) for r in capped}) or 1
        top = TOP_SHARE * sum(hits) / n_teams
        by_value = sorted(capped, key=lambda r: -values.get(str(r["sleeper_player_id"]), 0.0))
        self.fair = {str(r["sleeper_player_id"]): hits[i] * (TOP_WORTH if hits[i] >= top else 1.0) for i, r in enumerate(by_value)}
        self.values = values
        total_cap = sum(hits)
        self.cap_dollar = CAP_DOLLAR_SHARE * sum(values.get(str(r["sleeper_player_id"]), 0.0) for r in capped) / total_cap if total_cap else 0.0
        self.book = PickBook(pick_rows or [], teams=teams, rounds=3, season=season, records=records)

    def player(self, pid: str, salary: float, years: int) -> float:
        from league_ai.trade_value import CONTRACT_WEIGHT

        base = float(self.values.get(str(pid), 0.0))
        fair = self.fair.get(str(pid))
        if salary <= 0 or not fair or not base:
            return base
        gap = max(-1.0, min(1.0, (fair - salary) / max(fair, salary)))
        return base + CONTRACT_WEIGHT * gap * min(max(int(years or 1), 1), 3) / 3 * base

    def pick(self, year: int, rnd: int, original_team_id: str | None) -> float:
        return self.book.value(f"{year} R{rnd}", original_team_id)[0]

    def cap(self, dollars: float) -> float:
        return max(0.0, float(dollars or 0.0)) * self.cap_dollar


def build_pricer(client: Any, state: Mapping[str, Any], season: int, teams: int = 10,
                 records: Mapping[str, tuple[int, int]] | None = None) -> ReadPricer:
    """League-wide prices for the GM read (shared by the app's trade page and the website's trade window)."""
    from api.trade_feed import market_now
    from league_ai.trade_finder import _pick_rows
    ids = sorted({str(r.get("sleeper_player_id")) for r in state.get("roster", []) if r.get("sleeper_player_id")})
    return ReadPricer(state.get("roster", []), market_now(client, ids), int(season), pick_rows=_pick_rows(client), teams=teams, records=records)


def side(state: Mapping[str, Any], team_id: str, info: Mapping[str, Mapping[str, Any]], ppg: Mapping[str, float],
         values: Mapping[str, float], picks_value: Any, owner_of: Mapping[str, str], pricer: ReadPricer | None = None) -> dict[str, Any]:
    """One team's tradeable players and picks, best first. With a pricer, each 'value' is the GM read's price
    (contract and our own pick values counted); without one, the plain market value."""
    players = []
    for r in state.get("roster", []):
        if str(r.get("league_team_id")) != team_id or not r.get("contract_agreement_id"):
            continue
        pid = str(r.get("sleeper_player_id"))
        meta = info.get(pid) or {}
        salary, years = _f(r.get("contract_cap_hit") or r.get("cap_hit")), int(r.get("contract_years_left") or 1)
        players.append({
            "player_id": pid, "contract_id": str(r["contract_agreement_id"]),
            "name": meta.get("full_name") or r.get("player_name") or "Player", "pos": meta.get("position") or r.get("position") or "",
            "nfl_team": meta.get("team") or "FA", "salary": salary, "years": years, "ppg": ppg.get(pid),
            "value": round(pricer.player(pid, salary, years) if pricer else values.get(pid, 0.0)),
        })
    picks = []
    for r in state.get("draft_picks", []):
        if str(r.get("current_owner_league_team_id")) != team_id or str(r.get("asset_status") or "tradable") != "tradable" or not r.get("stable_pick_id"):
            continue
        year, rnd = int(r.get("draft_year") or 0), int(r.get("round_number") or 0)
        price = pricer.pick(year, rnd, str(r.get("original_league_team_id") or "")) if pricer else picks_value(year, rnd)
        picks.append({"pick_id": str(r["stable_pick_id"]), "label": pick_label(r, owner_of), "year": year, "round": rnd, "value": round(price)})
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


CapInput = Any  # a number (this season) or {season: amount} / [{season, amount}] for several seasons


def cap_years(value: CapInput) -> dict[int | None, float]:
    """Cap space one side sends, by season. A plain number is this season (key None: the database fills it in)."""
    if isinstance(value, Mapping):
        pairs = list(value.items())
    elif isinstance(value, (list, tuple)):
        pairs = [(i.get("season"), i.get("amount")) for i in value if isinstance(i, Mapping)]
    else:
        pairs = [(None, value)]
    out: dict[int | None, float] = {}
    for season, amount in pairs:
        try:
            amount = round(float(amount or 0), 2)
            key = int(season) if season not in (None, "") else None
        except (TypeError, ValueError):
            continue
        if amount > 0:
            out[key] = round(out.get(key, 0.0) + amount, 2)
    return out


def cap_total(value: CapInput) -> float:
    return round(sum(cap_years(value).values()), 2)


def cap_text(value: CapInput) -> str:
    """'$5 cap space', or '$8 cap space ($5 in 2026, $3 in 2027)' across seasons."""
    years = cap_years(value)
    if not years:
        return ""
    total = f"${sum(years.values()):g} cap space"
    if len(years) == 1 and None in years:
        return total
    return total + " (" + ", ".join(f"${a:g} in {y}" if y else f"${a:g} this season" for y, a in sorted(years.items(), key=lambda kv: kv[0] or 0)) + ")"


def cap_items(me: str, them: str, give_cap: CapInput, get_cap: CapInput) -> list[dict[str, Any]]:
    """Cap space in an offer: [{from_league_team_id, to_league_team_id, amount, season?}], any amount (no limit).
    Each side can send a different amount in each season (this one and the next three)."""
    out = []
    for value, frm, to in ((give_cap, me, them), (get_cap, them, me)):
        for season, amount in sorted(cap_years(value).items(), key=lambda kv: kv[0] or 0):
            row = {"from_league_team_id": frm, "to_league_team_id": to, "amount": amount}
            if season:
                row["season"] = season
            out.append(row)
    return out


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


def offer_request(league_id: str, me: str, them: str, give: Iterable[Mapping[str, Any]], get: Iterable[Mapping[str, Any]],
                  give_cap: CapInput = 0, get_cap: CapInput = 0, note: str | None = None, parent_offer_id: str | None = None) -> dict[str, Any]:
    """The propose_trade_offer_authenticated request for an offer (the trade screen and the GM's confirm card send the same one)."""
    items = offer_items(me, them, give, get)
    return {
        "league_id": league_id, "to_league_team_id": them,
        "players": [i for i in items if i.get("contract_id")], "picks": [i for i in items if i.get("draft_pick_id")],
        "cap": cap_items(me, them, give_cap, get_cap),
        "note": (note or "")[:280] or None, "parent_offer_id": parent_offer_id if parent_offer_id and is_uuid(parent_offer_id) else None,
    }


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
        for item in r.get("cap") or []:
            amount, season = float(item.get("amount") or 0), item.get("season")
            shown = {"kind": "cap", "amount": amount, "season": season, "pos": "", "nfl_team": "",
                     "name": f"${amount:g} cap space" + (f" ({season})" if season else "")}
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
