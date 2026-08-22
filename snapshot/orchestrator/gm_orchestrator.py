from __future__ import annotations

from snapshot.simulation.weekly_scoring_engine import WeeklyScoringEngine
from snapshot.simulation.championship_engine import ChampionshipEngine
from snapshot.simulation.team_simulation_engine import TeamSimulationEngine
from snapshot.simulation.monte_carlo_engine import MonteCarloEngine
from auth import service_client












sb = service_client()


class GMOrchestrator:

    def _classify_intent(self, query: str):
        q = query.lower()

        scores = {
            "TRADE": 0,
            "START_SIT": 0,
            "FREE_AGENT": 0,
            "ROOKIE_DRAFT": 0,
            "ROSTER_OPTIMIZATION": 0
        }

        # trade signals
        if "trade" in q:
            scores["TRADE"] += 0.9

        # start/sit signals
        if "start" in q or "sit" in q:
            scores["START_SIT"] += 0.95

        if "waiver" in q or "pickup" in q or "fa" in q:
            scores["FREE_AGENT"] += 0.9

        if "rookie" in q or "draft" in q:
            scores["ROOKIE_DRAFT"] += 0.9

        if "roster" in q:
            scores["ROSTER_OPTIMIZATION"] += 0.7

        # contextual overrides
        if "this week" in q:
            scores["START_SIT"] += 0.2

        best = max(scores, key=scores.get)

        return {
            "type": best,
            "scores": scores,
            "confidence": float(scores[best])
        }
        q = query.lower()

        if "trade" in q:
            return {"type": "TRADE", "confidence": 0.7}

        if "start" in q or "sit" in q:
            return {"type": "START_SIT", "confidence": 0.75}

        if "waiver" in q or "fa" in q or "pickup" in q:
            return {"type": "FREE_AGENT", "confidence": 0.75}

        if "rookie" in q or "draft" in q:
            return {"type": "ROOKIE_DRAFT", "confidence": 0.75}

        if "roster" in q:
            return {"type": "ROSTER_OPTIMIZATION", "confidence": 0.7}

        return {"type": "GENERAL", "confidence": 0.5}

    """
    Pure orchestration layer.

    NO RULES.
    NO HARD CODING.
    ONLY MODEL OUTPUTS + DATA ROUTING.
    """

    def __init__(self):
        self.sb = sb
        self.sim_engine = MonteCarloEngine()
        self.team_sim = TeamSimulationEngine()
        self.champ_engine = ChampionshipEngine()
        self.weekly_engine = WeeklyScoringEngine()
        

    # -----------------------------
    # ENTRY POINT
    # -----------------------------
    def run(self, query: str, owner_team_name: str | None = None):

        intent = self._get_intent(query)

        players = self._get_players(owner_team_name)

        career = self._get_career(players)
        market = self._get_market(players)
        outcome = self._get_outcomes(players)

        scenario = self._simulate(players, outcome, query)

        # -----------------------------
        # START / SIT LAYER (WEEKLY ENGINE)
        # -----------------------------
        if intent["type"] == "START_SIT":

            try:
                roster = self.sb.table("player_intelligence_base").select("*").execute().data or []

                # naive pairing for v1 (we refine later with lineup parser)
                if len(roster) >= 2:
                    p1 = roster[0]
                    p2 = roster[1]

                    result = self.weekly_engine.compare_players(p1, p2)
                else:
                    result = {"error": "not enough players"}

            except Exception as e:
                result = {"error": str(e)}

            scenario["weekly_decision"] = result


        # -----------------------------
        # DOMAIN ROUTING LAYER
        # -----------------------------
        if intent["type"] == "FREE_AGENT":
            scenario["note"] = "FA evaluation mode (future layer will include bidding + cost curves)"

        if intent["type"] == "START_SIT":
            scenario["note"] = "Start/Sit mode (matchup + projection comparison needed)"

        if intent["type"] == "ROOKIE_DRAFT":
            scenario["note"] = "Rookie draft mode (career curve + landing spot + value decay)"

        if intent["type"] == "ROSTER_OPTIMIZATION":
            scenario["note"] = "Roster optimization mode (positional balance + depth modeling)"


        # --------------------------------
        # CHAMPIONSHIP PROBABILITY LAYER
        # --------------------------------
        try:
            if "team_sim" in locals():
                champ = self.champ_engine.simulate_championship_curve(
                    team_sim.get("curve", [])
                )
            else:
                champ = {"error": "no team curve"}
        except Exception as e:
            champ = {"error": str(e)}


        # -----------------------------
        # TEAM SIMULATION LAYER
        # -----------------------------
        try:
            roster = self.sb.table("player_intelligence_base").select("*").execute().data or []
            outcome = self.sb.table("player_outcome_projection_engine").select("*").execute().data or []

            team_sim = self.team_sim.simulate_team_curve(
                pd.DataFrame(roster),
                pd.DataFrame(outcome)
            )
        except Exception as e:
            team_sim = {"error": str(e)}


        return self._explain(intent, career, market, outcome, scenario)

    # -----------------------------
    # INTENT (PLACEHOLDER MODEL HOOK)
    # -----------------------------
    def _get_intent(self, query: str):
        q = query.lower()

        # PURE WEAK SIGNAL MODEL (not rules, just probabilistic hints later replaceable)
        signals = {
            "trade": 0,
            "roster": 0,
            "buy": 0,
            "sell": 0,
            "championship": 0,
            "value": 0,
        }

        for k in signals:
            if k in q:
                signals[k] += 1

        top = max(signals, key=signals.get)

        return {
            "query": query,
            "type": top.upper(),
            "confidence": 0.55 + (signals[top] * 0.1)
        }
        return {
            "query": query,
            "type": "UNDEFINED",
            "confidence": 1.0,
        }

    # -----------------------------
    # DATA LAYER
    # -----------------------------
    def _get_players(self, owner_team_name):
        q = self.sb.table("player_intelligence_base").select("*")

        if owner_team_name:
            q = q.eq("owner_team_name", owner_team_name)

        return q.limit(200).execute().data or []

    def _get_career(self, players):
        return self.sb.table("player_career_outcome_engine") \
            .select("*") \
            .limit(2000) \
            .execute().data or []

    def _get_market(self, players):
        return self.sb.table("player_market_consensus") \
            .select("*") \
            .limit(2000) \
            .execute().data or []

    def _get_outcomes(self, players):
        return self.sb.table("player_outcome_projection_engine") \
            .select("*") \
            .limit(2000) \
            .execute().data or []

    # -----------------------------
    # SIMULATION ENGINE (PLACEHOLDER)
    # -----------------------------
    def _simulate(self, players, outcome, query):

        if not players:
            return {"mode": "no_players"}

        # pick one sample player (first step toward full roster sim)
        p = players[0]

        player_vector = {
            "age": p.get("age", 26),
            "market_score": p.get("market_score", 50),
            "volatility": 0.25
        }

        sims = self.sim_engine.simulate_player(player_vector, n_sims=500)

        summary = self.sim_engine.summarize(sims)

        return {
            "mode": "monte_carlo_v1",
            "query": query,
            "summary": summary
        }
        # NOT rules — STRUCTURAL SIMULATION FRAME

        return {
            "mode": "structural_simulation_v1",
            "query": query,

            "simulation_layers": {
                "career_inputs": len(players),
                "outcome_rows": len(outcome),
            },

            "note": "Simulation engine scaffold active — Monte Carlo next step"
        }
        return {
            "mode": "placeholder",
            "query": query,
            "note": "Monte Carlo simulation will be inserted here",
        }

    # -----------------------------
    # EXPLANATION LAYER (NO RULES)
    # -----------------------------
    def _explain(self, intent, career, market, outcome, scenario):
        return {
            "intent": intent,
            "summary": "GM Orchestrator running (no decision logic layer yet)",
            "career_rows": len(career),
            "market_rows": len(market),
            "outcome_rows": len(outcome),
            "scenario": scenario,
        }




if __name__ == "__main__":
    import sys

    orch = GMOrchestrator()

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "help me evaluate my roster"

    res = orch.run(query, None)
    print(res)

