"""Moves the Assistant GM proposes and the owner confirms: lineups, trade offers, bids, adds and drops.

The GM never makes a move. Its propose_* tools check the move against the owner's roster right now and put a
card under the answer; nothing happens until the owner taps Confirm. Confirming goes through the very same
database calls the owner's own taps use (set_lineup_authenticated, propose_trade_offer_authenticated,
place_waiver_claim_authenticated, add_free_agent_authenticated, drop_player_authenticated), with the owner's own
sign-in, so every league rule (kickoff locks, slots, taxi/IR, the trade deadline, FAAB, roster size) is checked
exactly as if they had done it by hand. The tools are only offered while the league's switch
(league_app_transactions.enabled) is on.

Each card is the decision itself: two or three options (the GM's pick highlighted, e.g. "Start Odunze" or
"Keep Pittman"; "Waddle" or "Waddle + 2027 2nd"), and tapping one makes it. "Keep" options change nothing.

A card is saved with the conversation ({"moves": [...]} on the assistant message), so it shows again when the
chat is reopened, and its status (pending, done, failed, dismissed) goes back to the GM in the next turn.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from api import lineups as lineup_rules
from api import trade_offers, waivers

PENDING, DONE, FAILED, DISMISSED = "pending", "done", "failed", "dismissed"
STATUS_WORD = {PENDING: "waiting for the owner to confirm", DONE: "confirmed and done", FAILED: "tried, but the league refused it",
               DISMISSED: "dismissed by the owner"}
MAX_MOVES = 3  # cards per answer

CARD_NOTE = ("A card with these options now shows under your answer, your pick highlighted. Nothing has happened yet: the owner taps the "
             "option they want. Don't repeat the options at length or say anything is done; at most say they can tap to make it.")


class MoveError(Exception):
    """A move the GM proposed that doesn't fit the roster or the rules right now (the GM is told why)."""


@dataclass
class Tray:
    """The cards proposed in one answer."""
    moves: list[dict[str, Any]] = field(default_factory=list)

    def add(self, move: dict[str, Any]) -> dict[str, Any]:
        # the same move proposed twice in one answer replaces the first card
        self.moves = [m for m in self.moves if m.get("key") != move.get("key")]
        if len(self.moves) >= MAX_MOVES:
            raise MoveError(f"at most {MAX_MOVES} move cards per answer")
        self.moves.append(move)
        return move


def option(oid: str, label: str, sub: str = "", action: dict[str, Any] | None = None, pick: bool = False) -> dict[str, Any]:
    """One button on a card. action None = keep things as they are."""
    return {"id": oid, "label": label, "sub": sub, "pick": pick, "action": action}


def _new(kind: str, key: str, title: str, lines: list[str], options: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "kind": kind, "key": key, "title": title, "lines": lines, "options": options, "status": PENDING}


def public(move: Mapping[str, Any]) -> dict[str, Any]:
    """A card as the app and website show it (never the raw requests behind the options)."""
    out = {k: move.get(k) for k in ("id", "kind", "title", "lines", "status", "result", "chosen")}
    out["options"] = [{k: o.get(k) for k in ("id", "label", "sub", "pick")} for o in move.get("options") or []]
    return out


def history_note(moves: Iterable[Mapping[str, Any]]) -> str:
    """What the GM sees about earlier cards in the next turn."""
    rows = []
    for m in moves or []:
        opts = " / ".join(f"{o.get('label')}{' (your pick)' if o.get('pick') else ''}" for o in m.get("options") or [])
        chosen = next((o.get("label") for o in m.get("options") or [] if o.get("id") == m.get("chosen")), None)
        rows.append(f"- {m.get('title')}: {opts} ({STATUS_WORD.get(str(m.get('status')), m.get('status'))}"
                    f"{', owner chose ' + str(chosen) if chosen else ''}{': ' + str(m['result']) if m.get('result') else ''})")
    return "[Move cards under this answer]\n" + "\n".join(rows) if rows else ""


def short(name: str) -> str:
    """Last name for a button ('Michael Pittman Jr.' -> 'Pittman')."""
    parts = [p for p in str(name or "").split() if p.rstrip(".").lower() not in {"jr", "sr", "ii", "iii", "iv", "v"}]
    return parts[-1] if parts else str(name)


