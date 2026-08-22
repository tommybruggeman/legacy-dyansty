from __future__ import annotations

import json
from pathlib import Path


SNAPSHOT_PATH = (
    Path(__file__).parent
    / "snapshots"
    / "latest.json"
)


def read_snapshot() -> dict:
    if not SNAPSHOT_PATH.exists():
        raise FileNotFoundError(
            f"Snapshot not found: {SNAPSHOT_PATH}. "
            "Run: python3 -m snapshot.write_snapshot"
        )

    with open(SNAPSHOT_PATH, "r") as f:
        return json.load(f)


if __name__ == "__main__":
    snapshot = read_snapshot()

    print("Snapshot loaded")
    print("Teams:", len(snapshot.get("teams", [])))
    print("Players:", len(snapshot.get("players", [])))
    print("Rosters:", len(snapshot.get("rosters", [])))