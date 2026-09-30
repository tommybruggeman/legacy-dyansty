"""Read-only probe: row counts and freshness for every player/NFL/intelligence table.
Run from the project root:  python3 scripts/probe_data_tables.py
"""
from __future__ import annotations
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
LEAGUE = os.getenv("LEAGUE_AI_PROBE_LEAGUE_ID", "b03edc51-bec1-4064-9201-72e48ba413f9")

TABLES = [
    "player_universe","players","sleeper_players","player_identity_map","player_identity_bridge","player_identity_aliases",
    "player_season_stats","player_weekly_stats","nflverse_weekly_stats","player_rankings","player_market_consensus",
    "player_engine_scores","player_values","roster_asset_values","league_relative_player_values",
    "player_nfl_intelligence","player_situation_context","player_usage_context","player_projection_context",
    "player_prospect_context","player_development_features","player_strategic_profiles","player_dynasty_asset_engine",
    "player_market_value_engine","player_intelligence_base","player_contract_efficiency","player_graph","player_brain_context",
    "player_recommendations","player_data_need_queue","rookie_draft_board","rookie_draft_outcomes",
    "team_brain","team_brain_context","team_future_context","league_brain","gm_user_memory",
    "league_rules","league_seasons","transactions_enriched","v_team_caps",
]
DATE_COLS = ("updated_at","refreshed_at","last_updated","created_at","imported_at","as_of","snapshot_at","season","week")

def probe(t: str) -> str:
    try:
        r = sb.table(t).select("*", count="exact").limit(1).execute()
    except Exception as e:
        msg = str(e)
        return f"{t:38s} MISSING/ERR {msg[:60]}"
    cols = list(r.data[0].keys()) if r.data else []
    freshness = ""
    for c in DATE_COLS:
        if c in cols:
            try:
                latest = sb.table(t).select(c).order(c, desc=True).limit(1).execute().data
                if latest:
                    freshness = f"latest {c}={latest[0][c]}"
                    break
            except Exception:
                pass
    league_note = ""
    if "league_id" in cols:
        try:
            lr = sb.table(t).select("*", count="exact").eq("league_id", LEAGUE).limit(1).execute()
            league_note = f"(this league: {lr.count})"
        except Exception:
            pass
    return f"{t:38s} {r.count:>8} rows {league_note:22s} {freshness}"

print(f"Supabase project: {os.environ['SUPABASE_URL']}")
print(f"League filter: {LEAGUE}\n")
for t in TABLES:
    print(probe(t))
