"""League history tools over league_history_events and league_matchups. Read-only."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from league_ai.tools import ToolSpec
from league_ai.tools.nfl import normalize_name


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


class LeagueHistory:
    def __init__(self, client: Any, league_id: str):
        self.client = client
        self.league_id = league_id

    def events(self, kind: str | None = None, owner: str | None = None, player: str | None = None, season: int | None = None, limit: int = 40) -> list[dict[str, Any]]:
        q = self.client.table("league_history_events").select("kind, season, week, occurred_at, owner_name, counterparty_names, player_name, position, faab_bid, contract_salary, contract_years, summary").eq("league_id", self.league_id)
        if kind:
            q = q.eq("kind", kind)
        if season:
            q = q.eq("season", int(season))
        if owner:
            q = q.ilike("owner_name", f"%{owner}%")
        if player:
            q = q.ilike("player_name", f"%{player}%")
        rows = q.order("occurred_at", desc=True).limit(limit * 3).execute().data or []
        # a trade appears once per leg; collapse to one line per trade summary
        seen: set[str] = set()
        out = []
        for r in rows:
            key = r.get("summary") or ""
            if r.get("kind") == "trade":
                if key in seen:
                    continue
                seen.add(key)
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def waiver_prices(self, season: int | None = None, position: str | None = None, limit: int = 30) -> dict[str, Any]:
        q = self.client.table("league_history_events").select("season, week, owner_name, player_name, position, faab_bid").eq("league_id", self.league_id).eq("kind", "waiver")
        if season:
            q = q.eq("season", int(season))
        if position:
            q = q.eq("position", position.upper())
        rows = [r for r in (q.order("faab_bid", desc=True).limit(400).execute().data or []) if r.get("faab_bid") is not None]
        by_pos: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            by_pos[r.get("position") or "?"].append(_num(r["faab_bid"]))
        return {
            "biggest_bids": rows[:limit],
            "by_position": {p: {"count": len(v), "avg": round(sum(v) / len(v), 1), "max": max(v)} for p, v in by_pos.items()},
        }

    def trade_partners(self) -> dict[str, Any]:
        rows = self.client.table("league_history_events").select("season, owner_name, counterparty_names, summary").eq("league_id", self.league_id).eq("kind", "trade").limit(2000).execute().data or []
        trades: dict[str, dict[str, Any]] = {}
        for r in rows:
            trades.setdefault(r.get("summary") or "", r)
        by_owner: dict[str, int] = defaultdict(int)
        pairs: dict[str, int] = defaultdict(int)
        by_season: dict[int, int] = defaultdict(int)
        for t in trades.values():
            names = sorted({t.get("owner_name")} | set(t.get("counterparty_names") or []))
            for n in names:
                if n:
                    by_owner[n] += 1
            if len(names) >= 2:
                pairs[" & ".join(names)] += 1
            if t.get("season"):
                by_season[int(t["season"])] += 1
        return {
            "total_trades": len(trades),
            "trades_by_owner": dict(sorted(by_owner.items(), key=lambda x: -x[1])),
            "most_common_pairs": dict(sorted(pairs.items(), key=lambda x: -x[1])[:10]),
            "trades_by_season": dict(sorted(by_season.items())),
        }

    def matchups(self, owner: str | None = None, season: int | None = None, limit: int = 60) -> list[dict[str, Any]]:
        q = self.client.table("league_matchups").select("season, week, owner_name, opponent_name, points, opponent_points, won, top_scorer_bonus, standing_points, is_playoff").eq("league_id", self.league_id)
        if owner:
            q = q.ilike("owner_name", f"%{owner}%")
        if season:
            q = q.eq("season", int(season))
        return q.order("season", desc=True).order("week", desc=True).limit(limit).execute().data or []

    def season_summaries(self) -> dict[str, Any]:
        rows = self.client.table("league_matchups").select("season, week, owner_name, points, opponent_points, won, standing_points, is_playoff").eq("league_id", self.league_id).limit(5000).execute().data or []
        agg: dict[int, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: {"w": 0, "l": 0, "pf": 0.0, "pa": 0.0, "sp": 0.0, "g": 0}))
        for r in rows:
            if r.get("is_playoff"):
                continue
            a = agg[int(r["season"])][r["owner_name"]]
            a["g"] += 1
            a["w"] += 1 if r.get("won") else 0
            a["l"] += 0 if r.get("won") else 1
            a["pf"] += _num(r.get("points"))
            a["pa"] += _num(r.get("opponent_points"))
            a["sp"] += _num(r.get("standing_points"))
        out: dict[str, Any] = {}
        for season in sorted(agg, reverse=True):
            teams = sorted(agg[season].items(), key=lambda x: (-x[1]["sp"], -x[1]["pf"]))
            out[str(season)] = [
                {"rank": i + 1, "owner": name, "record": f"{int(a['w'])}-{int(a['l'])}", "standing_points": int(a["sp"]), "pf": round(a["pf"], 1), "pa": round(a["pa"], 1), "ppg": round(a["pf"] / a["g"], 1) if a["g"] else 0}
                for i, (name, a) in enumerate(teams)
            ]
        return out

    def head_to_head(self, owner_a: str, owner_b: str) -> dict[str, Any]:
        rows = [r for r in self.matchups(owner_a, limit=500) if normalize_name(owner_b) in normalize_name(r.get("opponent_name"))]
        wins = sum(1 for r in rows if r.get("won"))
        return {"games": len(rows), "wins_for_first": wins, "wins_for_second": len(rows) - wins, "recent": rows[:8]}


def pack_history_summary(client: Any, league_id: str, max_seasons: int = 4) -> list[str]:
    """Compact per-season standings lines for the league pack (past seasons only)."""
    try:
        summaries = LeagueHistory(client, league_id).season_summaries()
    except Exception:
        return []
    lines = []
    for season, teams in list(summaries.items())[:max_seasons]:
        top = ", ".join(f"{t['owner']} {t['record']} ({t['pf']:.0f} PF)" for t in teams[:3])
        bottom = ", ".join(f"{t['owner']} {t['record']}" for t in teams[-2:]) if len(teams) > 3 else ""
        lines.append(f"{season}: top {top}" + (f"; bottom {bottom}" if bottom else ""))
    return lines


def history_tools(client: Any, league_id: str) -> list[ToolSpec]:
    h = LeagueHistory(client, league_id)
    return [
        ToolSpec(
            "get_league_history",
            "League transaction history from every season on Sleeper: trades (with all legs), adds, drops, waiver claims with FAAB prices, draft picks. Filter by kind (trade/add/drop/waiver/draft_pick), owner, player or season. Use for 'what did X trade', 'how did Y acquire Z', 'what has this owner done'.",
            {"type": "object", "properties": {"kind": {"type": "string"}, "owner": {"type": "string"}, "player": {"type": "string"}, "season": {"type": "integer"}, "limit": {"type": "integer"}}},
            lambda kind=None, owner=None, player=None, season=None, limit=40: h.events(kind, owner, player, season, limit),
        ),
        ToolSpec(
            "get_waiver_prices",
            "What this league pays on waivers: biggest FAAB bids and average/max by position, optionally for one season or position. Use to price a pickup or to judge what a player would have cost.",
            {"type": "object", "properties": {"season": {"type": "integer"}, "position": {"type": "string"}, "limit": {"type": "integer"}}},
            lambda season=None, position=None, limit=30: h.waiver_prices(season, position, limit),
        ),
        ToolSpec(
            "get_trade_tendencies",
            "Who trades in this league: total trades, trades by owner, most common trade partners, trades by season. Use to judge which owners are realistic partners.",
            {"type": "object", "properties": {}},
            lambda: h.trade_partners(),
        ),
        ToolSpec(
            "get_matchup_history",
            "Weekly matchup results (points, opponent, win, top-scorer bonus) for an owner and/or season, plus per-season standings summaries for the whole league when no owner is given. head_to_head compares two owners.",
            {"type": "object", "properties": {"owner": {"type": "string"}, "season": {"type": "integer"}, "head_to_head_with": {"type": "string"}, "limit": {"type": "integer"}}},
            lambda owner=None, season=None, head_to_head_with=None, limit=40: (
                h.head_to_head(owner, head_to_head_with) if owner and head_to_head_with else
                {"matchups": h.matchups(owner, season, limit)} if owner else
                {"seasons": h.season_summaries()}
            ),
        ),
    ]
