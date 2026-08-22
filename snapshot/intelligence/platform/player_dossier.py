from typing import Dict, Any

from snapshot.intelligence.platform.player_knowledge_graph import build_player_knowledge_graph
from snapshot.intelligence.platform.scout_agent import evaluate_scout
from snapshot.intelligence.platform.situation_agent import evaluate_situation
from snapshot.intelligence.platform.risk_agent import evaluate_risk
from snapshot.intelligence.platform.market_agent import evaluate_market
from snapshot.intelligence.platform.projection_agent import evaluate_projection
from snapshot.intelligence.platform.decision_agent import make_decision


def build_player_dossier(row: Dict[str, Any]) -> Dict[str, Any]:
    graph = build_player_knowledge_graph(row)

    scout = evaluate_scout(graph)
    situation = evaluate_situation(graph)
    risk = evaluate_risk(graph)
    market = evaluate_market(graph)
    projection = evaluate_projection(graph)

    decision = make_decision(graph, scout, situation, risk, market, projection)

    return {
        "player_name": graph.get("player_name"),
        "pos": graph.get("pos"),
        "nfl_team": graph.get("nfl_team"),
        "knowledge_graph": graph,
        "agents": {
            "scout": scout,
            "situation": situation,
            "risk": risk,
            "market": market,
            "projection": projection,
            "decision": decision,
        },
        "coach_summary": decision.get("summary"),
    }