# ---- finding players and teams ---------------------------------------------------------------------------------


def _fold(text: Any) -> str:
    return " ".join(str(text or "").replace(".", "").replace("'", "").casefold().split())


def pick_one(query: str, options: Mapping[str, str], what: str) -> str:
    """The id in options ({id: name}) that query names: an id, the full name, or a unique part of it."""
    q = _fold(query)
    if not q:
        raise MoveError(f"no {what} given")
    if query in options:
        return query
    exact = [i for i, n in options.items() if _fold(n) == q]
    if len(exact) == 1:
        return exact[0]
    part = [i for i, n in options.items() if q in _fold(n)] or [i for i, n in options.items() if _fold(n).split(" ")[-1:] == q.split(" ")[-1:]]
    if len(part) == 1:
        return part[0]
    if not part:
        raise MoveError(f"no {what} matching '{query}'")
    raise MoveError(f"'{query}' matches more than one {what}: " + ", ".join(sorted(options[i] for i in part)[:6]))


def _roster_rows(state: Mapping[str, Any], team_id: str) -> list[Mapping[str, Any]]:
    return [r for r in state.get("roster") or [] if str(r.get("league_team_id")) == str(team_id)]


def _pid(row: Mapping[str, Any]) -> str:
    return str(row.get("sleeper_player_id") or row.get("sleeper_id") or "")


def _name(row: Mapping[str, Any]) -> str:
    return str(row.get("player_name") or row.get("player") or "Player")


# ---- lineups --------------------------------------------------------------------------------------------------


def apply_lineup(slots: list[str], starters: list[str | None], changes: Iterable[Mapping[str, Any]], *, roster: Iterable[str],
                 pos: Mapping[str, str], inactive: Iterable[str], locked: Iterable[str], names: Mapping[str, str]) -> list[str | None]:
    """The starters after each change ({"start": player id, "bench": player id or None}), keeping every slot legal.

    A starter goes straight into the benched player's slot when he fits it; otherwise he takes another slot he fits
    (a FLEX, say) and that slot's player slides into the open one. "bench" left out fills an empty slot.
    """
    ids = list(starters) + [None] * (len(slots) - len(starters))
    roster, inactive, locked = set(roster), set(inactive), set(locked)
    label = lambda p: names.get(p) or p  # noqa: E731
    for ch in changes:
        s, b = str(ch.get("start") or ""), (str(ch["bench"]) if ch.get("bench") else None)
        if s not in roster:
            raise MoveError(f"{label(s)} isn't on this roster")
        if s in inactive:
            raise MoveError(f"{label(s)} is on taxi or IR and can't start")
        if s in ids:
            raise MoveError(f"{label(s)} is already starting")
        if s in locked:
            raise MoveError(f"{label(s)}'s game has started, so he's locked")
        if b is not None and b not in ids:
            raise MoveError(f"{label(b)} isn't in the starting lineup")
        if b is not None and b in locked:
            raise MoveError(f"{label(b)}'s game has started, so he's locked in")
        open_slots = [ids.index(b)] if b is not None else [i for i, x in enumerate(ids) if x is None]
        if not open_slots:
            raise MoveError("there's no empty slot; say who to bench")
        placed = False
        for k in open_slots:
            if lineup_rules.fits(slots[k], pos.get(s, "")):
                ids[k] = s
                placed = True
                break
        if not placed:
            k = open_slots[0]
            for j, other in enumerate(ids):
                if j == k or other is None or other in locked:
                    continue
                if lineup_rules.fits(slots[j], pos.get(s, "")) and lineup_rules.fits(slots[k], pos.get(other, "")):
                    ids[k], ids[j] = other, s
                    placed = True
                    break
        if not placed:
            raise MoveError(f"{label(s)} ({pos.get(s) or '?'}) doesn't fit an open slot" + (f" in place of {label(b)}" if b else ""))
    return ids


