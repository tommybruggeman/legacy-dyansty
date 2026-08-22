from __future__ import annotations


class ContractRepository:
    def __init__(self, client): self.client = client
    def get_player_contract_history(self, league_id, player_id): return self._agreements(league_id).eq("player_id", player_id).execute().data or []
    def get_team_contracts_for_season(self, league_id, league_team_id, season): return self._seasons(league_id, season).eq("league_team_id", league_team_id).execute().data or []
    def get_contract_schedule(self, contract_id): return self.client.table("contract_seasons").select("*").eq("contract_id", contract_id).order("season").execute().data or []
    def get_contract_events(self, contract_id): return self.client.table("contract_events").select("*").eq("contract_id", contract_id).order("effective_at").execute().data or []
    def get_team_future_commitments(self, league_id, league_team_id, after_season):
        rows=self.client.table("contract_seasons").select("*").eq("league_id",league_id).eq("league_team_id",league_team_id).execute().data or []
        return [x for x in rows if int(x["season"])>int(after_season)]
    def get_expiring_contracts(self, league_id, after_season): return self.client.table("contract_agreements").select("*").eq("league_id",league_id).eq("end_season",int(after_season)).execute().data or []
    def _agreements(self,league_id): return self.client.table("contract_agreements").select("*").eq("league_id",league_id)
    def _seasons(self,league_id,season): return self.client.table("contract_seasons").select("*").eq("league_id",league_id).eq("season",int(season))
