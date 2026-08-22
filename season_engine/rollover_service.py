from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal
import hashlib
import json
from typing import Any, Iterable

from contract_engine.contract_read_service import ContractReadService
from contract_engine.operational_season import resolve_contract_operational_season
from season_engine.resolver import SeasonResolver
from season_engine.rollover_models import (
    CapAuthorityKind, CapSeasonPolicy, CommissionerRolloverDecision,
    ContractRosterException, FreeAgentPublicationState, ProjectedTeamCap,
    RequirementNode, RolloverReadinessReport, SeasonRolloverContext,
)
from services.strict_pagination import complete_rows


def stable_fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


class RolloverAuthorityService:
    """Read-only Phase 3B.4D planner. Every database interaction is SELECT-only."""

    def __init__(self, client: Any):
        self.client = client

    def build_rollover_readiness_report(self, league_id: str) -> RolloverReadinessReport:
        active = SeasonResolver(self.client).get_active_season(league_id)
        target = active.season + 1
        operational = resolve_contract_operational_season(self.client, league_id)
        contracts = ContractReadService(self.client).get_contracts(league_id)[0]
        seasons = self._rows("league_seasons", league_id)
        teams = self._rows("league_teams", league_id)
        roster_rows = self._roster_rows(active.id)
        adjustments, adjustment_error = self._optional_rows("cap_adjustments", league_id)
        dead_cap, dead_cap_error = self._optional_rows("dead_cap_ledger", league_id)
        publication, publication_error = self._publication_rows(league_id)
        classifications, _ = self._optional_rows("contract_rollover_classifications", league_id)
        rules, rules_error = self._optional_rows("league_rules", league_id)
        context = self._context(league_id, active.season, target, operational, seasons)
        cap_policy = self._cap_policy(active.season, target, rules, adjustments, dead_cap, adjustment_error, dead_cap_error, rules_error)
        caps = self._project_caps(contracts, teams, adjustments, dead_cap, cap_policy)
        exceptions, decisions = self._exceptions(
            league_id, active.season, contracts, roster_rows, publication, publication_error,
            {str(x.get("contract_agreement_id")): str(x.get("classification") or "")
             for x in classifications},
        )
        graph = requirement_graph()
        blockers = list(context.blockers) + list(cap_policy.blockers)
        if publication_error:
            blockers.append("free_agent_publication_authority_absent")
        if any(item.classification == "ROSTERED_EXPIRED_POLICY_UNDEFINED" for item in exceptions):
            blockers.append("rostered_expired_policy_undefined")
        if any(item.classification == "ACTIVE_OFF_ROSTER_POLICY_REVIEW_REQUIRED" for item in exceptions):
            blockers.append("active_off_roster_liability_policy_undefined")
        if decisions:
            blockers.append("mandatory_commissioner_decisions_unresolved")
        blockers.extend("projected_2026_cap_incomplete:" + cap.league_team_id for cap in caps if not cap.complete)
        blockers = list(dict.fromkeys(blockers))
        pages = visible_page_inventory(active.season, operational)
        writes = write_path_inventory()
        if any(row["authority_conflict"] for row in writes):
            blockers.append("write_paths_use_conflicting_contract_authority")
        source = {
            "seasons": seasons, "contracts": [asdict(x) for x in contracts], "teams": teams,
            "roster": roster_rows, "adjustments": adjustments, "dead_cap": dead_cap,
            "publication": publication, "rules": rules,
        }
        source_fp = stable_fingerprint(source)
        plan_basis = {"context": asdict(context), "caps": [asdict(x) for x in caps], "exceptions": [asdict(x) for x in exceptions], "decisions": [asdict(x) for x in decisions], "graph": [asdict(x) for x in graph]}
        plan_fp = stable_fingerprint(plan_basis)
        context = replace(context, readiness_status="blocked" if blockers else "ready", blockers=tuple(blockers), source_fingerprint=source_fp, plan_fingerprint=plan_fp)
        return RolloverReadinessReport(context, cap_policy, tuple(caps), "absent" if publication_error else "free_agents", tuple(exceptions), tuple(decisions), graph, pages, writes, tuple(blockers), tuple(dict.fromkeys(context.warnings + cap_policy.warnings)), "Phase 3B.4D commissioner-policy/schema resolution" if blockers else "Phase 3B.4E visible consumer cutover", source_fp, plan_fp)

    def integrate_target_authorities(self, report, *, schema_available:bool, policy=None,
                                     publication_authority_status="absent",
                                     dead_cap_authority_status="uninitialized",
                                     cap_authority_status="uninitialized"):
        blockers=[]
        if not schema_available:blockers.append("target_authority_schema_required")
        if policy is None:blockers.append("commissioner_policy_draft_required")
        elif policy.status=="draft":blockers.append("commissioner_policy_approval_required")
        elif policy.status not in {"approved","active"}:blockers.append("approved_commissioner_policy_required")
        if publication_authority_status not in {"validated","authoritative"}:blockers.append("publication_authority_initialization_required")
        if dead_cap_authority_status not in {"initialized","validated"}:blockers.append("dead_cap_authority_initialization_required")
        if cap_authority_status!="validated":blockers.append("target_cap_validation_required")
        blockers.extend(x for x in report.blockers if x=="write_paths_use_conflicting_contract_authority")
        if not schema_available:status="schema_required"
        elif policy is None:status="policy_draft_required"
        elif policy.status=="draft":status="commissioner_approval_required"
        elif any("initialization" in x or "validation" in x for x in blockers):status="authority_initialization_required"
        elif blockers:status="blocked"
        else:status="execution_ready"
        return {"status":status,"blockers":tuple(dict.fromkeys(blockers)),"policy_fingerprint":getattr(policy,"fingerprint",None),"source_report_fingerprint":report.source_fingerprint,"writes_performed":0}

    def _context(self, league_id, source, target, operational, seasons):
        target_rows = [x for x in seasons if int(x.get("season") or 0) == target]
        blockers = []
        if len(target_rows) != 1:
            blockers.append("target_league_season_missing_or_duplicated")
        warnings = []
        if source != operational:
            warnings.append(f"mixed_authority:league={source},contracts={operational},cap={source}")
        basis = {"league_id": league_id, "source": source, "target": target, "operational": operational}
        return SeasonRolloverContext(league_id, source, target, "active", operational, source, source, None, target, "captured", "validated" if operational == target else "not_transitioned", "not_started", "not_started", "not_started", "not_started", "blocked", tuple(blockers), tuple(warnings), {"league":"league_seasons","contract":"validated contract_transition_executions","cap":"visible active-season calculations","roster":"season_roster_assignments"}, stable_fingerprint(basis), stable_fingerprint({"basis":basis,"phase":"3B.4D"}))

    def _cap_policy(self, current, target, rules, adjustments, dead_cap, adj_error, dead_error, rules_error):
        limit = _decimal((rules[0] if rules else {}).get("salary_cap"))
        blockers=[]; warnings=[]
        if limit is None: blockers.append("target_salary_cap_limit_unavailable")
        adj_scoped = bool(adjustments) and all(_season(x) is not None for x in adjustments)
        dead_scoped = bool(dead_cap) and all(_season(x) is not None for x in dead_cap)
        if adj_error or (adjustments and not adj_scoped): blockers.append("cap_adjustment_target_season_authority_ambiguous")
        if dead_error or (dead_cap and not dead_scoped): blockers.append("dead_cap_target_season_authority_ambiguous")
        if not adjustments: warnings.append("no_target_cap_adjustments_found")
        if not dead_cap: warnings.append("no_target_dead_cap_found")
        if rules_error: blockers.append("salary_cap_rule_authority_unavailable")
        return CapSeasonPolicy(current,target,CapAuthorityKind.AUTHORITATIVE_CURRENT,CapAuthorityKind.BLOCKED if blockers else CapAuthorityKind.PROJECTED_TARGET,limit,adj_scoped,dead_scoped,"commissioner-approved league/cap season transition",tuple(blockers),tuple(warnings))

    def _project_caps(self, contracts, teams, adjustments, dead_cap, policy):
        result=[]
        for team in sorted(teams,key=lambda x:str(x.get("id"))):
            tid=str(team.get("id")); names={tid,str(team.get("owner_name") or ""),str(team.get("team_name") or "")}
            salary=sum((x.operational_salary or Decimal("0")) for x in contracts if x.agreement_status=="active" and x.canonical_team_id==tid)
            adj_rows=[x for x in adjustments if _belongs(x,names) and _season(x)==policy.target_season]
            dead_rows=[x for x in dead_cap if _belongs(x,names) and _season(x)==policy.target_season]
            adj=sum((_amount(x) for x in adj_rows),Decimal("0")) if policy.adjustments_season_scoped else None
            dead=sum((_amount(x) for x in dead_rows),Decimal("0")) if policy.dead_cap_season_scoped else None
            complete=policy.salary_cap_limit is not None and adj is not None and dead is not None
            total=salary+adj+dead if complete else None
            available=policy.salary_cap_limit-total if complete else None
            blockers=() if complete else tuple(policy.blockers or ("target_cap_components_incomplete",))
            result.append(ProjectedTeamCap(tid,str(team.get("team_name") or team.get("owner_name") or tid),policy.target_season,salary,adj,dead,total,policy.salary_cap_limit,available,complete,("contract_seasons","cap_adjustments","dead_cap_ledger","league_rules"),blockers))
        return result

    def _exceptions(self, league_id, source_season, contracts, roster_rows, publication, publication_error,
                    classifications=None):
        classifications = classifications or {}
        roster={str(x.get("sleeper_player_id") or x.get("player_id")):x for x in roster_rows}
        published={str(x.get("sleeper_player_id") or x.get("player_id")):x for x in publication}
        exceptions=[]; decisions=[]
        for record in contracts:
            on_roster=record.sleeper_player_id in roster or record.player_id in roster
            if record.agreement_status=="expired" and on_roster:
                canonical = classifications.get(record.agreement_id)
                if canonical == "ordinary_expiration":
                    classification="ROSTERED_ORDINARY_EXPIRATION_READY"; action="CONTROLLED_RELEASE_AT_ROLLOVER"
                elif canonical == "rookie_option_eligible":
                    classification="ROSTERED_EXPIRED_POLICY_UNDEFINED"; action="HOLD_FOR_OWNER_ROOKIE_OPTION_DECISION"
                else:
                    classification="ROSTERED_EXPIRED_POLICY_UNDEFINED"; action="HOLD_FOR_COMMISSIONER_DECISION"
            elif record.agreement_status=="active" and not on_roster:
                classification="ACTIVE_OFF_ROSTER_POLICY_REVIEW_REQUIRED"; action="RETAIN_ORIGINAL_TEAM_LIABILITY_PENDING_REVIEW"
            elif record.agreement_status=="expired" and not on_roster:
                classification="EXPIRED_UNROSTERED_PUBLICATION_PENDING"; action="EVALUATE_PUBLICATION_WITHOUT_AUTO_PUBLISH"
            else: continue
            decision_id=stable_fingerprint({"league":league_id,"agreement":record.agreement_id,"classification":classification})[:24]
            pub="unknown" if publication_error else "published" if record.sleeper_player_id in published else "not_published"
            roster_row=roster.get(record.sleeper_player_id) or roster.get(record.player_id) or {}
            raw_designation = str(
                roster_row.get("roster_designation")
                or roster_row.get("roster_status")
                or roster_row.get("status")
                or ""
            ).lower()
            taxi_ir = raw_designation if raw_designation in {"taxi", "ir"} else None
            source_read = next((season for season in record.provenance.get("season_reads", ())
                                if int(season.season) == int(source_season)), None)
            source_contract_years = max(int(record.expiration_season) - int(source_season), 0)
            exceptions.append(ContractRosterException(record.agreement_id,record.sleeper_player_id,record.player_name,record.canonical_team_id,"rostered" if on_roster else "unrostered",record.agreement_status,classification,action,taxi_ir,pub,decision_id,{"salary":str(source_read.salary) if source_read is not None else None,"years_remaining":source_contract_years,"expiration_season":record.expiration_season,"future_obligations":[asdict(x) for x in record.future_contract_seasons],"drop_does_not_terminate":True,"natural_expiration_dead_cap":False}))
            if classification != "ROSTERED_ORDINARY_EXPIRATION_READY":
                decisions.append(CommissionerRolloverDecision(decision_id,league_id,record.sleeper_player_id,record.canonical_team_id,classification,f"contract={record.agreement_status}; roster={'present' if on_roster else 'absent'}",action,("hold","review","approve_later_execution_action"),None,exceptions[-1].evidence,None,"policy not yet evidenced",True,"broader rollover execution",("contract_agreements","contract_seasons","season_roster_assignments")))
        return exceptions, decisions

    def publication_state(self, player_id, *, contract_unbound, unrostered, row=None, authority_available=False):
        row=row or {}
        return FreeAgentPublicationState(str(player_id),bool(contract_unbound),bool(unrostered),bool(row.get("published")) if authority_available else None,bool(row.get("acquisition_eligible")) if authority_available else None,bool(row.get("waiver_locked")) if authority_available else None,bool(row.get("rookie_not_yet_available")) if authority_available else None,bool(row.get("commissioner_hold")) if authority_available else None,"trusted_publication_source" if authority_available else "unknown",() if authority_available else ("free_agent_publication_authority_absent",))

    def _rows(self, table, league_id): return complete_rows(self.client, table, filters={"league_id": league_id})
    def _optional_rows(self, table, league_id):
        try:return self._rows(table,league_id),None
        except Exception as exc:return [],str(exc)
    def _publication_rows(self, league_id): return self._optional_rows("free_agents",league_id)
    def _roster_rows(self, league_season_id):
        if not league_season_id:return []
        return complete_rows(self.client, "season_roster_assignments", filters={"league_season_id": str(league_season_id)})


