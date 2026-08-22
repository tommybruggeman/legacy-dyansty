from __future__ import annotations

from gm_assistant.executor.models import CapabilityResult
from gm_assistant.evidence import builders


def placeholder(name: str):
    def _run(*args, **kwargs):
        return CapabilityResult(
            name=name,
            success=False,
            data=None,
            message=f"Capability '{name}' is planned but not implemented yet.",
        )
    return _run


CAPABILITIES = {
    "load_relevant_context": builders.load_relevant_context,

    "load_user_roster": builders.load_user_roster,
    "load_team_scores": builders.load_team_scores,
    "identify_strengths_and_weaknesses": builders.identify_strengths_and_weaknesses,
    "rank_actionable_moves": builders.rank_actionable_moves,

    "load_player_context": builders.load_player_context,
    "load_contract_context": builders.load_contract_context,
    "load_team_fit": builders.load_team_fit,
    "make_player_decision": placeholder("make_player_decision"),

    "load_league_players": builders.load_league_players,
    "load_contracts": builders.load_contracts,
    "calculate_points_per_dollar": builders.calculate_points_per_dollar,
    "rank_contract_values": builders.rank_contract_values,

    "identify_team_needs": builders.identify_team_needs,
    "load_available_or_trade_targets": builders.load_available_or_trade_targets,
    "score_fit": builders.score_fit,
    "rank_targets": builders.rank_targets,

    "load_rookie_board": placeholder("load_rookie_board"),
    "rank_rookie_fit": placeholder("rank_rookie_fit"),
    "compare_pick_trade_value": placeholder("compare_pick_trade_value"),

    "load_league_rosters": builders.load_league_players,
    "identify_partner_needs": placeholder("identify_partner_needs"),
    "match_surplus_to_need": placeholder("match_surplus_to_need"),
    "construct_trade_framework": placeholder("construct_trade_framework"),
}
