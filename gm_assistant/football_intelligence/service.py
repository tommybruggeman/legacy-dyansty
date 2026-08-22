from __future__ import annotations

from collections import defaultdict
from typing import Any

from gm_assistant.draft_intelligence import DraftIntelligenceService
from gm_assistant.football_intelligence.models import (
    AgeCurveProfile,
    ContractExposureProfile,
    DraftFlexibilityProfile,
    FootballIntelligenceContext,
    FootballLineage,
    FootballPlayerSnapshot,
    PositionGroupProfile,
    RosterConstructionProfile,
    RosterNeed,
    RosterRisk,
    StrategyFitDimension,
)
from gm_assistant.football_intelligence.normalization import (
    normalize_roster_status,
    row_age,
    row_contract_years,
    row_experience,
    row_is_rookie,
    row_player_id,
    row_player_name,
    row_position,
    row_salary,
)
from gm_assistant.football_intelligence.rules import (
    PLAYER_SALARY_CONCENTRATION_RATIO,
    age_risks,
    build_depth_evaluations,
    contract_risks,
    normalize_lineup_rules,
    numeric_summary,
)
from gm_assistant.player_intelligence import PlayerIntelligenceService
from gm_assistant.repositories import CapRepository, ContractRepository, DraftPickRepository, LeagueRepository, RosterRepository
from gm_assistant.repositories.common import clean_id, require_scoped_context
from gm_assistant.request_context import AssistantRequestContext


def unavailable_football_intelligence_context(context: AssistantRequestContext, warning: str) -> FootballIntelligenceContext:
    return FootballIntelligenceContext(
        league_id=context.league_id,
        league_team_id=context.league_team_id,
        season=context.requested_season or context.current_season,
        availability="unavailable",
        completeness={"football_intelligence": "unavailable"},
        warnings=(warning,),
        lineage=(FootballLineage("football_intelligence", "football_intelligence_service", "team", context.league_id, context.league_team_id, status="unavailable"),),
    )


