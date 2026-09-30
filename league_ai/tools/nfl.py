"""Read-only tools over the nfl_* tables and the league state.

Each tool is a plain function taking simple arguments and returning JSON-able
data. `build_registry` binds them to a Supabase client and the current league
pack inputs, and returns a ToolRegistry the Claude client can run.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable, Mapping

from league_ai.tools import ToolRegistry, ToolSpec

STAT_FIELDS = [
    "completions", "attempts", "passing_yards", "passing_tds", "interceptions", "sacks",
    "carries", "rushing_yards", "rushing_tds", "rushing_first_downs",
    "targets", "receptions", "receiving_yards", "receiving_tds", "receiving_air_yards", "receiving_yac", "receiving_first_downs",
    "fumbles_lost", "offense_snaps", "fantasy_points", "fantasy_points_ppr",
]
RATE_FIELDS = ["target_share", "air_yards_share", "wopr", "offense_pct", "passing_epa", "rushing_epa", "receiving_epa"]
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: Any) -> str:
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9 ]", "", text).lower().strip()
    return " ".join(p for p in text.split() if p not in _SUFFIXES)


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _round(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(v, nd)


def positional_tier(position: str | None, pos_rank: float | None) -> str | None:
    """Translate a positional rank into starter language for a 10-team league."""
    if pos_rank is None or not position:
        return None
    pos = position.upper()
    r = float(pos_rank)
    if pos == "QB":
        if r <= 5: return "elite QB1"
        if r <= 12: return "QB1"
        if r <= 20: return "solid OP-slot starter (QB2)"
        if r <= 30: return "streamer / backup QB"
        return "roster filler"
    if pos in ("RB", "WR"):
        if r <= 5: return f"elite {pos}1"
        if r <= 12: return f"{pos}1"
        if r <= 24: return f"{pos}2"
        if r <= 36: return f"{pos}3 / flex"
        if r <= 60: return "bench depth with some value"
        return "roster filler"
    if pos == "TE":
        if r <= 3: return "elite TE1"
        if r <= 10: return "TE1"
        if r <= 16: return "low-end TE1 / streamer"
        return "roster filler"
    return None


def overall_tier(overall_rank: int | None) -> str | None:
    if overall_rank is None:
        return None
    if overall_rank <= 12: return "top-12 dynasty asset (franchise piece)"
    if overall_rank <= 36: return "top-36 dynasty asset (core starter)"
    if overall_rank <= 75: return "top-75 asset (starter)"
    if overall_rank <= 150: return "top-150 asset (depth with trade value)"
    return "little trade value"


class NflData:
    """Thin query layer; every method returns plain dicts. Never writes."""

    def __init__(self, client: Any, *, league_state: Mapping[str, Any] | None = None, salary_cap: Any = 225, dead_cap_pct: float = 50.0):
        self.client = client
        self.league_state = league_state or {}
        self.salary_cap = Decimal(str(salary_cap or 225))
        self.dead_cap_pct = float(dead_cap_pct or 50)
        self._roster_index: dict[str, list[dict[str, Any]]] | None = None

    # ---- players ---------------------------------------------------------
    def find_players(self, name: str, position: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        key = normalize_name(name)
        if not key:
            return []
        q = self.client.table("nfl_players").select("*")
        rows = q.eq("search_name", key).limit(limit).execute().data or []
        if not rows:
            rows = self.client.table("nfl_players").select("*").ilike("search_name", f"%{key}%").order("search_rank").limit(limit * 3).execute().data or []
            last = key.split()[-1] if key.split() else key
            if not rows and last:
                rows = self.client.table("nfl_players").select("*").ilike("search_name", f"%{last}%").order("search_rank").limit(limit * 3).execute().data or []
        if position:
            pos = position.upper()
            narrowed = [r for r in rows if (r.get("position") or "").upper() == pos]
            rows = narrowed or rows
        rostered = self._rosters()
        rows.sort(key=lambda r: (str(r.get("sleeper_id")) not in rostered, not r.get("team"), r.get("search_rank") or 999999))
        return rows[:limit]

    def player(self, sleeper_id: str) -> dict[str, Any] | None:
        rows = self.client.table("nfl_players").select("*").eq("sleeper_id", str(sleeper_id)).limit(1).execute().data or []
        return rows[0] if rows else None

    # ---- league contract lookup ------------------------------------------
    def _rosters(self) -> dict[str, list[dict[str, Any]]]:
        if self._roster_index is None:
            idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in self.league_state.get("roster", []):
                sid = str(r.get("sleeper_player_id") or r.get("player_id") or "")
                if sid:
                    idx[sid].append(r)
                idx["name:" + normalize_name(r.get("player_name"))].append(r)
            self._roster_index = idx
        return self._roster_index

    def league_contract(self, sleeper_id: str | None, name: str | None = None) -> dict[str, Any] | None:
        rows = self._rosters().get(str(sleeper_id or ""), []) or (self._rosters().get("name:" + normalize_name(name), []) if name else [])
        if not rows:
            return None
        r = rows[0]
        return {
            "league_team": r.get("team_name") or r.get("owner_name"),
            "owner": r.get("owner_name"),
            "cap_hit": _num(r.get("cap_hit")),
            "salary": _num(r.get("salary")),
            "years_left": r.get("contract_years_left"),
            "contract_type": r.get("contract_type"),
            "designation": r.get("roster_designation"),
            "acquired_via": r.get("initial_acquisition_type") or r.get("source"),
            "if_dropped": self._drop_dead_cap(r),
        }

    def _drop_dead_cap(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Dead cap on a drop: dead_cap_pct of each remaining season's hit; $1 deals carry none."""
        hit = _num(row.get("cap_hit") or row.get("salary"))
        years = max(1, int(row.get("contract_years_left") or 1))
        if hit <= 1:
            return {"dead_cap_total": 0.0, "dead_cap_per_season": 0.0, "seasons_charged": 0, "cap_saved_this_season": hit, "note": "$1 deal: no dead cap"}
        per = round(hit * self.dead_cap_pct / 100.0, 2)
        return {"dead_cap_total": round(per * years, 2), "dead_cap_per_season": per, "seasons_charged": years, "cap_saved_this_season": round(hit - per, 2)}

    # ---- injuries ----------------------------------------------------------
    def injury(self, sleeper_id: str | None, name: str | None = None) -> dict[str, Any] | None:
        rows = []
        if sleeper_id:
            rows = self.client.table("nfl_injuries").select("*").eq("sleeper_id", str(sleeper_id)).limit(1).execute().data or []
        if not rows and name:
            rows = self.client.table("nfl_injuries").select("*").eq("search_name", normalize_name(name)).limit(1).execute().data or []
        if not rows:
            return None
        r = rows[0]
        return {
            "status": r.get("status"),
            "injury": " ".join(x for x in [r.get("side"), r.get("injury_type"), f"({r.get('detail')})" if r.get("detail") and r.get("detail") != "Not Specified" else None] if x) or r.get("injury_type"),
            "estimated_return": r.get("return_date"),
            "latest_update": r.get("short_comment"),
            "context": r.get("long_comment"),
            "reported_at": str(r.get("reported_at") or "")[:16],
            "source": "ESPN injury report, refreshed " + str(r.get("refreshed_at") or "")[:16],
        }

    def practice_history(self, sleeper_id: str | None, gsis_id: str | None, seasons: list[int] | None = None) -> dict[str, Any]:
        q = self.client.table("nfl_practice_reports").select("*")
        if gsis_id:
            q = q.eq("gsis_id", gsis_id)
        elif sleeper_id:
            q = q.eq("sleeper_id", str(sleeper_id))
        else:
            return {}
        if seasons:
            q = q.in_("season", [int(x) for x in seasons])
        rows = q.order("season", desc=True).order("week", desc=True).limit(200).execute().data or []
        if not rows:
            return {"note": "no practice or game-status reports on file"}
        by_season: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            by_season[int(r["season"])].append(r)
        out: dict[str, Any] = {"seasons": {}}
        for season in sorted(by_season, reverse=True):
            srows = by_season[season]
            statuses = [r.get("report_status") for r in srows if r.get("report_status")]
            injuries = sorted({(r.get("report_injury") or r.get("practice_injury") or "").strip() for r in srows} - {""})
            out["seasons"][str(season)] = {
                "weeks_listed": len(srows),
                "weeks_out": sum(1 for x in statuses if x == "Out"),
                "weeks_doubtful": sum(1 for x in statuses if x == "Doubtful"),
                "weeks_questionable": sum(1 for x in statuses if x == "Questionable"),
                "injuries_reported": injuries,
            }
        latest = max(by_season)
        out["this_season_weekly"] = [
            {"week": int(r["week"]), "injury": r.get("report_injury") or r.get("practice_injury"), "practice": r.get("practice_status"), "game_status": r.get("report_status")}
            for r in sorted(by_season[latest], key=lambda r: int(r["week"]), reverse=True)[:6]
        ]
        out["this_season_weekly_season"] = latest
        return out

    # ---- prospects ---------------------------------------------------------
    def prospects(self, draft_year: int | None, position: str | None, name: str | None, limit: int = 20) -> dict[str, Any]:
        """Draft class = college season before the draft (2027 class -> 2026 season)."""
        q = self.client.table("nfl_prospects").select("*")
        if name:
            rows = q.eq("search_name", normalize_name(name)).order("season", desc=True).limit(6).execute().data or []
            if not rows:
                rows = self.client.table("nfl_prospects").select("*").ilike("search_name", f"%{normalize_name(name)}%").order("season", desc=True).limit(6).execute().data or []
            return {"matches": [self._prospect_dict(r) for r in rows]}
        seasons = self.client.table("nfl_prospects").select("season").order("season", desc=True).limit(1).execute().data or []
        if not seasons:
            return {"note": "No college prospect data loaded yet (the CFBD loader has not run)."}
        season = (int(draft_year) - 1) if draft_year else int(seasons[0]["season"])
        q = self.client.table("nfl_prospects").select("*").eq("season", season)
        if position:
            q = q.eq("position", position.upper())
        rows = q.order("scrimmage_yds", desc=True).limit(limit * 3).execute().data or []
        eligible = [r for r in rows if r.get("draft_eligible") in (True, None)]
        return {
            "draft_class": season + 1,
            "college_season": season,
            "note": "Ranked by scrimmage yards this college season among draft-eligible players (class year 3+). Passers are ranked separately by passing yards when position=QB. Recruiting stars/rating show pedigree; usage_overall is the share of team plays the player is involved in.",
            "prospects": [self._prospect_dict(r) for r in eligible[:limit]],
        }

    @staticmethod
    def _prospect_dict(r: Mapping[str, Any]) -> dict[str, Any]:
        cls = {1: "Fr", 2: "So", 3: "Jr", 4: "Sr"}.get(r.get("class_year") or 0, "?")
        line = []
        if r.get("pass_yds"):
            line.append(f"{r.get('pass_yds'):g} pass yds, {r.get('pass_td') or 0:g} TD, {r.get('pass_int') or 0:g} INT")
        if r.get("rush_yds"):
            line.append(f"{r.get('rush_att') or 0:g} car for {r.get('rush_yds'):g} yds, {r.get('rush_td') or 0:g} TD")
        if r.get("rec_yds"):
            line.append(f"{r.get('receptions') or 0:g} rec for {r.get('rec_yds'):g} yds, {r.get('rec_td') or 0:g} TD")
        pedigree = None
        if r.get("recruit_stars"):
            pedigree = f"{r.get('recruit_stars')}-star recruit ({r.get('recruit_year')})" + (f", #{r.get('recruit_rank')} overall" if r.get("recruit_rank") else "")
        drafted = None
        if r.get("nfl_draft_year"):
            drafted = f"drafted {r.get('nfl_draft_year')} round {r.get('nfl_draft_round')} pick {r.get('nfl_draft_pick')} by {r.get('nfl_team')}"
        return {
            "name": r.get("name"), "position": r.get("position"), "college": r.get("college"), "conference": r.get("conference"),
            "class": cls, "season": r.get("season"), "draft_eligible": r.get("draft_eligible"),
            "size": f"{r.get('height') or '?'} in, {r.get('weight') or '?'} lb",
            "season_line": "; ".join(line) or "no production on file",
            "usage_share": r.get("usage_overall"),
            "recruiting": pedigree or "unranked / unknown",
            "nfl_draft": drafted,
        }

    # ---- market ------------------------------------------------------------
    def latest_scrape_date(self) -> str | None:
        rows = self.client.table("nfl_market_values").select("scrape_date").order("scrape_date", desc=True).limit(1).execute().data or []
        return rows[0]["scrape_date"] if rows else None

    def market_value(self, sleeper_id: str | None, name: str | None = None) -> dict[str, Any] | None:
        latest = self.latest_scrape_date()
        if not latest:
            return None
        rows = []
        if sleeper_id:
            rows = self.client.table("nfl_market_values").select("*").eq("sleeper_id", str(sleeper_id)).order("scrape_date", desc=True).limit(12).execute().data or []
        if not rows and name:
            rows = self.client.table("nfl_market_values").select("*").ilike("player_name", f"%{name}%").order("scrape_date", desc=True).limit(12).execute().data or []
        if not rows:
            return None
        current = rows[0]
        older = [r for r in rows if r["scrape_date"] != current["scrape_date"]]
        prior = older[-1] if older else None
        pos_rank = current.get("ecr_pos")
        pos_rank_int = int(round(float(pos_rank))) if pos_rank is not None else None
        overall = self._overall_rank(current)
        change = (current.get("value_2qb") or 0) - (prior.get("value_2qb") or 0) if prior else None
        trend = None
        if prior is not None and prior.get("value_2qb"):
            pct = change / float(prior["value_2qb"]) * 100
            trend = f"{'up' if pct > 3 else 'down' if pct < -3 else 'flat'} ({pct:+.0f}%) since {prior['scrape_date']}"
        return {
            "as_of": current["scrape_date"],
            "positional_rank": f"{current.get('position')}{pos_rank_int}" if pos_rank_int else None,
            "positional_tier": positional_tier(current.get("position"), pos_rank),
            "overall_rank_superflex": overall,
            "overall_tier": overall_tier(overall),
            "trend": trend,
            "raw_value_superflex": current.get("value_2qb"),
            "source": "DynastyProcess dynasty market (superflex scale, since this league starts an OP)",
            "how_to_cite": "Cite the positional rank and tier (e.g. 'RB6, a clear RB1'), never the raw value number.",
        }

    def _overall_rank(self, row: Mapping[str, Any]) -> int | None:
        try:
            value = row.get("value_2qb")
            if value is None:
                return None
            res = self.client.table("nfl_market_values").select("fantasypros_id", count="exact").eq("scrape_date", row["scrape_date"]).gt("value_2qb", value).limit(1).execute()
            count = getattr(res, "count", None)
            if count is None:
                return None
            return int(count) + 1
        except Exception:
            return None

    def market_table(self, position: str | None = None, limit: int = 30, superflex: bool = False) -> list[dict[str, Any]]:
        latest = self.latest_scrape_date()
        if not latest:
            return []
        q = self.client.table("nfl_market_values").select("player_name, position, team, age, sleeper_id, value_1qb, value_2qb, ecr_1qb, ecr_2qb, ecr_pos").eq("scrape_date", latest)
        if position:
            q = q.eq("position", position.upper())
        col = "value_2qb" if superflex else "value_1qb"
        rows = q.order(col, desc=True).limit(limit).execute().data or []
        out = []
        for i, r in enumerate(rows, start=1):
            c = self.league_contract(r.get("sleeper_id"), r.get("player_name"))
            pos_rank = r.get("ecr_pos")
            pr = int(round(float(pos_rank))) if pos_rank is not None else None
            out.append({
                "player": r.get("player_name"),
                "position": r.get("position"),
                "nfl_team": r.get("team"),
                "age": r.get("age"),
                "positional_rank": f"{r.get('position')}{pr}" if pr else None,
                "tier": positional_tier(r.get("position"), pos_rank),
                "rank_in_this_list": i,
                "league_status": (f"{c['owner']} · ${c['cap_hit']:g} x {c['years_left']}yr" if c else "free agent in this league"),
            })
        return out

    def pick_values(self, draft_year: int | None = None) -> list[dict[str, Any]]:
        rows = self.client.table("nfl_pick_values").select("pick_label, draft_year, round, slot, value_1qb, value_2qb, scrape_date").order("scrape_date", desc=True).limit(400).execute().data or []
        if not rows:
            return []
        latest = rows[0]["scrape_date"]
        rows = [r for r in rows if r["scrape_date"] == latest and (draft_year is None or r.get("draft_year") == draft_year)]
        rows.sort(key=lambda r: (r.get("draft_year") or 0, r.get("round") or 0, r.get("slot") or ""))
        # express each pick as the overall asset rank its value would hold among players
        players = self.client.table("nfl_market_values").select("value_2qb").eq("scrape_date", self.latest_scrape_date()).order("value_2qb", desc=True).limit(400).execute().data or []
        values = [float(p["value_2qb"] or 0) for p in players]
        out = []
        for r in rows:
            v = float(r.get("value_2qb") or 0)
            rank = sum(1 for x in values if x > v) + 1 if values else None
            out.append({"pick": r["pick_label"], "draft_year": r.get("draft_year"), "round": r.get("round"), "worth_about": f"the #{rank} dynasty asset overall" if rank else None, "tier": overall_tier(rank) if rank else None})
        return out

    # ---- stats -------------------------------------------------------------
    def weekly(self, sleeper_id: str | None, gsis_id: str | None, seasons: Iterable[int] | None = None) -> list[dict[str, Any]]:
        q = self.client.table("nfl_player_stats").select("*")
        if gsis_id:
            q = q.eq("gsis_id", gsis_id)
        elif sleeper_id:
            q = q.eq("sleeper_id", str(sleeper_id))
        else:
            return []
        if seasons:
            q = q.in_("season", [int(s) for s in seasons])
        return q.eq("season_type", "REG").order("season", desc=True).order("week", desc=True).limit(120).execute().data or []

    @staticmethod
    def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        games = len(rows)
        if not games:
            return {"games": 0}
        out: dict[str, Any] = {"games": games}
        for f in STAT_FIELDS:
            total = sum(_num(r.get(f)) for r in rows)
            out[f] = _round(total, 1)
        for f in RATE_FIELDS:
            vals = [_num(r.get(f)) for r in rows if r.get(f) is not None]
            out[f + "_avg"] = _round(sum(vals) / len(vals), 3) if vals else None
        out["ppg_ppr"] = _round(out["fantasy_points_ppr"] / games, 2)
        out["ppg_std"] = _round(out["fantasy_points"] / games, 2)
        if out.get("targets"):
            out["targets_per_game"] = _round(out["targets"] / games, 1)
        if out.get("carries"):
            out["carries_per_game"] = _round(out["carries"] / games, 1)
        touches = out.get("carries", 0) + out.get("receptions", 0)
        out["touches_per_game"] = _round(touches / games, 1)
        return out

    def stats_summary(self, sleeper_id: str | None, gsis_id: str | None, seasons: list[int] | None, split: str | None = None, recent_weeks: int = 4) -> dict[str, Any]:
        rows = self.weekly(sleeper_id, gsis_id, seasons)
        by_season: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            by_season[int(r["season"])].append(r)
        result: dict[str, Any] = {"seasons": {}}
        for season in sorted(by_season, reverse=True):
            srows = by_season[season]
            entry: dict[str, Any] = {"totals": self.aggregate(srows), "teams": sorted({r.get("team") for r in srows if r.get("team")})}
            if split == "home_away":
                entry["home"] = self.aggregate([r for r in srows if r.get("is_home") is True])
                entry["away"] = self.aggregate([r for r in srows if r.get("is_home") is False])
            result["seasons"][str(season)] = entry
        if rows:
            latest_season = max(by_season)
            recent = sorted(by_season[latest_season], key=lambda r: int(r["week"]), reverse=True)[:recent_weeks]
            result["recent"] = {"season": latest_season, "weeks": [int(r["week"]) for r in recent], "totals": self.aggregate(recent)}
            result["game_log_latest"] = [
                {k: r.get(k) for k in ("week", "opponent", "is_home", "fantasy_points_ppr", "targets", "receptions", "receiving_yards", "carries", "rushing_yards", "passing_yards", "passing_tds", "rushing_tds", "receiving_tds", "offense_pct", "target_share")}
                for r in sorted(by_season[latest_season], key=lambda r: int(r["week"]), reverse=True)[:8]
            ]
        return result


