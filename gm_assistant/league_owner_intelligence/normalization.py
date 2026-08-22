from __future__ import annotations

import re
from collections import Counter
from typing import Any

from gm_assistant.league_owner_intelligence.models import (
    LeagueOwnerLineage,
    LeagueTeamIdentity,
    ObservedTransaction,
    TeamReferenceResolution,
    TransactionActionCategory,
)
from gm_assistant.repositories.common import clean_id, safe_float, safe_int


def normalize_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def identity_from_team_row(row: dict[str, Any], league_id: str) -> LeagueTeamIdentity | None:
    team_id = clean_id(row.get("id") or row.get("league_team_id") or row.get("team_id"))
    if not team_id or clean_id(row.get("league_id")) != league_id:
        return None
    aliases = tuple(
        sorted(
            {
                normalize_key(value)
                for value in (
                    row.get("team_name"),
                    row.get("owner_name"),
                    row.get("sleeper_team_name"),
                    row.get("sleeper_roster_id"),
                )
                if normalize_key(value)
            }
        )
    )
    return LeagueTeamIdentity(
        league_id=league_id,
        league_team_id=team_id,
        team_name=clean_id(row.get("team_name")),
        owner_name=clean_id(row.get("owner_name")),
        user_id=clean_id(row.get("user_id")),
        sleeper_roster_id=clean_id(row.get("sleeper_roster_id")),
        sleeper_owner_id=clean_id(row.get("sleeper_owner_id")),
        sleeper_team_name=clean_id(row.get("sleeper_team_name")),
        aliases=aliases,
        lineage=[LeagueOwnerLineage("team_identity", "league_teams", "league", league_id=league_id, league_team_id=team_id)],
    )


def resolve_team_reference(value: Any, identities: list[LeagueTeamIdentity]) -> TeamReferenceResolution:
    text = clean_id(value)
    if not text:
        return TeamReferenceResolution("unresolved", warning="empty_team_reference")
    for identity in identities:
        if text == identity.league_team_id:
            return TeamReferenceResolution("resolved", identity.league_team_id, "league_team_id")
        if identity.sleeper_roster_id and text == identity.sleeper_roster_id:
            return TeamReferenceResolution("resolved", identity.league_team_id, "sleeper_roster_id")
    normalized = normalize_key(text)
    matches = [
        identity
        for identity in identities
        if normalized in identity.aliases
    ]
    if len(matches) == 1:
        return TeamReferenceResolution("resolved", matches[0].league_team_id, "exact_scoped_name")
    if len(matches) > 1:
        return TeamReferenceResolution("ambiguous", candidates=[item.league_team_id for item in matches], warning="ambiguous_scoped_team_reference")
    return TeamReferenceResolution("unresolved", warning="team_reference_not_found")


def normalize_transaction_row(row: dict[str, Any], identities: list[LeagueTeamIdentity], league_id: str) -> list[ObservedTransaction]:
    if clean_id(row.get("league_id")) != league_id:
        return []
    source_name = clean_id(row.get("_source_name")) or "transaction_source"
    tx_id = clean_id(row.get("id") or row.get("tx_id") or row.get("sleeper_transaction_id") or row.get("transaction_id"))
    season = safe_int(row.get("season") or row.get("league_year") or row.get("year"))
    occurred_at = clean_id(row.get("executed_at") or row.get("created_at") or row.get("processed_at") or row.get("timestamp"))
    tx_type = normalize_key(row.get("transaction_type") or row.get("tx_type") or row.get("type") or row.get("event_type"))
    warnings: list[str] = []
    teams = _transaction_team_ids(row, identities, warnings)
    category_rows: list[ObservedTransaction] = []

    added_player = clean_id(row.get("added_player_id") or row.get("player_added_id"))
    dropped_player = clean_id(row.get("dropped_player_id") or row.get("player_dropped_id"))
    player_name = clean_id(row.get("player_name") or row.get("added_player_name") or row.get("dropped_player_name"))

    if tx_type in {"trade", "traded"}:
        if _has_pick(row):
            category_rows.extend(_pick_movements(row, league_id, tx_id, season, occurred_at, source_name, teams, warnings))
        if added_player:
            category_rows.append(_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.TRADE_PLAYER_IN.value, player_id=added_player, player_name=player_name, warnings=warnings))
        if dropped_player:
            category_rows.append(_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.TRADE_PLAYER_OUT.value, player_id=dropped_player, player_name=player_name, warnings=warnings))
        if not category_rows:
            category_rows.append(_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.OTHER_VERIFIED_TRANSACTION.value, warnings=warnings))
        return category_rows

    if tx_type in {"waiver", "free_agent", "free agent", "add"} and added_player:
        return [_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.FREE_AGENT_ADD.value, player_id=added_player, player_name=player_name, warnings=warnings)]
    if tx_type in {"drop", "release", "cut"} or dropped_player:
        return [_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.PLAYER_RELEASE.value, player_id=dropped_player, player_name=player_name, warnings=warnings)]
    if tx_type in {"draft", "draft_selection", "rookie_draft"}:
        return [_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.DRAFT_SELECTION.value, player_id=added_player or clean_id(row.get("player_id")), player_name=player_name, warnings=warnings)]
    if tx_type in {"contract_extension", "extension"}:
        return [_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.CONTRACT_EXTENSION.value, player_id=clean_id(row.get("player_id") or row.get("sleeper_player_id")), player_name=player_name, warnings=warnings)]
    if tx_type in {"contract_change", "contract"}:
        return [_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.CONTRACT_CHANGE.value, player_id=clean_id(row.get("player_id") or row.get("sleeper_player_id")), player_name=player_name, warnings=warnings)]
    if tx_type in {"taxi_add", "taxi"}:
        return [_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.TAXI_ADD.value, player_id=clean_id(row.get("player_id")), player_name=player_name, warnings=warnings)]
    if tx_type == "taxi_remove":
        return [_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.TAXI_REMOVE.value, player_id=clean_id(row.get("player_id")), player_name=player_name, warnings=warnings)]
    if tx_type in {"ir_add", "injured_reserve_add"}:
        return [_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.IR_ADD.value, player_id=clean_id(row.get("player_id")), player_name=player_name, warnings=warnings)]
    if tx_type == "ir_remove":
        return [_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.IR_REMOVE.value, player_id=clean_id(row.get("player_id")), player_name=player_name, warnings=warnings)]
    return [_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, TransactionActionCategory.UNSUPPORTED.value, warnings=warnings + ["unsupported_transaction_type"])]


