from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from season_engine.rollover_service import stable_fingerprint
from services.strict_pagination import complete_rows


VALIDATOR_VERSION = "rollover-contract-preflight-v1"


@dataclass(frozen=True)
class ContractAuthorityReadiness:
    league_id: str
    source_season: int
    target_season: int
    agreement_count: int
    source_season_count: int
    prepared_target_option_count: int
    active_target_count: int
    prior_transition_count: int
    ordinary_expiration_count: int
    taxi_paused_count: int
    unresolved_provenance_count: int
    blockers: tuple[str, ...]
    fingerprint: str
    validator_version: str = VALIDATOR_VERSION

    @property
    def ready(self) -> bool:
        return not self.blockers


class ContractAuthorityPreflightService:
    """Read-only readiness for rollover operations 10-12.

    This deliberately does not use ``contract_transition_executions`` as proof:
    that legacy ledger records a completed contract mutation.  Rollover preflight
    instead verifies the immutable inputs that operations 10-12 will freeze and
    mutate later.
    """

    def __init__(self, client: Any):
        self.client = client

    def run(self, league_id: str, source_season: int, target_season: int) -> ContractAuthorityReadiness:
        agreements = self._rows("contract_agreements", league_id=league_id)
        seasons = self._rows("contract_seasons", league_id=league_id)
        league_seasons = self._rows("league_seasons", league_id=league_id)
        assignments = []
        source_authorities = [x for x in league_seasons if int(x.get("season") or 0) == source_season]
        target_authorities = [x for x in league_seasons if int(x.get("season") or 0) == target_season]
        if len(source_authorities) == 1:
            assignments = self._rows("season_roster_assignments", league_season_id=source_authorities[0]["id"])
        transitions = [x for x in self._rows("contract_transition_executions", league_id=league_id)
                       if int(x.get("source_season") or 0) == source_season
                       and int(x.get("target_season") or 0) == target_season]
        classifications = self._optional_rows(
            "contract_rollover_classifications", league_id=league_id,
        )
        reconciliations = self._optional_rows(
            "contract_transition_reconciliations", league_id=league_id,
        )
        classifications = [x for x in classifications
                           if int(x.get("source_season") or 0) == source_season
                           and int(x.get("target_season") or 0) == target_season]
        reconciled_transition_ids = {
            str(x.get("legacy_transition_id")) for x in reconciliations
            if int(x.get("source_season") or 0) == source_season
            and int(x.get("target_season") or 0) == target_season
            and x.get("reconciliation_status") == "certified"
        }
        transitions = [x for x in transitions if str(x.get("id")) not in reconciled_transition_ids]
        class_by_agreement = {str(x.get("contract_agreement_id")): x for x in classifications}

        by_agreement: dict[str, list[Mapping[str, Any]]] = {}
        for row in seasons:
            by_agreement.setdefault(str(row.get("contract_id")), []).append(row)
        rostered = {str(x.get("sleeper_player_id") or x.get("canonical_player_id") or "") for x in assignments}
        blockers: list[str] = []
        if target_season != source_season + 1: blockers.append("contract_season_boundary_invalid")
        if len(source_authorities) != 1: blockers.append("contract_source_season_authority_missing")
        if len(target_authorities) != 1: blockers.append("contract_target_season_authority_missing")
        if not agreements: blockers.append("normalized_contract_agreements_missing")

        player_live: dict[str, int] = {}
        source_count = 0
        target_options = 0
        active_target = 0
        ordinary_expirations = 0
        taxi_paused = 0
        unresolved = 0
        for agreement in agreements:
            aid = str(agreement.get("id") or "")
            pid = str(agreement.get("player_id") or "")
            team = str(agreement.get("league_team_id") or "")
            rows = by_agreement.get(aid, [])
            source = [x for x in rows if int(x.get("season") or 0) == source_season]
            target = [x for x in rows if int(x.get("season") or 0) == target_season]
            if len(source) != 1:
                blockers.append(f"contract_source_obligation_count:{aid}:{len(source)}")
            else:
                source_count += 1
                if str(source[0].get("player_id")) != pid or str(source[0].get("league_team_id")) != team:
                    blockers.append(f"contract_source_ownership_mismatch:{aid}")
            if len(target) > 1: blockers.append(f"contract_target_obligation_duplicate:{aid}")
            for row in target:
                if str(row.get("player_id")) != pid or str(row.get("league_team_id")) != team:
                    blockers.append(f"contract_target_ownership_mismatch:{aid}")
                if row.get("obligation_status") == "active": active_target += 1
            classification = str(class_by_agreement.get(aid, {}).get("classification") or "")
            if classifications and not classification:
                unresolved += 1
                blockers.append(f"contract_rollover_classification_missing:{aid}")
            if classification == "ordinary_expiration":
                ordinary_expirations += 1
            elif classification == "rookie_initial_taxi_paused":
                taxi_paused += 1
            # Only a proven, unconsumed rookie option belongs in the owner
            # decision population. Ordinary expirations never manufacture one.
            option_required = (classification == "rookie_option_eligible" if classifications
                               else agreement.get("status") == "expired" and pid in rostered)
            if option_required:
                valid = [x for x in target if x.get("obligation_status") == "scheduled"
                         and bool(x.get("is_option_year")) and bool(x.get("option_type"))]
                if len(valid) != 1:
                    blockers.append(f"prepared_target_option_missing:{aid}")
                else: target_options += 1
            if agreement.get("status") in {"active", "scheduled"}:
                player_live[pid] = player_live.get(pid, 0) + 1
        blockers.extend(f"duplicate_live_agreement:{pid}" for pid, count in player_live.items() if count > 1)
        if active_target: blockers.append("target_contract_authority_already_activated")
        if transitions: blockers.append("prior_contract_transition_conflicts_with_rollover")

        material = {
            "validator": VALIDATOR_VERSION, "league_id": league_id, "source_season": source_season,
            "target_season": target_season,
            "agreements": sorted(({k: row.get(k) for k in ("id", "league_team_id", "player_id", "status", "start_season", "end_season")}
                                  for row in agreements), key=lambda x: str(x["id"])),
            "seasons": sorted(({k: row.get(k) for k in ("id", "contract_id", "league_team_id", "player_id", "season", "salary", "cap_hit", "obligation_status", "is_option_year", "option_type")}
                               for row in seasons), key=lambda x: (str(x["contract_id"]), int(x["season"]))),
            "rostered_players": sorted(rostered), "blockers": sorted(set(blockers)),
            "classifications": sorted(({k: row.get(k) for k in (
                "contract_agreement_id", "classification", "rookie_draft_assignment_id",
                "taxi_assignment_id", "option_consumed")}
                for row in classifications), key=lambda x: str(x["contract_agreement_id"])),
        }
        return ContractAuthorityReadiness(league_id, source_season, target_season, len(agreements), source_count,
            target_options, active_target, len(transitions), ordinary_expirations, taxi_paused,
            unresolved, tuple(dict.fromkeys(blockers)), stable_fingerprint(material))

    def _rows(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        return complete_rows(self.client, table, filters=filters)

    def _optional_rows(self, table: str, **filters: Any) -> list[dict[str, Any]]:
        try:
            return self._rows(table, **filters)
        except Exception as exc:
            # Backward compatibility is limited to an actually absent table.
            # Authorization, pagination, and transport failures must fail closed.
            text = str(exc).lower()
            if "does not exist" in text or "pgrst205" in text or "42p01" in text:
                return []
            raise
