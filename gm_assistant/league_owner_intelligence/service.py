from __future__ import annotations

from collections import defaultdict
from typing import Any

from gm_assistant.draft_intelligence.normalization import normalize_pick_label
from gm_assistant.league_owner_intelligence.models import (
    BehavioralTendencyType,
    LeagueOwnerIntelligenceContext,
    LeagueOwnerLineage,
    LeagueOwnerProfile,
    LeagueTeamIdentity,
    ObservationWindow,
    ObservedTransaction,
    TeamActivitySummary,
    TeamAssetMovement,
    TeamBehavioralTendency,
    TeamCurrentStateSummary,
    TradePartnerHistory,
    TransactionActionCategory,
)
from gm_assistant.league_owner_intelligence.normalization import dedupe_transactions, identity_from_team_row, normalize_transaction_row, resolve_team_reference
from gm_assistant.repositories.common import clean_id, require_scoped_context, rows, safe_float, safe_int
from gm_assistant.repositories.transactions import TransactionRepository
from gm_assistant.request_context import AssistantRequestContext


ACTIVE_TRADER_MIN_TRADES = 3
TENDENCY_MIN_DISTINCT_DATES = 2
NET_PICK_MIN_MOVEMENT = 3
FUTURE_FIRST_MIN_MOVEMENT = 2
FREE_AGENT_MIN_ADDITIONS = 5


