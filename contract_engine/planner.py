from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from typing import Any

from season_engine.models import LeagueSeason
from .models import ContractBackfillPlan, money


def covered_seasons(active_season: int, years_remaining: Any) -> tuple[int, ...]:
    years = int(years_remaining)
    if years < 1: raise ValueError("years remaining must be at least one")
    return tuple(range(active_season, active_season + years))


def build_backfill_plan(*, active_season: LeagueSeason, legacy_contracts: list[dict],
                        league_teams: list[dict], players: list[dict], league_seasons: list[dict]) -> ContractBackfillPlan:
    errors, warnings = [], []
    if active_season.season != 2025 or not active_season.is_active:
        errors.append(_issue("source_season", "Phase 3A initial backfill requires active 2025."))
    source = sorted(legacy_contracts, key=lambda x: str(x.get("id") or ""))
    fingerprint = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    team_by_owner: dict[str, list[dict]] = defaultdict(list)
    for team in league_teams:
        if str(team.get("league_id")) != active_season.league_id:
            errors.append(_issue("cross_league_team", f"Team {team.get('id')} belongs to another league.")); continue
        team_by_owner[_norm(team.get("owner_name"))].append(team)
    player_by_sleeper = {str(p.get("sleeper_id")): p for p in players if p.get("sleeper_id")}
    source_ids, player_sources = set(), defaultdict(list)
    agreements, schedules, events = [], [], []
    existing_years = {int(x["season"]) for x in league_seasons if str(x.get("league_id")) == active_season.league_id}
    required_years = set()
    for row in source:
        legacy_id = str(row.get("id") or "")
        if not legacy_id or legacy_id in source_ids:
            errors.append(_issue("duplicate_legacy_source", f"Duplicate or missing legacy contract ID {legacy_id!r}.")); continue
        source_ids.add(legacy_id)
        if str(row.get("league_id")) != active_season.league_id:
            errors.append(_issue("cross_league_contract", f"Legacy contract {legacy_id} belongs to another league.")); continue
        teams = team_by_owner.get(_norm(row.get("owner_name")), [])
        if len(teams) != 1:
            errors.append(_issue("team_mapping", f"Contract {legacy_id} maps to {len(teams)} canonical teams.")); continue
        sleeper_id = str(row.get("sleeper_player_id") or "")
        player = player_by_sleeper.get(sleeper_id)
        if not player:
            errors.append(_issue("player_identity", f"Contract {legacy_id} has no canonical Sleeper player identity.")); continue
        player_sources[sleeper_id].append(row)
        try:
            years = covered_seasons(active_season.season, row.get("contract_years_left"))
            salary = money(row.get("salary"))
            if salary < Decimal("0"): raise ValueError("negative salary")
        except Exception as exc:
            errors.append(_issue("invalid_terms", f"Contract {legacy_id} has invalid terms: {exc}.")); continue
        team_id = str(teams[0]["id"]); required_years.update(years)
        contract_key = f"legacy:{legacy_id}"
        agreements.append({"client_key": contract_key, "league_id": active_season.league_id,
            "league_team_id": team_id, "player_id": sleeper_id, "sleeper_player_id": sleeper_id,
            "contract_type": "rookie" if row.get("is_rookie") is True else "unknown",
            "origin": "imported_initial_contract", "signed_season": None,
            "start_season": active_season.season, "end_season": years[-1], "status": "active",
            "source_legacy_contract_id": legacy_id})
        for season in years:
            schedules.append({"contract_key": contract_key, "league_id": active_season.league_id,
                "league_team_id": team_id, "player_id": sleeper_id, "season": season,
                "salary": str(salary), "cap_hit": str(salary),
                "obligation_status": "active" if season == active_season.season else "scheduled",
                "is_option_year": False, "source": "legacy_contract_backfill",
                "source_legacy_contract_id": legacy_id})
        events.append({"contract_key": contract_key, "league_id": active_season.league_id,
            "league_team_id": team_id, "player_id": sleeper_id, "event_type": "imported",
            "effective_season": active_season.season, "source": "legacy_contract_backfill",
            "new_values": {"salary": str(salary), "years_remaining": len(years)},
            "metadata": {"legacy_player_name": row.get("player_name")},
            "idempotency_key": f"contract-import:{legacy_id}:v1"})
    for sleeper_id, rows in sorted(player_sources.items()):
        if len(rows) > 1:
            errors.append(_issue("overlapping_current_contract", f"Player {sleeper_id} has {len(rows)} active legacy obligations.",
                                 legacy_contract_ids=sorted(str(x["id"]) for x in rows),
                                 owners=sorted(str(x.get("owner_name")) for x in rows)))
    future = tuple(sorted(y for y in required_years if y not in existing_years))
    return ContractBackfillPlan(active_season.league_id, str(active_season.id), active_season.season,
        len(source), fingerprint, f"contract-model-backfill:{active_season.league_id}:2025:v1",
        tuple(agreements), tuple(schedules), tuple(events), future, tuple(warnings), tuple(errors))


def _norm(value): return " ".join(str(value or "").strip().lower().split())
def _issue(code, message, **context): return {"code": code, "message": message, "context": context}
