from engine.engine_context import build_engine_context
from engine.scoring_engine import roster_with_scores
from engine.score_writer import upsert_player_values


def run_valuation_engine(league_id: str):
    ctx = build_engine_context(league_id)

    scored_roster = roster_with_scores(ctx)

    upsert_player_values(ctx, scored_roster)

    return scored_roster