def lineup_tool(data: Callable[[], Mapping[str, Any]], team_id: str, tray: Tray) -> Any:
    from league_ai.tools import ToolSpec

    def run(changes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        try:
            d = data()
            team = next((t for t in d["teams"] if str(t["team_id"]) == str(team_id)), None)
            if team is None:
                raise MoveError("this owner's team wasn't found")
            mine = {p: d["names"].get(p, p) for p in team["roster"]}
            pos = {p: str((d["info"].get(p) or {}).get("position") or "") for p in team["roster"]}
            wanted = [{"start": pick_one(str(c.get("start") or ""), mine, "player on this roster"),
                       "bench": pick_one(str(c["bench"]), mine, "player on this roster") if c.get("bench") else None} for c in changes or []]
            if not wanted:
                raise MoveError("no changes given")
            new = apply_lineup(d["slots"], team["starters"], wanted, roster=team["roster"], pos=pos, inactive=d["designation"].keys(),
                               locked=d["locked"], names=d["names"])
        except MoveError as exc:
            return {"error": f"Can't propose that lineup: {exc}."}
        proj = d.get("proj") or {}
        pts = lambda p: f"{proj[p]:.1f} proj" if p in proj else ""  # noqa: E731
        name = lambda p: d["names"].get(p) or p  # noqa: E731
        go = {"week": d["week"], "changes": wanted}
        key = "lineup:" + ",".join(sorted(c["start"] for c in wanted))
        if len(wanted) == 1 and wanted[0]["bench"]:
            s_, b_ = wanted[0]["start"], wanted[0]["bench"]
            slot = lineup_rules.label(d["slots"][team["starters"].index(b_)])
            options = [option("go", f"Start {short(name(s_))}", " · ".join(x for x in (pts(s_), "GM pick") if x), go, pick=True),
                       option("keep", f"Keep {short(name(b_))}", pts(b_))]
            move = tray.add(_new("lineup", key, f"Week {d['week']} {slot}", [], options))
        else:
            lines = []
            for c in wanted:
                slot = lineup_rules.label(d["slots"][new.index(c["start"])])
                lines.append(f"Start {name(c['start'])} at {slot}" + (f", {pts(c['start'])}" if pts(c["start"]) else "")
                             + (f" over {name(c['bench'])}" if c["bench"] else ""))
            options = [option("go", "Make these changes", "GM pick", go, pick=True), option("keep", "Keep my lineup")]
            move = tray.add(_new("lineup", key, f"Week {d['week']} lineup", lines, options))
        return {"card": public(move), "note": CARD_NOTE}

    return ToolSpec(
        "propose_lineup",
        "Put a decision card under your answer: your call ('Start Odunze', highlighted) next to keeping the lineup as set ('Keep Pittman'); "
        "the owner taps one (you can't set it yourself). Use it when you recommend "
        "a start/sit change to the lineup this owner has SET now (check get_lineups), or when they ask you to set it. Each change starts "
        "one player and benches another (bench may be left out to fill an empty slot); flex slots are handled for you. Players are names "
        "or ids from this owner's roster. Taxi, IR and locked players are refused.",
        {"type": "object", "properties": {"changes": {"type": "array", "items": {"type": "object", "properties": {
            "start": {"type": "string", "description": "Player to start"},
            "bench": {"type": "string", "description": "Starter to bench (omit to fill an empty slot)"}}, "required": ["start"]}}},
         "required": ["changes"]}, run)


def confirm_lineup(client: Any, league_id: str, season: int, team_id: str, action: Mapping[str, Any], data: Mapping[str, Any]) -> str:
    """Apply the option's changes to the lineup as it is NOW (it may have changed since) and save it like a tap would."""
    week = int(action["week"])
    if int(data.get("week") or 0) != week:
        raise MoveError(f"Week {week} is over; ask for a new lineup")
    team = next((t for t in data["teams"] if str(t["team_id"]) == str(team_id)), None)
    if team is None:
        raise MoveError("Your team wasn't found")
    pos = {p: str((data["info"].get(p) or {}).get("position") or "") for p in team["roster"]}
    new = apply_lineup(data["slots"], team["starters"], action["changes"], roster=team["roster"], pos=pos,
                       inactive=data["designation"].keys(), locked=data["locked"], names=data["names"])
    try:
        client.rpc("set_lineup_authenticated", {"p_request": {
            "league_id": league_id, "season": season, "week": week,
            "slots": [{"slot": s, "player_id": new[i] or None} for i, s in enumerate(data["slots"])]}}).execute()
    except Exception as exc:
        raise MoveError(lineup_rules.friendly(exc))
    return "Lineup saved."


# ---- trades ---------------------------------------------------------------------------------------------------


def _pick_options(state: Mapping[str, Any], team_id: str, owner_of: Mapping[str, str]) -> dict[str, str]:
    return {str(r["stable_pick_id"]): trade_offers.pick_label(r, owner_of) for r in state.get("draft_picks") or []
            if str(r.get("current_owner_league_team_id")) == str(team_id) and r.get("stable_pick_id")
            and str(r.get("asset_status") or "tradable") == "tradable"}


def _merge_cap(amount: Any, by_year: Any) -> Any:
    """The GM's give_cap (this season) plus give_cap_by_year ({season: dollars}) as one {season: amount} map."""
    years = trade_offers.cap_years(by_year or {})
    for season, value in trade_offers.cap_years(amount or 0).items():
        years[season] = round(years.get(season, 0.0) + value, 2)
    return {("" if k is None else k): v for k, v in years.items()} if years else 0


def _cap_body(value: Any, season: Any = None) -> float | dict[str, float]:
    """Cap for the offer body: a plain number when it's all this season, else {"2026": 5, "2027": 3}."""
    years = trade_offers.cap_years(value)
    if season:
        if None in years:
            years[int(season)] = round(years.get(int(season), 0.0) + years.pop(None), 2)
        if list(years) == [int(season)]:
            return years[int(season)]
    if not years:
        return 0.0
    if list(years) == [None]:
        return years[None]
    return {str(y): a for y, a in sorted(years.items(), key=lambda kv: kv[0] or 0) if y}


def build_offer(state: Mapping[str, Any], teams: list[Mapping[str, Any]], me: str, *, to_owner: str, give: list[str], get: list[str],
                give_picks: list[str], get_picks: list[str], give_cap: Any, get_cap: Any) -> tuple[str, dict[str, Any], list[str]]:
    """(partner team id, the offer body the trade screen sends, card lines) for an offer named in words."""
    owner_of = {str(t["id"]): str(t.get("owner_name") or "") for t in teams}
    them = pick_one(to_owner, {i: n for i, n in owner_of.items() if i != str(me)}, "other owner")

    def players(team: str, names: list[str]) -> list[dict[str, Any]]:
        rows = {str(r["contract_agreement_id"]): r for r in _roster_rows(state, team) if r.get("contract_agreement_id")}
        out = []
        for n in names:
            cid = pick_one(n, {c: _name(r) for c, r in rows.items()}, f"player on {owner_of.get(team)}'s roster")
            out.append({"contract_id": cid, "player_id": _pid(rows[cid]), "name": _name(rows[cid])})
        return out

    def picks(team: str, names: list[str]) -> list[dict[str, Any]]:
        opts = _pick_options(state, team, owner_of)
        out = []
        for n in names:
            # "2027 1st", "2027 R1" and "2027 round 1" all name the same pick
            q = _fold(n).replace("1st", "r1").replace("2nd", "r2").replace("3rd", "r3").replace("round ", "r")
            pid = pick_one(q, {i: _fold(lbl) for i, lbl in opts.items()}, f"pick {owner_of.get(team)} owns")
            out.append({"pick_id": pid, "label": opts[pid]})
        return out

    g, r = players(str(me), give) + picks(str(me), give_picks), players(them, get) + picks(them, get_picks)
    give_cap, get_cap = _cap_body(give_cap, state.get("season")), _cap_body(get_cap, state.get("season"))
    if not (g or give_cap) or not (r or get_cap):
        raise MoveError("each side has to send something")
    body = {"to_team_id": them, "give": g, "get": r, "give_cap": give_cap, "get_cap": get_cap}
    lines = [f"You send: {', '.join([p.get('name') or p.get('label') for p in g] + ([trade_offers.cap_text(give_cap)] if give_cap else []))}",
             f"You get: {', '.join([p.get('name') or p.get('label') for p in r] + ([trade_offers.cap_text(get_cap)] if get_cap else []))}"]
    return them, body, lines


def trade_tool(state: Callable[[], Mapping[str, Any]], teams: Callable[[], list[Mapping[str, Any]]], me: str, window: Callable[[], Mapping[str, Any]],
               tray: Tray) -> Any:
    from league_ai.tools import ToolSpec

    def run(to_owner: str = "", offers: list[dict[str, Any]] | None = None, note: str | None = None, **single: Any) -> dict[str, Any]:
        if not window().get("open"):
            return {"error": "Trades are closed (past the trade deadline), so no offer can be sent."}
        offers = (offers or [single])[:3]
        try:
            people = teams()
            built = []
            for o in offers:
                them, body, lines = build_offer(state(), people, me, to_owner=to_owner, give=o.get("give") or [], get=o.get("get") or [],
                                                give_picks=o.get("give_picks") or [], get_picks=o.get("get_picks") or [],
                                                give_cap=_merge_cap(o.get("give_cap"), o.get("give_cap_by_year")),
                                                get_cap=_merge_cap(o.get("get_cap"), o.get("get_cap_by_year")))
                if note:
                    body["note"] = str(note)[:280]
                built.append((o, them, body, lines))
        except MoveError as exc:
            return {"error": f"Can't build that offer: {exc}."}
        them = built[0][1]
        partner = next((str(t.get("owner_name") or "") for t in people if str(t["id"]) == them), "them")
        gets = {b[3][1] for b in built}
        same_get = len(gets) == 1
        title = f"Offer to {partner.split(' ')[0] or partner}" + (f" for {built[0][3][1].removeprefix('You get: ')}" if same_get else "")
        options = []
        for i, (o, _t, body, lines) in enumerate(built):
            label = str(o.get("label") or lines[0].removeprefix("You send: "))
            why = str(o.get("why") or "")
            sub = " · ".join(x for x in (("GM pick" if i == 0 else ""), why, ("" if same_get else lines[1])) if x)
            options.append(option(f"o{i + 1}", label, sub, body, pick=i == 0))
        lines = [f"Note to {partner.split(' ')[0]}: {note[:280]}"] if note else []
        key = "trade:" + them + ":" + "|".join(",".join(sorted(str(p.get("contract_id") or p.get("pick_id")) for p in b[2]["give"] + b[2]["get"])) for b in built)
        move = tray.add(_new("trade", key, title, lines, options))
        return {"card": public(move), "note": CARD_NOTE}

    return ToolSpec(
        "propose_trade",
        "Put an offer card under your answer: one to three offers to the same owner as buttons (your opening offer first and highlighted, "
        "then e.g. where it should close); the offer the owner taps goes to the other owner (you can't send it). Use it when the owner asks "
        "you to send, draft or build an offer, or says yes to one you suggested; never for a value question or a brainstorm. Players are names "
        "from each roster; picks like '2027 1st' (picks are tradable three drafts ahead); cap space in dollars: give_cap/get_cap for this "
        "season, or give_cap_by_year/get_cap_by_year like {\"2026\": 5, \"2027\": 3} to send different amounts in different seasons "
        "(this season and the next three). The other owner still has to accept it.",
        {"type": "object", "properties": {
            "to_owner": {"type": "string", "description": "The other owner's name"},
            "offers": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
                "label": {"type": "string", "description": "Short button text, e.g. 'Waddle' or 'Waddle + 2027 2nd'"},
                "why": {"type": "string", "description": "A few words under the button, e.g. 'open here' or 'where it closes'"},
                "give": {"type": "array", "items": {"type": "string"}, "description": "Players this owner sends"},
                "get": {"type": "array", "items": {"type": "string"}, "description": "Players this owner gets"},
                "give_picks": {"type": "array", "items": {"type": "string"}}, "get_picks": {"type": "array", "items": {"type": "string"}},
                "give_cap": {"type": "number"}, "get_cap": {"type": "number"},
                "give_cap_by_year": {"type": "object", "additionalProperties": {"type": "number"}, "description": "Season -> dollars this owner sends"},
                "get_cap_by_year": {"type": "object", "additionalProperties": {"type": "number"}, "description": "Season -> dollars this owner gets"}}}},
            "note": {"type": "string", "description": "Optional short note to the other owner, in this owner's voice"}},
         "required": ["to_owner", "offers"]}, run)