def requirement_graph() -> tuple[RequirementNode,...]:
    specs=(
        ("historical_capture",()),("contract_transition",("historical_capture",)),("commissioner_decisions",("contract_transition",)),
        ("cap_authority",("commissioner_decisions",)),("free_agent_authority",("commissioner_decisions",)),
        ("rostered_expired_actions",("free_agent_authority",)),("off_roster_liability_actions",("cap_authority","free_agent_authority")),
        ("draft_authority",("historical_capture",)),("taxi_ir_policy",("commissioner_decisions",)),
        ("league_season_transition",("cap_authority","free_agent_authority","rostered_expired_actions","off_roster_liability_actions","draft_authority","taxi_ir_policy")),
        ("visible_read_cutover",("league_season_transition",)),("transaction_write_cutover",("league_season_transition",)),
        ("rollover_execution",("visible_read_cutover","transaction_write_cutover")),("post_state_validation",("rollover_execution",)),
        ("enable_2026_legality",("post_state_validation",)),
    )
    irreversible={"league_season_transition","rollover_execution"}
    return tuple(RequirementNode(node,tuple(prereqs),True,node in irreversible,node in irreversible,"authority transition" if node in irreversible else None,f"validate:{node}") for node,prereqs in specs)


def visible_page_inventory(league_season:int, contract_season:int):
    pages=("My Team","Teams","Free Agent","Trade Analyzer","Settings","Commit Contracts","cap displays","commissioner reports","GM Assistant UI")
    return tuple({"page":page,"current_season_meaning":league_season,"current_contract_source":"normalized evidence" if page in {"Trade Analyzer","GM Assistant UI"} else "legacy contracts","current_cap_source":"2025 visible cap","current_roster_source":"legacy/canonical current roster","current_free_agent_source":"derived, not publication authority","target_source":"normalized contracts + explicit domain authorities","required_authority_change":"league/cap/publication rollover","safe_before_rollover":False,"safe_during_rollover":False,"safe_only_after_rollover":True,"expected_visible_differences":"119 expired agreements stop appearing active","acceptance":"same-season authority and approved exception policy"} for page in pages)


