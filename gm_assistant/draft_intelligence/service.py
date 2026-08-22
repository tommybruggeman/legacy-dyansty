from __future__ import annotations

from typing import Any

from gm_assistant.draft_intelligence.models import (
    DraftBoardState,
    DraftContextCompleteness,
    DraftIntelligenceAvailability,
    DraftIntelligenceContext,
    DraftLineage,
    DraftPickAsset,
    DraftSelection,
    DraftSlot,
    ParsedPickReference,
    ProspectProfile,
)
from gm_assistant.draft_intelligence.normalization import normalize_pick_label
from gm_assistant.player_intelligence import PlayerIntelligenceService
from gm_assistant.player_intelligence.normalization import normalize_player_id, normalize_player_name, player_name_key, safe_float, safe_int
from gm_assistant.repositories import DraftPickRepository, TeamRepository
from gm_assistant.repositories.common import load_league_teams, require_scoped_context, rows
from gm_assistant.request_context import AssistantRequestContext
from services.rookie_prospects import classify_rookie_identity


PROSPECT_TABLES = ("rookie_draft_board", "player_prospect_context", "rookie_class_registry")
SELECTION_TABLES = ("rookie_draft_results", "rookie_draft_board", "draft_selections")


class DraftIntelligenceService:
    """Compose verified draft state without generating recommendations."""

    def __init__(self, sb: Any):
        self.sb = sb
        self.pick_repo = DraftPickRepository(sb)
        self.player_intelligence = PlayerIntelligenceService(sb)

    def get_context(
        self,
        context: AssistantRequestContext,
        *,
        season: int | None = None,
        requested_pick_labels: list[str] | None = None,
        team_id: str | None = None,
    ) -> DraftIntelligenceContext:
        require_scoped_context(context)
        draft_season = int(season or context.requested_season or context.current_season)
        target_team_id = team_id or context.league_team_id
        picks = self.get_pick_assets(context, season=draft_season)
        owned = [pick for pick in picks if pick.current_owner_team_id == target_team_id]
        requested = _filter_requested_picks(picks, requested_pick_labels or [])
        prospects, prospect_missing, player_profiles_available = self.get_prospects(context, season=draft_season)
        selections, selection_missing = self.get_selections(context, season=draft_season)
        board = DraftBoardState(
            draft_season=draft_season,
            status="partial" if prospect_missing or selection_missing else "available",
            completed_selections=selections,
            available_prospects=_available_prospects(prospects, selections),
            user_owned_picks=owned,
            missing_data=[item for item in [prospect_missing, selection_missing] if item],
            lineage=_dedupe_lineage([line for pick in picks for line in pick.lineage] + [line for item in prospects for line in item.lineage] + [line for item in selections for line in item.lineage]),
        )
        roster_needs = self._roster_needs(context)
        completeness = _completeness(
            picks=picks,
            selections=selections,
            prospects=prospects,
            roster_needs=roster_needs,
            prospect_missing=prospect_missing,
            selection_missing=selection_missing,
            player_profiles_available=player_profiles_available,
        )
        states = []
        if picks:
            states.append(DraftIntelligenceAvailability.PICK_OWNERSHIP_AVAILABLE.value)
        if not prospects:
            states.append(DraftIntelligenceAvailability.PROSPECT_POOL_UNAVAILABLE.value)
        if not selections:
            states.append(DraftIntelligenceAvailability.SELECTIONS_INCOMPLETE.value)
        return DraftIntelligenceContext(
            league_id=context.league_id,
            league_team_id=target_team_id,
            season=draft_season,
            requested_pick=_pick_ref_from_label(requested_pick_labels[0]) if requested_pick_labels else None,
            owned_picks=owned,
            requested_picks=requested,
            board_state=board,
            prospect_profiles=prospects,
            roster_needs=roster_needs,
            availability_states=states,
            completeness=completeness,
            warnings=board.missing_data,
            lineage=board.lineage,
        )

    def get_pick_assets(
        self,
        context: AssistantRequestContext,
        *,
        season: int | None = None,
        team_id: str | None = None,
    ) -> list[DraftPickAsset]:
        result = self.pick_repo.get_draft_picks(context, league_team_id=team_id, seasons=[season] if season else None)
        return [_asset_from_row(context, row, result.source.source_name) for row in result.rows]

    def get_selections(self, context: AssistantRequestContext, *, season: int) -> tuple[list[DraftSelection], str | None]:
        selections: list[DraftSelection] = []
        found_source = None
        for table in SELECTION_TABLES:
            source_rows = _safe_rows(self.sb, table, context.league_id, season)
            if not source_rows:
                continue
            found_source = table
            for row in source_rows:
                selection = _selection_from_row(context, row, table)
                if selection:
                    selections.append(selection)
        selections = _dedupe_selections(selections)
        return selections, None if found_source else "selections_unavailable"

    def get_prospects(self, context: AssistantRequestContext, *, season: int) -> tuple[list[ProspectProfile], str | None, bool]:
        prospect_rows: list[tuple[str, dict[str, Any]]] = []
        for table in PROSPECT_TABLES:
            for row in _safe_rows(self.sb, table, context.league_id, season):
                prospect_rows.append((table, row))
        if not prospect_rows:
            return [], "prospect_pool_unavailable", False

        ids = _dedupe([normalize_player_id(_row_player_id(row)) for _, row in prospect_rows])
        profiles_by_id = {}
        if ids:
            for profile in self.player_intelligence.get_profiles(context, player_ids=ids, include_league_context=False):
                if profile.identity.player_id:
                    profiles_by_id[profile.identity.player_id] = profile
        prospects = [_prospect_from_row(context, table, row, profiles_by_id) for table, row in prospect_rows]
        prospects = _dedupe_prospects([prospect for prospect in prospects if prospect])
        return prospects, None, bool(profiles_by_id)

    def _roster_needs(self, context: AssistantRequestContext) -> dict[str, Any]:
        try:
            result = TeamRepository(self.sb).get_team_brain(context, league_team_id=context.league_team_id)
        except Exception:
            return {}
        if not result.rows:
            return {}
        row = result.rows[0]
        return {
            key: row[key]
            for key in ("position_needs", "position_strengths", "team_direction", "championship_window_score")
            if row.get(key) not in (None, "", [], {})
        }


