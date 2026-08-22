from __future__ import annotations

from typing import Any, Dict, List

from auth import service_client


def load_rookie_candidates(limit: int = 25) -> List[Dict[str, Any]]:
    """
    Load rookie candidates from the real database.

    This intentionally avoids hardcoded player names.
    It tries likely existing tables/columns and safely returns []
    if the rookie board is not connected yet.
    """

    sb = service_client()

    table_attempts = [
        ("rookie_draft_board", "player_name,pos,rank,overall_rank,rookie_rank,team,score,value_score,future_score,brain_score"),
        ("draft_board", "player_name,pos,rank,overall_rank,rookie_rank,team,score,value_score,future_score,brain_score"),
        ("player_universe", "player_name,pos,is_rookie,rookie_rank,overall_rank,rank,team,future_score,brain_score,market_score,situation_score"),
    ]

    for table, columns in table_attempts:
        try:
            query = sb.table(table).select(columns).limit(limit)

            if table == "player_universe":
                query = query.eq("is_rookie", True)

            rows = query.execute().data or []

            clean = []
            for r in rows:
                name = r.get("player_name")
                pos = r.get("pos")

                if not name or pos not in {"QB", "RB", "WR", "TE"}:
                    continue

                clean.append(r)

            if clean:
                return clean

        except Exception:
            continue

    return []


def score_rookie_candidate(row: Dict[str, Any]) -> float:
    """
    Creates a dynamic score from whatever fields are available.
    No hardcoded player assumptions.
    """

    score_fields = [
        "score",
        "value_score",
        "future_score",
        "brain_score",
        "market_score",
        "situation_score",
    ]

    values = []
    for f in score_fields:
        try:
            v = float(row.get(f) or 0)
            if v > 0:
                values.append(v)
        except Exception:
            pass

    if values:
        return sum(values) / len(values)

    for rank_field in ["rank", "overall_rank", "rookie_rank"]:
        try:
            rank = float(row.get(rank_field) or 0)
            if rank > 0:
                return max(1.0, 100.0 - rank)
        except Exception:
            pass

    return 0.0


def ranked_rookie_candidates(limit: int = 25) -> List[Dict[str, Any]]:
    rows = load_rookie_candidates(limit=limit)

    scored = []
    for r in rows:
        rr = dict(r)
        rr["_rookie_score"] = round(score_rookie_candidate(r), 2)
        scored.append(rr)

    return sorted(
        scored,
        key=lambda r: (
            -float(r.get("_rookie_score") or 0),
            float(r.get("rank") or r.get("overall_rank") or r.get("rookie_rank") or 999),
        ),
    )
