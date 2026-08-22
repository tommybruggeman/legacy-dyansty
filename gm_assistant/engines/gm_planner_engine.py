from __future__ import annotations

import re
from typing import Any

from auth import service_client
from gm_assistant.engines.gm_reasoning_engine import reason_from_planned_evidence


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _norm(v):
    return str(v or "").strip().lower()


def _extract_target_player(question: str) -> str | None:
    q = question.strip()

    patterns = [
        r"i want\s+(.+?)(?:\.|\?|$)",
        r"how do i get\s+(.+?)(?:\.|\?|$)",
        r"target(?:ing)?\s+(.+?)(?:\.|\?|$)",
        r"trade for\s+(.+?)(?:\.|\?|$)",
        r"acquire\s+(.+?)(?:\.|\?|$)",
    ]

    for pat in patterns:
        m = re.search(pat, q, flags=re.I)
        if m:
            name = m.group(1).strip(" ?.!").strip()
            if name and len(name.split()) <= 4:
                return name

    return None


def make_plan(question: str, owner_team_name: str) -> dict[str, Any]:
    q = _norm(question)

    engines = ["team_context", "player_graph"]

    if any(x in q for x in ["rookie", "draft", "1.01", "1.02", "pick"]):
        route = "rookie_draft"
        engines += ["rookie_board", "team_needs", "draft_pick_context"]
    elif any(x in q for x in ["free agent", "free agency", "waiver", "fa target", "available"]):
        route = "free_agency"
        engines += ["fa_pool", "team_needs", "contract_value"]
    elif any(x in q for x in ["target", "targets", "who should i target", "right player", "put me over the top"]):
        route = "target_recommendation"
        engines += ["league_graph", "trade_constructor", "team_needs"]
    elif any(x in q for x in ["how do i get", "i want", "trade for", "acquire"]):
        route = "acquire_player"
        engines += ["league_graph", "trade_constructor", "target_player"]
    elif any(x in q for x in ["drop", "cut", "bench"]):
        route = "cut_or_churn"
        engines += ["bench_churn", "contract_value"]
    else:
        return {"route": None, "engines": [], "target_player": None, "confidence": 0.0}

    return {
        "route": route,
        "engines": list(dict.fromkeys(engines)),
        "target_player": _extract_target_player(question),
        "confidence": 0.92,
    }


def _players() -> list[dict[str, Any]]:
    sb = service_client()
    try:
        return (
            sb.table("player_graph_v2")
            .select("*")
            .eq("reasoning_eligible", True)
            .execute()
            .data
            or []
        )
    except Exception:
        return sb.table("player_graph").select("*").execute().data or []


def _league() -> list[dict[str, Any]]:
    sb = service_client()
    try:
        return sb.table("league_graph").select("*").execute().data or []
    except Exception:
        return []



def _prospects() -> list[dict[str, Any]]:
    sb = service_client()
    try:
        return (
            sb.table("prospect_graph")
            .select("*")
            .order("rookie_rank")
            .execute()
            .data
            or []
        )
    except Exception:
        return []


def _my_roster(owner_team_name: str) -> list[dict[str, Any]]:
    return [p for p in _players() if p.get("owner_team_name") == owner_team_name]



def _trusted_roster_for_cuts(owner_team_name: str) -> list[dict[str, Any]]:
    sb = service_client()
    try:
        return (
            sb.table("player_graph_v2")
            .select("*")
            .eq("owner_team_name", owner_team_name)
            .eq("reasoning_eligible", True)
            .execute()
            .data
            or []
        )
    except Exception:
        return _my_roster(owner_team_name)


def _free_agents() -> list[dict[str, Any]]:
    return [
        p for p in _players()
        if p.get("is_free_agent") is True
        and p.get("player_status") == "FREE_AGENT_ACTIVE"
        and _num(p.get("data_confidence")) >= 0.55
    ]


def _player_score(p: dict[str, Any]) -> float:
    return round(
        _num(p.get("expected_ppg")) * 4
        + _num(p.get("dynasty_asset_score")) * 0.35
        + _num(p.get("contract_efficiency_score")) * 0.25
        - max(_num(p.get("salary")) - 10, 0) * 0.5,
        2,
    )


def _target_score(p: dict[str, Any], preferred_pos: set[str] | None = None) -> float:
    preferred_pos = preferred_pos or set()
    score = _player_score(p)

    if p.get("pos") in preferred_pos:
        score += 12

    salary = _num(p.get("salary"))
    contract = _num(p.get("contract_efficiency_score"))

    if salary <= 12 and contract >= 50:
        score += 8

    return round(score, 2)


