from __future__ import annotations

from datetime import datetime, timezone
from collections import defaultdict

from auth import service_client

def fetch_all(sb, table: str, batch_size: int = 1000):
    rows = []
    start = 0

    while True:
        batch = (
            sb.table(table)
            .select("*")
            .range(start, start + batch_size - 1)
            .execute()
            .data
            or []
        )

        rows.extend(batch)

        if len(batch) < batch_size:
            break

        start += batch_size

    return rows


def num(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def avg(values):
    values = [num(v) for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else 0.0


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def team_window(win_now, future, contract, risk):
    if win_now >= 62 and future >= 55:
        return "CONTENDER_WITH_FUTURE"
    if win_now >= 62:
        return "WIN_NOW"
    if future >= 60 and win_now < 50:
        return "ASCENDING_REBUILD"
    if future >= 52:
        return "SOFT_RETOOL"
    if contract < 35 or risk > 65:
        return "REBUILD_PRESSURE"
    return "MIDDLE"


def build_team_summary(t):
    return (
        f"{t['owner_team_name']} profile: {t['team_window']}. "
        f"Win-now {t['win_now_score']}, future {t['future_score']}, "
        f"contract health {t['contract_health_score']}, risk {t['risk_score']}. "
        f"Strengths: {', '.join(t['strengths']) or 'none clear'}. "
        f"Needs: {', '.join(t['need_positions']) or 'none clear'}."
    )


def build_contexts():
    sb = service_client()

    players = fetch_all(sb, "player_brain_context")

    fake_names = {
        "Tom Brady", "Drew Brees", "Matt Ryan", "Steve Smith", "Tiki Barber",
        "Calvin Johnson", "Arian Foster", "Anquan Boldin", "Larry Fitzgerald",
        "Brandon Marshall", "Wes Welker", "Jordy Nelson", "Demaryius Thomas",
    }

    filtered = [
        p for p in players
        if p.get("player_name") not in fake_names
        and str(p.get("pos") or "") in {"QB", "RB", "WR", "TE"}
    ]

    rostered = [
        p for p in filtered
        if p.get("current_owner")
        and str(p.get("current_owner")).upper() not in {"FA", "FREE_AGENT", "WAIVERS"}
    ]

    free_agents = [
        p for p in filtered
        if not p.get("current_owner")
        and str(p.get("market_pool") or "").upper() in {"FA", "FREE_AGENT", "WAIVERS", "FA_AUCTION", ""}
    ]

    by_team = defaultdict(list)
    for p in rostered:
        by_team[p.get("current_owner")].append(p)

    team_rows = []

    for owner, rows in by_team.items():
        by_pos = defaultdict(list)
        for p in rows:
            by_pos[p.get("pos") or "UNK"].append(p)

        pos_scores = {}
        for pos in ["QB", "RB", "WR", "TE"]:
            top = sorted(by_pos.get(pos, []), key=lambda x: num(x.get("brain_score")), reverse=True)[:4]
            pos_scores[pos] = avg([x.get("brain_score") for x in top])

        strengths = [p for p, s in pos_scores.items() if s >= 58]
        weaknesses = [p for p, s in pos_scores.items() if s < 40]
        surplus = [p for p, s in pos_scores.items() if s >= 55]
        needs = [p for p, s in pos_scores.items() if s < 42]

        win_now = clamp(
            avg([p.get("present_score") for p in rows]) * 0.35
            + avg([p.get("role_score") for p in rows]) * 0.25
            + avg([p.get("situation_score") for p in rows]) * 0.15
            + avg([p.get("brain_score") for p in rows]) * 0.25
        )

        future = clamp(
            avg([p.get("future_score") for p in rows]) * 0.35
            + avg([p.get("dynasty_score") for p in rows]) * 0.25
            + avg([p.get("age_curve_score") for p in rows]) * 0.20
            + avg([p.get("brain_score") for p in rows]) * 0.20
        )

        contract = clamp(avg([p.get("contract_score") for p in rows]))
        risk = clamp(avg([p.get("risk_score") for p in rows]))

        t = {
            "owner_team_name": owner,
            "roster_count": len(rows),
            "cap_used": round(sum(num(p.get("salary")) for p in rows), 2),
            "avg_age_curve_score": avg([p.get("age_curve_score") for p in rows]),
            "avg_brain_score": avg([p.get("brain_score") for p in rows]),
            "total_brain_score": round(sum(num(p.get("brain_score")) for p in rows), 2),

            "qb_score": pos_scores["QB"],
            "rb_score": pos_scores["RB"],
            "wr_score": pos_scores["WR"],
            "te_score": pos_scores["TE"],

            "win_now_score": round(win_now, 2),
            "future_score": round(future, 2),
            "contract_health_score": round(contract, 2),
            "risk_score": round(risk, 2),

            "team_window": team_window(win_now, future, contract, risk),
            "strengths": strengths,
            "weaknesses": weaknesses,
            "surplus_positions": surplus,
            "need_positions": needs,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        t["team_summary"] = build_team_summary(t)
        team_rows.append(t)

    sb.table("team_brain_context").delete().neq("id", 0).execute()
    sb.table("league_brain_context").delete().neq("id", 0).execute()

    if team_rows:
        sb.table("team_brain_context").upsert(
            team_rows,
            on_conflict="owner_team_name",
        ).execute()

    # League context
    def simple_player(p):
        return {
            "player_name": p.get("player_name"),
            "pos": p.get("pos"),
            "current_owner": p.get("current_owner"),
            "salary": p.get("salary"),
            "years": p.get("years"),
            "brain_score": p.get("brain_score"),
            "future_score": p.get("future_score"),
            "contract_score": p.get("contract_score"),
        }

    top_fas = sorted(
        free_agents,
        key=lambda x: num(x.get("brain_score")),
        reverse=True,
    )[:15]

    trade_assets = sorted(
        rostered,
        key=lambda x: (
            num(x.get("future_score")) + num(x.get("brain_score")) + num(x.get("market_score"))
        ),
        reverse=True,
    )[:25]

    pos_scarcity = {}
    for pos in ["QB", "RB", "WR", "TE"]:
        useful = [p for p in rostered if p.get("pos") == pos and num(p.get("brain_score")) >= 50]
        pos_scarcity[pos] = clamp(100 - len(useful) * 7)

    league = {
        "league_key": "default",
        "player_count": len(players),
        "rostered_count": len(rostered),
        "free_agent_count": len(free_agents),
        "avg_salary": avg([p.get("salary") for p in rostered]),
        "avg_contract_score": avg([p.get("contract_score") for p in rostered]),
        "avg_brain_score": avg([p.get("brain_score") for p in rostered]),
        "qb_scarcity_score": pos_scarcity["QB"],
        "rb_scarcity_score": pos_scarcity["RB"],
        "wr_scarcity_score": pos_scarcity["WR"],
        "te_scarcity_score": pos_scarcity["TE"],
        "best_fa_players": [simple_player(p) for p in top_fas],
        "top_trade_assets": [simple_player(p) for p in trade_assets],
        "league_summary": (
            f"League context: {len(rostered)} rostered players, {len(free_agents)} free agents. "
            f"Scarcity QB {pos_scarcity['QB']}, RB {pos_scarcity['RB']}, "
            f"WR {pos_scarcity['WR']}, TE {pos_scarcity['TE']}."
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    sb.table("league_brain_context").upsert(
        [league],
        on_conflict="league_key",
    ).execute()

    print(f"✅ Upserted {len(team_rows)} team_brain_context rows")
    print("✅ Upserted league_brain_context row")


if __name__ == "__main__":
    build_contexts()