def confirm_trade(client: Any, league_id: str, team_id: str, b: Mapping[str, Any]) -> str:
    if not trade_offers.league_window(client, league_id).get("open"):
        raise MoveError(trade_offers.CLOSED)
    try:
        client.rpc("propose_trade_offer_authenticated", {"p_request": trade_offers.offer_request(
            league_id, team_id, b["to_team_id"], b.get("give") or [], b.get("get") or [], b.get("give_cap") or 0, b.get("get_cap") or 0,
            b.get("note"))}).execute()
    except Exception as exc:
        raise MoveError(trade_offers.friendly(exc))
    return "Offer sent. It's in Transactions."


# ---- bids, adds and drops --------------------------------------------------------------------------------------

WAIVER_RPC = {"bid": "place_waiver_claim_authenticated", "add": "add_free_agent_authenticated", "drop": "drop_player_authenticated"}


def waiver_tool(client: Any, state: Callable[[], Mapping[str, Any]], team_id: str, tray: Tray) -> Any:
    from league_ai.tools import ToolSpec

    def free_agent(query: str) -> tuple[str, str]:
        rostered = {_pid(r) for r in state().get("roster") or []}
        if query.isdigit():
            rows = client.table("nfl_players").select("sleeper_id,full_name,position,team").eq("sleeper_id", query).limit(1).execute().data or []
        else:
            rows = (client.table("nfl_players").select("sleeper_id,full_name,position,team").ilike("full_name", f"%{query.strip()}%")
                    .limit(12).execute().data or [])
        opts = {str(r["sleeper_id"]): f"{r.get('full_name')}" for r in rows if str(r["sleeper_id"]) not in rostered}
        pid = pick_one(query, opts, "free agent")
        meta = next(r for r in rows if str(r["sleeper_id"]) == pid)
        return pid, f"{meta.get('full_name')} ({meta.get('position') or '?'}, {meta.get('team') or 'FA'})"

    def run(action: str = "", player: str = "", bid: int = 0, drop: str | None = None, other_bids: list[int] | None = None) -> dict[str, Any]:
        if action not in WAIVER_RPC:
            return {"error": "action must be bid, add or drop"}
        try:
            mine = {_pid(r): _name(r) for r in _roster_rows(state(), team_id) if _pid(r)}
            if action == "drop":
                pid = pick_one(player, mine, "player on this roster")
                title, lines = f"Drop {mine[pid]}?", ["Dead cap is charged for any seasons still owed."]
                options = [option("o1", f"Drop {short(mine[pid])}", "GM pick", {"type": "drop", "player_id": pid}, pick=True),
                           option("keep", f"Keep {short(mine[pid])}")]
            else:
                pid, label = free_agent(player)
                body = {"player_id": pid, "drop_player_id": pick_one(drop, mine, "player on this roster") if drop else None}
                lines = [f"Drop {mine[body['drop_player_id']]} to make room" ] if body["drop_player_id"] else []
                if action == "bid":
                    title = f"Bid on {label}"
                    lines.append("Bids run Wednesday and Sunday at 3 AM ET.")
                    amounts = [int(bid)] + [int(b) for b in other_bids or [] if int(b) != int(bid)][:2]
                    options = [option(f"o{i + 1}", f"Bid ${a}", "GM pick" if i == 0 else "", {"type": "bid", **body, "bid": a}, pick=i == 0)
                               for i, a in enumerate(amounts)]
                else:
                    title = f"Add {label} at $1"
                    options = [option("o1", f"Add {short(label.split(' (')[0])}", "GM pick", {"type": "add", **body}, pick=True)]
        except MoveError as exc:
            return {"error": f"Can't propose that move: {exc}."}
        move = tray.add(_new(action, f"{action}:{pid}", title, lines, options))
        return {"card": public(move), "note": CARD_NOTE}

    return ToolSpec(
        "propose_waiver_move",
        "Put a card under your answer for a FAAB bid, a $1 free-agent add (Sundays after the 3 AM run) or a drop; it happens only when the "
        "owner taps Confirm (you can't do it). Use it when the owner asks you to make the move or says yes to one you suggested. For bid/add "
        "the player is a free agent (name or player id from search_players); drop is someone on this owner's roster (needed when the roster is full).",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["bid", "add", "drop"]},
            "player": {"type": "string", "description": "Player name or id"},
            "bid": {"type": "integer", "description": "Whole-dollar FAAB bid you recommend (bid only)"},
            "other_bids": {"type": "array", "items": {"type": "integer"}, "description": "Up to two other amounts worth offering as buttons (bid only)"},
            "drop": {"type": "string", "description": "Player to drop to make room (bid/add only)"}},
         "required": ["action", "player"]}, run)