def _asset_from_row(context: AssistantRequestContext, row: dict[str, Any], source_name: str) -> DraftPickAsset:
    label = normalize_pick_label(row.get("pick_label") or row.get("label"))
    round_number = safe_int(row.get("round"))
    rank = safe_int(row.get("original_pick_rank"))
    slot_value = safe_int(row.get("slot") or row.get("pick"))
    exact_slot = None
    if label:
        parts = label.split(".")
        exact_slot = DraftSlot(safe_int(row.get("season")), safe_int(parts[0]), safe_int(parts[1]), label, selecting_team_id=normalize_player_id(row.get("resolved_current_owner_team_id") or row.get("current_owner_team_id") or row.get("league_team_id")))
    elif slot_value and round_number:
        exact_slot = DraftSlot(safe_int(row.get("season")), round_number, slot_value, f"{round_number}.{slot_value:02d}")
    lineage = [DraftLineage("draft", source_name, "league", league_id=context.league_id)]
    current_owner = normalize_player_id(row.get("resolved_current_owner_team_id") or row.get("current_owner_team_id") or row.get("league_team_id") or row.get("team_id"))
    original_owner = normalize_player_id(row.get("resolved_original_team_id") or row.get("original_team_id"))
    warnings = []
    if not current_owner:
        warnings.append("current_owner_unresolved")
    if not label and rank and not slot_value:
        warnings.append("original_pick_rank_is_not_verified_exact_slot")
    return DraftPickAsset(
        league_id=context.league_id,
        season=safe_int(row.get("season")),
        round=round_number,
        current_owner_team_id=current_owner,
        original_team_id=original_owner,
        pick_label=label,
        original_pick_rank=rank,
        exact_slot=exact_slot,
        ownership_status=str(row.get("status") or row.get("pick_status") or "available"),
        warnings=warnings,
        lineage=lineage,
    )


