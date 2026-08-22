from auth import service_client
from snapshot.engines.rookies.rookie_roi_curve_engine import RookieROICurveEngine
from snapshot.engines.rookies.rookie_contract_roi_engine import RookieContractROIEngine


class RookieIntelligenceEngine:
    """
    GM-facing rookie intelligence layer.
    Combines ROI + contract efficiency into decision outputs.
    """

    def __init__(self):
        self.roi_engine = RookieROICurveEngine()
        self.contract_engine = RookieContractROIEngine()
        self.sb = service_client()

    def load_data(self):
        return (
            self.sb.table("rookie_draft_outcomes")
            .select("*")
            .execute()
            .data
            or []
        )

    def build(self):
        rows = self.load_data()

        self.roi_engine.ingest(rows)
        self.contract_engine.ingest(rows)

        return {
            "roi_curves": self.roi_engine.compute_curves(),
            "strategy": self.roi_engine.strategy(),
            "contract_efficiency": self.contract_engine.contract_efficiency(),
            "position_strategy": self.contract_engine.strategy(),
        }

    def ask(self, question: str):
        data = self.build()
        q = question.lower()

        # RB VALUE
        if "rb" in q and "value" in q:
            return sorted(
                [(k, v) for k, v in data["strategy"].items() if "RB" in k],
                key=lambda x: x[1]["roi_score"],
                reverse=True
            )

        # WR VALUE
        if "wr" in q:
            return sorted(
                [(k, v) for k, v in data["strategy"].items() if "WR" in k],
                key=lambda x: x[1]["roi_score"],
                reverse=True
            )

        # QB VIEW
        if "qb" in q:
            return {k: v for k, v in data["strategy"].items() if "QB" in k}

        return data
