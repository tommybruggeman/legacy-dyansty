from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import logging
from types import MappingProxyType
from typing import Mapping, Sequence

from contract_engine.contract_read_service import ContractReadService, compare_normalized_and_legacy_reads
from season_engine.resolver import SeasonResolver
from services.trade_contract_models import TradeCalculationContext, TradeContractEvidence, TradePackageContractImpact


class TradeContractEvidenceError(RuntimeError):
    pass


class TradeContractEvidenceService:
    """Read-only trade interpretation over normalized contract authority."""

    def __init__(self, client, *, mode: str = "normalized", diagnostic_sink=None):
        if mode not in {"legacy", "compare", "normalized"}:
            raise ValueError("Trade contract read mode must be legacy, compare, or normalized.")
        self.client=client;self.mode=mode;self.diagnostic_sink=diagnostic_sink

    def calculation_context(self, league_id: str, *, cap_calculation_season: int | None = None) -> TradeCalculationContext:
        league_season=SeasonResolver(self.client).get_active_season(league_id).season
        contract_season=ContractReadService(self.client).get_contract_operational_season(league_id)
        return TradeCalculationContext(league_season,contract_season,cap_calculation_season,league_season)

    def load_trade_contract_evidence(self, league_id: str, roster_ownership: Mapping[str,str] | None = None) -> tuple[TradeContractEvidence,...]:
        roster_ownership={str(k):str(v) for k,v in (roster_ownership or {}).items()}
        service=ContractReadService(self.client)
        records,data=service.get_contracts(league_id)
        normalized=[self._evidence(record,roster_ownership) for record in records]
        if self.mode in {"compare","legacy"}:
            projected=service.project_legacy_contract_shape(league_id)
            legacy=data["legacy"]
            diagnostics=compare_normalized_and_legacy_reads(projected,legacy,service.get_contract_operational_season(league_id),league_id,"trade_contract_evidence",self.mode)
            diagnostics["difference_classification"]={"expired_legacy_rows":"expected_transition_difference","years":"material_calculation_difference","mixed_season_legality":"blocked_mixed_season_result"}
            if self.diagnostic_sink:self.diagnostic_sink(diagnostics)
            else:logging.getLogger(__name__).info("trade_contract_comparison %s",diagnostics)
            if self.mode=="legacy":return tuple(self._legacy(row,roster_ownership,service.get_contract_operational_season(league_id)) for row in legacy)
        return tuple(normalized)

    def get_trade_player_contract(self, league_id: str, player_id: str, roster_ownership: Mapping[str,str] | None = None) -> TradeContractEvidence | None:
        wanted=str(player_id);rows=[x for x in self.load_trade_contract_evidence(league_id,roster_ownership) if wanted in {x.player_id,x.sleeper_player_id}]
        if len(rows)>1:raise TradeContractEvidenceError(f"Trade asset {wanted} maps to multiple contract identities.")
        return rows[0] if rows else None

    def get_trade_package_contracts(self, league_id: str, player_ids: Sequence[str], roster_ownership: Mapping[str,str] | None = None) -> tuple[TradeContractEvidence,...]:
        ids=[str(x) for x in player_ids]
        if len(ids)!=len(set(ids)):raise TradeContractEvidenceError("A trade package contains a duplicate player asset.")
        all_rows=self.load_trade_contract_evidence(league_id,roster_ownership);by={x.player_id:x for x in all_rows};by.update({x.sleeper_player_id:x for x in all_rows})
        missing=[x for x in ids if x not in by]
        if missing:raise TradeContractEvidenceError(f"Contract evidence is missing for trade assets: {sorted(missing)}")
        return tuple(by[x] for x in ids)

    def calculate_trade_contract_impact(self, outgoing: Sequence[TradeContractEvidence], incoming: Sequence[TradeContractEvidence]) -> TradePackageContractImpact:
        all_ids=[x.player_id for x in (*outgoing,*incoming)]
        if len(all_ids)!=len(set(all_ids)):raise TradeContractEvidenceError("A player appears more than once across the trade package.")
        def salary(rows):return sum((x.operational_salary or Decimal("0") for x in rows if x.agreement_status=="active"),Decimal("0"))
        def future(rows):
            totals=defaultdict(lambda:Decimal("0"))
            for row in rows:
                for season in row.future_contract_schedule:totals[season.season]+=season.salary
            return MappingProxyType(dict(sorted(totals.items())))
        warnings=tuple(w for row in (*outgoing,*incoming) for w in row.warnings)
        return TradePackageContractImpact(salary(outgoing),salary(incoming),tuple(x.years_remaining for x in outgoing),tuple(x.years_remaining for x in incoming),future(outgoing),future(incoming),warnings)

    def _evidence(self,record,roster):
        roster_team=roster.get(record.player_id) or roster.get(record.sleeper_player_id);warnings=list(record.warnings)
        if roster_team and record.agreement_status=="expired":classification="rostered_contract_expired";warnings.append("Rostered player has an expired agreement; broader league handling is pending.")
        elif not roster_team and record.agreement_status=="active":classification="active_off_roster_liability";warnings.append("Active contract liability does not establish trade eligibility without roster authority.")
        elif roster_team:classification="rostered_active_contract"
        else:classification="unrostered_contract_unbound"
        future_2027=sum((x.salary for x in record.future_contract_seasons if x.season==2027),Decimal("0"))
        return TradeContractEvidence(record.agreement_id,record.league_id,record.player_id,record.sleeper_player_id,record.player_name,record.canonical_team_id,record.canonical_team_name,record.agreement_status,record.operational_season,record.operational_salary,record.remaining_contract_seasons,record.expiration_season,record.future_contract_seasons,future_2027,record.source_legacy_contract_id,record.contract_type,roster_team,classification,MappingProxyType({**record.provenance,"authority":"normalized_contract_model"}),tuple(warnings))

    def _legacy(self,row,roster,operational):
        pid=str(row.get("sleeper_player_id") or row.get("player_id"));team=roster.get(pid);years=int(row.get("contract_years_left") or row.get("years_remaining") or 0);salary=Decimal(str(row.get("salary"))) if row.get("salary") is not None else None
        return TradeContractEvidence(str(row.get("id")),str(row.get("league_id")),pid,pid,str(row.get("player_name") or ""),str(row.get("league_team_id") or row.get("owner_name") or ""),str(row.get("owner_name") or ""),"legacy_unresolved",operational,salary,years,operational+max(years-1,0),(),Decimal("0"),str(row.get("id")),row.get("contract_type"),team,"legacy_unresolved",MappingProxyType({"authority":"legacy_emergency_fallback"}),("Legacy mode is non-authoritative for lifecycle, salary season, and remaining term.",))
