from __future__ import annotations

from collections import defaultdict


def _num(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _money(value) -> str:
    return f"${_num(value):g}"


def _owner(row: dict) -> str:
    return (
        row.get("owner_team_name")
        or row.get("owner")
        or row.get("team_name")
        or "Unknown"
    )


def _pos(row: dict) -> str:
    return str(row.get("pos") or "UNK").upper()


def _score(row: dict, col: str) -> float:
    return _num(row.get(col), 0)


def _top(rows: list[dict], col: str, limit=5, reverse=True) -> list[dict]:
    return sorted(rows, key=lambda r: _score(r, col), reverse=reverse)[:limit]


def build_league_market_intelligence(rows: list[dict], my_team: str | None = None) -> dict:
    rows = rows or []

    if not rows:
        return {
            "status": "empty",
            "summary": "No league roster asset data available.",
            "teams": {},
            "market": {},
            "trade_partners": [],
        }

    teams = defaultdict(list)
    for r in rows:
        owner = _owner(r)
        salary = _num(r.get("salary"))
        asset = _num(r.get("asset_value_score"))

        # Skip ghost/unassigned rows with no meaningful salary or value.
        if owner == "Unknown":
            continue
        if salary <= 0 and asset <= 0:
            continue

        teams[owner].append(r)

    # Drop likely ghost teams / duplicate import shells.
    teams = {
        team: players
        for team, players in teams.items()
        if len(players) >= 8 and sum(_num(p.get("salary")) for p in players) > 0
    }

    team_reports = {}

    for team, players in teams.items():
        by_pos = defaultdict(list)
        for p in players:
            by_pos[_pos(p)].append(p)

        total_salary = sum(_num(p.get("salary")) for p in players)
        total_asset = sum(_num(p.get("asset_value_score")) for p in players)
        avg_asset = total_asset / max(len(players), 1)

        core_assets = _top(players, "asset_value_score", 5)
        weak_assets = _top(players, "asset_value_score", 5, reverse=False)

        pos_needs = []
        pos_surplus = []

        for pos in ["QB", "RB", "WR", "TE"]:
            count = len(by_pos.get(pos, []))
            avg_pos_asset = (
                sum(_num(p.get("asset_value_score")) for p in by_pos.get(pos, []))
                / max(count, 1)
            )

            if pos == "QB" and count < 3:
                pos_needs.append("QB")
            elif pos == "WR" and count < 5:
                pos_needs.append("WR")
            elif pos == "TE" and count < 2:
                pos_needs.append("TE")
            elif pos == "RB" and count < 4:
                pos_needs.append("RB")

            if pos == "QB" and count >= 4:
                pos_surplus.append("QB")
            elif pos == "RB" and count >= 7:
                pos_surplus.append("RB")
            elif pos == "WR" and count >= 8:
                pos_surplus.append("WR")
            elif pos == "TE" and count >= 3:
                pos_surplus.append("TE")

            if count >= 3 and avg_pos_asset < 35:
                pos_needs.append(f"{pos}_QUALITY")

        contender_score = (
            avg_asset * 0.7
            + len([p for p in players if _num(p.get("asset_value_score")) >= 55]) * 4
            - len([p for p in players if _num(p.get("age")) >= 30 and _pos(p) != "QB"]) * 1.5
        )

        if contender_score >= 47:
            window = "CONTENDER"
        elif contender_score >= 38:
            window = "MIDDLE"
        else:
            window = "REBUILDER"

        team_reports[team] = {
            "team": team,
            "roster_size": len(players),
            "total_salary": round(total_salary, 2),
            "avg_asset_value": round(avg_asset, 2),
            "contender_score": round(contender_score, 2),
            "window": window,
            "needs": sorted(set(pos_needs)),
            "surplus": sorted(set(pos_surplus)),
            "core_assets": [
                p.get("player_name", "Unknown")
                for p in core_assets
            ],
            "weak_assets": [
                p.get("player_name", "Unknown")
                for p in weak_assets
            ],
            "players": players,
        }

    market = {
        "contenders": [
            t for t in team_reports.values()
            if t["window"] == "CONTENDER"
        ],
        "middle": [
            t for t in team_reports.values()
            if t["window"] == "MIDDLE"
        ],
        "rebuilders": [
            t for t in team_reports.values()
            if t["window"] == "REBUILDER"
        ],
    }

    trade_partners = []

    if my_team and my_team in team_reports:
        mine = team_reports[my_team]
        my_surplus = set(mine.get("surplus", []))
        my_needs = set(mine.get("needs", []))

        for team, report in team_reports.items():
            if team == my_team:
                continue

            their_needs = set(report.get("needs", []))
            their_surplus = set(report.get("surplus", []))

            fit_reasons = []

            if my_surplus & their_needs:
                fit_reasons.append(
                    "They need a position where you may have surplus: "
                    + ", ".join(sorted(my_surplus & their_needs))
                )

            if my_needs & their_surplus:
                fit_reasons.append(
                    "They may have surplus at a position you need: "
                    + ", ".join(sorted(my_needs & their_surplus))
                )

            if mine["window"] == "CONTENDER" and report["window"] == "REBUILDER":
                fit_reasons.append("Classic contender/rebuilder trade fit.")

            if mine["window"] == "REBUILDER" and report["window"] == "CONTENDER":
                fit_reasons.append("They may pay for veteran production or depth.")

            fit_score = len(fit_reasons) * 25

            if report["window"] != mine["window"]:
                fit_score += 10

            if fit_score > 0:
                trade_partners.append({
                    "team": team,
                    "fit_score": fit_score,
                    "their_window": report["window"],
                    "their_needs": report["needs"],
                    "their_surplus": report["surplus"],
                    "reasons": fit_reasons,
                    "their_core_assets": report["core_assets"],
                    "their_weak_assets": report["weak_assets"],
                })

        trade_partners = sorted(
            trade_partners,
            key=lambda r: r["fit_score"],
            reverse=True,
        )

    return {
        "status": "ok",
        "teams": team_reports,
        "market": market,
        "trade_partners": trade_partners,
    }


def format_league_market_report(report: dict, my_team: str | None = None) -> str:
    if report.get("status") != "ok":
        return report.get("summary", "No league market report available.")

    market = report.get("market", {})

    lines = []
    lines.append("## League Market Intelligence")
    lines.append("")

    lines.append(
        f"Market shape: **{len(market.get('contenders', []))} contenders**, "
        f"**{len(market.get('middle', []))} middle teams**, "
        f"and **{len(market.get('rebuilders', []))} rebuilders**."
    )

    lines.append("")
    lines.append("### Team windows")

    teams = report.get("teams", {})
    ordered = sorted(
        teams.values(),
        key=lambda t: t.get("contender_score", 0),
        reverse=True,
    )

    for t in ordered:
        lines.append(
            f"- **{t['team']}**: {t['window']} "
            f"(score {t['contender_score']}, avg asset {t['avg_asset_value']}, salary {_money(t['total_salary'])})"
        )
        if t.get("needs"):
            lines.append(f"  - Needs: {', '.join(t['needs'])}")
        if t.get("surplus"):
            lines.append(f"  - Surplus: {', '.join(t['surplus'])}")

    partners = report.get("trade_partners", [])

    if my_team:
        lines.append("")
        lines.append(f"### Best trade partner fits for {my_team}")

        if not partners:
            lines.append("- No obvious trade partner fit detected yet.")
        else:
            for p in partners[:6]:
                lines.append(
                    f"- **{p['team']}** — fit score {p['fit_score']} "
                    f"({p['their_window']})"
                )
                for reason in p.get("reasons", []):
                    lines.append(f"  - {reason}")

    return "\n".join(lines)
