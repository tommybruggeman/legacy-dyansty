from typing import Dict, Any

from snapshot.intelligence.platform.player_dossier import build_player_dossier
from snapshot.intelligence.llm.dossier_reviewer_agent import review_dossier_with_llm


def build_reasoned_player_dossier(row: Dict[str, Any], use_llm: bool = False) -> Dict[str, Any]:
    dossier = build_player_dossier(row)

    if not use_llm:
        dossier["llm_review"] = None
        dossier["final_coach_summary"] = dossier.get("coach_summary")
        return dossier

    try:
        review = review_dossier_with_llm(dossier)
        dossier["llm_review"] = review
        dossier["final_coach_summary"] = review.get("coach_summary") or dossier.get("coach_summary")
    except Exception as e:
        dossier["llm_review"] = {"error": str(e)}
        dossier["final_coach_summary"] = dossier.get("coach_summary")

    return dossier
