from __future__ import annotations

from datetime import datetime, timezone
import re

from auth import service_client


TARGET_TABLE = "prospect_graph"


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _norm(v):
    s = str(v or "").strip().lower()
    s = s.replace(".", "").replace("'", "")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b$", "", s).strip()
    return " ".join(s.split())


def _tier(score: float) -> str:
    if score >= 92:
        return "ELITE_ROOKIE_ANCHOR"
    if score >= 85:
        return "ROUND_1_PRIORITY"
    if score >= 78:
        return "STRONG_ROOKIE_TARGET"
    if score >= 70:
        return "DEVELOPMENT_UPSIDE"
    return "LATE_ROOKIE_DART"


def build_prospect_graph():
    sb = service_client()
    now = datetime.now(timezone.utc).isoformat()

    rows = (
        sb.table("player_prospect_context")
        .select("*")
        .execute()
        .data or []
    )

    latest_year = max([int(_num(r.get("draft_year"))) for r in rows], default=2025)

    current = [
        r for r in rows
        if int(_num(r.get("draft_year"))) == latest_year
    ]

    current = sorted(
        current,
        key=lambda r: (
            _num(r.get("prospect_score")),
            _num(r.get("draft_capital_score")),
            _num(r.get("opportunity_score")),
        ),
        reverse=True,
    )

    out = []

    for i, r in enumerate(current, 1):
        name = r.get("player_name")
        pos = r.get("position")
        score = _num(r.get("prospect_score"))

        summary = (
            f"{name} ({pos}) — rookie rank {i}, prospect score {score:.1f}, "
            f"draft capital {_num(r.get('draft_capital_score')):.1f}, "
            f"landing spot {_num(r.get('landing_spot_score')):.1f}. "
            f"Role: {r.get('fantasy_role') or 'unknown'}."
        )

        out.append({
            "prospect_id": f"{latest_year}:{_norm(name)}",
            "player_name": name,
            "search_name": _norm(name),
            "draft_year": latest_year,
            "pos": pos,
            "nfl_team": r.get("nfl_team"),
            "college": r.get("college"),
            "draft_round": _num(r.get("draft_round")),
            "draft_pick": _num(r.get("draft_pick")),
            "prospect_score": score,
            "draft_capital_score": _num(r.get("draft_capital_score")),
            "college_production_score": _num(r.get("college_production_score")),
            "landing_spot_score": _num(r.get("landing_spot_score")),
            "offensive_line_score": _num(r.get("offensive_line_score")),
            "scheme_fit_score": _num(r.get("scheme_fit_score")),
            "opportunity_score": _num(r.get("opportunity_score")),
            "fantasy_role": r.get("fantasy_role"),
            "risk_notes": r.get("risk_notes"),
            "upside_notes": r.get("upside_notes"),
            "rookie_rank": i,
            "prospect_tier": _tier(score),
            "prospect_summary": summary,
            "updated_at": now,
        })

    if out:
        sb.table(TARGET_TABLE).delete().neq("prospect_id", "__never__").execute()
        sb.table(TARGET_TABLE).upsert(out, on_conflict="prospect_id").execute()

    print(f"Upserted {len(out)} prospect_graph rows for draft_year {latest_year}")


if __name__ == "__main__":
    build_prospect_graph()
