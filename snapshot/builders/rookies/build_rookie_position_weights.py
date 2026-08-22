from __future__ import annotations

from collections import defaultdict

from auth import service_client


def n(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def build_rookie_position_weights():
    sb = service_client()

    rows = (
        sb.table("rookie_draft_outcomes")
        .select("*")
        .execute()
        .data or []
    )

    grouped = defaultdict(list)

    for r in rows:
        pos = r.get("pos")
        year = r.get("rookie_class_year")

        if pos not in {"QB", "RB", "WR", "TE"} or not year:
            continue

        grouped[(year, pos)].append(r)

    out = []

    for (year, pos), items in grouped.items():
        sample = len(items)

        avg_ppd = sum(n(r.get("points_per_dollar")) for r in items) / sample
        avg_surplus = sum(n(r.get("surplus_value_score")) for r in items) / sample
        hit_rate = sum(1 for r in items if n(r.get("hit_score")) >= 60) / sample
        bust_rate = sum(1 for r in items if r.get("bust_flag")) / sample

        # League-specific rookie positional value.
        # This is intentionally outcome-based, not opinion-based.
        weight = (
            1.0
            + avg_ppd * 0.015
            + avg_surplus * 0.010
            + hit_rate * 0.35
            - bust_rate * 0.25
        )

        out.append({
            "rookie_class_year": int(year),
            "pos": pos,
            "avg_points_per_dollar": round(avg_ppd, 3),
            "avg_surplus_value": round(avg_surplus, 3),
            "hit_rate": round(hit_rate, 3),
            "bust_rate": round(bust_rate, 3),
            "positional_weight": round(max(0.6, min(weight, 1.6)), 3),
            "sample_size": sample,
        })

    if out:
        sb.table("rookie_position_weights").upsert(
            out,
            on_conflict="rookie_class_year,pos",
        ).execute()

    print(f"Upserted {len(out)} rookie_position_weights rows")


if __name__ == "__main__":
    build_rookie_position_weights()