class FootballIntelligenceService:
    """Read-only deterministic football context from already verified sources."""

    def __init__(self, sb: Any):
        self.sb = sb
        self.roster_repo = RosterRepository(sb)
        self.contract_repo = ContractRepository(sb)
        self.cap_repo = CapRepository(sb)
        self.league_repo = LeagueRepository(sb)
        self.pick_repo = DraftPickRepository(sb)
        self.player_intelligence = PlayerIntelligenceService(sb)

    def get_context(
        self,
        *,
        context: AssistantRequestContext,
        season: int | None = None,
        league_team_id: str | None = None,
        owner_goal: str | None = None,
    ) -> FootballIntelligenceContext:
        require_scoped_context(context)
        target_team_id = clean_id(league_team_id) or context.league_team_id
        evaluated_season = int(season or context.requested_season or context.current_season)
        warnings: list[str] = []
        lineage: list[FootballLineage] = [FootballLineage("football_intelligence", "football_intelligence_service", "team", context.league_id, target_team_id)]

        roster_result = self.roster_repo.get_team_roster(context, league_team_id=target_team_id)
        contract_result = self.contract_repo.get_contracts(context, league_team_ids=[target_team_id])
        cap_result = self.cap_repo.get_cap_summary(context, league_team_id=target_team_id)
        rule_result = self.league_repo.get_rule_sources(context)
        settings_result = self.league_repo.get_league_settings(context)
        picks_result = self.pick_repo.get_draft_picks(context, league_team_id=target_team_id)
        player_profiles = self._player_profiles(context, roster_result.rows, contract_result.rows)

        player_rows = _merge_player_rows(roster_result.rows, contract_result.rows, player_profiles)
        snapshots, skipped = _snapshots(player_rows)
        warnings.extend(skipped)
        lineup = normalize_lineup_rules(rule_result.rows, settings_result.rows)
        groups = _position_groups(snapshots, lineup.required_by_position())
        total_salary = _sum_optional(player.salary for player in snapshots)
        strengths, depth_needs = build_depth_evaluations(groups)
        risks = contract_risks(groups, total_salary) + age_risks(groups)
        risks.extend(_single_player_salary_risks(snapshots, total_salary))
        draft_profile = _draft_flexibility(picks_result.rows, evaluated_season)
        needs = list(depth_needs) + _future_pick_needs(draft_profile)
        contract_profile = _contract_exposure(snapshots, groups, total_salary)
        age_profile = _age_profile(snapshots)
        construction = RosterConstructionProfile(
            roster_count=len([player for player in snapshots if player.roster_status != "released"]),
            active_roster_count=len([player for player in snapshots if player.roster_status == "active"]),
            taxi_count=len([player for player in snapshots if player.roster_status == "taxi"]),
            ir_count=len([player for player in snapshots if player.roster_status == "ir"]),
            position_groups=tuple(groups),
            lineup_rules=lineup,
            contract_exposure=contract_profile,
            age_curve=age_profile,
            draft_flexibility=draft_profile,
            strengths=tuple(strengths),
            needs=tuple(needs),
            risks=tuple(risks),
            strategy_fit=tuple(_strategy_fit(strengths, needs, risks, draft_profile, cap_result.rows, owner_goal)),
            warnings=tuple(dict.fromkeys(warnings + list(lineup.warnings))),
        )
        completeness = {
            "roster": "available" if roster_result.rows else "empty",
            "contracts": "available" if contract_result.rows else "empty",
            "salary_cap": "available" if cap_result.rows else "empty",
            "lineup_rules": lineup.availability,
            "draft_context": "available" if picks_result.rows else "empty",
            "player_intelligence": "available" if player_profiles else "empty",
        }
        return FootballIntelligenceContext(
            league_id=context.league_id,
            league_team_id=target_team_id,
            season=evaluated_season,
            availability="available" if snapshots else "partial",
            roster_construction=construction,
            owner_goal=owner_goal,
            completeness=completeness,
            warnings=tuple(dict.fromkeys(warnings + list(lineup.warnings))),
            lineage=tuple(lineage + list(lineup.lineage)),
        )

    def compare_contexts(self, before: FootballIntelligenceContext, after: FootballIntelligenceContext) -> dict[str, Any]:
        before_profile = before.roster_construction
        after_profile = after.roster_construction
        if not before_profile or not after_profile:
            return {"availability": "unavailable", "warnings": ["both_contexts_required"]}
        return {
            "availability": "available",
            "league_id": before.league_id,
            "league_team_id": before.league_team_id,
            "roster_count_delta": after_profile.roster_count - before_profile.roster_count,
            "active_roster_count_delta": after_profile.active_roster_count - before_profile.active_roster_count,
            "need_count_delta": len(after_profile.needs) - len(before_profile.needs),
            "risk_count_delta": len(after_profile.risks) - len(before_profile.risks),
        }

    def _player_profiles(self, context: AssistantRequestContext, roster_rows: list[dict[str, Any]], contract_rows: list[dict[str, Any]]) -> dict[str, Any]:
        ids = _dedupe(row_player_id(row) for row in roster_rows + contract_rows)
        if not ids:
            return {}
        profiles = self.player_intelligence.get_profiles(context, player_ids=ids, include_league_context=True)
        return {profile.identity.player_id: profile for profile in profiles if profile.identity.player_id}


