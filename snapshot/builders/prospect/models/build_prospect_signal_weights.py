from __future__ import annotations

from auth import service_client


TARGET_TABLE = "prospect_signal_weights"


DEFAULTS = {
    "QB": {"draft_capital": 0.32, "consensus_rank": 0.25, "production": 0.15, "age_declare": 0.10, "situation": 0.10, "market": 0.08},
    "RB": {"draft_capital": 0.28, "consensus_rank": 0.20, "production": 0.22, "age_declare": 0.08, "situation": 0.14, "market": 0.08},
    "WR": {"draft_capital": 0.25, "consensus_rank": 0.25, "production": 0.22, "age_declare": 0.12, "situation": 0.08, "market": 0.08},
    "TE": {"draft_capital": 0.22, "consensus_rank": 0.20, "production": 0.12, "age_declare": 0.08, "situation": 0.18, "market": 0.20},
}


def n(v, default=0.0):
    try:
        return float(v if v is not None else default)
    except Exception:
        return default


def load_all(sb, table: str) -> list[dict]:
    out = []
    page = 1000
    start = 0

    while True:
        rows = (
            sb.table(table)
            .select("*")
            .range(start, start + page - 1)
            .execute()
            .data or []
        )

        out.extend(rows)

        if len(rows) < page:
            break

        start += page

    return out


def rate(rows: list[dict], threshold: float) -> float:
    if not rows:
        return 0.0

    hits = [r for r in rows if n(r.get("rookie_impact_score")) >= threshold]
    return round(len(hits) / len(rows) * 100, 2)


def avg(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    return round(sum(n(r.get("rookie_impact_score")) for r in rows) / len(rows), 2)


def build_weights() -> None:
    sb = service_client()
    hist = load_all(sb, "rookie_historical_impact_model")

    print(f"Loaded {len(hist)} historical impact rows")

    out = []

    for pos, weights in DEFAULTS.items():
        pos_rows = [r for r in hist if (r.get("pos") or r.get("position")) == pos]
        y1 = [r for r in pos_rows if int(n(r.get("years_exp"))) == 1]
        y2 = [r for r in pos_rows if int(n(r.get("years_exp"))) == 2]
        y3 = [r for r in pos_rows if int(n(r.get("years_exp"))) == 3]

        print(pos, "rows:", len(pos_rows))
        avg_impact = avg(pos_rows)

        row = {
            "pos": pos,
            "position_confidence": round(avg_impact, 2),

            # Historical outcome bands
            "year1_hit_rate": rate(y1, 55),
            "year2_hit_rate": rate(y2, 55),
            "year3_hit_rate": rate(y3, 55),
            "starter_hit_rate": rate(pos_rows, 55),
            "elite_hit_rate": rate(pos_rows, 85),

            "avg_year1_impact": avg(y1),
            "avg_year2_impact": avg(y2),
            "avg_year3_impact": avg(y3),

            **weights,
        }

        out.append(row)

    sb.table(TARGET_TABLE).upsert(out, on_conflict="pos").execute()
    print(f"Upserted {len(out)} prospect signal weight rows with hit-rate bands")


if __name__ == "__main__":
    build_weights()
