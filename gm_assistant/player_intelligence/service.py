from __future__ import annotations

from typing import Any

from gm_assistant.player_intelligence.models import (
    PlayerFieldConflict,
    PlayerIdentity,
    PlayerIntelligenceAvailability,
    PlayerIntelligenceCompleteness,
    PlayerIntelligenceLineage,
    PlayerIntelligenceProfile,
    PlayerLeagueContext,
)
from gm_assistant.player_intelligence.normalization import (
    clean_text,
    normalize_player_id,
    normalize_player_name,
    player_name_key,
    safe_float,
    safe_int,
)
from gm_assistant.repositories import ContractRepository, PlayerRepository, RosterRepository
from gm_assistant.repositories.common import RepositoryResult
from gm_assistant.request_context import AssistantRequestContext


SOURCE_PRIORITY = ("global", "scoped", "roster", "contract")
CAREER_KEYS = ("age", "experience", "years_exp", "is_rookie", "draft_year", "draft_round", "draft_pick")
STRATEGIC_KEYS = ("strategic_label", "priority", "role", "risk", "window_fit", "contract_flag", "is_rookie", "rookie_draft_selected")
RELATIVE_VALUE_KEYS = ("league_value_tier", "overall_percentile", "position_percentile", "rank", "value_score", "trend", "trajectory", "opportunity")


class PlayerIntelligenceService:
    """Compose global and scoped player rows into typed assistant profiles."""

    def __init__(self, sb: Any):
        self.sb = sb
        self.player_repo = PlayerRepository(sb)
        self.roster_repo = RosterRepository(sb)
        self.contract_repo = ContractRepository(sb)

    def get_profile(
        self,
        context: AssistantRequestContext,
        *,
        player_id: str | None = None,
        player_name: str | None = None,
        include_league_context: bool = True,
    ) -> PlayerIntelligenceProfile:
        return self.get_profiles(
            context,
            player_ids=[player_id] if player_id else None,
            player_names=[player_name] if player_name else None,
            include_league_context=include_league_context,
        )[0]

    def get_profiles(
        self,
        context: AssistantRequestContext,
        *,
        player_ids: list[str] | None = None,
        player_names: list[str] | None = None,
        include_league_context: bool = True,
    ) -> list[PlayerIntelligenceProfile]:
        requested_ids = _dedupe([normalize_player_id(value) for value in (player_ids or [])])
        requested_names = _dedupe([normalize_player_name(value) for value in (player_names or [])])
        if player_ids and not requested_ids:
            return [
                _empty_profile(
                    availability=PlayerIntelligenceAvailability.MALFORMED_SOURCE_DATA.value,
                    include_league_context=include_league_context,
                    warnings=["requested_player_id_is_missing_or_malformed"],
                )
            ]
        scoped_result = self.player_repo.get_scoped_player_profiles(context, player_ids=requested_ids or None)
        global_result = self.player_repo.get_global_player_intelligence(context, player_ids=requested_ids or None)
        roster_result = None
        contract_result = None
        if include_league_context:
            roster_result = self.roster_repo.get_team_roster(context, league_team_id=context.league_team_id)
            contract_result = self.contract_repo.get_contracts(
                context,
                league_team_ids=[context.league_team_id],
                player_ids=requested_ids or None,
            )

        sources = _collect_sources(global_result, scoped_result, roster_result, contract_result)
        if not requested_ids and not requested_names:
            requested_ids = _dedupe([source.player_id for source in sources if source.player_id])
        profiles = []
        for player_id in requested_ids:
            profiles.append(self._profile_for_id(context, player_id, sources, include_league_context))
        for player_name in requested_names:
            if player_name and player_name_key(player_name) not in {player_name_key(profile.identity.player_name) for profile in profiles}:
                profiles.append(self._profile_for_name(context, player_name, sources, include_league_context))
        if not profiles:
            profiles.append(_empty_profile(include_league_context=include_league_context))
        return profiles

    def _profile_for_id(
        self,
        context: AssistantRequestContext,
        player_id: str | None,
        sources: list["_SourceRow"],
        include_league_context: bool,
    ) -> PlayerIntelligenceProfile:
        if not player_id:
            return _empty_profile(
                availability=PlayerIntelligenceAvailability.MALFORMED_SOURCE_DATA.value,
                include_league_context=include_league_context,
                warnings=["requested_player_id_is_missing_or_malformed"],
            )
        matching = [source for source in sources if source.player_id == player_id]
        if not matching:
            return _empty_profile(
                player_id=player_id,
                availability=PlayerIntelligenceAvailability.NOT_FOUND.value,
                include_league_context=include_league_context,
            )
        return _build_profile(context, matching, include_league_context)

    def _profile_for_name(
        self,
        context: AssistantRequestContext,
        player_name: str,
        sources: list["_SourceRow"],
        include_league_context: bool,
    ) -> PlayerIntelligenceProfile:
        key = player_name_key(player_name)
        matching = [source for source in sources if source.name_key == key]
        distinct_ids = {source.player_id for source in matching if source.player_id}
        if len(distinct_ids) > 1:
            return _empty_profile(
                player_name=player_name,
                availability=PlayerIntelligenceAvailability.AMBIGUOUS_IDENTITY.value,
                include_league_context=include_league_context,
                warnings=["player_name_matches_multiple_ids"],
                lineage=[source.lineage for source in matching],
            )
        if not matching:
            return _empty_profile(
                player_name=player_name,
                availability=PlayerIntelligenceAvailability.NOT_FOUND.value,
                include_league_context=include_league_context,
            )
        return _build_profile(context, matching, include_league_context)