# ---- tool functions ---------------------------------------------------------

def _profile_dict(data: NflData, p: dict[str, Any], include_market: bool = True) -> dict[str, Any]:
    out = {
        "sleeper_id": p.get("sleeper_id"),
        "name": p.get("full_name"),
        "position": p.get("position"),
        "nfl_team": p.get("team") or "free agent / unsigned",
        "age": p.get("age"),
        "birthdate": p.get("birthdate"),
        "years_exp": p.get("years_exp"),
        "depth_chart": f"{p.get('depth_chart_position') or ''} {p.get('depth_chart_order') or ''}".strip() or None,
        "injury_status": p.get("injury_status"),
        "injury_note": " ".join(x for x in [p.get("injury_body_part"), p.get("injury_notes")] if x) or None,
        "nfl_status": p.get("status"),
        "college": p.get("college"),
        "draft": (f"{p.get('draft_year')} round {p.get('draft_round')} pick {p.get('draft_ovr') or p.get('draft_pick')}" if p.get("draft_year") else "undrafted or unknown"),
        "height_in": p.get("height"),
        "weight_lb": p.get("weight"),
        "data_as_of": str(p.get("refreshed_at") or "")[:16],
    }
    injury = data.injury(p.get("sleeper_id"), p.get("full_name"))
    if injury:
        out["injury_report"] = injury
    elif p.get("injury_status"):
        out["injury_report"] = {"status": p.get("injury_status"), "injury": out["injury_note"], "estimated_return": None, "source": "Sleeper only (not on ESPN's current report)"}
    out["league_contract"] = data.league_contract(p.get("sleeper_id"), p.get("full_name")) or "not rostered in this league (free agent)"
    if include_market:
        out["market"] = data.market_value(p.get("sleeper_id"), p.get("full_name")) or "no market value on file"
    return out


