import os
import requests
from pathlib import Path


def load_env():
    for p in [
        Path("../.env"),
        Path("../fantasy_env"),
        Path("../pages/.env"),
        Path("../pages/fantasy_env"),
    ]:
        if not p.exists():
            continue

        for line in p.read_text().splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue

            k, v = line.split("=", 1)
            os.environ.setdefault(
                k.strip(),
                v.split("#", 1)[0].strip().strip('"').strip("'"),
            )


load_env()

league_id = "".join(
    ch for ch in os.getenv("SLEEPER_LEAGUE_ID", "") if ch.isdigit()
)

print("league_id:", league_id)

if not league_id:
    raise SystemExit("Missing SLEEPER_LEAGUE_ID")

for week in range(1, 19):
    url = f"https://api.sleeper.app/v1/league/{league_id}/transactions/{week}"
    rows = requests.get(url, timeout=25).json()

    if rows:
        print(f"\n=== WEEK {week} ===")
        print("transactions returned:", len(rows))

        for tx in rows[:5]:
            print(tx)
            print("---")
