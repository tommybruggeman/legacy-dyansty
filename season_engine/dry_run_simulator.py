from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5
import hashlib, json

from season_engine.authority_preparation import AuthoritySimulationInput, material_fingerprint

SIMULATOR_VERSION = "rollover-dry-run-v1"
VALIDATOR_VERSION = "rollover-dry-run-validator-v1"


def _ordered(rows: Sequence[Mapping[str, Any]], *keys: str) -> tuple[Mapping[str, Any], ...]:
    return tuple(sorted((MappingProxyType(dict(x)) for x in rows), key=lambda x: tuple(str(x.get(k) or "") for k in keys)))


@dataclass(frozen=True)
class SimulationChange:
    domain: str; entity_id: str; player_id: str | None; league_team_id: str | None
    classification: str; current_state: Mapping[str, Any]; simulated_state: Mapping[str, Any]
    dependencies: tuple[str, ...]; blockers: tuple[str, ...]; warnings: tuple[str, ...]
    evidence_fingerprint: str; result_fingerprint: str


@dataclass(frozen=True)
class TeamDryRunResult:
    league_team_id: str; source_cap: Decimal; target_cap: Decimal; retained_salary: Decimal
    recontract_salary: Decimal | None; rookie_salary: Decimal; planned_dead_cap: Decimal
    cap_adjustments: Decimal; cap_credits_in: Decimal; cap_credits_out: Decimal
    total_cap_charge: Decimal | None; projected_cap_space: Decimal | None
    unresolved_exposure: bool; hard_cap_legal: bool | None; hard_cap_blocker: str | None
    warnings: tuple[str, ...]; result_fingerprint: str


@dataclass(frozen=True)
class TableMutationSummary:
    table_name: str; inserts: int; updates: int; deletes: int; supersessions: int
    archives: int; unchanged: bool; expected_primary_keys: tuple[str, ...]
    dependency_order: int; blockers: tuple[str, ...]; result_fingerprint: str


@dataclass(frozen=True)
class RolloverDryRunValidationResult:
    valid: bool; executable: bool; plan_eligible: bool; checks: Mapping[str, bool]
    blockers: tuple[str, ...]; warnings: tuple[str, ...]; input_fingerprint: str
    result_fingerprint: str; validation_fingerprint: str; validator_version: str; validated_at: datetime


@dataclass(frozen=True)
class RolloverDryRunResult:
    id: str; execution_id: str; league_id: str; source_season: int; target_season: int
    simulation_version: int; simulator_version: str; input_fingerprint: str; result_fingerprint: str
    policy_fingerprint: str; preflight_fingerprint: str; owner_population_fingerprint: str
    commissioner_population_fingerprint: str; authority_preparation_fingerprint: str
    generated_at: datetime; generated_by: str | None; status: str; valid: bool; executable: bool
    blockers: tuple[str, ...]; warnings: tuple[str, ...]
    contract_changes: tuple[SimulationChange, ...]; roster_changes: tuple[SimulationChange, ...]
    publication_changes: tuple[SimulationChange, ...]; dead_cap_changes: tuple[SimulationChange, ...]
    cap_changes: tuple[SimulationChange, ...]; season_changes: tuple[SimulationChange, ...]
    taxi_changes: tuple[SimulationChange, ...]; ir_changes: tuple[SimulationChange, ...]
    draft_changes: tuple[SimulationChange, ...]; rookie_class_changes: tuple[SimulationChange, ...]
    transaction_effects: tuple[SimulationChange, ...]; team_results: tuple[TeamDryRunResult, ...]
    player_results: tuple[Mapping[str, Any], ...]; page_previews: Mapping[str, tuple[Mapping[str, Any], ...]]
    table_mutation_summary: tuple[TableMutationSummary, ...]; validation_summary: Mapping[str, Any]
    metadata: Mapping[str, Any]; provenance: tuple[str, ...]