def tool_get_player_profile(data: NflData, name: str, position: str | None = None) -> dict[str, Any]:
    matches = data.find_players(name, position, limit=5)
    if not matches:
        return {"error": f"No NFL player found matching '{name}'. Check the spelling or give the full name."}
    if len(matches) > 1 and normalize_name(matches[0].get("full_name")) != normalize_name(name):
        return {"ambiguous": [f"{m['full_name']} ({m.get('position')}, {m.get('team') or 'FA'})" for m in matches], "hint": "Call again with the exact name or a position."}
    return _profile_dict(data, matches[0])


def tool_get_injury_report(data: NflData, name: str, position: str | None = None) -> dict[str, Any]:
    matches = data.find_players(name, position, limit=3)
    if not matches:
        return {"error": f"No NFL player found matching '{name}'."}
    p = matches[0]
    current = data.injury(p.get("sleeper_id"), p.get("full_name"))
    out: dict[str, Any] = {
        "name": p.get("full_name"),
        "position": p.get("position"),
        "nfl_team": p.get("team"),
        "sleeper_status": {"status": p.get("injury_status"), "body_part": p.get("injury_body_part"), "note": p.get("injury_notes"), "nfl_status": p.get("status"), "as_of": str(p.get("refreshed_at") or "")[:16]},
        "current": current or "not on ESPN's current injury report",
        "history": data.practice_history(p.get("sleeper_id"), p.get("gsis_id")),
        "how_to_read": "estimated_return is ESPN's projection and moves; practice trend (DNP -> Limited -> Full) across a week is the best short-term signal; weeks_out per season shows durability.",
    }
    return out