def confirm_waiver(client: Any, league_id: str, action: Mapping[str, Any]) -> str:
    a = dict(action)
    kind = a.pop("type")
    try:
        client.rpc(WAIVER_RPC[kind], {"p_request": {"league_id": league_id, **{k: v for k, v in a.items() if v is not None}}}).execute()
    except Exception as exc:
        raise MoveError(waivers.friendly(exc))
    return {"bid": "Bid placed.", "add": "Player added.", "drop": "Player dropped."}[kind]


# ---- the tools for one answer, and confirming a card -----------------------------------------------------------


def tools(session: Any, fo: Mapping[str, Any], state: Mapping[str, Any], tray: Tray, lineup_data: Callable[[], Mapping[str, Any]]) -> list[Any]:
    o, client = session.owner, session.client
    teams = lambda: client.table("league_teams").select("id,owner_name").eq("league_id", o.league_id).execute().data or []  # noqa: E731
    return [lineup_tool(lineup_data, o.league_team_id, tray),
            trade_tool(lambda: state, teams, o.league_team_id, lambda: trade_offers.league_window(client, o.league_id), tray),
            waiver_tool(client, lambda: state, o.league_team_id, tray)]


def find(messages: list[Mapping[str, Any]], move_id: str) -> tuple[int, dict[str, Any]] | None:
    for i, m in enumerate(messages):
        for mv in m.get("moves") or []:
            if str(mv.get("id")) == move_id:
                return i, dict(mv)
    return None