@dataclass(frozen=True)
class DryRunExecutionPlanInput:
    rollover_execution_id: str; league_id: str; source_season: int; target_season: int
    simulation_id: str; simulation_version: int; simulator_version: str; validator_version: str
    simulation_input_fingerprint: str; simulation_result_fingerprint: str; preflight_fingerprint: str
    policy_fingerprint: str; owner_population_fingerprint: str; commissioner_population_fingerprint: str
    authority_preparation_fingerprint: str; expected_plan_version: int = 1
    planner_version: str = "rollover-execution-planner-v1"; requested_by: str | None = None
    idempotency_key: str = ""; metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class PersistedDryRunSimulation:
    id:str;rollover_execution_id:str;league_id:str;source_season:int;target_season:int
    simulation_version:int;simulator_version:str;simulation_status:str;input_fingerprint:str
    result_fingerprint:str;policy_fingerprint:str;preflight_fingerprint:str
    owner_population_fingerprint:str;commissioner_population_fingerprint:str
    authority_preparation_fingerprint:str;result_payload:Mapping[str,Any]
    blockers:tuple[Any,...];warnings:tuple[Any,...];valid:bool;executable:bool;plan_eligible:bool
    cancelled_at:str|None=None

    @classmethod
    def from_row(cls,row:Mapping[str,Any]):
        required=("id","rollover_execution_id","league_id","source_season","target_season","simulation_version",
          "simulator_version","simulation_status","input_fingerprint","result_fingerprint","policy_fingerprint",
          "preflight_fingerprint","owner_population_fingerprint","commissioner_population_fingerprint",
          "authority_preparation_fingerprint","result_payload","valid","executable","plan_eligible")
        missing=[x for x in required if row.get(x) is None]
        if missing:raise ValueError("malformed persisted dry run: "+",".join(missing))
        for key in ("input_fingerprint","result_fingerprint","policy_fingerprint","preflight_fingerprint",
                    "owner_population_fingerprint","commissioner_population_fingerprint","authority_preparation_fingerprint"):
            value=str(row[key]);
            if len(value)!=64 or any(c not in "0123456789abcdef" for c in value):raise ValueError(f"malformed {key}")
        if row["simulation_status"]=="cancelled" and not row.get("cancelled_at"):raise ValueError("cancelled simulation missing cancelled_at")
        return cls(str(row["id"]),str(row["rollover_execution_id"]),str(row["league_id"]),int(row["source_season"]),int(row["target_season"]),int(row["simulation_version"]),str(row["simulator_version"]),str(row["simulation_status"]),str(row["input_fingerprint"]),str(row["result_fingerprint"]),str(row["policy_fingerprint"]),str(row["preflight_fingerprint"]),str(row["owner_population_fingerprint"]),str(row["commissioner_population_fingerprint"]),str(row["authority_preparation_fingerprint"]),MappingProxyType(dict(row["result_payload"])),tuple(row.get("blockers") or ()),tuple(row.get("warnings") or ()),bool(row["valid"]),bool(row["executable"]),bool(row["plan_eligible"]),None if row.get("cancelled_at") is None else str(row["cancelled_at"]))