def tool_get_prospects(data: NflData, draft_year: int | None = None, position: str | None = None, name: str | None = None, limit: int = 20) -> dict[str, Any]:
    out = data.prospects(draft_year, position, name, limit)
    out["picks_market"] = data.pick_values(draft_year) if draft_year else []
    return out


def tool_get_player_stats(data: NflData, name: str, seasons: list[int] | None = None, split: str | None = None, position: str | None = None) -> dict[str, Any]:
    matches = data.find_players(name, position, limit=3)
    if not matches:
        return {"error": f"No NFL player found matching '{name}'."}
    p = matches[0]
    summary = data.stats_summary(p.get("sleeper_id"), p.get("gsis_id"), seasons, split=split)
    summary.update({"name": p.get("full_name"), "position": p.get("position"), "nfl_team": p.get("team"), "age": p.get("age"), "source": "nflverse weekly stats (REG season); snaps from nflverse snap counts"})
    if not summary.get("seasons"):
        summary["note"] = "No weekly stats on file for this player in the requested seasons (rookie, injured, or not yet loaded)."
    return summary


def tool_compare_players(data: NflData, names: list[str], seasons: list[int] | None = None) -> dict[str, Any]:
    out = {}
    for name in names[:5]:
        matches = data.find_players(name, limit=1)
        if not matches:
            out[name] = {"error": "not found"}
            continue
        p = matches[0]
        prof = _profile_dict(data, p)
        stats = data.stats_summary(p.get("sleeper_id"), p.get("gsis_id"), seasons)
        prof["stats"] = {s: v["totals"] for s, v in stats.get("seasons", {}).items()}
        prof["recent"] = stats.get("recent")
        out[p.get("full_name") or name] = prof
    return out


