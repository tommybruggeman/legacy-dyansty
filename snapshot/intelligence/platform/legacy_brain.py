from typing import Any, Dict, Optional

from auth import service_client
from snapshot.intelligence.llm.legacy_reasoning_layer import build_reasoned_player_dossier
from snapshot.intelligence.resolvers.resolver_router import route_data_needs


class LegacyBrain:
    """
    Central orchestrator.
    One player in -> full intelligence package out.
    """

    def __init__(self, season: int | None = None):
        if season is None:
            from snapshot.runtime.season import get_current_season
            season = get_current_season()
        self.season = season
        self.sb = service_client()

    def _get_projection(self, player_name: str, context_type: str = "rookie") -> Dict[str, Any]:
        projection_type = "rookie_v1" if context_type == "rookie" else "player_v1"

        rows = (
            self.sb.table("player_projection_context")
            .select("*")
            .eq("season", self.season)
            .eq("projection_type", projection_type)
            .eq("player_name", player_name)
            .limit(1)
            .execute()
            .data or []
        )
        return rows[0] if rows else {}

    def _get_quality(self, player_name: str, context_type: str = "rookie") -> Dict[str, Any]:
        rows = (
            self.sb.table("player_data_quality_context")
            .select("*")
            .eq("season", self.season)
            .eq("context_type", context_type)
            .eq("player_name", player_name)
            .limit(1)
            .execute()
            .data or []
        )
        return rows[0] if rows else {}

    def _get_open_needs(self, player_name: str, context_type: str = "rookie", limit: int = 10):
        grouped_need_names = [
            "identity_complete",
            "draft_profile_complete",
            "situation_complete",
            "production_complete",
            "market_complete",
            "contract_complete",
            "risk_complete",
            "general_review",
        ]

        rows = (
            self.sb.table("player_data_need_queue")
            .select("*")
            .eq("season", self.season)
            .eq("context_type", context_type)
            .eq("player_name", player_name)
            .eq("status", "open")
            .order("priority")
            .execute()
            .data or []
        )

        grouped = [r for r in rows if r.get("need") in grouped_need_names]
        raw = [r for r in rows if r.get("need") not in grouped_need_names]

        return (grouped + raw)[:limit]

    def build_player_brain(
        self,
        row: Dict[str, Any],
        context_type: str = "rookie",
        use_llm: bool = False,
    ) -> Dict[str, Any]:
        player_name = row.get("player_name")

        projection = self._get_projection(player_name, context_type) if player_name else {}
        quality = self._get_quality(player_name, context_type) if player_name else {}
        needs = self._get_open_needs(player_name, context_type) if player_name else []

        platform_row = {
            **row,
            **projection,
            "rookie_score": row.get("final_rookie_score") or row.get("rookie_score"),
            "market_score": row.get("future_score") or row.get("market_score"),
            "situation_score": row.get("team_need_fit_score") or row.get("situation_score"),
            "risk_score": row.get("risk_score") or 60,
        }

        dossier = build_reasoned_player_dossier(platform_row, use_llm=use_llm)

        trust_grade = quality.get("trust_grade") or "UNKNOWN"

        resolver_plan = route_data_needs(needs)

        return {
            "player_name": player_name,
            "pos": row.get("pos"),
            "nfl_team": row.get("nfl_team") or row.get("team"),

            "context_type": context_type,
            "season": self.season,

            "dossier": dossier,
            "quality": quality,
            "open_needs": needs,
            "resolver_plan": resolver_plan,

            "trust_grade": trust_grade,
            "decision": dossier.get("agents", {}).get("decision", {}),
            "summary": dossier.get("final_coach_summary"),

            "brain_warning": quality.get("coach_warning"),
            "should_overreact": not bool(quality.get("do_not_overreact", False)),
        }


def build_player_brain(row: Dict[str, Any], season: int | None = None, context_type: str = "rookie", use_llm: bool = False):
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    return LegacyBrain(season=season).build_player_brain(row, context_type=context_type, use_llm=use_llm)