def _team_needs(owner_team_name: str) -> list[str]:
    league = _league()
    mine = next((t for t in league if t.get("owner_team_name") == owner_team_name), None)
    needs = list(mine.get("needs") or []) if mine else []

    # Current league graph can be generous; keep known roster-construction bias.
    if "RB" not in needs:
        needs.append("RB")
    if "TE" not in needs:
        needs.append("TE")

    return needs


def collect_planned_evidence(plan: dict[str, Any], question: str, owner_team_name: str) -> dict[str, Any]:
    route = plan.get("route")
    needs = _team_needs(owner_team_name)
    preferred = set(needs)

    evidence: dict[str, Any] = {
        "plan": plan,
        "route": route,
        "owner_team_name": owner_team_name,
        "team_needs": needs,
        "target_player": plan.get("target_player"),
        "roster": _my_roster(owner_team_name),
    }

    players = _players()

    if route == "target_recommendation":
        candidates = [
            p for p in players
            if p.get("owner_team_name") != owner_team_name
            and p.get("pos") in preferred
            and _num(p.get("expected_ppg")) > 7
        ]
        evidence["targets"] = sorted(candidates, key=lambda p: _target_score(p, preferred), reverse=True)[:10]

    elif route == "free_agency":
        candidates = [
            p for p in _free_agents()
            if p.get("pos") in preferred
            and _num(p.get("expected_ppg")) > 4
        ]
        evidence["fa_targets"] = sorted(candidates, key=lambda p: _target_score(p, preferred), reverse=True)[:12]

    elif route == "rookie_draft":
        evidence["rookie_targets"] = _prospects()[:12]

    elif route == "acquire_player":
        target = plan.get("target_player")
        matches = []
        if target:
            matches = [p for p in players if target.lower() in str(p.get("player_name") or "").lower()]
        evidence["target_matches"] = matches[:5]

        if matches:
            target_player = sorted(matches, key=_player_score, reverse=True)[0]
            target_owner = target_player.get("current_owner")
            evidence["target_owner"] = target_owner
            evidence["target_player_row"] = target_player

            my_assets = sorted(
                _my_roster(owner_team_name),
                key=lambda p: _player_score(p),
                reverse=True,
            )
            evidence["possible_outgoing"] = [
                p for p in my_assets
                if p.get("player_name") not in {"Josh Allen"}
            ][:8]

    elif route == "cut_or_churn":
        roster = _trusted_roster_for_cuts(owner_team_name)
        candidates = []

        for p in roster:
            salary = _num(p.get("salary"))
            ppg = _num(p.get("expected_ppg"))
            dynasty = _num(p.get("dynasty_asset_score"))
            contract = _num(p.get("contract_efficiency_score"))
            confidence = _num(p.get("data_confidence"))

            # Guardrails: do not recommend real producers or real dynasty assets as cuts.
            protected = False
            protect_reasons = []

            if ppg >= 9:
                protected = True
                protect_reasons.append("usable weekly production")
            if dynasty >= 40:
                protected = True
                protect_reasons.append("real dynasty value")
            if contract >= 60 and salary <= 10:
                protected = True
                protect_reasons.append("efficient contract")
            if confidence < 0.45:
                protected = True
                protect_reasons.append("low data confidence; review manually")

            churn_score = 0
            if salary <= 5:
                churn_score += 12
            if ppg < 6:
                churn_score += 18
            if dynasty < 30:
                churn_score += 12
            if contract < 25:
                churn_score += 10
            if confidence >= 0.55:
                churn_score += 4

            if protected:
                churn_score = max(0, churn_score - 30)

            x = dict(p)
            x["churn_score"] = churn_score
            x["protected_from_cut"] = protected
            x["protect_reasons"] = protect_reasons
            candidates.append(x)

        evidence["cut_candidates"] = sorted(
            candidates,
            key=lambda p: (
                _num(p.get("churn_score")),
                -_num(p.get("expected_ppg")),
                -_num(p.get("dynasty_asset_score")),
            ),
            reverse=True,
        )[:8]

    return evidence


def _fmt_player(p: dict[str, Any]) -> str:
    return (
        f"{p.get('player_name')} ({p.get('pos')}, {p.get('owner_team_name') or 'FA'}) — "
        f"PPG {_num(p.get('expected_ppg')):.1f}, dynasty {_num(p.get('dynasty_asset_score')):.1f}, "
        f"contract {_num(p.get('contract_efficiency_score')):.1f}, ${_num(p.get('salary')):g}/{_num(p.get('years')):g} yrs"
    )


