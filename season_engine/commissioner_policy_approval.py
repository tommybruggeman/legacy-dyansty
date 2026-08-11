from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime,timezone
from typing import Any

from season_engine.commissioner_policy_draft import PreparedPolicyDraft
from season_engine.rollover_service import stable_fingerprint


@dataclass(frozen=True)
class PolicyApprovalResult:
    row:dict[str,Any];inserted:bool;idempotent:bool;readiness_status:str


class PolicyApprovalError(RuntimeError):pass


class CommissionerPolicyApprovalService:
    def __init__(self,client):self.client=client

    @staticmethod
    def verify(draft:PreparedPolicyDraft,expected_fingerprint:str)->None:
        payload={k:v for k,v in draft.payload.items() if k!="fingerprint"}
        recalculated=stable_fingerprint(payload)
        if recalculated != draft.fingerprint or recalculated != expected_fingerprint:
            raise PolicyApprovalError(f"Policy fingerprint mismatch: recalculated={recalculated} expected={expected_fingerprint}")
        if not draft.complete or draft.missing_inputs or draft.validation_errors:
            raise PolicyApprovalError(f"Policy is incomplete: missing={list(draft.missing_inputs)} errors={list(draft.validation_errors)}")

    def resolve_commissioner(self,league_id:str)->str:
        rows=self.client.table("league_memberships").select("user_id,role").eq("league_id",league_id).execute().data or []
        ids=sorted({str(x.get("user_id")) for x in rows if str(x.get("role") or "").lower() in {"commissioner","admin"} and x.get("user_id")})
        if len(ids)!=1:raise PolicyApprovalError(f"Expected exactly one authenticated commissioner membership; found {len(ids)}")
        return ids[0]

    def _existing(self,payload:dict[str,Any])->list[dict[str,Any]]:
        return self.client.table("league_rollover_policies").select("*").eq("league_id",payload["league_id"]).eq("source_season",payload["source_season"]).eq("target_season",payload["target_season"]).eq("version",payload["version"]).execute().data or []

    @staticmethod
    def _matches(row:dict[str,Any],draft:PreparedPolicyDraft)->bool:
        return row.get("status")=="approved" and row.get("fingerprint")==draft.fingerprint and (row.get("metadata") or {}).get("policy_payload")==draft.payload

    def approve(self,draft:PreparedPolicyDraft,expected_fingerprint:str,*,approved_at:str|None=None)->PolicyApprovalResult:
        self.verify(draft,expected_fingerprint);existing=self._existing(draft.payload)
        if existing:
            if len(existing)==1 and self._matches(existing[0],draft):return PolicyApprovalResult(existing[0],False,True,"authority_initialization_required")
            raise PolicyApprovalError("Conflicting rollover policy exists; no row was written")
        active=self.client.table("league_rollover_policies").select("id,fingerprint,status").eq("league_id",draft.payload["league_id"]).eq("target_season",draft.payload["target_season"]).eq("status","active").execute().data or []
        if active:raise PolicyApprovalError("A conflicting active rollover policy exists; no row was written")
        commissioner=self.resolve_commissioner(draft.payload["league_id"]);timestamp=approved_at or datetime.now(timezone.utc).isoformat()
        p=draft.payload
        row={"league_id":p["league_id"],"source_season":p["source_season"],"target_season":p["target_season"],"version":p["version"],"status":"approved",
            "rostered_expired_policy":p["rostered_expired_policy"],"off_roster_active_policy":p["off_roster_active_policy"],"free_agent_publication_policy":p["free_agent_publication_policy"],
            "waiver_policy":p["waiver_policy"],"extension_deadline":None,"taxi_policy":p["taxi_policy"],"ir_policy":p["ir_policy"],"dead_cap_policy":p["dead_cap_policy"],
            "early_termination_policy":p["early_termination_policy"],"cap_adjustment_policy":p["cap_adjustment_policy"],"draft_rookie_policy":p["draft_rookie_policy"],
            "effective_at":None,"created_by":commissioner,"approved_by":commissioner,"approved_at":timestamp,"metadata":{"policy_payload":p,"deadline_rule":p["extension_deadline"]},"fingerprint":draft.fingerprint}
        try:inserted=self.client.table("league_rollover_policies").insert(row).execute().data or []
        except Exception as exc:
            reread=self._existing(p)
            if len(reread)==1 and self._matches(reread[0],draft):return PolicyApprovalResult(reread[0],False,True,"authority_initialization_required")
            raise PolicyApprovalError(f"Policy insert failed without an idempotent match: {exc}") from exc
        if len(inserted)!=1 or not self._matches(inserted[0],draft):raise PolicyApprovalError("Persisted policy does not match approved payload")
        return PolicyApprovalResult(inserted[0],True,False,"authority_initialization_required")
