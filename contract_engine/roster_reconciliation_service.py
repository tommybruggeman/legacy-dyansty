from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
import requests

from .roster_reconciliation import MissingRosterEvidence, build_missing_roster_reconciliation
from .transition_service import ContractTransitionService


class MissingRosterReconciliationService:
    """Read-only Phase 3B.1A evidence collection; every database operation is a select."""
    def __init__(self, client: Any, http: Any = requests):
        self.client, self.http = client, http

    def reconcile(self, league_id: str, source_season: int = 2025, target_season: int = 2026) -> dict[str, Any]:
        transition = ContractTransitionService(self.client).plan(league_id, source_season, target_season)
        missing = [x for x in transition.free_agent_candidates if not x["remains_on_captured_roster"]]
        ids = {str(x["sleeper_player_id"]) for x in missing}

        def read(table: str, league: bool = True):
            query = self.client.table(table).select("*")
            if league: query = query.eq("league_id", league_id)
            return query.execute().data or []

        seasons = read("league_seasons")
        source_authority = next(x for x in seasons if int(x["season"]) == source_season)
        sleeper_league_id = str(source_authority["sleeper_league_id"])
        sleeper_rosters = self._get(f"https://api.sleeper.app/v1/league/{sleeper_league_id}/rosters")
        sleeper_transactions = []
        for week in range(0, 19):
            sleeper_transactions.extend(self._get(f"https://api.sleeper.app/v1/league/{sleeper_league_id}/transactions/{week}"))

        contracts, agreements, obligations = read("contracts"), read("contract_agreements"), read("contract_seasons")
        teams = read("league_teams")
        team_by_id = {str(x["id"]):x for x in teams}; team_by_roster = {int(x["sleeper_roster_id"]):x for x in teams}
        snapshots = (self.client.table("season_roster_assignments").select("*").eq("league_season_id",source_authority["id"]).execute().data or [])
        canonical_rosters = [x for x in read("rosters_current") if str(x.get("player_id")) in ids]
        universe = self.client.table("player_universe").select("*").in_("sleeper_id",sorted(ids)).execute().data or []
        ledger = [x for x in read("transaction_ledger") if str(x.get("added_player_id")) in ids or str(x.get("dropped_player_id")) in ids]
        dead_cap = [x for x in read("dead_cap_ledger") if str(x.get("sleeper_player_id")) in ids]
        cap = [x for x in read("cap_adjustments") if str(x.get("sleeper_player_id")) in ids]
        events = read("contract_events")

        evidence_rows=[]
        for candidate in missing:
            pid=str(candidate["sleeper_player_id"]); agreement=next(x for x in agreements if str(x["id"])==candidate["agreement_id"])
            legacy=next(x for x in contracts if str(x["id"])==str(agreement["source_legacy_contract_id"]))
            obligation=next(x for x in obligations if str(x["id"])==candidate["source_contract_season_id"])
            sleeper_team=next((team_by_roster[int(r["roster_id"])] for r in sleeper_rosters if pid in (r.get("players") or []) and int(r["roster_id"]) in team_by_roster),None)
            canonical_row=next((x for x in canonical_rosters if str(x.get("player_id"))==pid),None)
            canonical_team=team_by_id.get(str((canonical_row or {}).get("team_id")))
            tx=[x for x in sleeper_transactions if pid in (x.get("adds") or {}) or pid in (x.get("drops") or {})]
            evidence_rows.append(MissingRosterEvidence(
                agreement=agreement,legacy_contract=legacy,source_obligation=obligation,contract_owner=team_by_id[str(agreement["league_team_id"])],
                captured_assignment=next((x for x in snapshots if str(x.get("sleeper_player_id"))==pid),None),
                sleeper_roster_team=sleeper_team,canonical_roster_team=canonical_team,
                canonical_player=next((x for x in universe if str(x.get("sleeper_id"))==pid),None),
                latest_add=self._latest(tx,pid,"adds",team_by_roster),latest_drop=self._latest(tx,pid,"drops",team_by_roster),
                latest_trade=self._latest([x for x in tx if x.get("type")=="trade"],pid,None,team_by_roster),
                dead_cap=tuple(x for x in dead_cap if str(x.get("sleeper_player_id"))==pid),
                cap_adjustments=tuple(x for x in cap if str(x.get("sleeper_player_id"))==pid),
                processed_drop=next((x for x in ledger if str(x.get("dropped_player_id"))==pid and x.get("processed")),None),
                termination_event=next((x for x in events if str(x.get("contract_id"))==str(agreement["id"]) and x.get("event_type") in {"terminated","released"}),None),
                future_obligations=tuple(x for x in obligations if str(x.get("contract_id"))==str(agreement["id"]) and int(x.get("season") or 0)>source_season)))

        sources={"transition_source_fingerprints":transition.source_fingerprints,"sleeper_rosters":sleeper_rosters,
            "sleeper_transactions":sleeper_transactions,"internal_transaction_ledger":ledger,"canonical_rosters":canonical_rosters,
            "player_universe":universe,"dead_cap":dead_cap,"cap_adjustments":cap,"contract_events":events}
        report=build_missing_roster_reconciliation(evidence_rows,sources)
        corrections=[x for x in report["results"] if x["source_correction_required"]]
        report.update({"transition_counts_unchanged":transition.counts,"transition_source_fingerprint":transition.source_fingerprint,
            "source_correction_plan":{"apply_now":False,"legacy_contract_ids":[x["legacy_contract_id"] for x in corrections],
                "agreement_ids":[x["agreement_id"] for x in corrections],"expected_if_all_authoritatively_removed":{
                    "legacy_contracts":transition.counts["legacy_contracts"]-len(corrections),"agreements":transition.counts["agreements"]-len(corrections),
                    "continues":transition.counts["continues"],"expires":transition.counts["expires"]-len(corrections),
                    "planned_expiration_events":transition.counts["planned_expiration_events"]-len(corrections),
                    "free_agent_candidates":transition.counts["free_agent_candidates"]-len(corrections)}}})
        return report

    def _get(self,url):
        response=self.http.get(url,timeout=25); response.raise_for_status(); return response.json() or []

    @staticmethod
    def _latest(transactions,pid,side,team_by_roster):
        rows=[]
        for tx in transactions:
            if str(tx.get("status") or "").lower() != "complete":
                continue
            if side and pid not in (tx.get(side) or {}):
                continue
            roster_id=(tx.get(side) or {}).get(pid) if side else next(iter(tx.get("roster_ids") or []),None)
            team=team_by_roster.get(int(roster_id)) if roster_id is not None else None
            rows.append({"transaction_id":str(tx.get("transaction_id")),"type":tx.get("type"),"status":tx.get("status"),
                "created":tx.get("created"),"created_at":datetime.fromtimestamp(int(tx.get("created"))/1000,tz=timezone.utc).isoformat() if tx.get("created") else None,
                "week":tx.get("leg"),"roster_id":roster_id,"league_team_id":(team or {}).get("id"),
                "owner_name":(team or {}).get("owner_name")})
        return max(rows,key=lambda x:int(x.get("created") or 0),default=None)