class _SourceRow:
    def __init__(self, kind: str, row: dict[str, Any], lineage: PlayerIntelligenceLineage):
        self.kind = kind
        self.row = dict(row)
        self.player_id = _row_player_id(row)
        self.name = normalize_player_name(row.get("player_name") or row.get("name"))
        self.name_key = player_name_key(self.name)
        self.lineage = lineage


def _collect_sources(*results: RepositoryResult | None) -> list[_SourceRow]:
    out: list[_SourceRow] = []
    for result in results:
        if result is None:
            continue
        kind = _kind_for_source(result.source.source_name)
        for row in result.rows:
            lineage = PlayerIntelligenceLineage(
                domain=result.source.domain,
                source_name=result.source.source_name,
                scope=result.source.scope,
                league_id=result.source.league_id,
                league_team_id=result.source.league_team_id,
                player_id=_row_player_id(row),
                status=result.source.status,
            )
            out.append(_SourceRow(kind, row, lineage))
    return out


def _kind_for_source(source_name: str | None) -> str:
    source = source_name or ""
    if source == "player_intelligence":
        return "global"
    if "player_strategic_profiles" in source or "league_relative_player_values" in source:
        return "scoped"
    if "roster" in source:
        return "roster"
    if "contracts" in source:
        return "contract"
    return "scoped"


def _build_profile(
    context: AssistantRequestContext,
    matching: list[_SourceRow],
    include_league_context: bool,
) -> PlayerIntelligenceProfile:
    ordered = sorted(matching, key=lambda source: SOURCE_PRIORITY.index(source.kind) if source.kind in SOURCE_PRIORITY else 99)
    selected, conflicts = _selected_fields(ordered)
    player_id = selected.get("player_id")
    sleeper_id = selected.get("sleeper_id") or player_id
    global_rows = [source for source in matching if source.kind == "global"]
    scoped_rows = [source for source in matching if source.kind == "scoped"]
    roster_rows = [source for source in matching if source.kind == "roster"]
    contract_rows = [source for source in matching if source.kind == "contract"]
    league_context = _league_context(context, roster_rows, contract_rows, scoped_rows) if include_league_context else PlayerLeagueContext()
    strategic_profile = _compact_keys(_merge_rows(scoped_rows), STRATEGIC_KEYS)
    relative_value = _compact_keys(_merge_rows(scoped_rows), RELATIVE_VALUE_KEYS)
    career_context = _career_context(_merge_rows(global_rows + scoped_rows + roster_rows + contract_rows))
    global_intelligence = _global_intelligence(_merge_rows(global_rows))
    derived = _derived_fields(league_context, career_context)
    completeness = _completeness(
        include_league_context=include_league_context,
        global_rows=global_rows,
        scoped_rows=scoped_rows,
        roster_rows=roster_rows,
        contract_rows=contract_rows,
        league_context=league_context,
        has_identity=bool(player_id or selected.get("player_name")),
    )
    warnings = []
    if include_league_context and not completeness.league_context_resolved:
        warnings.append("scoped_context_unavailable")
    availability = _availability(completeness)
    return PlayerIntelligenceProfile(
        identity=PlayerIdentity(
            player_id=player_id,
            sleeper_id=sleeper_id,
            player_name=selected.get("player_name"),
            position=selected.get("position"),
            nfl_team=selected.get("nfl_team"),
        ),
        availability=availability,
        completeness=completeness,
        career_context=career_context,
        global_intelligence=global_intelligence,
        strategic_profile=strategic_profile,
        league_relative_value=relative_value,
        league_context=league_context,
        derived_fields=derived,
        conflicts=conflicts,
        warnings=warnings,
        lineage=[source.lineage for source in ordered],
    )


