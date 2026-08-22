from __future__ import annotations

from typing import Any
import re

from auth import service_client
from gm_assistant.engines.gm_intent_engine import classify_gm_intent


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _clean(s: str | None) -> str:
    return str(s or "").strip()


def _extract_player_name(question: str) -> str | None:
    q = _clean(question)

    patterns = [
        r"should i (?:trade|cut|drop|sell|hold|keep|move|move on from|shop)\s+(.+)",
        r"would you (?:trade|cut|drop|sell|hold|keep|move|move on from|shop)\s+(.+)",
        r"what should i do with\s+(.+)",
        r"is\s+(.+?)\s+(?:hurting|killing|worth|a hold|a sell|a cut)",
    ]

    for pat in patterns:
        m = re.search(pat, q, flags=re.I)
        if m:
            return m.group(1).strip(" ?.!")

    return None


def _intent(question: str) -> str:
    return classify_gm_intent(question)


def _player_row(owner_team_name: str, player_name: str | None) -> dict[str, Any] | None:
    if not player_name:
        return None

    sb = service_client()

    rows = (
        sb.table("player_graph")
        .select("*")
        .eq("current_owner", owner_team_name)
        .ilike("player_name", f"%{player_name}%")
        .limit(10)
        .execute()
        .data
        or []
    )

    if not rows:
        rows = (
            sb.table("player_graph")
            .select("*")
            .ilike("player_name", f"%{player_name}%")
            .limit(10)
            .execute()
            .data
            or []
        )

    if not rows:
        return None

    return sorted(
        rows,
        key=lambda r: (
            _num(r.get("dynasty_asset_score")),
            _num(r.get("contract_efficiency_score")),
            _num(r.get("expected_ppg") or r.get("season_ppg")),
        ),
        reverse=True,
    )[0]


