from auth import service_client
from gm_assistant.engines.trade_availability_v2 import build_trade_availability_v2

import pandas as pd

sb = service_client()

OWNER_TEAM_NAME = "Tommy Bruggeman"

# -----------------------------
# Load roster
# -----------------------------

league_rosters = pd.DataFrame(
    sb.table("roster")
    .select("*")
    .execute()
    .data
    or []
)

# -----------------------------
# Load engine scores
# -----------------------------

scores = pd.DataFrame(
    sb.table("player_engine_scores")
    .select("*")
    .execute()
    .data
    or []
)

if "player_name" not in league_rosters.columns and "player" in league_rosters.columns:
    league_rosters["player_name"] = league_rosters["player"]

# -----------------------------
# Merge engine scores
# -----------------------------

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

    league_rosters["dynasty_asset_score"] = (
        league_rosters["engine_score"]
        .fillna(league_rosters["base_player_score"])
    )

    league_rosters["win_now_score"] = (
        league_rosters["recent_production_score"]
        .fillna(league_rosters["dynasty_asset_score"])
    )

# -----------------------------
# My roster
# -----------------------------

my_roster = league_rosters[
    league_rosters["owner_team_name"] == OWNER_TEAM_NAME
].copy()

print(f"League players: {len(league_rosters)}")
print(f"My roster: {len(my_roster)}")
print(f"Rows with engine scores: {league_rosters['dynasty_asset_score'].notna().sum()}")

# -----------------------------
# Run engine
# -----------------------------

result = build_trade_availability_v2(
    league_rosters=league_rosters,
    my_roster=my_roster,
)

# -----------------------------
# Output
# -----------------------------

print("\n==============================")
print("MOST AVAILABLE TRADE TARGETS")
print("==============================\n")

cols = [
    "player_name",
    "owner_team_name",
    "pos",
    "salary",
    "years",
    "asset_score",
    "win_now_score",
    "team_asset_rank",
    "team_pos_count",
    "trade_availability_score",
    "availability_tier",
    "availability_reason",
]

print(result[[c for c in cols if c in result.columns]].head(40).to_string(index=False))

print("\n==============================")
print("HARDEST PLAYERS TO ACQUIRE")
print("==============================\n")

print(result[[c for c in cols if c in result.columns]].tail(30).to_string(index=False))
