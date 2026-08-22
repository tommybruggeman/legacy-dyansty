from __future__ import annotations

from gm_assistant.response_engine.models import UserIntent, Recommendation, ResponsePlan


def build_response_plan(intent: UserIntent, recommendation: Recommendation | None = None) -> ResponsePlan:
    if intent.intent_type == "team_goal_update":
        return ResponsePlan(
            opening="Perfect. That changes how I evaluate every move from here.",
            body_points=[
                "I’m now optimizing for 2026 championship odds, not maximum long-term dynasty value.",
                "That means veterans, expensive short-term production, and pick consolidation are more acceptable if they create a real weekly lineup edge.",
                "The key is not to randomly go all-in. It’s to turn surplus into starting lineup advantage.",
            ],
            caveat="I won’t recommend dumping good players just because their contracts are ugly. For a title push, production still matters.",
            next_action="From here, every answer should ask: does this move make your playoff lineup better?"
        )

    if recommendation:
        return ResponsePlan(
            opening=recommendation.thesis,
            body_points=recommendation.evidence[:4],
            caveat=recommendation.caveats[0] if recommendation.caveats else None,
            next_action=recommendation.next_action,
        )

    return ResponsePlan(
        opening="I’d answer this directly instead of restating the whole roster.",
        body_points=[
            "The response should react to the specific question first.",
            "Only pull in roster context if it changes the decision.",
        ],
    )
