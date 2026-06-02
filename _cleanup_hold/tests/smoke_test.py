# Legacy App/smoke_test.py
import os, sys

# Make "Legacy App" and its "lib" subfolder importable
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, "lib"))

import pandas as pd
from standings import build_standings_from_df   # import directly from lib/

# Two matchups: A vs B, C vs D
rows = [
    {"week": 1, "matchup_id": 1, "roster_id": 1, "points": 150.0, "opponent_roster_id": 2, "owner_name": "Team A"},
    {"week": 1, "matchup_id": 1, "roster_id": 2, "points": 120.0, "opponent_roster_id": 1, "owner_name": "Team B"},
    {"week": 1, "matchup_id": 2, "roster_id": 3, "points": 80.0,  "opponent_roster_id": 4, "owner_name": "Team C"},
    {"week": 1, "matchup_id": 2, "roster_id": 4, "points": 130.0, "opponent_roster_id": 3, "owner_name": "Team D"},
]
df = pd.DataFrame(rows)

standings = build_standings_from_df(df, tie_points=0, median_tie_counts=True)

print("=== Standings ===")
print(standings.to_string(index=False))

expected = {"Team A": 3, "Team D": 3, "Team B": 0, "Team C": 0}
for _, row in standings.iterrows():
    name, total = row["Team"], int(row["TotalPts"])
    assert expected.get(name) == total, f"{name}: {total} != {expected[name]}"

print("Smoke test passed ✅")
