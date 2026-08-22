from __future__ import annotations

SOURCE_POSITION_WEIGHT = {
    "player_weekly_stats": 100,
    "player_season_stats": 98,
    "player_career_features": 95,
    "player_value_engine": 90,
    "player_graph_v2": 80,
    "player_graph": 70,
    "player_universe": 55,
}


def choose_canonical_position(candidates) -> tuple[str | None, dict]:
    scores = {}

    for c in candidates:
        pos = c.pos
        if not pos:
            continue
        weight = SOURCE_POSITION_WEIGHT.get(c.table, 50)
        scores[pos] = scores.get(pos, 0) + weight

    if not scores:
        return None, {}

    winner = sorted(scores.items(), key=lambda x: x[1], reverse=True)[0][0]
    return winner, scores


def identity_confidence(cluster) -> float:
    confidence = 0.65

    if cluster.gsis_ids and cluster.sleeper_ids:
        confidence = 0.98
    elif cluster.gsis_ids:
        confidence = 0.94
    elif cluster.sleeper_ids:
        confidence = 0.90

    dangerous = [
        w for w in cluster.warnings
        if "gsis_conflict" in w or "sleeper_conflict" in w
    ]

    if dangerous:
        confidence = min(confidence, 0.72)
    elif cluster.warnings:
        confidence = min(confidence, 0.88)

    return round(confidence, 3)
