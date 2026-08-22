from auth import service_client
from snapshot.intelligence.ai.ai_intel_bot import AIIntelBot
from snapshot.intelligence.rookies.rookie_ranker import rank_rookie_board
from snapshot.intelligence.llm.legacy_reasoning_layer import build_reasoned_player_dossier

def _team(row):
    return row.get("nfl_team") or row.get("team") or "-"


def _score(row):
    return float(
        row.get("ai_adjusted_score")
        or row.get("final_rookie_score")
        or row.get("rookie_score")
        or row.get("draft_score")
        or row.get("overall_score")
        or row.get("score")
        or 0
    )


def rookie_board(limit=10, rookie_year=None, use_llm=False):
    sb = service_client()
    if rookie_year is None:
        contexts = sb.table("publication_context_generations").select("published_season_id").order("completed_at", desc=True).limit(1).execute().data or []
        if not contexts:
            return []
        seasons = sb.table("league_seasons").select("season").eq("id", contexts[0]["published_season_id"]).limit(1).execute().data or []
        if not seasons:
            return []
        rookie_year = int(seasons[0]["season"])

    quality_rows = (
        sb.table("player_data_quality_context")
        .select("*")
        .eq("season", rookie_year)
        .eq("context_type", "rookie")
        .execute()
        .data or []
    )

    quality_by_name = {
        r.get("player_name"): r
        for r in quality_rows
        if r.get("player_name")
    }

    projection_rows = (
        sb.table("player_projection_context")
        .select("*")
        .eq("season", rookie_year)
        .eq("projection_type", "rookie_v1")
        .execute()
        .data or []
    )

    projections_by_name = {
        r.get("player_name"): r
        for r in projection_rows
        if r.get("player_name")
    }

    rows = (
        sb.table("rookie_draft_board")
        .select("*")
        .eq("rookie_class_year", rookie_year)
        .execute()
        .data or []
    )

    flags = []

    if not rows:
        flags.append(f"No rookie_draft_board rows found for {rookie_year}")
        return {
            "decision": "ROOKIE_BOARD",
            "summary": f"No rookie board rows found for {rookie_year}.",
            "flags": flags,
        }

    weak_source_count = sum(
        1 for r in rows
        if r.get("source") == "computed_from_player_universe"
    )

    if weak_source_count == len(rows):
        flags.append("No consensus-source prospect rows detected.")

    # Rank ALL rows with the rookie ranker before limiting.
    rows = rank_rookie_board(rows)

    ai = AIIntelBot()
    intel = ai.rookie_board_intel(rows, limit=len(rows))
    rows = intel.get("players", rows)
    flags.extend(intel.get("flags", []))

    rows = rank_rookie_board(rows)

    for i, r in enumerate(rows, start=1):
        r["true_overall_rank"] = r.get("overall_rookie_rank", i)

    top_rows = rows[:limit]

    lines = [f"Here is my current {rookie_year} rookie-board read:", ""]

    for r in top_rows:
        rank = r.get("ai_rank") or r.get("true_overall_rank")
        name = r.get("player_name") or "Unknown"
        pos = r.get("pos") or "-"
        team = _team(r)
        score = round(float(r.get("ai_score") or r.get("rank_score") or _score(r)), 2)

        lines.append(f"{rank}. {name} ({pos}, {team}) — score {score}")

        projection_row = projections_by_name.get(r.get("player_name"), {})
        quality_row = quality_by_name.get(r.get("player_name"), {})

        platform_row = {
            **r,
            **projection_row,
            "rookie_score": r.get("final_rookie_score"),
            "market_score": r.get("future_score"),
            "situation_score": r.get("team_need_fit_score"),
            "risk_score": r.get("risk_score") or 60,
        }
        dossier = build_reasoned_player_dossier(platform_row, use_llm=use_llm)

        lines.append(f"   Intel: {dossier.get('final_coach_summary')}")

        if quality_row.get("trust_grade") == "LOW":
            lines.append(f"   Data Quality: LOW — {quality_row.get('coach_warning')}")

        review = dossier.get("llm_review")
        if use_llm and isinstance(review, dict) and not review.get("error"):
            lines.append(f"   Bull: {review.get('bull_case')}")
            lines.append(f"   Bear: {review.get('bear_case')}")

        if r.get("ai_flags"):
            lines.append(f"   Flags: {', '.join(r.get('ai_flags'))}")

    lines.append("")

    if flags:
        lines.append("Data warning: " + " ".join(sorted(set(flags))))
        lines.append("")

    lines.append(
        "My GM stance: use this as a working board, not a final board, until the prospect source layer is stronger."
    )

    return {
        "decision": "ROOKIE_BOARD",
        "summary": "\n".join(lines),
        "flags": sorted(set(flags)),
    }


def answer_coach_question(question="rookie board"):
    q = question.lower()

    if "rookie" in q or "draft" in q:
        return rookie_board()

    return {
        "decision": "UNKNOWN",
        "summary": "I can currently answer rookie-board questions from coach_brain.py.",
        "flags": [],
    }


if __name__ == "__main__":
    res = rookie_board()
    print("ROOKIE_BOARD")
    print(res["summary"])
    print("FLAGS:", res.get("flags", []))
