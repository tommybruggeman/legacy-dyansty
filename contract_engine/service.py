from __future__ import annotations

from season_engine import SeasonResolver
from .planner import build_backfill_plan


class ContractBackfillService:
    def __init__(self, client): self.client=client
    def plan(self, league_id):
        active=SeasonResolver(self.client).get_active_season(league_id)
        contracts=self.client.table("contracts").select("*").eq("league_id",league_id).execute().data or []
        teams=self.client.table("league_teams").select("*").eq("league_id",league_id).execute().data or []
        players=self.client.table("player_universe").select("sleeper_id,canonical_player_id,player_name").execute().data or []
        seasons=self.client.table("league_seasons").select("*").eq("league_id",league_id).execute().data or []
        return build_backfill_plan(active_season=active,legacy_contracts=contracts,league_teams=teams,players=players,league_seasons=seasons)
    def backfill(self,league_id,*,dry_run=True):
        plan=self.plan(league_id)
        if dry_run:return plan
        if not plan.safe_to_apply: raise ValueError(f"Contract backfill blocked: {plan.blocking_errors}")
        current=self.plan(league_id)
        if current.source_fingerprint!=plan.source_fingerprint: raise ValueError("Legacy contract fingerprint changed during planning.")
        return self.client.rpc("backfill_contract_model",{"p_plan":plan.payload()}).execute().data
