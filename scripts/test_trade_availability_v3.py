from auth import service_client
from gm_assistant.engines.trade_availability_v3 import build_trade_availability_v3

import pandas as pd

sb = service_client()

OWNER_TEAM_NAME = "Tommy Bruggeman"

league_rosters = pd.DataFrame(
    sb.table("roster").select("*").execute().data or []
)

scores = pd.DataFrame(
    sb.table("player_engine_scores").select("*").execute().data or []
)

if "player_name" not in league_rosters.columns and "player" in league_rosters.columns:
    league_rosters["player_name"] = league_rosters["player"]

if not scores.empty:
    score_cols = [
        "sleeper_id",
        "engine_score",
        "base_player_score",
        "recent_production_score",
        "age_curve_score",
        "engine_tier",
    ]

    league_rosters = league_rosters.merge(
        scores[[c for c in score_cols if c in scores.columns]],
        on="sleeper_id",
        how="left",
    )

my_roster = league_rosters[
    league_rosters["owner_team_name"] == OWNER_TEAM_NAME
].copy()

print(f"League players: {len(league_rosters)}")
print(f"My roster: {len(my_roster)}")
print(f"Rows with engine scores: {league_rosters['engine_score'].notna().sum()}")

result = build_trade_availability_v3(
    league_rosters=league_rosters,
    my_roster=my_roster,
)

cols = [
    "player_name",
    "owner_team_name",
    "pos",
    "salary",
    "years",
    "production_score",
    "contract_efficiency_score",
    "replacement_difficulty_score",
    "team_importance_score",
    "player_surplus_value_score",
    "trade_availability_score",
    "availability_tier",
    "availability_reason",
]

print("\n==============================")
print("MOST AVAILABLE BY DECISION MODEL")
print("==============================\n")
print(result[[c for c in cols if c in result.columns]].head(40).to_string(index=False))

print("\n==============================")
print("HARDEST TO ACQUIRE BY DECISION MODEL")
print("==============================\n")
print(result[[c for c in cols if c in result.columns]].tail(35).to_string(index=False))
