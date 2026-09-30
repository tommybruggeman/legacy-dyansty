"""League AI settings: the league-specific rules the bot relies on.

`league_rules` already holds salary cap, dead-cap percent, contract terms and
rookie scale. This table holds what it lacks (taxi/IR cap fractions and
limits, FAAB-to-salary rule, roster limits, trade limits, standings scoring,
Sleeper scoring settings and free-text house rules) so every rule the bot
cites lives in the app, editable by the commissioner in Settings.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

import requests

TABLE = "league_ai_settings"
SLEEPER_API = "https://api.sleeper.app/v1"


@dataclass
class LeagueAISettings:
    league_id: str
    taxi_cap_fraction: float = 0.5
    ir_cap_fraction: float = 0.5
    taxi_limit: int = 1
    ir_limit: int = 1
    roster_max: int = 22
    taxi_rookies_only: bool = True
    taxi_locked_full_season: bool = True
    taxi_skips_contract_year: bool = True
    max_trade_teams: int = 4
    faab_dollar_per_salary: float = 1.0
    faab_zero_counts_as: float = 1.0
    faab_pickup_years: int = 1
    one_dollar_deals_no_dead_cap: bool = True
    traded_faab_moves_cap: bool = True
    win_points: int = 2
    top_scorer_bonus_points: int = 1
    top_scorer_bonus_count: int = 5
    playoff_teams: int = 6
    standings_tiebreaker: str = "season points for"
    trade_deadline: str = ""
    house_rules: str = ""
    scoring_settings: dict[str, Any] = field(default_factory=dict)
    roster_positions: list[str] = field(default_factory=list)
    scoring_source: str = "sleeper"
    scoring_synced_at: str | None = None

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["scoring_settings"] = self.scoring_settings or {}
        row["roster_positions"] = self.roster_positions or []
        return row

    @classmethod
    def from_row(cls, row: dict[str, Any] | None, league_id: str) -> "LeagueAISettings":
        base = cls(league_id=league_id)
        if not row:
            return base
        allowed = {f.name for f in fields(cls)}
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if key not in allowed or value is None:
                continue
            if key in {"scoring_settings", "roster_positions"} and isinstance(value, str):
                try:
                    value = json.loads(value)
                except Exception:
                    continue
            clean[key] = value
        clean["league_id"] = league_id
        try:
            return cls(**clean)
        except TypeError:
            return base


def load_settings(client: Any, league_id: str) -> LeagueAISettings:
    if client is None or not league_id:
        return LeagueAISettings(league_id=str(league_id or ""))
    try:
        rows = client.table(TABLE).select("*").eq("league_id", league_id).limit(1).execute().data or []
    except Exception:
        rows = []
    return LeagueAISettings.from_row(rows[0] if rows else None, str(league_id))


def save_settings(client: Any, settings: LeagueAISettings) -> None:
    row = settings.to_row()
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    client.table(TABLE).upsert(row, on_conflict="league_id").execute()


def fetch_sleeper_scoring(sleeper_league_id: str, *, get_json=None) -> dict[str, Any]:
    """Return {'scoring_settings': {...}, 'roster_positions': [...], 'name': ...} from Sleeper."""
    if not sleeper_league_id:
        return {}
    if get_json is None:
        def get_json(url: str):
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            return resp.json()
    payload = get_json(f"{SLEEPER_API}/league/{sleeper_league_id}") or {}
    return {
        "name": payload.get("name"),
        "season": payload.get("season"),
        "scoring_settings": payload.get("scoring_settings") or {},
        "roster_positions": payload.get("roster_positions") or [],
        "league_settings": payload.get("settings") or {},
    }


def apply_sleeper_scoring(settings: LeagueAISettings, sleeper_payload: dict[str, Any]) -> LeagueAISettings:
    settings.scoring_settings = dict(sleeper_payload.get("scoring_settings") or {})
    settings.roster_positions = list(sleeper_payload.get("roster_positions") or [])
    settings.scoring_source = "sleeper"
    settings.scoring_synced_at = datetime.now(timezone.utc).isoformat()
    league_settings = sleeper_payload.get("league_settings") or {}
    if league_settings.get("playoff_teams"):
        settings.playoff_teams = int(league_settings["playoff_teams"])
    if league_settings.get("trade_deadline") not in (None, "", 99):
        settings.trade_deadline = f"week {league_settings['trade_deadline']}"
    return settings


# Human-readable scoring summary for the prompt ------------------------------

_SCORING_LABELS = {
    "pass_yd": ("per passing yard", 1),
    "pass_td": ("per passing TD", 1),
    "pass_int": ("per interception", 1),
    "pass_2pt": ("per passing 2-pt", 1),
    "rush_yd": ("per rushing yard", 1),
    "rush_td": ("per rushing TD", 1),
    "rec": ("per reception", 1),
    "rec_yd": ("per receiving yard", 1),
    "rec_td": ("per receiving TD", 1),
    "bonus_rec_te": ("TE reception bonus", 1),
    "fum_lost": ("per fumble lost", 1),
    "pass_sack": ("per sack taken", 1),
    "bonus_pass_yd_300": ("300-yard passing bonus", 1),
    "bonus_rush_yd_100": ("100-yard rushing bonus", 1),
    "bonus_rec_yd_100": ("100-yard receiving bonus", 1),
}


def lineup_summary(positions: list[str]) -> str:
    """'1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX, 1 OP (superflex)' plus bench/IR/taxi counts and what the OP slot means."""
    if not positions:
        return ""
    from collections import Counter

    starters = Counter(p for p in positions if p not in {"BN", "IR", "TAXI"})
    order = ["QB", "RB", "WR", "TE", "FLEX", "SUPER_FLEX", "REC_FLEX", "K", "DEF", "DL", "LB", "DB", "IDP_FLEX"]
    labels = {"SUPER_FLEX": "OP (superflex: QB/RB/WR/TE)", "FLEX": "FLEX (RB/WR/TE)", "REC_FLEX": "REC FLEX (WR/TE)", "DEF": "DST"}
    parts = [f"{starters[p]} {labels.get(p, p)}" for p in order if starters.get(p)]
    parts += [f"{starters[p]} {p}" for p in starters if p not in order]
    bench = positions.count("BN")
    text = "Starting lineup: " + ", ".join(parts)
    text += f"; {bench} bench" + (", 1 IR slot" if "IR" in positions else "") + (", 1 taxi slot" if "TAXI" in positions else "") + "."
    qbs = starters.get("QB", 0)
    if starters.get("SUPER_FLEX"):
        text += f" This is a {qbs}QB + {starters['SUPER_FLEX']} OP league: a second QB can start every week, so a real QB2 has starter value and QB depth is not surplus."
    elif qbs:
        text += f" This is a {qbs}QB league with no OP slot, so backup QBs have little value."
    return text


def scoring_summary(settings: LeagueAISettings) -> str:
    scoring = settings.scoring_settings or {}
    if not scoring and not settings.roster_positions:
        return "Scoring and lineup settings not synced yet (commissioner: Settings > League AI > Sync scoring from Sleeper)."
    parts = []
    for key, (label, _) in _SCORING_LABELS.items():
        if key in scoring and scoring[key]:
            parts.append(f"{scoring[key]:g} {label}")
    ppr = scoring.get("rec")
    fmt = "full PPR" if ppr == 1 else ("half PPR" if ppr == 0.5 else ("standard (no PPR)" if not ppr else f"{ppr:g} PPR"))
    te_prem = scoring.get("bonus_rec_te")
    if te_prem:
        fmt += f", TE premium +{te_prem:g} per reception"
    pieces = [f"Scoring format: {fmt}" if scoring else "Scoring: not synced"]
    lineup = lineup_summary(settings.roster_positions or [])
    if lineup:
        pieces.append(lineup)
    if parts:
        pieces.append("Scoring detail: " + ", ".join(parts))
    return " ".join(pieces)