def dedupe_transactions(items: list[ObservedTransaction]) -> tuple[list[ObservedTransaction], list[str]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[ObservedTransaction] = []
    conflicts: list[str] = []
    for item in items:
        key = (item.source_name, item.transaction_id, item.action_category, item.player_id, item.draft_pick_identity, tuple(sorted(item.involved_league_team_ids)))
        if key in seen:
            conflicts.append(f"duplicate_transaction:{item.transaction_id or item.source_name}")
            continue
        seen.add(key)
        out.append(item)
    return out, conflicts


def _transaction_team_ids(row: dict[str, Any], identities: list[LeagueTeamIdentity], warnings: list[str]) -> list[str]:
    values = [
        row.get("league_team_id"),
        row.get("team_id"),
        row.get("to_team"),
        row.get("from_team"),
        row.get("owner_name"),
        row.get("team_name"),
        row.get("roster_id"),
    ]
    found: list[str] = []
    for value in values:
        resolution = resolve_team_reference(value, identities)
        if resolution.status == "resolved" and resolution.league_team_id and resolution.league_team_id not in found:
            found.append(resolution.league_team_id)
        elif resolution.status == "ambiguous":
            warnings.append("ambiguous_team_reference")
    if not found:
        warnings.append("no_canonical_team_resolved")
    return found


def _has_pick(row: dict[str, Any]) -> bool:
    return any(clean_id(row.get(key)) for key in ("pick_id", "pick_label", "draft_pick_id", "draft_pick_identity"))


def _pick_movements(row: dict[str, Any], league_id: str, tx_id: str | None, season: int | None, occurred_at: str | None, source_name: str, teams: list[str], warnings: list[str]) -> list[ObservedTransaction]:
    pick = clean_id(row.get("pick_id") or row.get("pick_label") or row.get("draft_pick_id") or row.get("draft_pick_identity"))
    direction = normalize_key(row.get("direction"))
    category = TransactionActionCategory.TRADE_PICK_IN.value if direction in {"in", "acquired", "to"} else TransactionActionCategory.TRADE_PICK_OUT.value if direction in {"out", "sent", "from"} else TransactionActionCategory.TRADE_PICK_IN.value
    return [_observed(row, league_id, tx_id, season, occurred_at, source_name, teams, category, draft_pick_identity=pick, warnings=warnings)]


def _observed(row: dict[str, Any], league_id: str, tx_id: str | None, season: int | None, occurred_at: str | None, source_name: str, teams: list[str], category: str, *, player_id: str | None = None, player_name: str | None = None, draft_pick_identity: str | None = None, warnings: list[str] | None = None) -> ObservedTransaction:
    complete = "complete" if teams and tx_id and category != TransactionActionCategory.UNSUPPORTED.value else "partial"
    return ObservedTransaction(
        league_id=league_id,
        transaction_id=tx_id,
        season=season,
        occurred_at=occurred_at,
        involved_league_team_ids=teams,
        action_category=category,
        player_id=player_id,
        player_name=player_name,
        draft_pick_identity=draft_pick_identity,
        salary_or_cap_effect=safe_float(row.get("salary_delta") or row.get("cap_delta") or row.get("amount")),
        source_record_type=clean_id(row.get("transaction_type") or row.get("tx_type") or row.get("type")) or "unknown",
        source_name=source_name,
        completeness=complete,
        warnings=list(warnings or []),
        lineage=[LeagueOwnerLineage("transactions", source_name, "league", league_id=league_id, transaction_id=tx_id)],
    )


def most_common_or_none(values: list[str]) -> str | None:
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]
