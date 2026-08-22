from __future__ import annotations

from typing import Any

from gm_assistant.safety.protected_gm_answer import answer_gm_question_protected as answer_gm_question
from gm_assistant.engines.trade_scenario_engine import parse_trade_scenario
from gm_assistant.decision.intent import GMIntent
from gm_assistant.decision.synthesizer import answer_from_intent
from gm_assistant.decision.intent_router import classify_intent
from gm_assistant.conversation.state import ConversationState


class GMConversationEngine:
    def __init__(self, owner_team_name: str):
        self.state = ConversationState(owner_team_name=owner_team_name)

    def ask(self, question: str) -> dict[str, Any]:
        self.state.update_from_question(question)
        resolved_question = self.state.resolve_followup(question)

        trade_parts = parse_trade_scenario(resolved_question)

        primary_player = self.state.current_player
        comparison_player = self.state.compared_player

        intent_name = classify_intent(question, current_player=primary_player)

        if trade_parts:
            primary_player, comparison_player = trade_parts
            intent_name = "trade_scenario"

        intent = GMIntent(
            intent=intent_name,
            owner_team_name=self.state.owner_team_name,
            primary_player=primary_player,
            comparison_player=comparison_player,
            team_goal=self.state.team_goal,
            topic=self.state.current_topic,
            original_question=question,
            resolved_question=resolved_question,
            previous_recommendation=self.state.last_recommendation,
        )

        answer = answer_from_intent(intent)

        self.state.record_turn(question, resolved_question, answer)

        return {
            "original_question": question,
            "resolved_question": resolved_question,
            "answer": answer,
            "intent": intent.to_dict(),
            "state": {
                "current_player": self.state.current_player,
                "compared_player": self.state.compared_player,
                "current_topic": self.state.current_topic,
                "team_goal": self.state.team_goal,
                "last_recommendation": self.state.last_recommendation,
            },
        }