def tool_get_market_values(data: NflData, position: str | None = None, limit: int = 25, superflex: bool = False, draft_year: int | None = None, include_picks: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"as_of": data.latest_scrape_date(), "source": "DynastyProcess dynasty market, superflex scale", "how_to_cite": "Use positional rank and tier, never raw value numbers.", "players": data.market_table(position, limit, superflex)}
    if include_picks or draft_year:
        out["picks"] = data.pick_values(draft_year)
    return out


def tool_search_players(data: NflData, position: str | None = None, nfl_team: str | None = None, max_age: float | None = None, min_age: float | None = None, league_free_agents_only: bool = False, limit: int = 25) -> dict[str, Any]:
    q = data.client.table("nfl_players").select("sleeper_id, full_name, position, team, age, years_exp, depth_chart_order, injury_status, search_rank")
    if position:
        q = q.eq("position", position.upper())
    if nfl_team:
        q = q.eq("team", nfl_team.upper())
    if max_age is not None:
        q = q.lte("age", max_age)
    if min_age is not None:
        q = q.gte("age", min_age)
    rows = q.order("search_rank").limit(limit * 4).execute().data or []
    out = []
    for r in rows:
        c = data.league_contract(r.get("sleeper_id"), r.get("full_name"))
        if league_free_agents_only and c:
            continue
        r["league_status"] = (f"{c['owner']} · ${c['cap_hit']:g} x {c['years_left']}yr" if c else "free agent in this league")
        out.append(r)
        if len(out) >= limit:
            break
    return {"players": out, "note": "search_rank is Sleeper's popularity rank (lower = more relevant)."}


