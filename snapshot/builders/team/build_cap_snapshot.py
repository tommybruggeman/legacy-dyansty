from __future__ import annotations

import pandas as pd

from snapshot.loaders.cap import load_team_caps, load_cap_adjustments


def _records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []

    return df.where(pd.notnull(df), None).to_dict(orient="records")


def build_cap_snapshot() -> dict:
    caps = load_team_caps()
    adjustments = load_cap_adjustments()

    return {
        "team_caps": _records(caps),
        "cap_adjustments": _records(adjustments),
    }


if __name__ == "__main__":
    cap = build_cap_snapshot()

    print("Team cap rows:", len(cap["team_caps"]))
    print("Cap adjustment rows:", len(cap["cap_adjustments"]))
    print("Sample team caps:", cap["team_caps"][:3])
    print("Sample adjustments:", cap["cap_adjustments"][:3])
