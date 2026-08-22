from __future__ import annotations

from auth import service_client
from gm_assistant.context_builder import build_question_context


def _score_from_context(player_context: dict) -> float:
    scores = player_context.get("scores") or {}

    for key in [
        "final_rookie_score",
        "prospect_score",
        "future_score",
        "positional_value_score",
        "team_need_fit_score",
    ]:
        value = scores.get(key)
        if value is not None:
            try:
                return float(value)
            except Exception:
                pass

    return 0.0


def _format_player_context(player_context: dict) -> str:
    rookie = player_context.get("rookie_draft_board") or {}
    scores = player_context.get("scores") or {}

    name = player_context.get("player_name")
    pos = rookie.get("pos") or "-"
    team = rookie.get("nfl_team") or "-"
    rank = rookie.get("rookie_rank")
    tier = rookie.get("tier")

    return (
        f"{name} ({pos}, {team}) — rookie rank {rank}, tier {tier}, "
        f"final score {scores.get('final_rookie_score')}, "
        f"prospect {scores.get('prospect_score')}, "
        f"future {scores.get('future_score')}, "
        f"team fit {scores.get('team_need_fit_score')}"
    )


def _context_data_warning(player_context: dict) -> str:
    tasks = player_context.get("source_tasks") or []
    warnings = player_context.get("warnings") or []

    top_tasks = []
    for task in tasks[:3]:
        source_id = task.get("source_id")
        needs = task.get("needs") or []
        top_tasks.append(f"{source_id}: {', '.join(needs[:4])}")

    lines = []

    if warnings:
        lines.append("Warnings: " + "; ".join(warnings))

    if top_tasks:
        lines.append("Open data: " + " | ".join(top_tasks))

    return "\n".join(lines)


def _fetch_rookie_position_rows(pos: str = "QB", limit: int = 10) -> list[dict]:
    sb = service_client()

    rows = (
        sb.table("rookie_draft_board")
        .select("*")
        .eq("pos", pos)
        .order("final_rookie_score", desc=True)
        .limit(limit)
        .execute()
        .data or []
    )

    return rows


def answer_rookie_understanding_question(question: str, owner_team_name: str, understanding: dict) -> dict:
    intent = understanding.get("intent")

    ctx = build_question_context(question, owner_team_name)
    player_contexts = ctx.get("players") or {}

    if intent == "ROOKIE_PLAYER_DECISION":
        if not player_contexts:
            return {
                "answer_type": "rookie_understanding",
                "decision": "ROOKIE_PLAYER_NOT_FOUND",
                "summary": "I understood this as a player-specific rookie question, but I could not identify the player in the loaded data.",
                "understanding": understanding,
                "context": ctx,
            }

        player_name, pc = next(iter(player_contexts.items()))
        score = _score_from_context(pc)
        warning = _context_data_warning(pc)

        if score >= 80:
            lean = "Yes — he is in a draftable/high-interest range based on the loaded rookie board."
        elif score >= 70:
            lean = "Maybe — he is draftable, but I would compare cost and tier before locking it in."
        else:
            lean = "No strong yes yet — he needs either a cheaper cost or better supporting data."

        summary = (
            f"I understood this as a decision on {player_name}, not a generic best-player-at-pick answer.\n\n"
            f"{_format_player_context(pc)}\n\n"
            f"Lean: {lean}\n\n"
            f"{warning}"
        ).strip()

        return {
            "answer_type": "rookie_understanding",
            "decision": "ROOKIE_PLAYER_DECISION",
            "summary": summary,
            "context": ctx,
        }

    if intent == "ROOKIE_PLAYER_COMPARISON":
        if not player_contexts:
            return {
                "answer_type": "rookie_understanding",
                "decision": "ROOKIE_COMPARISON_NOT_FOUND",
                "summary": "I understood this as a rookie comparison, but I could not identify matching players.",
                "understanding": understanding,
                "context": ctx,
            }

        ranked = sorted(
            player_contexts.values(),
            key=_score_from_context,
            reverse=True,
        )

        lines = [f"{i+1}. {_format_player_context(pc)}" for i, pc in enumerate(ranked)]
        winner = ranked[0].get("player_name")

        warnings = "\n\n".join(
            f"{pc.get('player_name')}: {_context_data_warning(pc)}"
            for pc in ranked
        )

        return {
            "answer_type": "rookie_understanding",
            "decision": "ROOKIE_PLAYER_COMPARISON",
            "summary": (
                "I understood this as a direct player comparison, so I am only comparing the named players.\n\n"
                + "\n".join(lines)
                + f"\n\nLean: {winner} is the better current board value based on the full context bundle.\n\n"
                + warnings
            ).strip(),
            "context": ctx,
        }

    if intent == "ROOKIE_POSITION_VALUE":
        rows = _fetch_rookie_position_rows(pos="QB", limit=10)

        lines = []
        for i, r in enumerate(rows):
            lines.append(
                f"{i+1}. {r.get('player_name')} ({r.get('pos')}, {r.get('nfl_team') or '-'}) "
                f"— rank {r.get('rookie_rank')}, tier {r.get('tier')}, "
                f"final score {r.get('final_rookie_score')}, "
                f"prospect {r.get('prospect_score')}, "
                f"future {r.get('future_score')}"
            )

        return {
            "answer_type": "rookie_understanding",
            "decision": "ROOKIE_POSITION_VALUE",
            "summary": (
                "I understood this as a rookie QB value question, so I filtered the rookie board to QBs only.\n\n"
                + "\n".join(lines)
                + "\n\nLean: this is now using final_rookie_score instead of the broken generic score field."
            ),
            "context": ctx,
        }

    return {
        "answer_type": "rookie_understanding",
        "decision": "UNHANDLED_ROOKIE_UNDERSTANDING",
        "summary": "I understood this as a rookie question, but this rookie intent is not handled yet.",
        "understanding": understanding,
        "context": ctx,
    }