def _selected_fields(sources: list[_SourceRow]) -> tuple[dict[str, Any], list[PlayerFieldConflict]]:
    selected: dict[str, Any] = {}
    selected_source: dict[str, str] = {}
    conflicts: list[PlayerFieldConflict] = []
    fields = {
        "player_id": lambda row: _row_player_id(row),
        "sleeper_id": lambda row: normalize_player_id(row.get("sleeper_id") or row.get("sleeper_player_id") or row.get("player_id")),
        "player_name": lambda row: normalize_player_name(row.get("player_name") or row.get("name")),
        "position": lambda row: clean_text(row.get("position") or row.get("player_position") or row.get("pos")),
        "nfl_team": lambda row: clean_text(row.get("nfl_team") or row.get("team")),
    }
    for source in sources:
        for field, getter in fields.items():
            value = getter(source.row)
            if value in (None, "", [], {}):
                continue
            if field not in selected:
                selected[field] = value
                selected_source[field] = source.kind
            elif selected[field] != value:
                conflicts.append(PlayerFieldConflict(field, selected[field], value, selected_source[field], source.kind))
    return selected, conflicts


def _league_context(
    context: AssistantRequestContext,
    roster_rows: list[_SourceRow],
    contract_rows: list[_SourceRow],
    scoped_rows: list[_SourceRow],
) -> PlayerLeagueContext:
    merged = _merge_rows(roster_rows + contract_rows + scoped_rows)
    team_id = normalize_player_id(merged.get("league_team_id") or merged.get("team_id")) or context.league_team_id
    roster_status = clean_text(merged.get("status") or merged.get("roster_status"))
    designation = clean_text(merged.get("roster_designation") or merged.get("designation"))
    contract_status = clean_text(merged.get("contract_status"))
    years = safe_int(merged.get("contract_years_left") or merged.get("years_remaining"))
    salary = safe_float(merged.get("salary") or merged.get("contract_salary"))
    status_blob = " ".join(str(value or "").lower() for value in (roster_status, designation, contract_status))
    is_taxi = "taxi" in status_blob or merged.get("is_taxi") is True
    is_ir = "injured" in status_blob or status_blob == "ir" or merged.get("is_ir") is True
    return PlayerLeagueContext(
        league_id=context.league_id,
        league_team_id=team_id,
        fantasy_team_name=clean_text(merged.get("team_name") or merged.get("owner_team_name")) or context.team_name,
        owner_name=clean_text(merged.get("owner_name")) or context.owner_name,
        roster_status=roster_status,
        roster_designation=designation,
        is_free_agent=False if roster_rows or contract_rows or scoped_rows else None,
        is_taxi=is_taxi,
        is_ir=is_ir,
        salary=salary,
        contract_years_remaining=years,
        contract_status=contract_status,
    )


def _career_context(row: dict[str, Any]) -> dict[str, Any]:
    career: dict[str, Any] = {}
    if safe_float(row.get("age")) is not None:
        career["age"] = safe_float(row.get("age"))
    experience = safe_int(row.get("experience") or row.get("years_exp"))
    if experience is not None:
        career["experience"] = experience
    for key in ("is_rookie", "draft_year", "draft_round", "draft_pick"):
        value = row.get(key)
        if value not in (None, "", [], {}):
            career[key] = value
    return career


def _global_intelligence(row: dict[str, Any]) -> dict[str, Any]:
    excluded = {
        "id", "player_id", "sleeper_id", "sleeper_player_id", "player_name", "name",
        "position", "player_position", "pos", "nfl_team", "team", *CAREER_KEYS,
    }
    return {key: value for key, value in row.items() if key not in excluded and value not in (None, "", [], {})}


