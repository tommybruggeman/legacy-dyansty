from __future__ import annotations

import re
from typing import Any

from auth import service_client


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _find_player(name: str) -> dict[str, Any] | None:
    sb = service_client()

    rows = (
        sb.table("player_universe")
        .select("*")
        .ilike("player_name", f"%{name}%")
        .limit(10)
        .execute()
        .data
        or []
    )

    if not rows:
        return None

    rows = sorted(
        rows,
        key=lambda r: (
            1 if r.get("has_contract") else 0,
            1 if r.get("current_owner") else 0,
            _num(r.get("dynasty_asset_score")),
            _num(r.get("contract_efficiency_score")),
        ),
        reverse=True,
    )

    return rows[0]


def _score_player(p: dict[str, Any], team_goal: str | None = None) -> float:
    dynasty = _num(p.get("dynasty_asset_score"))
    contract = _num(p.get("contract_efficiency_score"))
    ppg = _num(p.get("expected_ppg") or p.get("season_ppg"))
    salary = _num(p.get("salary"))
    years = _num(p.get("years"))

    base = dynasty * 0.45 + contract * 0.3 + min(ppg * 4, 100) * 0.25

    if team_goal == "contend":
        base += min(ppg * 2, 20)

    if team_goal == "rebuild":
        if years >= 2:
            base += 5
        if salary > 30:
            base -= 8

    if salary > 35 and contract < 35:
        base -= 8

    return round(base, 1)


def evaluate_trade_scenario(
    owner_team_name: str,
    give_player: str,
    receive_player: str,
    team_goal: str | None = None,
) -> dict[str, Any]:
    give = _find_player(give_player)
    receive = _find_player(receive_player)

    if not give or not receive:
        return {
            "answer_type": "trade_scenario",
            "owner_team_name": owner_team_name,
            "summary": f"I could not find enough data to compare {give_player} and {receive_player}.",
            "found": {
                "give": bool(give),
                "receive": bool(receive),
            },
        }

    give_score = _score_player(give, team_goal)
    receive_score = _score_player(receive, team_goal)
    delta = round(receive_score - give_score, 1)

    give_salary = _num(give.get("salary"))
    receive_salary = _num(receive.get("salary"))
    cap_delta = round(receive_salary - give_salary, 1)

    recommendation = "DECLINE"
    if delta >= 8:
        recommendation = "ACCEPT"
    elif delta >= 2:
        recommendation = "LEAN ACCEPT"
    elif delta > -4:
        recommendation = "FAIR / DEPENDS"
    elif delta > -10:
        recommendation = "LEAN DECLINE"

    summary = (
        f"Trade scenario for **{owner_team_name}**:\n\n"
        f"Give: **{give.get('player_name')}** ({give.get('pos')}) — "
        f"${give_salary:g}/{_num(give.get('years')):g} yrs, "
        f"dynasty {_num(give.get('dynasty_asset_score')):.1f}, "
        f"contract {_num(give.get('contract_efficiency_score')):.1f}, "
        f"expected PPG {_num(give.get('expected_ppg') or give.get('season_ppg')):.1f}.\n\n"
        f"Receive: **{receive.get('player_name')}** ({receive.get('pos')}) — "
        f"${receive_salary:g}/{_num(receive.get('years')):g} yrs, "
        f"dynasty {_num(receive.get('dynasty_asset_score')):.1f}, "
        f"contract {_num(receive.get('contract_efficiency_score')):.1f}, "
        f"expected PPG {_num(receive.get('expected_ppg') or receive.get('season_ppg')):.1f}.\n\n"
        f"Team goal: **{team_goal or 'unspecified'}**.\n\n"
        f"Internal trade score: receive side {receive_score}, give side {give_score}, delta {delta:+}.\n"
        f"Cap change: {cap_delta:+}.\n\n"
        f"My move: **{recommendation}**."
    )

    if recommendation in {"ACCEPT", "LEAN ACCEPT"}:
        summary += " The incoming side gives you enough contract-adjusted value to justify moving the outgoing player."
    elif recommendation in {"DECLINE", "LEAN DECLINE"}:
        summary += " The outgoing player is worth more than the return once contract, production, and roster context are included."
    else:
        summary += " This is close enough that roster construction, cap pressure, and replacement options should decide it."

    return {
        "answer_type": "trade_scenario",
        "owner_team_name": owner_team_name,
        "give": give,
        "receive": receive,
        "team_goal": team_goal,
        "give_score": give_score,
        "receive_score": receive_score,
        "delta": delta,
        "cap_delta": cap_delta,
        "recommendation": recommendation,
        "summary": summary,
    }


def parse_trade_scenario(question: str) -> tuple[str, str] | None:
    q = question.lower()

    m = re.search(r"give ([a-z .'-]+?), receive ([a-z .'-]+?)(?:\.|$)", q)
    if m:
        return m.group(1).strip().title(), m.group(2).strip().title()

    m = re.search(r"give ([a-z .'-]+) receive ([a-z .'-]+)", q)
    if m:
        return m.group(1).strip().title(), m.group(2).strip().title()

    return None
