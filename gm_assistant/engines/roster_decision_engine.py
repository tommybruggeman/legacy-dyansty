from __future__ import annotations

from typing import Any

from auth import service_client


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _owned_players(owner_team_name: str) -> list[dict[str, Any]]:
    sb = service_client()

    rows = (
        sb.table("player_universe")
        .select("*")
        .eq("current_owner", owner_team_name)
        .execute()
        .data
        or []
    )

    return rows


def _player_value_score(p: dict[str, Any]) -> float:
    dynasty = _num(p.get("dynasty_asset_score"))
    contract = _num(p.get("contract_efficiency_score"))
    ppg = _num(p.get("expected_ppg") or p.get("season_ppg"))
    salary = _num(p.get("salary"))

    score = dynasty * 0.35 + contract * 0.35 + min(ppg * 4, 100) * 0.3

    if salary >= 30 and contract < 30:
        score -= 12
    elif salary >= 20 and contract < 35:
        score -= 6

    return round(score, 1)


def roster_liability_report(owner_team_name: str, limit: int = 6) -> dict[str, Any]:
    players = _owned_players(owner_team_name)

    ranked = []

    for p in players:
        salary = _num(p.get("salary"))
        years = _num(p.get("years"))
        contract = _num(p.get("contract_efficiency_score"))
        dynasty = _num(p.get("dynasty_asset_score"))
        ppg = _num(p.get("expected_ppg") or p.get("season_ppg"))
        value_score = _player_value_score(p)

        liability = 100 - value_score

        if salary >= 30:
            liability += 10
        if years >= 3 and contract < 35:
            liability += 10
        if ppg < 8 and salary >= 10:
            liability += 8
        if contract <= 20:
            liability += 8

        ranked.append({
            "player": p.get("player_name"),
            "pos": p.get("pos"),
            "salary": salary,
            "years": years,
            "contract": round(contract, 1),
            "dynasty": round(dynasty, 1),
            "ppg": round(ppg, 1),
            "liability_score": round(liability, 1),
            "action": _liability_action(salary, years, contract, dynasty, ppg),
        })

    ranked = sorted(ranked, key=lambda x: x["liability_score"], reverse=True)[:limit]

    lines = [f"Players hurting **{owner_team_name}** most right now:\n"]

    for i, r in enumerate(ranked, 1):
        lines.append(
            f"{i}. **{r['player']}** ({r['pos']}) — "
            f"${r['salary']:g}/{r['years']:g} yrs, "
            f"contract {r['contract']}, dynasty {r['dynasty']}, PPG {r['ppg']} → "
            f"**{r['action']}**"
        )

    lines.append(
        "\nMy read: start with the expensive/low-efficiency contracts first. "
        "Do not cut real asset value just because the contract is annoying; shop those players before taking a dead-cap hit."
    )

    return {
        "answer_type": "roster_liability",
        "owner_team_name": owner_team_name,
        "players": ranked,
        "summary": "\n".join(lines),
    }


def _liability_action(salary: float, years: float, contract: float, dynasty: float, ppg: float) -> str:
    if salary >= 30 and contract < 25 and dynasty >= 50:
        return "SHOP, DO NOT CUT"
    if salary >= 20 and contract < 25:
        return "MARKET-CHECK"
    if salary <= 5 and ppg < 5 and dynasty < 20:
        return "CHURN / REPLACE"
    if contract < 20 and years >= 2:
        return "SHOP OR RESTRUCTURE"
    return "MONITOR"


def cut_decision(owner_team_name: str, player_name: str, team_goal: str | None = None) -> dict[str, Any]:
    sb = service_client()

    rows = (
        sb.table("player_universe")
        .select("*")
        .eq("current_owner", owner_team_name)
        .ilike("player_name", f"%{player_name}%")
        .limit(5)
        .execute()
        .data
        or []
    )

    if not rows:
        return {
            "answer_type": "cut_decision",
            "owner_team_name": owner_team_name,
            "summary": f"I could not find {player_name} on {owner_team_name}'s roster.",
        }

    p = sorted(
        rows,
        key=lambda r: (
            _num(r.get("dynasty_asset_score")),
            _num(r.get("contract_efficiency_score")),
        ),
        reverse=True,
    )[0]

    salary = _num(p.get("salary"))
    years = _num(p.get("years"))
    contract = _num(p.get("contract_efficiency_score"))
    dynasty = _num(p.get("dynasty_asset_score"))
    ppg = _num(p.get("expected_ppg") or p.get("season_ppg"))

    dead_cap_est = round(salary * years * 0.5, 1)
    asset_score = dynasty + min(ppg * 3, 40)

    if asset_score >= 70:
        rec = "KEEP OR SHOP — DO NOT CUT"
    elif salary >= 25 and contract < 25 and dynasty >= 40:
        rec = "SHOP FIRST — CUT ONLY AS LAST RESORT"
    elif salary <= 5 and dynasty < 20 and ppg < 6:
        rec = "CUT / CHURN"
    else:
        rec = "HOLD WHILE MARKET-CHECKING"

    summary = (
        f"On **{p.get('player_name')}**, I would choose: **{rec}**.\n\n"
        f"Why: he is at **${salary:g}** with **{years:g} years** left. "
        f"Contract efficiency is **{contract:.1f}**, dynasty score is **{dynasty:.1f}**, "
        f"and expected PPG is **{ppg:.1f}**.\n\n"
        f"Estimated dead-cap pain if cut: about **${dead_cap_est:g}** before any league-specific nuance.\n\n"
        f"My GM answer: if a player still has name value or dynasty value, do **not** drop him just because the contract is bad. "
        f"Shop him first, try to convert him into RB/TE help or cap flexibility, and only cut if the market is dead and the roster spot is more valuable than the asset."
    )

    return {
        "answer_type": "cut_decision",
        "owner_team_name": owner_team_name,
        "player": p,
        "recommendation": rec,
        "dead_cap_estimate": dead_cap_est,
        "summary": summary,
    }


def next_move(owner_team_name: str, team_goal: str | None = None) -> dict[str, Any]:
    liabilities = roster_liability_report(owner_team_name, limit=5)["players"]

    first = liabilities[0] if liabilities else None

    if not first:
        return {
            "answer_type": "next_move",
            "owner_team_name": owner_team_name,
            "summary": "I do not see enough roster data to give a clean next move.",
        }

    summary = (
        f"Your next move should be to **market-check {first['player']}**.\n\n"
        f"Reason: this is one of your clearest contract/value pressure points: "
        f"${first['salary']:g}/{first['years']:g} yrs, contract {first['contract']}, "
        f"dynasty {first['dynasty']}, PPG {first['ppg']}.\n\n"
        f"The goal is not to dump him. The goal is to see whether another manager still prices the name above your internal contract-adjusted value.\n\n"
        f"Target outcome: turn that value into **RB/TE help, cheaper production, or cap flexibility**."
    )

    return {
        "answer_type": "next_move",
        "owner_team_name": owner_team_name,
        "player": first,
        "summary": summary,
    }