class TrustedDryRunGenerationService:
    """Server-side boundary: user-scoped authorization/evidence, canonical simulation, service-only persistence."""
    def __init__(self,user_client,service_client):self.user_client=user_client;self.service_client=service_client
    def build_authority_simulation_input(self,execution_id:str,decoder):
        executions=self.user_client.table("rollover_executions").select("*").eq("id",execution_id).execute().data or []
        if len(executions)!=1:raise ValueError("exactly one execution required")
        execution=executions[0]
        owners=self.user_client.table("rollover_owner_decisions").select("*").eq("rollover_execution_id",execution_id).execute().data or []
        reviews=self.user_client.table("rollover_commissioner_reviews").select("*").eq("rollover_execution_id",execution_id).execute().data or []
        preparations=self.user_client.table("rollover_authority_preparations").select("*").eq("rollover_execution_id",execution_id).execute().data or []
        current=sorted((x for x in preparations if x.get("authority_status")=="prepared"),key=lambda x:str(x.get("authority_type")))
        if [x.get("authority_type") for x in current] != ["dead_cap","publication","salary_cap"]:raise ValueError("exactly three prepared authority domains required")
        if any(x.get("decision_status") not in {"planned_retention","planned_release","commissioner_review_requested","no_response","execution_ready"} for x in owners):raise ValueError("unresolved owner outcomes")
        if any(x.get("review_state") not in {"approved","rejected"} for x in reviews):raise ValueError("unresolved commissioner outcomes")
        evidence={"execution":dict(execution),"owner_outcomes":sorted((dict(x) for x in owners),key=lambda x:(str(x.get("agreement_id")),str(x.get("player_id")))),"commissioner_outcomes":sorted((dict(x) for x in reviews),key=lambda x:(str(x.get("review_type")),str(x.get("agreement_id")),str(x.get("player_id")))),"preparations":current}
        result=decoder(evidence)
        if not isinstance(result,AuthoritySimulationInput):raise TypeError("decoder must return AuthoritySimulationInput")
        return result,evidence
    def generate_from_execution(self,execution_id:str,request:Mapping[str,Any],decoder):
        simulation_input,evidence=self.build_authority_simulation_input(execution_id,decoder)
        expected={x["authority_type"]:{"id":x["id"],"version":x["version"],"authority_fingerprint":x["authority_fingerprint"],"evidence_fingerprint":x["evidence_fingerprint"],"preparation_fingerprint":x["preparation_fingerprint"]} for x in evidence["preparations"]}
        return self.generate(
            simulation_input,
            {
                **dict(request),
                "execution_id": execution_id,
                "league_id": simulation_input.league_id,
                "source_season": simulation_input.source_season,
                "target_season": simulation_input.target_season,
                "expected_policy_fingerprint":
                    simulation_input.policy_fingerprint,
                "expected_preflight_fingerprint":
                    simulation_input.preflight_fingerprint,
                "expected_owner_population_fingerprint":
                    simulation_input.owner_population_fingerprint,
                "expected_commissioner_population_fingerprint":
                    simulation_input.commissioner_population_fingerprint,
                "expected_authority_preparation_fingerprint":
                    simulation_input.authority_preparation_fingerprint,
                "expected_authorities": expected,
            },
        )
    @staticmethod
    def _clean(v):
        def clean(v):
            if hasattr(v,"__dataclass_fields__"):return {k:clean(getattr(v,k)) for k in v.__dataclass_fields__ if k not in {"generated_at","generated_by","id"}}
            if isinstance(v,Mapping):return {str(k):clean(x) for k,x in sorted(v.items())}
            if isinstance(v,(tuple,list)):return [clean(x) for x in v]
            if isinstance(v,Decimal):return str(v)
            if isinstance(v,datetime):return v.astimezone(timezone.utc).isoformat()
            return v
        return clean(v)
    @classmethod
    def canonical_result_payload(cls,result:RolloverDryRunResult,validation:RolloverDryRunValidationResult):
        return {"simulation":cls._clean(result),"validation":cls._clean(validation)}
    def generate(self,simulation_input:AuthoritySimulationInput,request:Mapping[str,Any]):
        forbidden={"result_payload","valid","executable","plan_eligible","simulation_status","blockers","warnings"}
        supplied=forbidden.intersection(request)
        if supplied:raise ValueError("caller-authoritative result fields forbidden: "+",".join(sorted(supplied)))
        # User client performs the league-scoped commissioner assertion; service-role persistence is separate.
        auth=self.user_client.rpc("assert_rollover_dry_run_commissioner_authenticated",{"p_execution_id":simulation_input.execution_id}).execute().data
        if not isinstance(auth,Mapping) or not auth.get("authorized"):raise PermissionError("commissioner authority required")
        result=RolloverDryRunSimulator().simulate(simulation_input,generated_by=str(auth.get("actor_user_id")))
        validation=RolloverDryRunValidator().validate(result);payload=self.canonical_result_payload(result,validation)
        if request.get("expected_input_fingerprint") and request["expected_input_fingerprint"]!=result.input_fingerprint:raise ValueError("expected input fingerprint mismatch")
        if request.get("expected_result_fingerprint") and request["expected_result_fingerprint"]!=result.result_fingerprint:raise ValueError("expected result fingerprint mismatch")
        canonical_input=self._clean(simulation_input)

        # PostgreSQL is the canonical fingerprint authority. Its implementation
        # hashes canonical_input::jsonb::text, whose serialization is not
        # guaranteed to match Python json.dumps().
        fingerprint_response = self.service_client.rpc(
            "rollover_material_fingerprint",
            {"p_material": canonical_input},
        ).execute().data

        if isinstance(fingerprint_response, str):
            transport_fp = fingerprint_response
        elif (
            isinstance(fingerprint_response, Mapping)
            and isinstance(
                fingerprint_response.get(
                    "rollover_material_fingerprint"
                ),
                str,
            )
        ):
            transport_fp = fingerprint_response[
                "rollover_material_fingerprint"
            ]
        else:
            raise ValueError(
                "malformed canonical-input fingerprint response"
            )

        if len(transport_fp) != 64:
            raise ValueError(
                "canonical-input fingerprint is not SHA-256"
            )

        body={**dict(request),"trusted_actor_user_id":auth.get("actor_user_id"),"canonical_input":canonical_input,"canonical_input_transport_fingerprint":transport_fp,"canonical_result":payload,
              "input_fingerprint":result.input_fingerprint,"result_fingerprint":result.result_fingerprint,
              "valid":validation.valid,"executable":validation.executable,"plan_eligible":validation.plan_eligible,
              "blockers":list(validation.blockers),"warnings":list(validation.warnings)}
        data=self.service_client.rpc("persist_rollover_dry_run_service",{"p_request":body}).execute().data
        if not isinstance(data,Mapping) or not isinstance(data.get("simulation"),Mapping):raise ValueError("malformed persistence result")
        return PersistedDryRunSimulation.from_row(data["simulation"])


