from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from services.strict_pagination import complete_rows

@dataclass(frozen=True)
class RolloverControlState:
    execution:dict[str,Any]|None;owner_decisions:tuple[dict[str,Any],...];commissioner_reviews:tuple[dict[str,Any],...];plans:tuple[dict[str,Any],...];active_locks:tuple[dict[str,Any],...];validation_results:tuple[dict[str,Any],...];interpretation:str

class RolloverControlRepository:
    """Read-only Phase 3B.5B repository. Missing rows never imply authority."""
    def __init__(self,client):self.client=client
    def _list(self,table,field,value):return tuple(complete_rows(self.client,table,filters={field:value}))
    def get_execution(self,execution_id):
        rows=self._list("rollover_executions","id",execution_id)
        if len(rows)>1:raise RuntimeError("duplicate execution id")
        return rows[0] if rows else None
    def find_boundary(self,league_id,source_season,target_season):
        rows=complete_rows(self.client,"rollover_executions",filters={"league_id":league_id,"source_season":source_season,"target_season":target_season})
        active=[x for x in rows if x.get("status")!="cancelled"]
        if len(active)>1:raise RuntimeError("conflicting rollover executions")
        return active[0] if active else None
    def list_executions(self,league_id):return self._list("rollover_executions","league_id",league_id)
    def inspect(self,execution_id):
        execution=self.get_execution(execution_id)
        if not execution:return RolloverControlState(None,(),(),(),(),(),"no_execution_started")
        owner=self._list("rollover_owner_decisions","rollover_execution_id",execution_id);reviews=self._list("rollover_commissioner_reviews","rollover_execution_id",execution_id);plans=self._list("rollover_execution_plans","rollover_execution_id",execution_id);results=self._list("rollover_validation_results","rollover_execution_id",execution_id)
        locks=tuple(x for x in self._list("rollover_execution_locks","rollover_execution_id",execution_id) if x.get("status")=="active")
        return RolloverControlState(execution,owner,reviews,plans,locks,results,"execution_control_initialized")
    def readiness(self,league_id,source_season,target_season,*,policy_approved):
        execution=self.find_boundary(league_id,source_season,target_season)
        if not execution:return {"status":"execution_control_ready" if policy_approved else "execution_schema_required","blockers":("rollover execution not created",) if policy_approved else ("approved policy required",)}
        return {"status":execution.get("status") or "blocked","blockers":()}
