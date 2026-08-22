from snapshot.intelligence.agents.scout_agent import ScoutAgent
from snapshot.intelligence.agents.situation_agent import SituationAgent
from snapshot.intelligence.agents.market_agent import MarketAgent
from snapshot.intelligence.agents.contract_agent import ContractAgent
from snapshot.intelligence.agents.risk_agent import RiskAgent
from snapshot.intelligence.agents.decision_agent import DecisionAgent


class PlayerDossierBuilder:
    def __init__(self):
        self.scout = ScoutAgent()
        self.situation = SituationAgent()
        self.market = MarketAgent()
        self.contract = ContractAgent()
        self.risk = RiskAgent()
        self.decision = DecisionAgent()

    def build(self, row: dict, mode: str = "dynasty") -> dict:
        dossier = {
            "scout": self.scout.evaluate(row),
            "situation": self.situation.evaluate(row),
            "market": self.market.evaluate(row),
            "contract": self.contract.evaluate(row),
            "risk": self.risk.evaluate(row),
        }

        dossier["decision"] = self.decision.synthesize(row, dossier, mode=mode)

        return dossier

    def enrich_rows(self, rows: list[dict], mode: str = "dynasty") -> list[dict]:
        enriched = []

        for row in rows:
            row = dict(row)
            dossier = self.build(row, mode=mode)
            decision = dossier["decision"]

            row["ai_dossier"] = dossier
            row["ai_score"] = decision["decision_score"]
            row["ai_recommendation"] = decision["recommendation"]
            row["ai_confidence"] = decision["confidence"]
            row["ai_summary"] = decision["summary"]

            enriched.append(row)

        enriched = sorted(
            enriched,
            key=lambda r: (r.get("ai_score", 0), r.get("ai_confidence", 0)),
            reverse=True,
        )

        for i, row in enumerate(enriched, start=1):
            row["ai_rank"] = i

        return enriched
