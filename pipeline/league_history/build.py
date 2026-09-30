"""Pure transforms: Sleeper payloads (+ app contract records) -> history event and matchup rows."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from pipeline.nfl_data.common import normalize_name, now_iso, to_float, to_int, to_text


def _ts(ms: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


class OwnerMap:
    """roster_id / user_id -> owner display name, preferring the app's league_teams names."""

    def __init__(self, users: list[dict[str, Any]], rosters: list[dict[str, Any]], league_teams: list[dict[str, Any]] | None = None):
        self.by_user: dict[str, str] = {}
        for u in users:
            uid = to_text(u.get("user_id"))
            name = to_text((u.get("metadata") or {}).get("team_name")) or to_text(u.get("display_name")) or to_text(u.get("username"))
            if uid and name:
                self.by_user[uid] = name
        for t in league_teams or []:  # app names win
            uid = to_text(t.get("sleeper_user_id"))
            if uid and t.get("owner_name"):
                self.by_user[uid] = str(t["owner_name"])
        self.by_roster: dict[int, str] = {}
        app_by_roster = {to_int(t.get("sleeper_roster_id")): str(t.get("owner_name")) for t in (league_teams or []) if t.get("sleeper_roster_id") and t.get("owner_name")}
        for r in rosters:
            rid = to_int(r.get("roster_id"))
            if rid is None:
                continue
            self.by_roster[rid] = app_by_roster.get(rid) or self.by_user.get(to_text(r.get("owner_id")) or "") or f"Roster {rid}"

    def name(self, roster_id: Any) -> str:
        rid = to_int(roster_id)
        return self.by_roster.get(rid, f"Roster {rid}") if rid is not None else "Unknown"


