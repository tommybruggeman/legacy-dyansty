from __future__ import annotations

from statistics import median
from typing import Any

from gm_assistant.football_intelligence.models import (
    FootballLineage,
    FootballPlayerSnapshot,
    LineupRequirement,
    LineupRulesProfile,
    PositionGroupProfile,
    RosterNeed,
    RosterRisk,
    RosterStrength,
)
from gm_assistant.football_intelligence.normalization import eligible_positions_for_slot, normalize_lineup_slot
from gm_assistant.repositories.common import safe_int


DEPTH_BUFFER = 1
CONTRACT_CLIFF_RATIO = 0.50
POSITION_SALARY_CONCENTRATION_RATIO = 0.35
PLAYER_SALARY_CONCENTRATION_RATIO = 0.25
AGE_CONCENTRATION_RATIO = 0.50
AGE_RISK_THRESHOLDS = {
    "QB": 32,
    "RB": 27,
    "WR": 29,
    "TE": 30,
    "K": 32,
    "DST": 30,
    "DL": 30,
    "LB": 30,
    "DB": 30,
    "IDP": 30,
}

LINEUP_KEY_ALIASES = {
    "qb": "QB",
    "qbs": "QB",
    "starting_qb": "QB",
    "starting_qbs": "QB",
    "rb": "RB",
    "rbs": "RB",
    "starting_rb": "RB",
    "starting_rbs": "RB",
    "wr": "WR",
    "wrs": "WR",
    "starting_wr": "WR",
    "starting_wrs": "WR",
    "te": "TE",
    "tes": "TE",
    "starting_te": "TE",
    "starting_tes": "TE",
    "k": "K",
    "dst": "DST",
    "def": "DST",
    "dl": "DL",
    "lb": "LB",
    "db": "DB",
    "idp": "IDP",
    "flex": "FLEX",
    "superflex": "SUPERFLEX",
    "sf": "SUPERFLEX",
    "op": "SUPERFLEX",
}


def normalize_lineup_rules(rule_rows: list[dict[str, Any]], settings_rows: list[dict[str, Any]]) -> LineupRulesProfile:
    values: dict[str, int] = {}
    warnings: list[str] = []
    lineage: list[FootballLineage] = []
    for source_name, rows_in in (("league_rules", rule_rows), ("league_settings", settings_rows)):
        for row in rows_in:
            league_id = str(row.get("league_id") or "") or None
            lineage.append(FootballLineage("football_rules", source_name, "league", league_id=league_id))
            for raw_key, raw_value in _iter_rule_values(row):
                key = _canonical_rule_key(raw_key)
                if not key:
                    continue
                count = safe_int(raw_value)
                if count is None:
                    warnings.append(f"malformed_lineup_count:{raw_key}")
                    continue
                if count < 0:
                    warnings.append(f"negative_lineup_count:{raw_key}")
                    continue
                values.setdefault(key, count)
    starters = []
    for key in ("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB", "IDP", "FLEX", "SUPERFLEX"):
        count = values.get(key)
        if count:
            starters.append(LineupRequirement(key, count, eligible_positions_for_slot(key), key.lower()))
    availability = "available" if starters else "partial"
    if not starters:
        warnings.append("lineup_requirements_unavailable")
    return LineupRulesProfile(
        availability=availability,
        starter_slots=tuple(starters),
        bench_slots=values.get("BENCH"),
        taxi_slots=values.get("TAXI"),
        ir_slots=values.get("IR"),
        warnings=tuple(dict.fromkeys(warnings)),
        lineage=tuple(lineage),
    )


def build_depth_evaluations(groups: list[PositionGroupProfile]) -> tuple[list[RosterStrength], list[RosterNeed]]:
    strengths: list[RosterStrength] = []
    needs: list[RosterNeed] = []
    for group in groups:
        if group.required_starters is None:
            continue
        if group.active_count < group.required_starters:
            needs.append(RosterNeed("immediate_starter_shortage.v1", f"{group.position} starter shortage", group.position, "high", f"{group.position} has {group.active_count} active eligible players for {group.required_starters} direct starting slots.", ("lineup_rules", "team_roster")))
        elif group.active_count == group.required_starters:
            needs.append(RosterNeed("minimal_depth.v1", f"{group.position} minimal depth", group.position, "medium", f"{group.position} exactly meets the direct starter requirement with no extra active depth.", ("lineup_rules", "team_roster")))
        elif group.active_count >= group.required_starters + DEPTH_BUFFER:
            strengths.append(RosterStrength("depth_coverage.v1", f"{group.position} depth coverage", group.position, f"{group.position} has {group.active_count} active players for {group.required_starters} direct starting slots.", ("lineup_rules", "team_roster")))
    return strengths, needs


def contract_risks(groups: list[PositionGroupProfile], total_salary: float | None) -> list[RosterRisk]:
    risks: list[RosterRisk] = []
    for group in groups:
        if group.roster_count and group.expiring_contract_count / group.roster_count >= CONTRACT_CLIFF_RATIO:
            risks.append(RosterRisk("contract_cliff.v1", f"{group.position} contract cliff", group.position, "medium", f"{group.expiring_contract_count} of {group.roster_count} {group.position} players have one year or less remaining.", ("contracts",)))
        if total_salary and group.committed_salary and group.committed_salary / total_salary > POSITION_SALARY_CONCENTRATION_RATIO:
            risks.append(RosterRisk("position_salary_concentration.v1", f"{group.position} salary concentration", group.position, "medium", f"{group.position} accounts for {round(group.committed_salary / total_salary * 100, 1)}% of verified committed salary.", ("contracts",)))
    return risks


def age_risks(groups: list[PositionGroupProfile]) -> list[RosterRisk]:
    risks: list[RosterRisk] = []
    for group in groups:
        threshold = AGE_RISK_THRESHOLDS.get(group.position)
        if threshold is None:
            continue
        known = [player for player in group.players if player.age is not None]
        older = [player for player in known if (player.age or 0) >= threshold]
        if known and len(older) / len(known) >= AGE_CONCENTRATION_RATIO:
            risks.append(RosterRisk("age_concentration.v1", f"{group.position} veteran concentration", group.position, "medium", f"{len(older)} of {len(known)} {group.position} players are at or above the documented age threshold of {threshold}.", ("player_intelligence",)))
    return risks


def numeric_summary(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    return round(sum(values) / len(values), 2), round(float(median(values)), 2)


def _iter_rule_values(row: dict[str, Any]):
    if row.get("key") or row.get("name"):
        yield row.get("key") or row.get("name"), row.get("value") or row.get("count") or row.get("setting_value")
    for key, value in row.items():
        yield key, value


def _canonical_rule_key(raw_key: Any) -> str | None:
    text = str(raw_key or "").strip().lower()
    if not text:
        return None
    if text in {"bench", "bench_spots", "bench_slots"}:
        return "BENCH"
    if text in {"taxi", "taxi_slots", "taxi_squad_slots"}:
        return "TAXI"
    if text in {"ir", "ir_slots", "injured_reserve_slots"}:
        return "IR"
    return LINEUP_KEY_ALIASES.get(text) or normalize_lineup_slot(text)
