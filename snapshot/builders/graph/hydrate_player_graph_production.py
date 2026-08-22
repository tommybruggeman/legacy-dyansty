from __future__ import annotations

from auth import service_client


def hydrate_player_graph_production():
    sb = service_client()

    prod_rows = (
        sb.table("player_production_intelligence")
        .select("*")
        .execute()
        .data
        or []
    )

    graph_rows = (
        sb.table("player_graph_v2")
        .select("sleeper_id,canonical_player_id,player_name,pos")
        .execute()
        .data
        or []
    )

    graph_by_sid = {
        str(g.get("sleeper_id")): g
        for g in graph_rows
        if g.get("sleeper_id")
    }

    updates = []
    skipped_missing_graph = 0

    for r in prod_rows:
        sid = str(r.get("sleeper_id") or "")
        if not sid:
            continue

        g = graph_by_sid.get(sid)
        if not g:
            skipped_missing_graph += 1
            continue

        source = r.get("source") or "unknown"
        primary_ppg = float(r.get("primary_ppg") or 0)

        # Do not preserve fake/stale positive graph PPG when production has no source.
        if source == "no_production_source":
            primary_ppg = 0.0

        expected_ppg = float(r.get("expected_ppg") or primary_ppg or 0)

        updates.append({
            "sleeper_id": sid,
            "canonical_player_id": g.get("canonical_player_id"),
            "player_name": g.get("player_name"),
            "pos": g.get("pos"),
            "season_ppg": primary_ppg,
            "expected_ppg": expected_ppg,
            "production": {
                "season_ppg": r.get("season_ppg"),
                "expected_ppg": expected_ppg,
                "historical_ppg": r.get("historical_ppg"),
                "recent_ppg_signal": r.get("recent_ppg_signal"),
                "primary_ppg": primary_ppg,
                "production_score": r.get("production_score"),
                "trend_delta": r.get("trend_delta"),
                "trend_label": r.get("trend_label"),
                "production_confidence": r.get("production_confidence"),
                "production_warnings": r.get("production_warnings") or [],
                "source": source,
            },
        })

    sb.table("player_graph_v2").upsert(
        updates,
        on_conflict="sleeper_id",
    ).execute()

    print(f"Bulk hydrated production into player_graph_v2 for {len(updates)} players")
    print(f"Skipped missing graph rows: {skipped_missing_graph}")


if __name__ == "__main__":
    hydrate_player_graph_production()