class LeagueOwnerIntelligenceService:
    """Build factual, scoped owner/team behavior context without predicting intent."""

    def __init__(self, sb: Any):
        self.sb = sb

    def get_context(
        self,
        *,
        context: AssistantRequestContext,
        seasons: list[int] | None = None,
        target_team_id: str | None = None,
    ) -> LeagueOwnerIntelligenceContext:
        require_scoped_context(context)
        identities = self._load_team_identities(context)
        if not identities:
            return unavailable_league_owner_intelligence_context(context, "No canonical league teams are available.")
        if target_team_id and not any(item.league_team_id == target_team_id for item in identities):
            return unavailable_league_owner_intelligence_context(context, "Requested target team is outside the active league.")

        transactions_result = TransactionRepository(self.sb).get_league_transactions(context)
        observed: list[ObservedTransaction] = []
        for row in transactions_result.rows:
            observed.extend(normalize_transaction_row(row, identities, context.league_id))
        observed, tx_conflicts = dedupe_transactions(observed)
        if seasons:
            allowed = {int(season) for season in seasons}
            observed = [item for item in observed if item.season in allowed]
        current_state = self._current_state_by_team(context, identities)
        window = _observation_window(observed, transaction_history_available=bool(transactions_result.rows))
        partner_histories = _partner_histories(observed)
        profiles = []
        for identity in identities:
            if target_team_id and identity.league_team_id != target_team_id:
                continue
            team_observed = [item for item in observed if identity.league_team_id in item.involved_league_team_ids]
            summary = _activity_summary(identity.league_team_id, context.league_team_id, team_observed, window)
            tendencies = _tendencies(identity.league_team_id, summary, window, team_observed)
            completeness = {
                "current_team_state": "available" if identity.league_team_id in current_state else "unavailable",
                "transaction_history": window.history_state,
                "tendencies": "supported" if tendencies else "unsupported_low_evidence",
                "player_age_enrichment": "available_when_roster_age_fields_exist",
            }
            profile = LeagueOwnerProfile(
                identity=identity,
                current_state=current_state.get(identity.league_team_id) or TeamCurrentStateSummary(identity.league_team_id, warnings=["current_state_unavailable"]),
                activity_summary=summary,
                tendencies=tendencies,
                trade_partner_history=[history for history in partner_histories if identity.league_team_id in {history.team_a_id, history.team_b_id}],
                completeness=completeness,
                lineage=identity.lineage + [LeagueOwnerLineage("league_owner_intelligence", "league_owner_intelligence_service", "league", league_id=context.league_id, league_team_id=identity.league_team_id)],
                warnings=[],
                conflicts=[item for item in tx_conflicts if item],
            )
            profiles.append(profile)

        return LeagueOwnerIntelligenceContext(
            league_id=context.league_id,
            requesting_user_id=context.user_id,
            requesting_league_team_id=context.league_team_id,
            profiles=profiles,
            observed_transactions=observed,
            observation_window=window,
            completeness={
                "canonical_teams": "available",
                "transaction_history": window.history_state,
                "current_state": "available",
            },
            lineage=[
                LeagueOwnerLineage("team_identity", "league_teams", "league", league_id=context.league_id),
                LeagueOwnerLineage("transactions", transactions_result.source.source_name, "league", league_id=context.league_id, status=transactions_result.source.status),
            ],
            warnings=[] if transactions_result.rows else ["transaction_history_unavailable"],
            conflicts=tx_conflicts,
            availability="available",
        )

    def resolve_team_reference(self, context: AssistantRequestContext, raw_reference: Any):
        require_scoped_context(context)
        return resolve_team_reference(raw_reference, self._load_team_identities(context))

    def _load_team_identities(self, context: AssistantRequestContext) -> list[LeagueTeamIdentity]:
        team_rows = rows(
            self.sb.table("league_teams")
            .select("id,league_id,team_name,owner_name,user_id,sleeper_roster_id,sleeper_owner_id,sleeper_team_name")
            .eq("league_id", context.league_id)
            .limit(200)
        )
        identities = [identity_from_team_row(row, context.league_id) for row in team_rows]
        return [identity for identity in identities if identity]

    def _current_state_by_team(self, context: AssistantRequestContext, identities: list[LeagueTeamIdentity]) -> dict[str, TeamCurrentStateSummary]:
        roster_rows = _safe_league_rows(self.sb, "team_roster_state", context.league_id)
        contract_rows = _safe_league_rows(self.sb, "contracts", context.league_id)
        cap_rows = _safe_league_rows(self.sb, "v_team_caps", context.league_id)
        pick_rows = _safe_league_rows(self.sb, "draft_picks", context.league_id)
        by_team: dict[str, TeamCurrentStateSummary] = {}
        for identity in identities:
            aliases = {identity.league_team_id, identity.team_name, identity.owner_name, identity.sleeper_roster_id}
            aliases = {value for value in aliases if value}
            team_roster = [_row for _row in roster_rows if _row_team_id(_row, identities) == identity.league_team_id or clean_id(_row.get("owner_name") or _row.get("team_name")) in aliases]
            team_contracts = [_row for _row in contract_rows if _row_team_id(_row, identities) == identity.league_team_id or clean_id(_row.get("owner_name") or _row.get("team_name") or _row.get("owner")) in aliases]
            active_contracts = [row for row in team_contracts if str(row.get("status") or row.get("contract_status") or "").strip().lower() not in {"released", "cut", "dropped", "waived"}]
            roster_source = team_roster or active_contracts
            positions: dict[str, int] = defaultdict(int)
            taxi = 0
            ir = 0
            for row in roster_source:
                position = clean_id(row.get("position") or row.get("player_position"))
                if position:
                    positions[position] += 1
                status = str(row.get("status") or row.get("roster_status") or "").strip().lower()
                if status == "taxi":
                    taxi += 1
                if status in {"ir", "injured", "injured_reserve"}:
                    ir += 1
            salary_values = [safe_float(row.get("salary")) for row in active_contracts]
            committed_salary = round(sum(value for value in salary_values if value is not None), 2) if salary_values else None
            cap_row = _matching_cap_row(cap_rows, identity)
            picks = [_row for _row in pick_rows if _pick_owner(_row, identities) == identity.league_team_id]
            future_counts: dict[str, int] = defaultdict(int)
            exact_picks: list[str] = []
            for pick in picks:
                if safe_int(pick.get("season")) and safe_int(pick.get("season")) <= context.current_season:
                    continue
                round_number = safe_int(pick.get("round"))
                if round_number:
                    future_counts[str(round_number)] += 1
                label = normalize_pick_label(pick.get("pick_label") or pick.get("label"))
                if label:
                    exact_picks.append(label)
            by_team[identity.league_team_id] = TeamCurrentStateSummary(
                league_team_id=identity.league_team_id,
                roster_count=len(roster_source),
                positional_counts=dict(sorted(positions.items())),
                contract_count=len(active_contracts),
                committed_salary=committed_salary,
                available_cap=safe_float((cap_row or {}).get("available_cap") or (cap_row or {}).get("cap_space")),
                future_pick_counts_by_round=dict(sorted(future_counts.items())),
                exact_picks=sorted(set(exact_picks)),
                taxi_count=taxi,
                ir_count=ir,
                expiring_contract_count=sum(1 for row in active_contracts if safe_int(row.get("contract_years_left") or row.get("years_remaining")) == 1),
                lineage=[
                    LeagueOwnerLineage("roster", "team_roster_state/contracts", "team", league_id=context.league_id, league_team_id=identity.league_team_id),
                    LeagueOwnerLineage("draft_picks", "draft_picks", "league", league_id=context.league_id, league_team_id=identity.league_team_id),
                ],
            )
        return by_team