def _selection_from_row(context: AssistantRequestContext, row: dict[str, Any], source_name: str) -> DraftSelection | None:
    if source_name == "rookie_draft_board" and not any(row.get(key) for key in ("player", "selected_player", "selected_player_id", "selected_at")):
        return None
    player_id = normalize_player_id(_row_player_id(row))
    player_name = normalize_player_name(row.get("player_name") or row.get("selected_player") or row.get("name") or row.get("player"))
    if not player_id and not player_name:
        return None
    round_number = safe_int(row.get("round"))
    slot = safe_int(row.get("slot") or row.get("pick") or row.get("original_pick_rank") or row.get("rookie_rank"))
    label = normalize_pick_label(row.get("pick_label")) or (f"{round_number}.{slot:02d}" if round_number and slot else None)
    draft_slot = DraftSlot(
        safe_int(row.get("season") or row.get("draft_year") or row.get("class_year") or row.get("rookie_class_year")),
        round_number,
        slot,
        label,
        selecting_team_id=normalize_player_id(row.get("league_team_id") or row.get("team_id") or row.get("resolved_current_owner_team_id")),
        selection_status="selected",
        selected_player_id=player_id,
        lineage=[DraftLineage("draft_selection", source_name, "league", league_id=context.league_id, player_id=player_id)],
    )
    return DraftSelection(
        draft_id=normalize_player_id(row.get("draft_id")),
        slot=draft_slot,
        selecting_team_id=draft_slot.selecting_team_id,
        player_id=player_id,
        player_name=player_name,
        selection_order=safe_int(row.get("selection_order") or row.get("overall_pick") or slot),
        selected_at=str(row.get("selected_at") or row.get("created_at") or "") or None,
        lineage=draft_slot.lineage,
    )


def _prospect_from_row(context: AssistantRequestContext, source_name: str, row: dict[str, Any], profiles_by_id: dict[str, Any]) -> ProspectProfile | None:
    player_id = normalize_player_id(_row_player_id(row))
    player_name = normalize_player_name(row.get("player_name") or row.get("name") or row.get("full_name"))
    if not player_id and not player_name:
        return None
    linked = profiles_by_id.get(player_id) if player_id else None
    position = str(row.get("position") or row.get("pos") or row.get("player_position") or (linked.identity.position if linked else "") or "").strip() or None
    conflicts = []
    if linked and linked.identity.position and position and linked.identity.position != position:
        conflicts.append({"field": "position", "selected_value": position, "conflicting_value": linked.identity.position, "source": source_name, "conflicting_source": "player_intelligence"})
    completeness = {
        "identity": bool(player_id or player_name),
        "draft_class": bool(row.get("draft_year") or row.get("class_year") or row.get("rookie_class_year")),
        "ranking": bool(row.get("rookie_rank") or row.get("rank") or row.get("consensus_rank")),
        "player_intelligence_linked": bool(linked),
    }
    return ProspectProfile(
        prospect_id=player_id or player_name_key(player_name),
        sleeper_id=player_id,
        player_name=player_name or (linked.identity.player_name if linked else None),
        position=position,
        college=str(row.get("college") or row.get("school") or "").strip() or None,
        age=safe_float(row.get("age")),
        rookie_status=classify_rookie_identity(row, context.current_season).value,
        draft_class=safe_int(row.get("draft_year") or row.get("class_year") or row.get("rookie_class_year")),
        stored_ranking=safe_int(row.get("rookie_rank") or row.get("rank") or row.get("consensus_rank")),
        stored_tier=str(row.get("tier") or row.get("rookie_tier") or "") or None,
        player_intelligence_id=linked.identity.player_id if linked else None,
        availability_state=DraftIntelligenceAvailability.PROSPECT_PROFILE_PARTIAL.value,
        completeness=completeness,
        conflicts=conflicts,
        lineage=[DraftLineage("prospect", source_name, "league", league_id=context.league_id, player_id=player_id)],
    )


def _safe_rows(sb: Any, table_name: str, league_id: str, season: int) -> list[dict[str, Any]]:
    try:
        table_rows = rows(sb.table(table_name).select("*").eq("league_id", league_id))
    except Exception:
        try:
            table_rows = rows(sb.table(table_name).select("*"))
        except Exception:
            return []
    if not table_rows:
        try:
            table_rows = rows(sb.table(table_name).select("*"))
        except Exception:
            table_rows = []
    out = []
    for row in table_rows:
        row_league_id = row.get("league_id")
        if row_league_id not in (None, "", league_id):
            continue
        row_season = safe_int(row.get("season") or row.get("draft_year") or row.get("class_year") or row.get("rookie_class_year"))
        if row_season is None or row_season == season:
            out.append(row)
    return out