def _merge_player_rows(roster_rows: list[dict[str, Any]], contract_rows: list[dict[str, Any]], profiles_by_id: dict[str, Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    no_id_rows: list[dict[str, Any]] = []
    for source, rows_in in (("roster", roster_rows), ("contract", contract_rows)):
        for row in rows_in:
            player_id = row_player_id(row)
            base = dict(row)
            base["_source_refs"] = [source]
            if not player_id:
                no_id_rows.append(base)
                continue
            current = merged.setdefault(player_id, {"sleeper_id": player_id, "_source_refs": []})
            current.update({key: value for key, value in base.items() if value not in (None, "", [], {})})
            current["_source_refs"] = sorted(set(current.get("_source_refs", []) + [source]))
    for player_id, profile in profiles_by_id.items():
        current = merged.setdefault(player_id, {"sleeper_id": player_id, "_source_refs": []})
        current.update({key: value for key, value in profile.to_evidence_row().items() if value not in (None, "", [], {})})
        current["_source_refs"] = sorted(set(current.get("_source_refs", []) + ["player_intelligence"]))
    return list(merged.values()) + no_id_rows


def _snapshots(rows_in: list[dict[str, Any]]) -> tuple[list[FootballPlayerSnapshot], list[str]]:
    snapshots: list[FootballPlayerSnapshot] = []
    warnings: list[str] = []
    seen: set[tuple[str | None, str]] = set()
    for row in rows_in:
        status = normalize_roster_status(row)
        if status == "released":
            continue
        name = row_player_name(row)
        player_id = row_player_id(row)
        if not name and not player_id:
            warnings.append("player_row_missing_identity")
            continue
        key = (player_id, (name or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        position = row_position(row)
        if not position:
            warnings.append(f"player_position_unavailable:{name or player_id}")
        snapshots.append(
            FootballPlayerSnapshot(
                player_id=player_id,
                player_name=name or player_id or "Unknown player",
                position=position,
                roster_status=status,
                age=row_age(row),
                experience=row_experience(row),
                is_rookie=row_is_rookie(row),
                salary=row_salary(row),
                contract_years_remaining=row_contract_years(row),
                value_tier=clean_id(row.get("league_value_tier") or row.get("value_tier") or row.get("tier")),
                source_refs=tuple(row.get("_source_refs") or ()),
            )
        )
    return snapshots, warnings


def _position_groups(players: list[FootballPlayerSnapshot], required: dict[str, int]) -> list[PositionGroupProfile]:
    by_position: dict[str, list[FootballPlayerSnapshot]] = defaultdict(list)
    for player in players:
        if player.position:
            by_position[player.position].append(player)
    groups = []
    for position in sorted(set(by_position) | set(required)):
        rows = by_position.get(position, [])
        ages = [player.age for player in rows if player.age is not None]
        average_age, median_age = numeric_summary(ages)
        active_count = len([player for player in rows if player.roster_status == "active"])
        req = required.get(position)
        salary = _sum_optional(player.salary for player in rows)
        groups.append(
            PositionGroupProfile(
                position=position,
                roster_count=len(rows),
                active_count=active_count,
                taxi_count=len([player for player in rows if player.roster_status == "taxi"]),
                ir_count=len([player for player in rows if player.roster_status == "ir"]),
                required_starters=req,
                depth_above_required=active_count - req if req is not None else None,
                average_age=average_age,
                median_age=median_age,
                rookie_count=len([player for player in rows if player.is_rookie is True]),
                veteran_count=len([player for player in rows if player.experience is not None and player.experience >= 4]),
                expiring_contract_count=len([player for player in rows if player.contract_years_remaining is not None and player.contract_years_remaining <= 1]),
                committed_salary=salary,
                salary_share=None,
                players=tuple(sorted(rows, key=lambda item: item.player_name)),
                warnings=("missing_age_data",) if rows and len(ages) < len(rows) else (),
            )
        )
    total_salary = _sum_optional(group.committed_salary for group in groups)
    if not total_salary:
        return groups
    return [
        PositionGroupProfile(
            **{
                **group.__dict__,
                "salary_share": round((group.committed_salary or 0.0) / total_salary, 4),
            }
        )
        for group in groups
    ]


def _contract_exposure(players: list[FootballPlayerSnapshot], groups: list[PositionGroupProfile], total_salary: float | None) -> ContractExposureProfile:
    expiring = [player for player in players if player.contract_years_remaining is not None and player.contract_years_remaining <= 1]
    highest = max([player for player in players if player.salary is not None], key=lambda item: item.salary or 0.0, default=None)
    return ContractExposureProfile(
        committed_salary=round(total_salary, 2) if total_salary is not None else None,
        expiring_contract_count=len(expiring),
        expiring_salary=_sum_optional(player.salary for player in expiring),
        position_salary_shares={group.position: group.salary_share for group in groups if group.salary_share is not None},
        highest_player_salary_share=round((highest.salary or 0.0) / total_salary, 4) if highest and total_salary else None,
        highest_player_salary_name=highest.player_name if highest else None,
        warnings=("contract_salary_unavailable",) if total_salary is None else (),
    )


def _age_profile(players: list[FootballPlayerSnapshot]) -> AgeCurveProfile:
    ages = [player.age for player in players if player.age is not None]
    average_age, median_age = numeric_summary(ages)
    return AgeCurveProfile(
        average_age=average_age,
        median_age=median_age,
        known_age_count=len(ages),
        missing_age_count=len(players) - len(ages),
        veteran_count=len([player for player in players if player.experience is not None and player.experience >= 4]),
        rookie_count=len([player for player in players if player.is_rookie is True]),
        warnings=("age_data_partial",) if len(ages) < len(players) else (),
    )


def _draft_flexibility(pick_rows: list[dict[str, Any]], season: int) -> DraftFlexibilityProfile:
    future = [row for row in pick_rows if int(row.get("season") or 0) > season]
    return DraftFlexibilityProfile(
        future_first_count=len([row for row in future if int(row.get("round") or 0) == 1]),
        future_second_count=len([row for row in future if int(row.get("round") or 0) == 2]),
        future_pick_count=len(future),
        evaluated_seasons=tuple(sorted({int(row.get("season")) for row in future if row.get("season")})),
        warnings=("future_pick_data_unavailable",) if not pick_rows else (),
    )


def _future_pick_needs(profile: DraftFlexibilityProfile) -> list[RosterNeed]:
    if profile.future_first_count or profile.future_second_count:
        return []
    return [RosterNeed("future_pick_limitation.v1", "Limited verified premium future picks", None, "medium", "No verified future first- or second-round picks were found for the selected team.", ("draft_picks",))]


def _single_player_salary_risks(players: list[FootballPlayerSnapshot], total_salary: float | None) -> list[RosterRisk]:
    if not total_salary:
        return []
    out = []
    for player in players:
        if player.salary is not None and player.salary / total_salary > PLAYER_SALARY_CONCENTRATION_RATIO:
            out.append(RosterRisk("single_player_salary_concentration.v1", "Single-player salary concentration", player.position, "medium", f"{player.player_name} accounts for {round(player.salary / total_salary * 100, 1)}% of verified committed salary.", ("contracts",)))
    return out


def _strategy_fit(strengths: list[Any], needs: list[RosterNeed], risks: list[RosterRisk], draft: DraftFlexibilityProfile, cap_rows: list[dict[str, Any]], owner_goal: str | None) -> list[StrategyFitDimension]:
    available_cap = None
    if cap_rows:
        available_cap = cap_rows[0].get("available_cap") or cap_rows[0].get("cap_space")
    cap_status = "aligned" if available_cap is not None and float(available_cap) > 0 else "limited" if available_cap is not None else "unavailable"
    readiness = "aligned" if not any(need.rule_id == "immediate_starter_shortage.v1" for need in needs) else "limited"
    long_term = "limited" if any(risk.rule_id in {"contract_cliff.v1", "age_concentration.v1"} for risk in risks) else "aligned"
    return [
        StrategyFitDimension("short_term_readiness", readiness, "Based on direct starter coverage and verified roster depth.", ("lineup_rules", "team_roster")),
        StrategyFitDimension("long_term_control", long_term, "Based on verified contract and age concentration risks.", ("contracts", "player_intelligence")),
        StrategyFitDimension("future_draft_flexibility", "aligned" if draft.future_first_count or draft.future_second_count else "limited", "Based on verified future first- and second-round picks.", ("draft_picks",)),
        StrategyFitDimension("cap_flexibility", cap_status, "Based on verified cap summary when available.", ("cap_summary",)),
        StrategyFitDimension("owner_goal_framing", "available" if owner_goal else "not_provided", "Owner goal can frame the explanation but does not change the deterministic metrics.", ("owner_intelligence",)),
    ]


def _sum_optional(values: Any) -> float | None:
    found = [float(value) for value in values if value is not None]
    return round(sum(found), 2) if found else None


def _dedupe(values: Any) -> list[str]:
    out = []
    seen = set()
    for value in values:
        text = clean_id(value)
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out
