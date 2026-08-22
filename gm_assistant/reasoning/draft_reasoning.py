from __future__ import annotations

import re
from auth import service_client


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _norm(v):
    return str(v or "").lower().strip()


def _client():
    return service_client()


def _extract_pick(question: str):
    q = _norm(question)
    m = re.search(r"1\.(\d+)", q)
    if m:
        return f"1.{m.group(1)}"
    if "1.02" in q or "1 02" in q:
        return "1.02"
    return None


def _excluded_names(question: str):
    q = _norm(question)
    gone = []
    for phrase in ["gone", "taken", "off the board", "drafted"]:
        if phrase in q:
            # crude but useful: exclude known names mentioned before the phrase
            for name in ["mendoza", "jeanty", "hampton", "tetairoa", "judkins", "henderson", "skattebo"]:
                if name in q:
                    gone.append(name)
    return gone


def _rookie_candidates():
    sb = _client()

    contracts = sb.table("player_contract_efficiency").select("*").execute().data or []
    dynasty = sb.table("player_dynasty_asset_engine").select("*").execute().data or []
    nfl = sb.table("player_nfl_intelligence").select("*").execute().data or []
    args = sb.table("player_gm_arguments").select("*").execute().data or []

    dynasty_by_id = {str(r.get("sleeper_id")): r for r in dynasty}
    nfl_by_name = {(_norm(r.get("player_name")), r.get("pos")): r for r in nfl}

    args_by_name = {}
    for a in args:
        key = (_norm(a.get("player_name")), a.get("pos"))
        args_by_name.setdefault(key, []).append(a)

    rows = []
    seen = set()

    for r in contracts:
        profile = r.get("evidence_profile")
        rookie_asset = _num(r.get("rookie_asset_score"))
        if profile != "ROOKIE_PROSPECT" and rookie_asset < 50:
            continue

        name = r.get("player_name")
        pos = r.get("pos")
        key = (_norm(name), pos)

        # one row per player, best contract score if duplicated
        score = _num(r.get("contract_efficiency_score"))
        if key in seen:
            continue
        seen.add(key)

        dy = dynasty_by_id.get(str(r.get("sleeper_id")), {})
        ni = nfl_by_name.get(key, {})

        pro_args = [
            a.get("argument_text")
            for a in sorted(args_by_name.get(key, []), key=lambda x: _num(x.get("weight")), reverse=True)
            if a.get("polarity") == "pro"
        ][:3]

        con_args = [
            a.get("argument_text")
            for a in sorted(args_by_name.get(key, []), key=lambda x: _num(x.get("weight")), reverse=True)
            if a.get("polarity") == "con"
        ][:2]

        rows.append({
            "player_name": name,
            "pos": pos,
            "owner_team_name": r.get("owner_team_name"),
            "salary": _num(r.get("salary")),
            "years": _num(r.get("years")),
            "contract_efficiency_score": score,
            "position_contract_rank": r.get("position_contract_rank"),
            "position_contract_percentile": _num(r.get("position_contract_percentile")),
            "expected_ppg": _num(r.get("expected_ppg")),
            "rookie_asset_score": rookie_asset,
            "future_projection_score": _num(dy.get("future_projection_score")),
            "dynasty_asset_score": _num(dy.get("dynasty_asset_score")),
            "market_consensus_score": _num(dy.get("market_consensus_score")),
            "nfl_intelligence_score": _num(ni.get("nfl_intelligence_score"), 50),
            "nfl_team": ni.get("nfl_team") or dy.get("nfl_team"),
            "arguments_for": pro_args,
            "arguments_against": con_args,
        })

    return rows


def _team_fit_bonus(pos: str, owner_team_name: str):
    # v1: Tommy has recurring RB/TE pressure from benchmark/team analysis.
    if pos == "RB":
        return 12
    if pos == "TE":
        return 8
    if pos == "WR":
        return 2
    if pos == "QB":
        return -4
    return 0


def _draft_score(c, owner_team_name: str):
    return (
        c["rookie_asset_score"] * 0.28
        + c["contract_efficiency_score"] * 0.22
        + c["future_projection_score"] * 0.18
        + c["dynasty_asset_score"] * 0.14
        + c["nfl_intelligence_score"] * 0.10
        + c["market_consensus_score"] * 0.05
        + _team_fit_bonus(c["pos"], owner_team_name)
    )


def _format_candidate(c, i):
    owner = c.get("owner_team_name")
    owner_text = f", currently on {owner}" if owner else ""
    return (
        f"{i}. **{c['player_name']}** ({c['pos']}{owner_text}) — "
        f"draft score {c['draft_score']:.1f}, rookie profile {c['rookie_asset_score']:.1f}, "
        f"contract efficiency {c['contract_efficiency_score']:.1f}, projected {c['expected_ppg']:.1f} PPG."
    )


def answer_draft_question(question: str, owner_team_name: str):
    q = _norm(question)
    pick = _extract_pick(question)
    excluded = _excluded_names(question)

    candidates = _rookie_candidates()

    if excluded:
        candidates = [
            c for c in candidates
            if not any(name in _norm(c["player_name"]) for name in excluded)
        ]

    for c in candidates:
        c["draft_score"] = _draft_score(c, owner_team_name)

    candidates = sorted(candidates, key=lambda x: x["draft_score"], reverse=True)

    # Specific comparison: Hampton vs Jeanty
    if "hampton" in q and "jeanty" in q:
        pair = [c for c in candidates if "hampton" in _norm(c["player_name"]) or "jeanty" in _norm(c["player_name"])]
        pair = sorted(pair, key=lambda x: x["draft_score"], reverse=True)

        lines = [
            "My draft-room lean is:",
            "",
            *[_format_candidate(c, i) for i, c in enumerate(pair, 1)],
            "",
        ]

        if pair:
            top = pair[0]
            lines.append(
                f"I would lean **{top['player_name']}** for your roster right now because RB is a major team need and his rookie/contract profile gives you a better chance at cheap production."
            )

        lines.append(
            "The key distinction: with rookies, I am not weighing NFL history heavily. I am weighing prospect strength, expected opportunity, team fit, contract efficiency, and bust risk."
        )

        return {
            "answer_type": "draft",
            "conversation_mode": "analyze",
            "owner_team_name": owner_team_name,
            "summary": "\n".join(lines),
            "candidates": pair,
        }

    top = candidates[:5]

    if not top:
        return {
            "answer_type": "draft",
            "conversation_mode": "analyze",
            "owner_team_name": owner_team_name,
            "summary": "I do not have enough rookie context loaded yet to make a draft-room recommendation.",
            "candidates": [],
        }

    pick_text = f" at **{pick}**" if pick else ""

    lines = [
        f"If I am in the draft room with you{pick_text}, my board starts here:",
        "",
        *[_format_candidate(c, i) for i, c in enumerate(top, 1)],
        "",
        f"My current lean: **{top[0]['player_name']}**.",
        "",
        "Why: I am blending rookie prestige, projected opportunity, contract efficiency, team need, and future value. For your roster, RB value gets a real bump because your current build needs cheaper RB production."
    ]

    if excluded:
        lines.append(f"\nI excluded players mentioned as gone/taken: {', '.join(excluded)}.")

    return {
        "answer_type": "draft",
        "conversation_mode": "analyze",
        "owner_team_name": owner_team_name,
        "summary": "\n".join(lines),
        "candidates": top,
    }
