from __future__ import annotations

from gm_assistant.reasoning.models import BrainState, QuestionAnalysis


_SESSION_STATE: dict[str, BrainState] = {}


def get_brain_state(owner_team_name: str) -> BrainState:
    if owner_team_name not in _SESSION_STATE:
        _SESSION_STATE[owner_team_name] = BrainState(owner_team_name=owner_team_name)
    return _SESSION_STATE[owner_team_name]


def apply_analysis_to_state(state: BrainState, analysis: QuestionAnalysis) -> BrainState:
    if analysis.update_state and analysis.goal:
        state.team_goal = analysis.goal

    if analysis.player_name:
        state.current_player = analysis.player_name

    state.current_topic = analysis.intent
    return state