def _team_future(owner_team_name: str) -> dict[str, Any]:
    sb = service_client()
    rows = (
        sb.table("team_future_context")
        .select("*")
        .eq("owner_team_name", owner_team_name)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else {}


def _team_needs(owner_team_name: str) -> dict[str, Any]:
    sb = service_client()

    candidate_tables = [
        "team_future_context",
        "team_scheme_context",
        "team_window_scores",
    ]

    for table in candidate_tables:
        try:
            rows = (
                sb.table(table)
                .select("*")
                .eq("owner_team_name", owner_team_name)
                .limit(1)
                .execute()
                .data
                or []
            )
            if rows:
                return rows[0]
        except Exception:
            continue

    return {}


def _roster_liability_facts(owner_team_name: str, limit: int = 5) -> list[dict[str, Any]]:
    sb = service_client()

    try:
        rows = (
            sb.table("player_graph")
            .select("*")
            .eq("current_owner", owner_team_name)
            .execute()
            .data
            or []
        )
    except Exception:
        return []

    scored = []

    for p in rows:
        name = p.get("player_name")
        pos = p.get("pos")
        salary = _num(p.get("salary"))
        years = _num(p.get("years"))
        contract = _num(p.get("contract_efficiency_score"))
        dynasty = _num(p.get("dynasty_asset_score"))
        ppg = _num(p.get("expected_ppg") or p.get("season_ppg"))

        liability = 0

        # Salary only becomes a problem when efficiency/production does not justify it.
        if salary >= 30 and contract < 50:
            liability += 25
        elif salary >= 20 and contract < 40:
            liability += 16
        elif salary >= 10 and contract < 30:
            liability += 8

        # Multi-year commitments are risky only when the contract is inefficient.
        if years >= 3 and contract < 45:
            liability += 15
        elif years >= 2 and contract < 35:
            liability += 8

        # Core contract inefficiency signal.
        if contract < 20:
            liability += 28
        elif contract < 35:
            liability += 16
        elif contract < 50:
            liability += 6

        # Low production on meaningful salary is a real roster drag.
        if ppg < 8 and salary >= 10:
            liability += 18
        elif ppg < 5 and salary >= 5:
            liability += 10

        # Protect elite efficient anchors from being treated as liabilities.
        if dynasty >= 70 and contract >= 60 and ppg >= 18:
            liability -= 35
        elif dynasty >= 60 and contract >= 55:
            liability -= 20

        liability = max(liability, 0)

        if liability >= 45 and dynasty >= 50:
            action = "shop first, do not cut"
        elif liability >= 45:
            action = "market-check, restructure, or churn"
        elif salary <= 5 and ppg < 6 and dynasty < 35:
            action = "churn candidate"
        elif liability >= 25:
            action = "monitor or market-check"
        else:
            action = "not a priority"

        scored.append({
            "player": name,
            "pos": pos,
            "salary": salary,
            "years": years,
            "contract": contract,
            "dynasty": dynasty,
            "ppg": ppg,
            "liability": liability,
            "action": action,
        })

    scored = sorted(scored, key=lambda x: x["liability"], reverse=True)[:limit]

    facts = []
    for r in scored:
        facts.append(_fact(
            "roster_liability",
            f"{r['player']} is a roster pressure point: ${r['salary']:g}/{r['years']:g} yrs, contract {r['contract']:.1f}, dynasty {r['dynasty']:.1f}, PPG {r['ppg']:.1f}; suggested action: {r['action']}.",
            0.88,
            r,
        ))

    return facts


def _fact(kind: str, text: str, importance: float = 0.5, data: dict[str, Any] | None = None):
    return {
        "kind": kind,
        "importance": round(float(importance), 2),
        "text": text,
        "data": data or {},
    }


def build_evidence(question: str, owner_team_name: str) -> dict[str, Any]:
    intent = _intent(question)
    player_name = _extract_player_name(question)
    player = _player_row(owner_team_name, player_name)
    future = _team_future(owner_team_name)
    needs = _team_needs(owner_team_name)

    facts: list[dict[str, Any]] = []

    if intent in {"next_move", "roster_liability", "team_overview", "general_gm_question"}:
        facts.extend(_roster_liability_facts(owner_team_name, limit=5))

    if player:
        name = player.get("player_name")
        pos = player.get("pos")
        salary = _num(player.get("salary"))
        years = _num(player.get("years"))
        dynasty = _num(player.get("dynasty_asset_score"))
        contract = _num(player.get("contract_efficiency_score"))
        ppg = _num(player.get("expected_ppg") or player.get("season_ppg"))
        trade = _num(
            player.get("trade_value_score")
            or player.get("market_value_score")
            or player.get("dynasty_trade_value")
            or player.get("dynasty_asset_score")
        )
        age = _num(player.get("age_curve_score"))

        facts.append(_fact(
            "player_identity",
            f"{name} is a {pos} on {owner_team_name}.",
            0.75,
            {"player": name, "pos": pos},
        ))

        facts.append(_fact(
            "contract",
            f"{name} is on a ${salary:g} salary with {years:g} years left.",
            0.9,
            {"salary": salary, "years": years},
        ))

        facts.append(_fact(
            "contract_efficiency",
            f"Contract efficiency is {contract:.1f}.",
            0.92 if contract < 35 else 0.65,
            {"contract_efficiency_score": contract},
        ))

        facts.append(_fact(
            "dynasty_value",
            f"Dynasty asset score is {dynasty:.1f}.",
            0.9 if dynasty >= 50 else 0.7,
            {"dynasty_asset_score": dynasty},
        ))

        facts.append(_fact(
            "production",
            f"Expected or recent PPG is {ppg:.1f}.",
            0.8,
            {"ppg": ppg},
        ))

        facts.append(_fact(
            "market_value",
            f"Trade value score is {trade:.1f}.",
            0.75,
            {"trade_value_score": trade},
        ))

        facts.append(_fact(
            "development",
            f"Age/development curve score is {age:.1f}.",
            0.55,
            {"age_curve_score": age},
        ))

        dead_cap_total = round(salary * years * 0.5, 1)
        facts.append(_fact(
            "dead_cap",
            f"Estimated total dead-cap exposure if cut is about ${dead_cap_total:g}.",
            0.95 if salary >= 20 or years >= 2 else 0.65,
            {"dead_cap_total": dead_cap_total},
        ))

    if future:
        window = future.get("team_window")
        score = future.get("future_score")
        grade = future.get("future_grade")

        facts.append(_fact(
            "team_window",
            f"Team future context is {window} ({grade}, score {score}).",
            0.85,
            future,
        ))
    else:
        facts.append(_fact(
            "team_window",
            "No team future context is available yet.",
            0.4,
            {},
        ))

    if needs:
        weaknesses = needs.get("league_relative_weaknesses") or needs.get("weaknesses")
        roster_needs = needs.get("roster_needs") or needs.get("needs")

        if weaknesses:
            facts.append(_fact(
                "team_weakness",
                f"League-relative weaknesses: {weaknesses}.",
                0.8,
                {"weaknesses": weaknesses},
            ))

        if roster_needs:
            facts.append(_fact(
                "team_need",
                f"Roster needs: {roster_needs}.",
                0.8,
                {"needs": roster_needs},
            ))

    facts = sorted(facts, key=lambda f: f["importance"], reverse=True)

    return {
        "answer_type": "evidence",
        "intent": intent,
        "question": question,
        "owner_team_name": owner_team_name,
        "player_name": player.get("player_name") if player else player_name,
        "player": player,
        "team_future": future,
        "team_needs": needs,
        "facts": facts,
    }