def _filter_requested_picks(picks: list[DraftPickAsset], labels: list[str]) -> list[DraftPickAsset]:
    if not labels:
        return []
    wanted = set(labels)
    out = []
    for pick in picks:
        labels_for_pick = set()
        if pick.pick_label:
            labels_for_pick.add(pick.pick_label)
        if pick.canonical_pick_id:
            labels_for_pick.add(pick.canonical_pick_id)
        if pick.season and pick.round:
            labels_for_pick.add(f"{pick.season}_round_{pick.round}")
        if labels_for_pick.intersection(wanted):
            out.append(pick)
    return out


def _pick_ref_from_label(label: str) -> ParsedPickReference:
    normalized = normalize_pick_label(label)
    if normalized:
        round_number, slot = [int(part) for part in normalized.split(".")]
        return ParsedPickReference(label, "exact_slot", round=round_number, slot=slot, label=normalized)
    return ParsedPickReference(label, "round_only", label=label)


def _available_prospects(prospects: list[ProspectProfile], selections: list[DraftSelection]) -> list[ProspectProfile]:
    selected_ids = {selection.player_id for selection in selections if selection.player_id}
    selected_names = {player_name_key(selection.player_name) for selection in selections if selection.player_name}
    out = []
    for prospect in prospects:
        if prospect.sleeper_id and prospect.sleeper_id in selected_ids:
            continue
        if player_name_key(prospect.player_name) in selected_names:
            continue
        out.append(prospect)
    return out


def _completeness(
    *,
    picks: list[DraftPickAsset],
    selections: list[DraftSelection],
    prospects: list[ProspectProfile],
    roster_needs: dict[str, Any],
    prospect_missing: str | None,
    selection_missing: str | None,
    player_profiles_available: bool,
) -> DraftContextCompleteness:
    missing = []
    if not picks:
        missing.append("ownership")
    if selection_missing:
        missing.append("selections")
    if prospect_missing:
        missing.append("prospect_pool")
    if not player_profiles_available:
        missing.append("player_profiles")
    if not roster_needs:
        missing.append("roster_context")
    return DraftContextCompleteness(
        ownership=bool(picks),
        draft_order=bool(any(pick.exact_slot for pick in picks)),
        selections=bool(selections),
        prospect_pool=bool(prospects),
        player_profiles=player_profiles_available,
        roster_context=bool(roster_needs),
        missing_groups=tuple(missing),
        available_sources=tuple(_dedupe([line.source_name for pick in picks for line in pick.lineage] + [line.source_name for prospect in prospects for line in prospect.lineage] + [line.source_name for selection in selections for line in selection.lineage])),
        unavailable_sources=tuple(item for item in (prospect_missing, selection_missing) if item),
    )


def _row_player_id(row: dict[str, Any]) -> Any:
    return row.get("canonical_player_id") or row.get("sleeper_id") or row.get("sleeper_player_id") or row.get("player_id") or row.get("sleeper_player_id")


def _dedupe(values: list[Any]) -> list[Any]:
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _dedupe_lineage(lineage: list[DraftLineage]) -> list[DraftLineage]:
    out = []
    seen = set()
    for item in lineage:
        key = (item.domain, item.source_name, item.scope, item.league_id, item.league_team_id, item.player_id)
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _dedupe_selections(selections: list[DraftSelection]) -> list[DraftSelection]:
    out = []
    seen = set()
    for item in selections:
        key = (item.slot.season, item.slot.round, item.slot.overall_pick, item.player_id, player_name_key(item.player_name))
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _dedupe_prospects(prospects: list[ProspectProfile]) -> list[ProspectProfile]:
    out = []
    seen = set()
    for item in prospects:
        key = item.sleeper_id or player_name_key(item.player_name)
        if key and key not in seen:
            out.append(item)
            seen.add(key)
    return out
