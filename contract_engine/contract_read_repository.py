from __future__ import annotations


class NormalizedContractReadRepository:
    def __init__(self,client):self.client=client
    def load(self,league_id):
        def rows(table):return self.client.table(table).select("*").eq("league_id",league_id).execute().data or []
        agreements=rows("contract_agreements"); ids=sorted({str(x["player_id"]) for x in agreements})
        players=self.client.table("player_universe").select("sleeper_id,canonical_player_id,player_name,pos").in_("sleeper_id",ids).execute().data or []
        return {"agreements":agreements,"seasons":rows("contract_seasons"),"events":rows("contract_events"),
            "executions":rows("contract_transition_executions"),
            "reconciliations":rows("contract_transition_reconciliations"),
            "teams":rows("league_teams"),"players":players,"legacy":rows("contracts")}