class TrustedDryRunCancellationService:
    """Authenticated cancellation client using the complete simulation identity."""

    REQUIRED = (
        "execution_id", "simulation_id", "expected_simulation_version",
        "expected_simulation_status", "expected_input_fingerprint",
        "expected_result_fingerprint", "expected_preflight_fingerprint",
        "idempotency_key", "reason",
    )

    def __init__(self, user_client):
        self.user_client = user_client

    @staticmethod
    def _fingerprint(value: Any, field: str) -> str:
        value = str(value or "")
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"malformed {field}")
        return value

    @classmethod
    def build_request(cls, simulation: PersistedDryRunSimulation, *, idempotency_key: str,
                      reason: str, material_metadata: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        request = {
            "execution_id": simulation.rollover_execution_id,
            "simulation_id": simulation.id,
            "expected_simulation_version": simulation.simulation_version,
            "expected_simulation_status": simulation.simulation_status,
            "expected_input_fingerprint": simulation.input_fingerprint,
            "expected_result_fingerprint": simulation.result_fingerprint,
            "expected_preflight_fingerprint": simulation.preflight_fingerprint,
            "idempotency_key": str(idempotency_key or "").strip(),
            "reason": str(reason or "").strip(),
            "material_metadata": dict(material_metadata or {}),
        }
        cls.validate_request(request)
        return MappingProxyType(request)

    @classmethod
    def validate_request(cls, request: Mapping[str, Any]) -> None:
        missing = [key for key in cls.REQUIRED if request.get(key) in (None, "")]
        if missing:
            raise ValueError("missing cancellation fields: " + ",".join(missing))
        if "actor_user_id" in request or "requested_by" in request:
            raise ValueError("actor spoofing forbidden")
        if int(request["expected_simulation_version"]) < 1:
            raise ValueError("invalid expected_simulation_version")
        if request["expected_simulation_status"] not in {"generated", "blocked", "valid", "stale"}:
            raise ValueError("simulation is not cancellable")
        for key in ("expected_input_fingerprint", "expected_result_fingerprint", "expected_preflight_fingerprint"):
            cls._fingerprint(request[key], key)

    def cancel(self, request: Mapping[str, Any]) -> PersistedDryRunSimulation:
        self.validate_request(request)
        data = self.user_client.rpc(
            "cancel_rollover_dry_run_authenticated", {"p_request": dict(request)}
        ).execute().data
        if not isinstance(data, Mapping) or not isinstance(data.get("simulation"), Mapping):
            raise ValueError("malformed cancellation result")
        row = PersistedDryRunSimulation.from_row(data["simulation"])
        if row.simulation_status != "cancelled":
            raise ValueError("cancellation did not return cancelled simulation")
        for field, expected in (
            ("id", request["simulation_id"]),
            ("rollover_execution_id", request["execution_id"]),
            ("simulation_version", request["expected_simulation_version"]),
            ("input_fingerprint", request["expected_input_fingerprint"]),
            ("result_fingerprint", request["expected_result_fingerprint"]),
            ("preflight_fingerprint", request["expected_preflight_fingerprint"]),
        ):
            if getattr(row, field) != expected:
                raise ValueError(f"cancellation response changed {field}")
        return row


class RolloverDryRunSimulator:
    def simulate(self, simulation_input: AuthoritySimulationInput, *, generated_at: datetime | None = None,
                 generated_by: str | None = None) -> RolloverDryRunResult:
        blockers = list(simulation_input.blockers); warnings = list(simulation_input.warnings)
        if not simulation_input.execution_id: blockers.append("missing_execution")
        if simulation_input.target_season != simulation_input.source_season + 1: blockers.append("season_boundary_drift")
        owner = tuple(getattr(simulation_input, "finalized_owner_outcomes", ()) or ())
        reviews = tuple(getattr(simulation_input, "finalized_commissioner_outcomes", ()) or ())
        if not owner: blockers.append("finalized_owner_outcomes_missing")
        if not reviews: blockers.append("finalized_commissioner_outcomes_missing")
        contract = self._contracts(owner, reviews, simulation_input.target_season)
        roster, taxi, ir = self._rosters(owner, contract)
        publication = self._publication(simulation_input)
        dead_cap = self._dead_cap(simulation_input)
        teams = self._teams(simulation_input)
        for team in teams:
            if team.unresolved_exposure: blockers.append(f"unresolved_salary:{team.league_team_id}")
            if team.hard_cap_legal is False: blockers.append(f"hard_cap_violation:{team.league_team_id}")
        blockers.extend(b for rows in (contract, roster, publication, dead_cap) for x in rows for b in x.blockers)
        season = self._season(simulation_input)
        draft = (self._change("draft","draft-pick-ownership",None,None,"preserve_ownership",
                  {"season":simulation_input.source_season},{"season":simulation_input.target_season},("draft_authority",),(),()),)
        rookie = (self._change("rookie_class","rookie-class",None,None,"advance_current_class",
                   {"year":simulation_input.source_season},{"year":simulation_input.target_season},("season_authority",),(),()),)
        pages = self._pages(publication, contract, rookie)
        input_fp = material_fingerprint(simulation_input)
        mutation = self._mutations(contract, roster, publication, dead_cap, season, taxi, ir)
        unique_blockers = tuple(sorted(set(blockers))); unique_warnings = tuple(sorted(set(warnings)))
        material = {"input":input_fp,"contract":contract,"roster":roster,"publication":publication,
                    "dead_cap":dead_cap,"teams":teams,"season":season,"taxi":taxi,"ir":ir,"draft":draft,
                    "rookie":rookie,"pages":pages,"mutations":mutation,"blockers":unique_blockers,"warnings":unique_warnings}
        result_fp = material_fingerprint(material); valid = not any(b.startswith("malformed") for b in unique_blockers)
        executable = valid and not unique_blockers
        return RolloverDryRunResult(str(uuid5(NAMESPACE_URL,f"legacy:{simulation_input.execution_id}:{result_fp}")),
          simulation_input.execution_id,simulation_input.league_id,simulation_input.source_season,simulation_input.target_season,
          1,SIMULATOR_VERSION,input_fp,result_fp,simulation_input.policy_fingerprint,
          str(getattr(simulation_input,"preflight_fingerprint","")),simulation_input.owner_population_fingerprint,
          simulation_input.commissioner_population_fingerprint,simulation_input.authority_preparation_fingerprint,
          generated_at or datetime.now(timezone.utc),generated_by,"valid" if executable else "blocked",valid,executable,
          unique_blockers,unique_warnings,contract,roster,publication,dead_cap,(),season,taxi,ir,draft,rookie,(),teams,
          _ordered(({"player_id":x.player_id,"domain":x.domain,"classification":x.classification} for rows in (contract,roster,publication,dead_cap) for x in rows),"player_id","domain"),
          MappingProxyType(pages),mutation,MappingProxyType({"valid":valid,"executable":executable}),
          MappingProxyType({"writes_performed":0}),
          ("AuthoritySimulationInput","normalized_contracts","roster_evidence","league_rules","season_authority"))

    def _contracts(self, owner, reviews, target):
        by_review={str(x.get("agreement_id")):x for x in reviews}; result=[]
        for row in _ordered(owner,"agreement_id","player_id"):
            review=by_review.get(str(row.get("agreement_id")),{}); outcome=str(row.get("planned_outcome") or review.get("outcome") or "")
            blockers=[]; classification={"retain":"carry_forward","planned_retention":"recontract","release_at_rollover_to_commissioner_hold":"release","planned_release":"release"}.get(outcome,"manual_review_required")
            salary=row.get("target_salary");term=row.get("target_years")
            if classification=="recontract" and salary is None:blockers.append("recontract_salary_required")
            if classification=="recontract" and term is None:blockers.append("recontract_term_required")
            if review.get("outcome") in ("preserve_active_liability","retain_contract"):classification="preserve_active_liability"
            current={"season":row.get("source_contract_season"),"salary":row.get("source_salary"),"years":row.get("source_years_remaining"),"status":row.get("source_agreement_status")}
            simulated={"season":target,"salary":salary if classification=="recontract" else row.get("source_salary"),"years":term if classification=="recontract" else max(0,int(row.get("source_years_remaining") or 1)-1),"status":"active" if classification in ("carry_forward","recontract","preserve_active_liability") else "expired"}
            result.append(self._change("contract",str(row.get("agreement_id") or ""),row.get("player_id"),row.get("league_team_id"),classification,current,simulated,("owner_outcome","commissioner_outcome"),tuple(blockers),()))
        return tuple(result)

    def _rosters(self, owner, contracts):
        by_agreement={x.entity_id:x for x in contracts}; roster=[];taxi=[];ir=[]
        for row in _ordered(owner,"league_team_id","player_id"):
            status=str(row.get("roster_status") or "active");contract=by_agreement.get(str(row.get("agreement_id"))); remove=contract and contract.classification=="release" and bool(row.get("remove_from_roster"))
            classification="remove_from_roster" if remove else "preserve_roster_assignment"
            roster.append(self._change("roster",str(row.get("player_id")),row.get("player_id"),row.get("league_team_id"),classification,{"status":status},{"status":"unrostered" if remove else status},("contract_resolution",),(),()))
            if status.lower()=="taxi":taxi.append(self._change("taxi",str(row.get("player_id")),row.get("player_id"),row.get("league_team_id"),"release_taxi_lock",{"taxi":True},{"taxi":False,"team_preserved":not remove},("rollover",),(),()))
            if status.lower()=="ir":ir.append(self._change("ir",str(row.get("player_id")),row.get("player_id"),row.get("league_team_id"),"clear_ir_designation",{"ir":True},{"ir":False,"contract_unchanged":True},("rollover",),(),()))
        return tuple(roster),tuple(taxi),tuple(ir)

    def _publication(self, source):
        seen=set();result=[]
        for item in sorted(source.publication_instructions,key=lambda x:(x.player_id,x.agreement_id or "")):
            blockers=list(item.publication_blockers);classification="publish_at_execution" if item.publication_action=="plan_publication_at_execution" and not blockers else "commissioner_hold"
            if item.player_id in seen:blockers.append("duplicate_publication")
            seen.add(item.player_id)
            result.append(self._change("publication",item.player_id,item.player_id,item.league_team_id,classification,
             {"published":False},{"published":classification=="publish_at_execution","season":source.target_season},("contract_resolution","roster_resolution"),tuple(blockers),item.publication_warnings))
        return tuple(result)

    def _dead_cap(self, source):
        seen=set();result=[]
        for item in sorted(source.dead_cap_instructions,key=lambda x:(x.player_id,x.agreement_id or "")):
            blockers=list(item.blockers);key=(item.league_team_id,item.player_id,item.agreement_id,item.target_season)
            if key in seen and item.calculated_amount:blockers.append("duplicate_dead_cap_obligation")
            seen.add(key);amount=Decimal("0") if item.salary_basis==Decimal("1") else item.calculated_amount
            if amount and not item.qualifying_event_id:blockers.append("qualifying_event_required")
            result.append(self._change("dead_cap",item.player_id,item.player_id,item.league_team_id,
              "planned_dead_cap" if amount and not blockers else "no_dead_cap",{"amount":"0"},{"amount":str(amount),"season":item.target_season},("qualifying_event","penalty_rule"),tuple(blockers),item.warnings))
        return tuple(result)

    def _teams(self, source):
        result=[]
        for p in sorted(source.team_cap_projections,key=lambda x:x.league_team_id):
            unresolved=p.recontract_salary_total is None;charge=p.projected_cap_charge;space=p.projected_cap_space
            blocker="unresolved_material_salary" if unresolved else "hard_cap_violation" if space is not None and space<0 else None
            basis={"team":p.league_team_id,"charge":charge,"space":space,"unresolved":unresolved,"target":source.cap_authority_plan.target_cap}
            result.append(TeamDryRunResult(p.league_team_id,source.cap_authority_plan.source_cap,source.cap_authority_plan.target_cap,p.retained_salary_total,p.recontract_salary_total,Decimal("0"),p.planned_dead_cap,p.cap_adjustments,p.cap_credits_in,p.cap_credits_out,charge,space,unresolved,None if unresolved else space>=0,blocker,(),material_fingerprint(basis)))
        return tuple(result)

    def _season(self, source):
        return (self._change("season","league-season",None,None,"activate_target_season",{"active":source.source_season},{"completed":source.source_season,"active":source.target_season,"next":source.target_season+1},("atomic_rollover",),(),()),)

    def _pages(self, publication, contracts, rookie):
        market=({"player_id":x.player_id,"status":"published"} for x in publication if x.classification=="publish_at_execution" and not x.blockers)
        expiring=({"player_id":x.player_id,"agreement_id":x.entity_id} for x in contracts if x.classification in ("expire","release"))
        return {"open_market":_ordered(tuple(market),"player_id"),"expiring_contracts":_ordered(tuple(expiring),"player_id"),"rookie_class":({"class_year":rookie[0].simulated_state["year"],"ordering":"draft_position"},)}

    def _mutations(self,contract,roster,publication,dead,season,taxi,ir):
        specs=(("contract_agreements",0,len(contract)),("contract_events",len(contract),0),("season_roster_assignments",0,len(roster)),("free_agent_publications",sum(x.classification=="publish_at_execution" for x in publication),0),("dead_cap_obligations",sum(x.classification=="planned_dead_cap" for x in dead),0),("league_seasons",0,len(season)),("taxi_state",0,len(taxi)),("ir_state",0,len(ir)),("draft_picks",0,0),("cap_adjustments",0,0),("rollover_execution_plans",0,0))
        return tuple(TableMutationSummary(name,ins,upd,0,0,0,not(ins or upd),(),i,(),material_fingerprint({"table":name,"inserts":ins,"updates":upd,"order":i})) for i,(name,ins,upd) in enumerate(specs,1))

    @staticmethod
    def _change(domain,eid,player,team,kind,current,simulated,deps,blockers,warnings):
        evidence=material_fingerprint({"domain":domain,"entity":eid,"current":current,"dependencies":deps})
        result=material_fingerprint({"evidence":evidence,"classification":kind,"simulated":simulated,"blockers":blockers})
        return SimulationChange(domain,eid,None if player is None else str(player),None if team is None else str(team),kind,MappingProxyType(dict(current)),MappingProxyType(dict(simulated)),tuple(deps),tuple(blockers),tuple(warnings),evidence,result)


class RolloverDryRunValidator:
    def validate(self,result:RolloverDryRunResult,*,validated_at:datetime|None=None):
        checks=MappingProxyType({"sequential_seasons":result.target_season==result.source_season+1,"no_material_blockers":not result.blockers,"ten_unique_teams":len(result.team_results)==10 and len({x.league_team_id for x in result.team_results})==10,"no_domain_writes":result.metadata.get("writes_performed")==0})
        blockers=tuple(sorted(set((*result.blockers,*(k for k,v in checks.items() if not v)))))
        executable=result.valid and not blockers;fp=material_fingerprint({"checks":checks,"result":result.result_fingerprint,"blockers":blockers,"validator":VALIDATOR_VERSION})
        return RolloverDryRunValidationResult(result.valid,executable,executable,checks,blockers,result.warnings,result.input_fingerprint,result.result_fingerprint,fp,VALIDATOR_VERSION,validated_at or datetime.now(timezone.utc))


def dry_run_readiness(execution,preparations=(),simulation=None,*,drift=False,operation_active=False):
    if not execution:return {"status":"execution_control_ready","blockers":("rollover execution not created",)}
    if operation_active:return {"status":"dry_run_in_progress","blockers":()}
    if drift:return {"status":"dry_run_stale","blockers":("simulation evidence drift",)}
    if len({x.get("authority_type") for x in preparations if x.get("authority_status")=="prepared"})<3:return {"status":"dry_run_required","blockers":("authority preparations incomplete",)}
    if not simulation:return {"status":"dry_run_required","blockers":()}
    if not simulation.get("executable"):return {"status":"dry_run_blocked","blockers":tuple(simulation.get("blockers") or ())}
    return {"status":"execution_plan_required","dry_run_status":"dry_run_complete","blockers":()}


def to_execution_plan_input(result,validation):
    return DryRunExecutionPlanInput(result.execution_id,result.league_id,result.source_season,result.target_season,
      result.id,result.simulation_version,result.simulator_version,validation.validator_version,
      result.input_fingerprint,result.result_fingerprint,result.preflight_fingerprint,result.policy_fingerprint,
      result.owner_population_fingerprint,result.commissioner_population_fingerprint,
      result.authority_preparation_fingerprint)