def tool_simulate_trade(data: NflData, sides: list[dict[str, Any]]) -> dict[str, Any]:
    """sides: [{"team": "Tommy Bruggeman", "sends_players": [...], "sends_picks": [...], "sends_cap": 0}, ...]"""
    state = data.league_state
    teams = {normalize_name(t.get("owner_name")): t for t in state.get("teams", [])}
    teams.update({normalize_name(t.get("team_name")): t for t in state.get("teams", [])})
    roster = list(state.get("roster", []))
    from services.team_roster_state import calculate_team_financials, state_cap_adjustments, state_roster

    adjustments = state_cap_adjustments(state)
    norm_roster = state_roster(state)
    result: dict[str, Any] = {"teams": {}, "errors": []}
    outgoing: dict[str, list[dict[str, Any]]] = {}
    resolved_sides = []
    for side in sides:
        team = teams.get(normalize_name(side.get("team")))
        if not team:
            result["errors"].append(f"Unknown team '{side.get('team')}'")
            continue
        tid = str(team["league_team_id"])
        players = []
        for name in side.get("sends_players") or []:
            match = next((r for r in norm_roster if str(r.get("league_team_id")) == tid and normalize_name(r.get("player_name")) == normalize_name(name)), None)
            if not match:
                result["errors"].append(f"{name} is not on {team['owner_name']}'s roster")
                continue
            players.append(match)
        outgoing[tid] = players
        resolved_sides.append((tid, team, side, players))
    if result["errors"]:
        return result
    # each side receives everything the other sides send
    for tid, team, side, players in resolved_sides:
        before = calculate_team_financials(norm_roster, adjustments, salary_cap=data.salary_cap, league_team_id=tid)
        incoming = [p for other_tid, ps in outgoing.items() if other_tid != tid for p in ps]
        cap_out = sum(_num(side.get("sends_cap")) for _ in [0])
        cap_in = sum(_num(s.get("sends_cap")) for other_tid, _, s, _ in resolved_sides if other_tid != tid)
        new_roster = [r for r in norm_roster if not (str(r.get("league_team_id")) == tid and r in players)]
        for p in incoming:
            new_roster.append({**p, "league_team_id": tid})
        after = calculate_team_financials(new_roster, adjustments, salary_cap=data.salary_cap, league_team_id=tid)
        after_space = float(after["cap_space"]) + cap_in - cap_out
        count_after = sum(1 for r in new_roster if str(r.get("league_team_id")) == tid)
        result["teams"][team["owner_name"]] = {
            "sends": [f"{p.get('player_name')} (${_num(p.get('cap_hit')):g} x {p.get('contract_years_left')}yr)" for p in players] + [f"pick {x}" for x in side.get("sends_picks") or []] + ([f"${cap_out:g} cap"] if cap_out else []),
            "receives": [f"{p.get('player_name')} (${_num(p.get('cap_hit')):g} x {p.get('contract_years_left')}yr)" for p in incoming],
            "cap_space_before": float(before["cap_space"]),
            "cap_space_after": _round(after_space, 2),
            "roster_count_after": count_after,
            "over_cap": after_space < 0,
        }
    result["note"] = "Cap math uses this season's cap hits. Picks are listed but not valued here; use get_market_values with include_picks for pick values. This is a simulation only; nothing was executed."
    return result