KEPT = {"lineup": "Kept your lineup.", "drop": "Kept him."}


def settle(client: Any, *, league_id: str, season: int, team_id: str, move: Mapping[str, Any], action: str,
           lineup_data: Callable[[], Mapping[str, Any]], option_id: str | None = None) -> dict[str, Any]:
    """The owner tapped an option (action 'confirm', the GM's pick when option_id is left out) or dismissed the card:
    the move with its new status and the line to show."""
    move = dict(move)
    if move.get("status") != PENDING:
        raise MoveError("That card was already handled")
    if action == "dismiss":
        return {**move, "status": DISMISSED, "result": None}
    opts = move.get("options") or []
    chosen = next((o for o in opts if o.get("id") == option_id), None) if option_id else next((o for o in opts if o.get("pick")), None)
    if chosen is None:
        raise MoveError("That option wasn't found")
    when = datetime.now(ZoneInfo("UTC")).isoformat()
    if chosen.get("action") is None:
        return {**move, "status": DONE, "chosen": chosen["id"], "result": KEPT.get(move["kind"], "Kept as is."), "done_at": when}
    try:
        if move["kind"] == "lineup":
            text = confirm_lineup(client, league_id, season, team_id, chosen["action"], lineup_data())
        elif move["kind"] == "trade":
            text = confirm_trade(client, league_id, team_id, chosen["action"])
        else:
            text = confirm_waiver(client, league_id, chosen["action"])
    except MoveError as exc:
        return {**move, "status": FAILED, "chosen": chosen["id"], "result": str(exc)}
    return {**move, "status": DONE, "chosen": chosen["id"], "result": text, "done_at": when}
