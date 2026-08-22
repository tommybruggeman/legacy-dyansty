from __future__ import annotations

import json
from pathlib import Path

from snapshot.build_snapshot import build_snapshot


OUTPUT = (
    Path(__file__).parent
    / "snapshots"
    / "latest.json"
)


def write_snapshot():
    snapshot = build_snapshot()

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(OUTPUT, "w") as f:
        json.dump(
            snapshot,
            f,
            indent=2,
            default=str,
        )

    print(f"Snapshot written to {OUTPUT}")
    print(f"Teams: {len(snapshot['teams'])}")
    print(f"Players: {len(snapshot['players'])}")
    print(f"Rosters: {len(snapshot['rosters'])}")


if __name__ == "__main__":
    write_snapshot()