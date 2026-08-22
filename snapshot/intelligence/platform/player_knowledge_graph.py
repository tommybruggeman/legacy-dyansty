from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


@dataclass
class PlayerKnowledgeGraph:
    player_name: str
    pos: Optional[str] = None
    nfl_team: Optional[str] = None
    sleeper_id: Optional[str] = None

    identity: Dict[str, Any] = None
    contract: Dict[str, Any] = None
    production: Dict[str, Any] = None
    projection: Dict[str, Any] = None
    rookie: Dict[str, Any] = None
    situation: Dict[str, Any] = None
    market: Dict[str, Any] = None
    risk: Dict[str, Any] = None
    raw: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_player_knowledge_graph(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    v1 graph builder.

    Takes any player-ish row and normalizes it into one player fact object.
    This should stay raw-fact oriented. No recommendations here.
    """

    graph = PlayerKnowledgeGraph(
        player_name=(
            row.get("player_name")
            or row.get("name")
            or row.get("full_name")
            or "Unknown Player"
        ),
        pos=row.get("pos") or row.get("position"),
        nfl_team=(
            row.get("nfl_team")
            or row.get("team")
            or row.get("team_abbr")
            or row.get("pro_team")
        ),
        sleeper_id=row.get("sleeper_id"),

        identity={
            "player_name": row.get("player_name") or row.get("name"),
            "pos": row.get("pos") or row.get("position"),
            "age": row.get("age"),
            "rookie_year": row.get("rookie_year"),
            "draft_year": row.get("draft_year"),
            "draft_round": row.get("draft_round"),
            "draft_pick": row.get("draft_pick"),
        },

        contract={
            "owner": row.get("current_owner") or row.get("owner_team_name"),
            "salary": row.get("salary"),
            "years": row.get("years"),
            "has_contract": row.get("has_contract"),
            "market_pool": row.get("market_pool"),
        },

        production={
            "season_ppg": row.get("season_ppg"),
            "projected_ppg": row.get("projected_ppg"),
            "points": row.get("points"),
            "games": row.get("games"),
        },

        projection={
            "year_1_projected_points": row.get("year_1_projected_points"),
            "year_2_projected_points": row.get("year_2_projected_points"),
            "year_3_projected_points": row.get("year_3_projected_points"),
            "year_1_start_probability": row.get("year_1_start_probability"),
            "year_2_start_probability": row.get("year_2_start_probability"),
            "year_3_start_probability": row.get("year_3_start_probability"),
            "projection_confidence": row.get("projection_confidence"),
            "projection_summary": row.get("projection_summary"),
        },

        rookie={
            "rookie_score": row.get("rookie_score"),
            "rookie_rank": row.get("rookie_rank"),
            "class_year": row.get("class_year"),
            "prospect_tier": row.get("prospect_tier"),
            "source": row.get("source"),
        },

        situation={
            "situation_score": row.get("situation_score"),
            "role_score": row.get("role_score"),
            "depth_chart_score": row.get("depth_chart_score"),
            "team_context_confirmed": row.get("team_context_confirmed"),
        },

        market={
            "market_score": row.get("market_score"),
            "asset_score": row.get("asset_score"),
            "trade_value": row.get("trade_value"),
            "adp": row.get("adp"),
        },

        risk={
            "risk_score": row.get("risk_score"),
            "injury_risk": row.get("injury_risk"),
            "age_risk": row.get("age_risk"),
            "contract_risk": row.get("contract_risk"),
            "data_warning": row.get("data_warning"),
        },

        raw=row,
    )

    return graph.to_dict()
