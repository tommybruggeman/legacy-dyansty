from __future__ import annotations

import re
from typing import Any, Dict, Optional

from gm_assistant.engines.rookie_value_engine import decide_rookie_pick_strategy
from gm_assistant.engines.rookie_candidate_loader import ranked_rookie_candidates


def _pick_label(question: str) -> str:
    q = (question or "").lower()

    m = re.search(r"1\.\d{2}", q)
    if m:
        return m.group(0)

    m = re.search(r"#\s?(\d+)", q)
    if m:
        return f"#{m.group(1)}"

    return "this rookie pick"


def is_rookie_question(question: str) -> bool:
    q = (question or "").lower()

    rookie_terms = [
        "rookie",
        "rookie draft",
        "draft pick",
        "who should i draft",
        "who should i look at",
        "1.01",
        "1.02",
        "1.03",
        "1.04",
        "1.05",
        "pick #",
    ]

    return any(t in q for t in rookie_terms)



def is_trade_down_question(question: str) -> bool:
    q = (question or "").lower()
    return any(t in q for t in [
        "trade down",
        "move down",
        "trade back",
        "move back",
        "drop back",
    ])


def answer_rookie_question(
    question: str,
    owner_name: str,
    conversation_state: Optional[Dict[str, Any]] = None,
    brain_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pick = _pick_label(question)
    state = conversation_state or {}
    team_goal = state.get("team_goal") or (brain_context or {}).get("goal_context", {}).get("team_goal")
    rookie_candidates = ranked_rookie_candidates(limit=25)
    selected_rookie = rookie_candidates[0] if rookie_candidates else None

    win_now = team_goal in {"win_now", "championship", "contend", "contender"} or "championship" in str(team_goal).lower()

    # Placeholder values until this is connected to the real rookie board.
    # The important behavior is the decision framework:
    # elite value > need early; need breaks ties later.
    value_decision = decide_rookie_pick_strategy(
        pick_label=pick,
        best_player_name=(selected_rookie or {}).get("player_name") or "the top loaded rookie",
        best_player_pos=(selected_rookie or {}).get("pos") or "BPA",
        need_player_name="the need-fit prospect",
        need_player_pos="team need",
        best_player_score=92.0,
        need_player_score=82.0,
        team_need_score=72.0,
    )

    if selected_rookie:
        candidate_intro = f'My current data-driven draft recommendation is **{selected_rookie.get("player_name")} ({selected_rookie.get("pos")})**.'
        candidate_reason = f'That recommendation is based on the loaded rookie candidate data. Rookie score: {selected_rookie.get("_rookie_score")}.'
    else:
        return {
            "answer_type": "reasoned_gm_answer",
            "intent": "rookie_pick_fit",
            "decision": "ROOKIE_BOARD_NOT_CONNECTED",
            "summary": (
                "I cannot honestly name a rookie yet because the active rookie board is empty.\n\n"
                "The rookie engine is working safely: it will only recommend a player after "
                "draft_year is available and rookie_class_year is derived through the identity pipeline.\n\n"
                "Next step: load the real active rookie class into rookie_class_registry, rebuild "
                "player_identity_context, then rebuild rookie_draft_board."
            ),
            "conversation_state": state,
        }

    if win_now:
        summary = f"""
For {pick}: {candidate_intro}

{candidate_reason}

This is not a pure team-need decision.

The rookie engine should weigh best-player value, team need, pick slot, tier gap, and trade-back value.

Current pick zone: {value_decision.pick_zone}
Value weight: {value_decision.value_weight}
Need weight: {value_decision.need_weight}
Tier gap estimate: {value_decision.tier_gap}

Core rule: team need should not override a major prospect tier gap.

That means if there is a Jeanty-type elite player at 1.01, you do not take a lower-tier QB just because your roster needs QB. You either take the elite player or trade back.

At {pick}, my current rookie decision is: {value_decision.decision}.

{value_decision.explanation}

Because your current goal is to win now, I would also compare this pick against veteran trade value.

My final decision tree:

1. If the elite rookie is clearly above the board, draft him or trade back.
2. If a veteran RB/TE starter is available for the pick, compare that against the rookie's future value.
3. If the tier is flat, let team need break the tie.
4. If the tier gap is large, do not force need.

My current lean: preserve elite value first, then use trade-back or veteran trade paths to solve team need.
"""
    else:
        summary = f"""
For {pick}: {candidate_intro}

{candidate_reason}

I would lean toward long-term value, but not blindly.

Current pick zone: {value_decision.pick_zone}
Value weight: {value_decision.value_weight}
Need weight: {value_decision.need_weight}
Tier gap estimate: {value_decision.tier_gap}

Decision: {value_decision.decision}.

{value_decision.explanation}

The rule is simple: early picks should protect elite player value. Later picks can lean more into roster need when the tier gap is smaller.

My current lean: draft the best available rookie unless the tier is flat or someone overpays for the pick.
"""

    if is_trade_down_question(question):
        summary = f"""
Yes, I would explore trading down from {pick}, but I would not trade down just to trade down.

At {pick}, the first rule is: do not pass on an elite tier unless the trade-back return pays you for that tier gap.

Because this is an elite/premium rookie slot, the trade-down price needs to be meaningful.

I would trade down if:
1. The top rookie tier is not clearly separated.
2. You can stay inside the same tier and add a real asset.
3. The move helps you solve RB/TE without losing elite value.
4. The return gives you either a starter, a future 1st, or multiple strong assets.

I would not trade down if:
1. There is a clear Jeanty-type player available.
2. The offer only adds minor depth.
3. The move forces you into a lower prospect tier.
4. You are only doing it because of positional need.

My GM answer: shop the pick, but set a high price.

The ideal move is not simply trading down. The ideal move is trading down while staying in the same rookie tier or turning {pick} into a playoff starter plus value.
"""

    return {
        "answer_type": "reasoned_gm_answer",
        "intent": "rookie_pick_fit",
        "decision": "ROOKIE_PICK_DECISION",
        "summary": summary.strip(),
        "conversation_state": state,
    }
