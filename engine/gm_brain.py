from __future__ import annotations

from dataclasses import asdict

import pandas as pd

from gm_assistant.data import load_roster, load_player_values
from gm_assistant.reasoning import build_player_reasoning_view
from engine.config.league_config import DEFAULT_LEAGUE_CONFIG, LeagueConfig
from engine.analyzers.replacement_analyzer import add_replacement_value
from engine.analyzers.team_fit_analyzer import add_team_fit
from engine.analyzers.league_analyzer import analyze_league
from engine.strategy.priority_engine import build_team_priorities
from engine.strategy.opportunity_engine import build_team_opportunities


def build_gm_brain(
    league_id: str,
    owner_name: str,
    config: LeagueConfig = DEFAULT_LEAGUE_CONFIG,
    priority_limit: int = 10,
) -> dict:
    """
    Main GM Brain orchestration layer.

    Snapshot -> Player Reasoning -> Replacement Value -> Team Fit
    -> League Summary -> Team Priorities
    """

    roster = load_roster(league_id)
    values = load_player_values(league_id)

    reasoning = build_player_reasoning_view(roster, values, config)
    replacement = add_replacement_value(reasoning)
    fit = add_team_fit(replacement)

    league = analyze_league(fit, config)
    priorities = build_team_priorities(fit, owner_name, limit=priority_limit)
    opportunities = build_team_opportunities(fit, owner_name, limit=priority_limit)

    team_view = fit[
        fit["owner"].astype(str).str.strip().str.lower()
        .eq(str(owner_name).strip().lower())
    ].copy()

    team_summary = None
    for t in league.teams:
        if t.owner.strip().lower() == owner_name.strip().lower():
            team_summary = asdict(t)
            break

    return {
        "league_id": league_id,
        "owner_name": owner_name,
        "roster": roster,
        "player_values": values,
        "reasoning_view": reasoning,
        "replacement_view": replacement,
        "fit_view": fit,
        "team_view": team_view,
        "league_summary": league,
        "team_summary": team_summary,
        "priorities": priorities,
        "opportunities": opportunities,
    }


def summarize_gm_brain(brain: dict) -> dict:
    """
    Lightweight summary safe for UI/debug output.
    """

    if not brain:
        return {}

    league = brain.get("league_summary")
    priorities = brain.get("priorities", pd.DataFrame())
    opportunities = brain.get("opportunities", pd.DataFrame())
    team_view = brain.get("team_view", pd.DataFrame())
    team_summary = brain.get("team_summary") or {}

    return {
        "owner_name": brain.get("owner_name"),
        "team_players": len(team_view),
        "league_players": len(brain.get("fit_view", pd.DataFrame())),
        "priority_count": len(priorities),
        "opportunity_count": len(opportunities),
        "team_window": team_summary.get("window"),
        "starter_score": team_summary.get("starter_score"),
        "depth_score": team_summary.get("depth_score"),
        "cap_used": team_summary.get("cap_used"),
        "cap_remaining": team_summary.get("cap_remaining"),
        "best_team": getattr(league, "best_team", None),
        "deepest_team": getattr(league, "deepest_team", None),
        "most_cap_flexible_team": getattr(league, "most_cap_flexible_team", None),
    }