def unavailable_league_owner_intelligence_context(context: AssistantRequestContext, warning: str) -> LeagueOwnerIntelligenceContext:
    return LeagueOwnerIntelligenceContext(
        league_id=context.league_id,
        requesting_user_id=context.user_id,
        requesting_league_team_id=context.league_team_id,
        availability="unavailable",
        completeness={"league_owner_intelligence": "unavailable"},
        warnings=[warning],
    )


def _safe_league_rows(sb: Any, table_name: str, league_id: str) -> list[dict[str, Any]]:
    try:
        return rows(sb.table(table_name).select("*").eq("league_id", league_id).limit(3000))
    except Exception:
        return []


def _row_team_id(row: dict[str, Any], identities: list[LeagueTeamIdentity]) -> str | None:
    for value in (row.get("league_team_id"), row.get("team_id"), row.get("owner_name"), row.get("team_name"), row.get("owner"), row.get("roster_id")):
        resolved = resolve_team_reference(value, identities)
        if resolved.status == "resolved":
            return resolved.league_team_id
    return None


def _matching_cap_row(cap_rows: list[dict[str, Any]], identity: LeagueTeamIdentity) -> dict[str, Any] | None:
    for row in cap_rows:
        if clean_id(row.get("league_team_id") or row.get("team_id")) == identity.league_team_id:
            return row
        if clean_id(row.get("owner_name") or row.get("team_name")) in {identity.owner_name, identity.team_name}:
            return row
    return None


def _pick_owner(row: dict[str, Any], identities: list[LeagueTeamIdentity]) -> str | None:
    for key in ("resolved_current_owner_team_id", "current_owner_team_id", "league_team_id", "team_id", "current_owner", "owner"):
        resolved = resolve_team_reference(row.get(key), identities)
        if resolved.status == "resolved":
            return resolved.league_team_id
    return None


def _observation_window(observed: list[ObservedTransaction], *, transaction_history_available: bool) -> ObservationWindow:
    dates = sorted({item.occurred_at for item in observed if item.occurred_at})
    seasons = sorted({item.season for item in observed if item.season})
    state = "available" if transaction_history_available else "unavailable"
    if transaction_history_available and not observed:
        state = "available_no_supported_records"
    return ObservationWindow(
        first_recorded_transaction=dates[0] if dates else None,
        last_recorded_transaction=dates[-1] if dates else None,
        seasons_included=seasons,
        transaction_count=len(observed),
        history_state=state,
        source_complete=None,
    )


def _activity_summary(team_id: str, authenticated_team_id: str, observed: list[ObservedTransaction], window: ObservationWindow) -> TeamActivitySummary:
    trades = [item for item in observed if item.action_category.startswith("trade_")]
    partner_ids = sorted({other for item in trades for other in item.involved_league_team_ids if other != team_id})
    movement = TeamAssetMovement(
        players_acquired_by_trade=sum(1 for item in observed if item.action_category == TransactionActionCategory.TRADE_PLAYER_IN.value),
        players_sent_by_trade=sum(1 for item in observed if item.action_category == TransactionActionCategory.TRADE_PLAYER_OUT.value),
        picks_acquired=sum(1 for item in observed if item.action_category == TransactionActionCategory.TRADE_PICK_IN.value),
        picks_sent=sum(1 for item in observed if item.action_category == TransactionActionCategory.TRADE_PICK_OUT.value),
        future_firsts_acquired=sum(1 for item in observed if item.action_category == TransactionActionCategory.TRADE_PICK_IN.value and _is_first(item.draft_pick_identity)),
        future_firsts_sent=sum(1 for item in observed if item.action_category == TransactionActionCategory.TRADE_PICK_OUT.value and _is_first(item.draft_pick_identity)),
        free_agent_additions=sum(1 for item in observed if item.action_category == TransactionActionCategory.FREE_AGENT_ADD.value),
        player_releases=sum(1 for item in observed if item.action_category == TransactionActionCategory.PLAYER_RELEASE.value),
        draft_selections=sum(1 for item in observed if item.action_category == TransactionActionCategory.DRAFT_SELECTION.value),
    )
    unique_trade_ids = {item.transaction_id or repr(item) for item in trades}
    trades_with_authenticated = sum(1 for item in trades if authenticated_team_id in item.involved_league_team_ids and team_id != authenticated_team_id)
    return TeamActivitySummary(
        league_team_id=team_id,
        completed_trades=len(unique_trade_ids),
        distinct_trade_partner_ids=partner_ids,
        trades_with_authenticated_team=trades_with_authenticated,
        asset_movement=movement,
        transaction_count=len(observed),
        observation_window=window,
        warnings=[] if observed else ["no_supported_transaction_records_for_team"],
    )