def write_path_inventory():
    rows=(
        ("trade execution","legacy contracts/cap_adjustments",True),("contract commitment","legacy contracts",True),("extension/re-signing","legacy contracts",True),
        ("player release","legacy contracts/dead cap",True),("waiver acquisition","sign_free_agent RPC; authority unresolved",True),("free-agent signing","sign_free_agent RPC; authority unresolved",True),
        ("roster synchronization","roster state",False),("commissioner roster adjustment","roster state",False),("cap adjustment","cap_adjustments",False),
        ("dead-cap creation","dead_cap/cap_adjustments legacy path",True),("season rollover","contract transition only",True),("draft-pick movement","draft_picks",False),
    )
    return tuple({"workflow":name,"current_write":target,"normalized_contract_write":False,"authority_conflict":conflict,"action":"defer; no Phase 3B.4D mutation"} for name,target,conflict in rows)


def _decimal(value):
    try:return Decimal(str(value)) if value is not None else None
    except Exception:return None
def _amount(row): return _decimal(row.get("amount") or row.get("dead_cap_amount")) or Decimal("0")
def _season(row):
    try:return int(row.get("season"))
    except Exception:return None
def _belongs(row,names): return str(row.get("league_team_id") or row.get("team_id") or row.get("owner_name") or row.get("team_name") or "") in names