def write_planned_answer(evidence: dict[str, Any]) -> dict[str, Any]:
    route = evidence.get("route")
    owner = evidence.get("owner_team_name")
    needs = evidence.get("team_needs") or []

    reasoned_summary = reason_from_planned_evidence(evidence)
    if reasoned_summary:
        return {
            "answer_type": "planned_gm_answer",
            "intent": route,
            "summary": reasoned_summary,
            "plan": evidence.get("plan"),
            "evidence": evidence,
            "owner_team_name": owner,
        }

    if route == "target_recommendation":
        targets = evidence.get("targets") or []
        lines = [
            "Now we’re talking about actual targets, not just archetypes.",
            "",
            f"Given your build, I would prioritize **{', '.join(needs)}** help — ideally someone who raises your weekly ceiling without forcing you to nuke the future.",
            "",
            "The names I would start with:"
        ]
        for p in targets[:6]:
            lines.append(f"- **{_fmt_player(p)}**")
        lines.append("")
        lines.append("My GM read: start with players on teams where your QB/WR surplus creates leverage. Do not chase a name if the cost does not fix RB/TE.")
        summary = "\n".join(lines)

    elif route == "free_agency":
        targets = evidence.get("fa_targets") or []
        lines = [
            "For free agency, I would not chase the biggest name. I would chase cheap role insulation.",
            "",
            f"Your target positions should be **{', '.join(needs)}**.",
            "",
            "Best FA-style targets from the graph:"
        ]
        for p in targets[:8]:
            lines.append(f"- **{_fmt_player(p)}**")
        if not targets:
            lines.append("- I do not see strong FA targets yet from `player_graph`; this may mean the FA pool needs better `market_pool/current_owner` tagging.")
        lines.append("")
        lines.append("My GM read: FA should supplement this roster, not define it. Use FA for cheap RB touches, TE depth, and injury insulation.")
        summary = "\n".join(lines)

    elif route == "rookie_draft":
        rookies = evidence.get("rookie_targets") or []
        lines = [
            "For the 1.02, I would treat the pick as a major roster-shaping asset, not just a rookie selection.",
            "",
            "The question is: does the top rookie fit your actual build, or is the pick more valuable as trade ammo?",
            "",
            "Top rookie-board candidates from the graph:"
        ]
        for p in rookies[:8]:
            lines.append(
                f"- **{p.get('rookie_rank')}. {p.get('player_name')}** ({p.get('pos')}, {p.get('nfl_team')}) — "
                f"prospect {_num(p.get('prospect_score')):.1f}, tier {p.get('prospect_tier')}, "
                f"role: {p.get('fantasy_role') or 'unknown'}"
            )
        if not rookies:
            lines.append("- I do not see rookie-board rows in `player_graph` yet. The draft-board/rookie tables need to be plugged into the planner.")
        lines.append("")
        lines.append("My GM read: if the 1.01 is a clear RB, you should be ready to either take the next elite RB/WR profile or shop 1.02 to a QB/rookie-hungry manager for a proven RB/TE plus value.")
        summary = "\n".join(lines)

    elif route == "acquire_player":
        target = evidence.get("target_player_row")
        outgoing = evidence.get("possible_outgoing") or []
        target_owner = evidence.get("target_owner")

        if not target:
            summary = "I could not find that target in `player_graph` yet, so I cannot build a responsible acquisition plan."
        else:
            lines = [
                f"If you want **{target.get('player_name')}**, the first thing is knowing the seller: **{target_owner or 'unknown/FA'}**.",
                "",
                f"Target profile: **{_fmt_player(target)}**",
                "",
                "The outgoing pieces I would consider before touching the true core:"
            ]
            for p in outgoing[:6]:
                lines.append(f"- **{_fmt_player(p)}**")
            lines.append("")
            lines.append("My GM read: do not start with Josh Allen. Start with surplus QB/WR value, an uncomfortable contract, or pick flexibility. If the other manager needs QB, that is your leverage.")
            summary = "\n".join(lines)

    elif route == "cut_or_churn":
        cuts = evidence.get("cut_candidates") or []
        lines = [
            "For cuts, I would separate **bad contracts** from **replaceable roster spots**.",
            "",
            "The first cut/churn candidates from your roster:"
        ]
        for p in cuts[:6]:
            lines.append(f"- **{_fmt_player(p)}** — churn score {_num(p.get('churn_score')):.0f}")
        lines.append("")
        lines.append("My GM read: shop name-value players first. Cut low-salary, low-role players first.")
        summary = "\n".join(lines)

    else:
        return {}

    return {
        "answer_type": "planned_gm_answer",
        "intent": route,
        "summary": summary,
        "plan": evidence.get("plan"),
        "evidence": evidence,
        "owner_team_name": owner,
    }


def answer_with_planner(question: str, owner_team_name: str) -> dict[str, Any] | None:
    plan = make_plan(question, owner_team_name)

    if not plan.get("route") or plan.get("confidence", 0) < 0.5:
        return None

    evidence = collect_planned_evidence(plan, question, owner_team_name)
    answer = write_planned_answer(evidence)

    return answer or None