def build_transaction_events(league_id: str, season: int, transactions: Iterable[dict[str, Any]], owners: OwnerMap, players: Mapping[str, dict[str, Any]], contracts: Mapping[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One event per player leg (and per pick leg) of each completed Sleeper transaction."""
    stamp = now_iso()
    contracts = contracts or {}
    rows: list[dict[str, Any]] = []

    def pinfo(pid: str) -> tuple[str, str | None]:
        p = players.get(str(pid)) or {}
        name = to_text(p.get("full_name")) or " ".join(x for x in [to_text(p.get("first_name")), to_text(p.get("last_name"))] if x) or f"player {pid}"
        return name, to_text(p.get("position"))

    for tx in transactions:
        if tx.get("status") != "complete":
            continue
        tid = to_text(tx.get("transaction_id"))
        ttype = to_text(tx.get("type")) or "unknown"
        week = to_int(tx.get("leg")) or to_int(tx.get("week"))
        when = _ts(tx.get("status_updated") or tx.get("created"))
        adds = tx.get("adds") or {}
        drops = tx.get("drops") or {}
        bid = to_float((tx.get("settings") or {}).get("waiver_bid"))
        if ttype == "trade":
            participants = sorted({owners.name(r) for r in (tx.get("roster_ids") or [])})
            legs = []
            for pid, to_rid in adds.items():
                name, pos = pinfo(pid)
                from_rid = drops.get(pid)
                legs.append((pid, name, pos, owners.name(from_rid) if from_rid is not None else None, owners.name(to_rid)))
            pick_legs = []
            for pk in tx.get("draft_picks") or []:
                pick_legs.append((f"{pk.get('season')} R{pk.get('round')}", owners.name(pk.get("previous_owner_id")), owners.name(pk.get("owner_id")), owners.name(pk.get("roster_id"))))
            faab_legs = [(f"${b.get('amount')} FAAB", owners.name(b.get("sender")), owners.name(b.get("receiver"))) for b in tx.get("waiver_budget") or []]
            summary_parts = []
            for _, name, _, frm, to in legs:
                summary_parts.append(f"{name} {frm} -> {to}" if frm else f"{name} -> {to}")
            for label, frm, to, orig in pick_legs:
                summary_parts.append(f"{label} pick (orig. {orig}) {frm} -> {to}")
            for label, frm, to in faab_legs:
                summary_parts.append(f"{label} {frm} -> {to}")
            summary = f"Trade ({' / '.join(participants)}): " + "; ".join(summary_parts)
            details = {"participants": participants, "players": [{"player": n, "position": p, "from": f, "to": t} for _, n, p, f, t in legs], "picks": [{"pick": l, "from": f, "to": t, "original_owner": o} for l, f, t, o in pick_legs], "faab": [{"amount": l, "from": f, "to": t} for l, f, t in faab_legs]}
            for i, (pid, name, pos, frm, to) in enumerate(legs):
                c = contracts.get(str(pid)) or {}
                rows.append({"league_id": league_id, "event_id": f"sleeper:{tid}:p{i}", "kind": "trade", "season": season, "week": week, "occurred_at": when,
                             "owner_name": to, "counterparty_names": [x for x in participants if x != to], "player_name": name, "player_id": str(pid), "position": pos,
                             "faab_bid": None, "contract_salary": c.get("salary"), "contract_years": c.get("years"), "details": details, "summary": summary, "source": "sleeper", "refreshed_at": stamp})
            for i, (label, frm, to, orig) in enumerate(pick_legs):
                rows.append({"league_id": league_id, "event_id": f"sleeper:{tid}:k{i}", "kind": "trade", "season": season, "week": week, "occurred_at": when,
                             "owner_name": to, "counterparty_names": [x for x in participants if x != to], "player_name": f"{label} pick (orig. {orig})", "player_id": None, "position": "PICK",
                             "faab_bid": None, "contract_salary": None, "contract_years": None, "details": details, "summary": summary, "source": "sleeper", "refreshed_at": stamp})
            if not legs and not pick_legs:
                rows.append({"league_id": league_id, "event_id": f"sleeper:{tid}", "kind": "trade", "season": season, "week": week, "occurred_at": when, "owner_name": participants[0] if participants else None,
                             "counterparty_names": participants[1:], "player_name": None, "player_id": None, "position": None, "faab_bid": None, "contract_salary": None, "contract_years": None,
                             "details": details, "summary": summary, "source": "sleeper", "refreshed_at": stamp})
            continue
        # waiver / free agent / commissioner
        kind_add = "waiver" if ttype == "waiver" else ("commissioner" if ttype == "commissioner" else "add")
        for pid, rid in adds.items():
            name, pos = pinfo(pid)
            owner = owners.name(rid)
            c = contracts.get(str(pid)) or {}
            price = f" for ${bid:g} FAAB" if bid is not None and kind_add == "waiver" else ""
            rows.append({"league_id": league_id, "event_id": f"sleeper:{tid}:a{pid}", "kind": kind_add, "season": season, "week": week, "occurred_at": when, "owner_name": owner,
                         "counterparty_names": [], "player_name": name, "player_id": str(pid), "position": pos, "faab_bid": bid if kind_add == "waiver" else None,
                         "contract_salary": c.get("salary"), "contract_years": c.get("years"), "details": {"type": ttype},
                         "summary": f"{owner} added {name} ({pos or '?'}){price}", "source": "sleeper", "refreshed_at": stamp})
        for pid, rid in drops.items():
            name, pos = pinfo(pid)
            owner = owners.name(rid)
            rows.append({"league_id": league_id, "event_id": f"sleeper:{tid}:d{pid}", "kind": "drop", "season": season, "week": week, "occurred_at": when, "owner_name": owner,
                         "counterparty_names": [], "player_name": name, "player_id": str(pid), "position": pos, "faab_bid": None, "contract_salary": None, "contract_years": None,
                         "details": {"type": ttype}, "summary": f"{owner} dropped {name} ({pos or '?'})", "source": "sleeper", "refreshed_at": stamp})
    return rows


def build_draft_events(league_id: str, season: int, draft: Mapping[str, Any], picks: Iterable[dict[str, Any]], owners: OwnerMap, players: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    stamp = now_iso()
    rows = []
    draft_type = to_text(draft.get("type")) or "draft"
    when = _ts(draft.get("start_time") or draft.get("last_picked"))
    for pk in picks:
        pid = to_text(pk.get("player_id"))
        meta = pk.get("metadata") or {}
        name = " ".join(x for x in [to_text(meta.get("first_name")), to_text(meta.get("last_name"))] if x) or to_text((players.get(pid or "") or {}).get("full_name")) or f"player {pid}"
        pos = to_text(meta.get("position")) or to_text((players.get(pid or "") or {}).get("position"))
        owner = owners.name(pk.get("roster_id"))
        rnd, slot, overall = to_int(pk.get("round")), to_int(pk.get("draft_slot")), to_int(pk.get("pick_no"))
        amount = to_float(meta.get("amount"))
        label = f"{season} {'rookie ' if draft_type == 'linear' or 'rookie' in str(draft.get('metadata') or {}).lower() else ''}draft {rnd}.{slot:02d}" if rnd and slot else f"{season} draft pick {overall}"
        rows.append({"league_id": league_id, "event_id": f"sleeper:draft:{draft.get('draft_id')}:{overall or pid}", "kind": "draft_pick", "season": season, "week": 0, "occurred_at": when, "owner_name": owner,
                     "counterparty_names": [], "player_name": name, "player_id": pid, "position": pos, "faab_bid": amount, "contract_salary": amount, "contract_years": None,
                     "details": {"round": rnd, "slot": slot, "overall": overall, "draft_type": draft_type, "auction_amount": amount},
                     "summary": f"{owner} selected {name} ({pos or '?'}) at {label}" + (f" for ${amount:g}" if amount else ""), "source": "sleeper", "refreshed_at": stamp})
    return rows


def build_matchups(league_id: str, season: int, week: int, matchups: Iterable[dict[str, Any]], owners: OwnerMap, *, top_n: int = 5, win_points: int = 2, bonus_points: int = 1, playoff_start_week: int | None = None) -> list[dict[str, Any]]:
    stamp = now_iso()
    by_mid: dict[Any, list[dict[str, Any]]] = {}
    for m in matchups:
        by_mid.setdefault(m.get("matchup_id"), []).append(m)
    scored = []
    for pair in by_mid.values():
        if len(pair) != 2:
            continue
        a, b = pair
        pa, pb = to_float(a.get("points")) or 0.0, to_float(b.get("points")) or 0.0
        scored.append((a, pa, b, pb))
        scored.append((b, pb, a, pa))
    if not scored or all(p == 0 for _, p, _, _ in scored):
        return []
    ranked = sorted({(to_int(t.get("roster_id")), p) for t, p, _, _ in scored}, key=lambda x: -x[1])
    top = {rid for rid, _ in ranked[:top_n]}
    rows = []
    for t, p, o, op in scored:
        rid = to_int(t.get("roster_id"))
        won = p > op
        bonus = rid in top
        rows.append({"league_id": league_id, "season": season, "week": week, "owner_name": owners.name(rid), "opponent_name": owners.name(o.get("roster_id")),
                     "points": round(p, 2), "opponent_points": round(op, 2), "won": won, "top_scorer_bonus": bonus,
                     "standing_points": (win_points if won else 0) + (bonus_points if bonus else 0),
                     "is_playoff": bool(playoff_start_week and week >= playoff_start_week), "source": "sleeper", "refreshed_at": stamp})
    return rows
