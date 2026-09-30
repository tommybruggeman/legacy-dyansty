"""Read helpers over the nfl_* tables (freshness, lookups). Read-only."""
from __future__ import annotations

from typing import Any


def data_freshness(client: Any) -> dict[str, str]:
    """Latest successful run per loader, for the pack's freshness section."""
    out: dict[str, str] = {}
    if client is None:
        return out
    try:
        rows = (
            client.table("nfl_data_sync_log")
            .select("loader, finished_at, rows_written, ok")
            .eq("ok", True)
            .order("finished_at", desc=True)
            .limit(30)
            .execute()
            .data
            or []
        )
    except Exception:
        return {"NFL data": "not loaded yet (nightly sync has not run)"}
    labels = {"sleeper_players": "NFL players (Sleeper)", "nflverse_stats": "NFL stats (nflverse)", "dynastyprocess_values": "market values (DynastyProcess)", "injuries": "injuries (ESPN report + nflverse practice reports)", "cfbd_prospects": "rookie prospects (CFBD)", "league_history": "league history (Sleeper transactions, drafts, matchups)"}
    for row in rows:
        label = labels.get(row.get("loader"), row.get("loader"))
        if label not in out and row.get("finished_at"):
            out[label] = str(row["finished_at"])[:16].replace("T", " ") + " UTC"
    if not out:
        out["NFL data"] = "not loaded yet (nightly sync has not run)"
    return out