def _derived_fields(league_context: PlayerLeagueContext, career_context: dict[str, Any]) -> dict[str, Any]:
    derived: dict[str, Any] = {}
    if league_context.salary is not None and league_context.contract_years_remaining:
        derived["contract_cost_per_remaining_year"] = round(league_context.salary / league_context.contract_years_remaining, 2)
    age = safe_float(career_context.get("age"))
    if age is not None:
        if age < 24:
            derived["age_bucket"] = "young"
        elif age > 29:
            derived["age_bucket"] = "veteran"
        else:
            derived["age_bucket"] = "prime"
    if league_context.is_taxi:
        derived["roster_designation"] = "taxi"
    if league_context.is_ir:
        derived["injury_designation"] = "ir"
    return derived


def _completeness(
    *,
    include_league_context: bool,
    global_rows: list[_SourceRow],
    scoped_rows: list[_SourceRow],
    roster_rows: list[_SourceRow],
    contract_rows: list[_SourceRow],
    league_context: PlayerLeagueContext,
    has_identity: bool,
) -> PlayerIntelligenceCompleteness:
    groups = []
    if has_identity:
        groups.append("identity")
    if global_rows:
        groups.append("global_intelligence")
    if scoped_rows:
        groups.append("league_value_context")
    if roster_rows:
        groups.append("roster_context")
    if contract_rows:
        groups.append("contract_context")
    league_resolved = bool(league_context.league_id and league_context.league_team_id and (scoped_rows or roster_rows or contract_rows))
    expected = ["identity", "global_intelligence"]
    if include_league_context:
        expected.extend(["league_value_context", "roster_context", "contract_context"])
    missing = [group for group in expected if group not in groups]
    source_names = _dedupe([source.lineage.source_name for source in global_rows + scoped_rows + roster_rows + contract_rows])
    unavailable = []
    if not global_rows:
        unavailable.append("player_intelligence")
    if include_league_context and not league_resolved:
        unavailable.append("scoped_league_context")
    return PlayerIntelligenceCompleteness(
        present_groups=tuple(groups),
        missing_groups=tuple(missing),
        available_sources=tuple(source_names),
        unavailable_sources=tuple(unavailable),
        league_context_requested=include_league_context,
        league_context_resolved=league_resolved,
    )


def _availability(completeness: PlayerIntelligenceCompleteness) -> str:
    if not completeness.present_groups:
        return PlayerIntelligenceAvailability.NOT_FOUND.value
    if completeness.present_groups == ("identity",):
        return PlayerIntelligenceAvailability.IDENTITY_ONLY.value
    if "global_intelligence" not in completeness.present_groups:
        return PlayerIntelligenceAvailability.GLOBAL_INTELLIGENCE_UNAVAILABLE.value
    if completeness.league_context_requested and not completeness.league_context_resolved:
        return PlayerIntelligenceAvailability.SCOPED_CONTEXT_UNAVAILABLE.value
    if completeness.missing_groups:
        return PlayerIntelligenceAvailability.PARTIAL.value
    return PlayerIntelligenceAvailability.FOUND_COMPLETE_ENOUGH.value


def _empty_profile(
    *,
    player_id: str | None = None,
    player_name: str | None = None,
    availability: str = PlayerIntelligenceAvailability.NOT_FOUND.value,
    include_league_context: bool,
    warnings: list[str] | None = None,
    lineage: list[PlayerIntelligenceLineage] | None = None,
) -> PlayerIntelligenceProfile:
    return PlayerIntelligenceProfile(
        identity=PlayerIdentity(player_id=player_id, sleeper_id=player_id, player_name=player_name),
        availability=availability,
        completeness=PlayerIntelligenceCompleteness(
            missing_groups=("identity", "global_intelligence", "league_value_context", "roster_context", "contract_context") if include_league_context else ("identity", "global_intelligence"),
            league_context_requested=include_league_context,
        ),
        warnings=warnings or [],
        lineage=lineage or [],
    )


def _row_player_id(row: dict[str, Any]) -> str | None:
    return normalize_player_id(
        row.get("canonical_player_id")
        or row.get("sleeper_id")
        or row.get("sleeper_player_id")
        or row.get("player_id")
    )


def _merge_rows(sources: list[_SourceRow]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in sources:
        for key, value in source.row.items():
            if value not in (None, "", [], {}) and key not in merged:
                merged[key] = value
    return merged


def _compact_keys(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: row[key] for key in keys if row.get(key) not in (None, "", [], {})}


def _dedupe(values: list[Any]) -> list[Any]:
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out