def _tendencies(team_id: str, summary: TeamActivitySummary, window: ObservationWindow, observed: list[ObservedTransaction]) -> list[TeamBehavioralTendency]:
    dates = {item.occurred_at or f"season:{item.season}" for item in observed if item.occurred_at or item.season}
    enough_dates = len(dates) >= TENDENCY_MIN_DISTINCT_DATES
    if window.history_state != "available" or not enough_dates:
        return []
    out: list[TeamBehavioralTendency] = []
    if summary.completed_trades >= ACTIVE_TRADER_MIN_TRADES:
        out.append(_tendency(BehavioralTendencyType.ACTIVE_TRADER.value, team_id, summary.completed_trades, f">={ACTIVE_TRADER_MIN_TRADES} completed trades on >={TENDENCY_MIN_DISTINCT_DATES} dates", window, {"completed_trades": summary.completed_trades}))
    movement = summary.asset_movement
    net_picks = movement.picks_acquired - movement.picks_sent
    if net_picks >= NET_PICK_MIN_MOVEMENT:
        out.append(_tendency(BehavioralTendencyType.NET_PICK_ACQUIRER.value, team_id, net_picks, f"net picks acquired >= {NET_PICK_MIN_MOVEMENT}", window, {"net_pick_movement": net_picks}))
    if net_picks <= -NET_PICK_MIN_MOVEMENT:
        out.append(_tendency(BehavioralTendencyType.NET_PICK_SELLER.value, team_id, abs(net_picks), f"net picks sent >= {NET_PICK_MIN_MOVEMENT}", window, {"net_pick_movement": net_picks}))
    net_firsts = movement.future_firsts_acquired - movement.future_firsts_sent
    if net_firsts >= FUTURE_FIRST_MIN_MOVEMENT:
        out.append(_tendency(BehavioralTendencyType.FUTURE_FIRST_ACQUIRER.value, team_id, net_firsts, f"net future firsts acquired >= {FUTURE_FIRST_MIN_MOVEMENT}", window, {"net_future_first_movement": net_firsts}))
    if net_firsts <= -FUTURE_FIRST_MIN_MOVEMENT:
        out.append(_tendency(BehavioralTendencyType.FUTURE_FIRST_SELLER.value, team_id, abs(net_firsts), f"net future firsts sent >= {FUTURE_FIRST_MIN_MOVEMENT}", window, {"net_future_first_movement": net_firsts}))
    if movement.free_agent_additions >= FREE_AGENT_MIN_ADDITIONS:
        out.append(_tendency(BehavioralTendencyType.FREQUENT_FREE_AGENT_ACTIVITY.value, team_id, movement.free_agent_additions, f">={FREE_AGENT_MIN_ADDITIONS} free-agent additions", window, {"free_agent_additions": movement.free_agent_additions}))
    return out


def _tendency(kind: str, team_id: str, count: int, threshold: str, window: ObservationWindow, facts: dict[str, Any]) -> TeamBehavioralTendency:
    return TeamBehavioralTendency(kind, team_id, count, threshold, window, facts)


def _partner_histories(observed: list[ObservedTransaction]) -> list[TradePartnerHistory]:
    grouped: dict[tuple[str, str], list[ObservedTransaction]] = defaultdict(list)
    for item in observed:
        if not item.action_category.startswith("trade_") or len(item.involved_league_team_ids) < 2:
            continue
        ids = sorted(set(item.involved_league_team_ids))
        for idx, left in enumerate(ids):
            for right in ids[idx + 1:]:
                grouped[(left, right)].append(item)
    histories = []
    for (left, right), items in grouped.items():
        histories.append(TradePartnerHistory(
            left,
            right,
            len({item.transaction_id or repr(item) for item in items}),
            sorted({item.transaction_id for item in items if item.transaction_id}),
            sorted({item.season for item in items if item.season}),
            sorted([item.occurred_at for item in items if item.occurred_at])[-1] if any(item.occurred_at for item in items) else None,
            [item.action_category for item in items[:10]],
        ))
    return histories


def _is_first(pick: str | None) -> bool:
    if not pick:
        return False
    text = str(pick).lower()
    return "1st" in text or "first" in text or text.startswith("1.") or "round 1" in text