def build_registry(client: Any, *, league_state: Mapping[str, Any], salary_cap: Any = 225, dead_cap_pct: float = 50.0, extra: Iterable[ToolSpec] = ()) -> ToolRegistry:
    data = NflData(client, league_state=league_state, salary_cap=salary_cap, dead_cap_pct=dead_cap_pct)
    reg = ToolRegistry()
    reg.register(ToolSpec(
        "get_player_profile",
        "NFL profile for one player: age, team, depth chart, injury status, draft capital, college, plus his contract in this league (if rostered) and current dynasty market value. Use before evaluating any player.",
        {"type": "object", "properties": {"name": {"type": "string"}, "position": {"type": "string", "description": "QB/RB/WR/TE/K, to disambiguate"}}, "required": ["name"]},
        lambda name, position=None: tool_get_player_profile(data, name, position),
    ))
    reg.register(ToolSpec(
        "get_injury_report",
        "Injury detail for one player: current status with injury type, ESPN's estimated return date and latest update, this week's practice trend (DNP/limited/full), and injury history by season (weeks out, injuries reported). Use for any 'is he hurt / when is he back / is he injury-prone' question.",
        {"type": "object", "properties": {"name": {"type": "string"}, "position": {"type": "string"}}, "required": ["name"]},
        lambda name, position=None: tool_get_injury_report(data, name, position),
    ))
    reg.register(ToolSpec(
        "get_prospects",
        "College prospects for an upcoming rookie draft class (e.g. draft_year 2027 = the 2026 college season): production line, usage share, class, size, recruiting pedigree, and for past classes where they were drafted. Give a name to look one prospect up. Use for any rookie draft or 'who should I take at 1.02' question.",
        {"type": "object", "properties": {"draft_year": {"type": "integer"}, "position": {"type": "string"}, "name": {"type": "string"}, "limit": {"type": "integer"}}},
        lambda draft_year=None, position=None, name=None, limit=20: tool_get_prospects(data, draft_year, position, name, limit),
    ))
    reg.register(ToolSpec(
        "get_player_stats",
        "Production and usage for one player: per-season totals (2023 to now), recent-weeks totals, game log, targets/carries/snap share, and optional home/away split. Seasons default to all loaded.",
        {"type": "object", "properties": {"name": {"type": "string"}, "seasons": {"type": "array", "items": {"type": "integer"}}, "split": {"type": "string", "enum": ["home_away"]}, "position": {"type": "string"}}, "required": ["name"]},
        lambda name, seasons=None, split=None, position=None: tool_get_player_stats(data, name, seasons, split, position),
    ))
    reg.register(ToolSpec(
        "compare_players",
        "Side-by-side profile, league contract, market value and per-season stats for 2 to 5 players.",
        {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}, "seasons": {"type": "array", "items": {"type": "integer"}}}, "required": ["names"]},
        lambda names, seasons=None: tool_compare_players(data, names, seasons),
    ))
    reg.register(ToolSpec(
        "get_market_values",
        "Dynasty market ranking (DynastyProcess, superflex scale) by position or overall, as positional rank and tier with each player's status in this league. include_picks or draft_year adds rookie pick values expressed as the asset rank they equal.",
        {"type": "object", "properties": {"position": {"type": "string"}, "limit": {"type": "integer"}, "superflex": {"type": "boolean"}, "draft_year": {"type": "integer"}, "include_picks": {"type": "boolean"}}},
        lambda position=None, limit=25, superflex=True, draft_year=None, include_picks=False: tool_get_market_values(data, position, limit, superflex, draft_year, include_picks),
    ))
    reg.register(ToolSpec(
        "search_players",
        "Find NFL players by position, NFL team and age range, with their status in this league. Use league_free_agents_only to find pickup or auction targets.",
        {"type": "object", "properties": {"position": {"type": "string"}, "nfl_team": {"type": "string"}, "max_age": {"type": "number"}, "min_age": {"type": "number"}, "league_free_agents_only": {"type": "boolean"}, "limit": {"type": "integer"}}},
        lambda position=None, nfl_team=None, max_age=None, min_age=None, league_free_agents_only=False, limit=25: tool_search_players(data, position, nfl_team, max_age, min_age, league_free_agents_only, limit),
    ))
    reg.register(ToolSpec(
        "simulate_trade",
        "Cap and roster effect of a proposed trade for every team involved, using the app's cap math. Nothing is executed. sides: one entry per team with the players, picks and cap dollars it sends.",
        {"type": "object", "properties": {"sides": {"type": "array", "items": {"type": "object", "properties": {"team": {"type": "string", "description": "owner or team name"}, "sends_players": {"type": "array", "items": {"type": "string"}}, "sends_picks": {"type": "array", "items": {"type": "string"}}, "sends_cap": {"type": "number"}}, "required": ["team"]}}}, "required": ["sides"]},
        lambda sides: tool_simulate_trade(data, sides),
    ))
    for spec in extra:
        reg.register(spec)
    return reg
