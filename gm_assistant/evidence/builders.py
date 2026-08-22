from __future__ import annotations

from typing import Any

from gm_assistant.executor.models import CapabilityResult
from gm_assistant.production_context import get_production_context


def _safe_build_evidence(question: str, owner_team_name: str) -> dict[str, Any]:
    from gm_assistant.gm_brain import build_evidence

    return build_evidence(question, owner_team_name) or {}


def load_relevant_context(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    evidence = _safe_build_evidence(question, owner_team_name)

    return CapabilityResult(
        name="load_relevant_context",
        success=bool(evidence),
        data=evidence,
        message="Loaded legacy evidence context." if evidence else "No evidence returned.",
    )


def load_user_roster(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    from gm_assistant.gm_brain import _rows_for_owner

    rows = _rows_for_owner(owner_team_name) or []

    return CapabilityResult(
        name="load_user_roster",
        success=bool(rows),
        data=rows,
        message=f"Loaded {len(rows)} roster/player rows." if rows else "No roster rows found.",
    )


def load_player_context(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    evidence = _safe_build_evidence(question, owner_team_name)
    player = evidence.get("player") or {}

    return CapabilityResult(
        name="load_player_context",
        success=bool(player),
        data=player,
        message=f"Loaded player context for {player.get('player_name')}." if player else "No player context found.",
    )


def load_contract_context(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    evidence = _safe_build_evidence(question, owner_team_name)
    player = evidence.get("player") or {}
    facts = evidence.get("facts") or []

    contract_facts = [
        f for f in facts
        if str(f.get("kind", "")).lower() in {
            "contract",
            "contract_efficiency",
            "dead_cap",
            "roster_liability",
        }
    ]

    data = {
        "player": player,
        "contract_facts": contract_facts,
    }

    return CapabilityResult(
        name="load_contract_context",
        success=bool(player or contract_facts),
        data=data,
        message=f"Loaded {len(contract_facts)} contract facts.",
    )


def load_team_fit(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    evidence = _safe_build_evidence(question, owner_team_name)
    facts = evidence.get("facts") or []

    team_facts = [
        f for f in facts
        if str(f.get("kind", "")).lower() in {
            "team_window",
            "team_need",
            "roster_liability",
            "team_strength",
            "team_weakness",
        }
    ]

    return CapabilityResult(
        name="load_team_fit",
        success=bool(team_facts or evidence),
        data={"facts": team_facts, "raw_evidence": evidence},
        message=f"Loaded {len(team_facts)} team-fit facts.",
    )


def load_contracts(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    from gm_assistant.gm_brain import _rows_for_owner

    rows = _rows_for_owner(owner_team_name) or []
    contracts = [
        r for r in rows
        if float(r.get("salary") or 0) > 0
    ]

    return CapabilityResult(
        name="load_contracts",
        success=bool(contracts),
        data=contracts,
        message=f"Loaded {len(contracts)} contracts.",
    )


def calculate_points_per_dollar(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    from gm_assistant.gm_brain import _rows_for_owner

    rows = _rows_for_owner(owner_team_name) or []
    scored = []

    for r in rows:
        salary = float(r.get("salary") or 0)
        ppg = float(get_production_context(r).get("primary_ppg") or 0)

        if salary <= 0:
            continue

        scored.append({
            "player": r.get("player_name"),
            "pos": r.get("pos"),
            "team": r.get("owner_team_name") or r.get("current_owner"),
            "salary": salary,
            "years": float(r.get("years") or 0),
            "ppg": ppg,
            "points_per_dollar": round(ppg / salary, 3) if salary else 0,
            "contract_score": float(r.get("contract_efficiency_score") or 0),
        })

    scored = sorted(
        scored,
        key=lambda x: (x["points_per_dollar"], x["ppg"]),
        reverse=True,
    )

    return CapabilityResult(
        name="calculate_points_per_dollar",
        success=bool(scored),
        data=scored,
        message=f"Calculated points per dollar for {len(scored)} players.",
    )


def identify_team_needs(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    evidence = _safe_build_evidence("what is my biggest weakness?", owner_team_name)
    facts = evidence.get("facts") or []

    needs = []
    for f in facts:
        text = str(f.get("text") or "")
        data = f.get("data") or {}
        if data.get("pos"):
            needs.append(data.get("pos"))
        elif "RB" in text:
            needs.append("RB")
        elif "TE" in text:
            needs.append("TE")

    needs = list(dict.fromkeys(needs)) or ["RB", "TE"]

    return CapabilityResult(
        name="identify_team_needs",
        success=True,
        data=needs,
        message=f"Identified needs: {', '.join(needs)}.",
    )


def load_league_players(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    from auth import service_client

    sb = service_client()
    rows = (
        sb.table("player_universe")
        .select("*")
        .limit(2000)
        .execute()
        .data
        or []
    )

    return CapabilityResult(
        name="load_league_players",
        success=bool(rows),
        data=rows,
        message=f"Loaded {len(rows)} league/player universe rows.",
    )


def load_available_or_trade_targets(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    from auth import service_client

    sb = service_client()
    rows = (
        sb.table("player_universe")
        .select("*")
        .in_("pos", ["RB", "TE", "WR", "QB"])
        .limit(2000)
        .execute()
        .data
        or []
    )

    # Keep non-owned players and FAs first
    historical_names = {
        "Tiki Barber",
        "Arian Foster",
        "LaDainian Tomlinson",
        "Priest Holmes",
        "Shaun Alexander",
        "Jamaal Charles",
        "Marshall Faulk",
        "Emmitt Smith",
        "Barry Sanders",
    }

    targets = [
        r for r in rows
        if r.get("owner_team_name") != owner_team_name
        and r.get("current_owner") != owner_team_name
        and (r.get("player_name") not in historical_names)
        and not r.get("retired")
        and not r.get("is_retired")
        and not r.get("is_historical")
        and str(r.get("status") or "").lower() not in {"retired", "inactive", "historical"}
        and (
            r.get("current_owner")
            or r.get("owner_team_name")
            or str(r.get("market_pool") or "").upper() in {"FA", "FREE_AGENT", "WAIVERS"}
        )
        and not (
            str(r.get("current_owner") or "").upper() in {"FA", "FREE_AGENT"}
            and not r.get("nfl_team")
            and not r.get("team")
        )
    ]

    return CapabilityResult(
        name="load_available_or_trade_targets",
        success=bool(targets),
        data=targets,
        message=f"Loaded {len(targets)} available/trade target rows.",
    )


def load_team_scores(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    evidence = _safe_build_evidence("how does my team look?", owner_team_name)
    return CapabilityResult(
        name="load_team_scores",
        success=bool(evidence),
        data=evidence,
        message="Loaded team score evidence." if evidence else "No team score evidence found.",
    )


def identify_strengths_and_weaknesses(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    evidence = _safe_build_evidence("how does my team look?", owner_team_name)
    facts = evidence.get("facts") or []
    return CapabilityResult(
        name="identify_strengths_and_weaknesses",
        success=bool(facts),
        data=facts,
        message=f"Loaded {len(facts)} strength/weakness facts.",
    )


def rank_actionable_moves(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    evidence = _safe_build_evidence("what is the one move I should make first?", owner_team_name)
    return CapabilityResult(
        name="rank_actionable_moves",
        success=bool(evidence),
        data=evidence,
        message="Loaded actionable move evidence." if evidence else "No actionable move evidence found.",
    )


def score_fit(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    from gm_assistant.nlu.parser import parse_gm_question
    from gm_assistant.evidence.builders import load_available_or_trade_targets, identify_team_needs

    parsed = parse_gm_question(question)
    positions = parsed.positions or ["RB", "TE"]
    needs = identify_team_needs(question=question, owner_team_name=owner_team_name).data or []

    target_result = load_available_or_trade_targets(
        question=question,
        owner_team_name=owner_team_name,
    )
    targets = target_result.data or []

    scored = []

    for r in targets:
        pos = r.get("pos")
        if pos not in positions:
            continue

        ppg = float(get_production_context(r).get("primary_ppg") or 0)
        salary = float(r.get("salary") or 0)
        years = float(r.get("years") or 0)
        contract = float(r.get("contract_efficiency_score") or 0)
        dynasty = float(r.get("dynasty_asset_score") or 0)
        trade_value = float(r.get("trade_value_score") or dynasty or 0)

        need_bonus = 12 if pos in needs else 0
        win_now_score = ppg * 4
        contract_score = contract * 0.25
        affordability = max(0, 20 - salary) * 0.5
        gettable_bonus = 8 if salary <= 12 else 0

        fit_score = (
            win_now_score
            + contract_score
            + affordability
            + gettable_bonus
            + need_bonus
            - max(0, trade_value - 70) * 0.15
        )

        scored.append({
            "player": r.get("player_name"),
            "pos": pos,
            "owner": r.get("owner_team_name") or r.get("current_owner") or "FA",
            "nfl_team": r.get("nfl_team"),
            "salary": salary,
            "years": years,
            "ppg": ppg,
            "contract_score": contract,
            "dynasty_score": dynasty,
            "trade_value": trade_value,
            "fit_score": round(fit_score, 2),
            "why": _target_why(pos, ppg, salary, years, contract, owner_team_name),
        })

    scored = sorted(scored, key=lambda x: x["fit_score"], reverse=True)

    return CapabilityResult(
        name="score_fit",
        success=bool(scored),
        data=scored,
        message=f"Scored {len(scored)} target fits.",
    )


def rank_targets(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    scored = score_fit(question=question, owner_team_name=owner_team_name).data or []
    top = scored[:5]

    return CapabilityResult(
        name="rank_targets",
        success=bool(top),
        data=top,
        message=f"Ranked top {len(top)} targets.",
    )


def _target_why(pos: str, ppg: float, salary: float, years: float, contract: float, owner_team_name: str) -> str:
    reasons = []

    if ppg >= 13:
        reasons.append("real weekly starter profile")
    elif ppg >= 9:
        reasons.append("usable weekly depth/flex profile")
    else:
        reasons.append("speculative role/upside profile")

    if salary <= 8:
        reasons.append("manageable salary")
    elif salary <= 15:
        reasons.append("acceptable salary if role is real")
    else:
        reasons.append("expensive enough that price matters")

    if contract >= 75:
        reasons.append("strong contract efficiency")
    elif contract >= 50:
        reasons.append("neutral contract efficiency")
    else:
        reasons.append("contract is not the selling point")

    return ", ".join(reasons)


def rank_contract_values(*, question: str, owner_team_name: str, params=None) -> CapabilityResult:
    scored = calculate_points_per_dollar(
        question=question,
        owner_team_name=owner_team_name,
    ).data or []

    # Player rankings by position
    by_pos = {}
    for r in scored:
        pos = r.get("pos") or "UNK"
        by_pos.setdefault(pos, []).append(r)

    top_by_pos = {
        pos: sorted(players, key=lambda x: (x["points_per_dollar"], x["ppg"]), reverse=True)[:2]
        for pos, players in by_pos.items()
    }

    # Team rankings by aggregate usable value
    teams = {}
    for r in scored:
        team = r.get("team") or "Unknown"
        teams.setdefault(team, {
            "team": team,
            "total_ppg": 0.0,
            "total_salary": 0.0,
            "players": 0,
        })
        teams[team]["total_ppg"] += float(r.get("ppg") or 0)
        teams[team]["total_salary"] += float(r.get("salary") or 0)
        teams[team]["players"] += 1

    team_rankings = []
    for t in teams.values():
        salary = t["total_salary"] or 1
        t["points_per_dollar"] = round(t["total_ppg"] / salary, 3)
        t["total_ppg"] = round(t["total_ppg"], 2)
        t["total_salary"] = round(t["total_salary"], 2)
        team_rankings.append(t)

    team_rankings = sorted(
        team_rankings,
        key=lambda x: (x["points_per_dollar"], x["total_ppg"]),
        reverse=True,
    )

    data = {
        "top_players_by_position": top_by_pos,
        "team_rankings": team_rankings[:10],
        "all_players": scored,
    }

    return CapabilityResult(
        name="rank_contract_values",
        success=bool(scored),
        data=data,
        message=f"Ranked contract values for {len(scored)} players and {len(team_rankings)} teams.",
    )
