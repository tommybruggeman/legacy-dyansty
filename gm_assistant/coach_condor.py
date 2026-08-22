from __future__ import annotations

from typing import Dict, List

from gm_assistant.brain_context import load_gm_brain_context, update_gm_memory


def friendly_label(x: str | None) -> str:
    if not x:
        return "unclear"
    return str(x).replace("_", " ").lower()


def title_label(x: str | None) -> str:
    return friendly_label(x).title()


def quick_prompts_for_context(ctx: Dict) -> List[str]:
    team = ctx.get("team_brain") or {}

    needs = team.get("position_needs") or []
    strengths = team.get("position_strengths") or []
    trade = team.get("trade_candidates") or []
    problems = team.get("contract_problems") or []

    prompts = []

    if needs:
        prompts.append(f"How should I fix my {needs[0]} need?")

    if strengths and needs:
        prompts.append(f"How can I use my {strengths[0]} depth to upgrade {needs[0]}?")

    if problems:
        prompts.append(f"What should I do with {problems[0]}?")

    if trade:
        prompts.append("Who should I market-check first?")

    prompts.append("Give me a 3-step GM plan.")

    out = []
    for p in prompts:
        if p not in out:
            out.append(p)

    return out[:5]


def coach_opening(ctx: Dict) -> str:
    team_name = ctx.get("team_name")
    team = ctx.get("team_brain") or {}

    direction = friendly_label(team.get("team_direction"))
    strengths = team.get("position_strengths") or []
    needs = team.get("position_needs") or []

    strength_text = " and ".join(strengths) if strengths else "a few roster pockets"
    need_text = " and ".join(needs) if needs else "no obvious emergency spot"

    return (
        f"Hi, I’m **Coach Condor**, here to help **{team_name}’s team**. "
        f"I’m reading this roster as probably in **{direction}** mode. "
        f"Your strengths are your **{strength_text}**, and the spots putting pressure on you are **{need_text}**. "
        f"What move do you want to talk through first?"
    )


def answer_as_coach_condor(
    question: str,
    team_name: str,
    *,
    user_id: str,
    league_id: str,
    league_team_id: str | None,
    allow_legacy_fallback: bool = False,
) -> str:
    ctx = load_gm_brain_context(
        team_name,
        user_id=user_id,
        league_id=league_id,
        league_team_id=league_team_id,
        allow_legacy_fallback=allow_legacy_fallback,
    )

    q = question.lower()
    team = ctx.get("team_brain") or {}
    league = ctx.get("league_brain") or {}

    discussed_players = _extract_discussed_players(question, ctx)
    discussed_teams = _extract_discussed_teams(question, ctx)

    inferred_notes = []

    if any(x in q for x in ["keep josh", "keep allen", "not trade josh", "hoping to keep josh"]):
        inferred_notes.append("User prefers to keep Josh Allen unless overwhelmed by a premium offer.")

    if any(x in q for x in ["put me over the top", "win now", "contender", "larger contender"]):
        inferred_notes.append("User is leaning into a win-now upgrade rather than a slow rebuild.")

    if any(x in q for x in ["future", "long term", "don't destroy", "dont destroy"]):
        inferred_notes.append("User wants to preserve future flexibility while contending.")

    if any(x in q for x in ["what should i do", "3-step", "three step", "plan"]):
        response = _team_plan(team_name, team)

    elif any(x in q for x in ["trade partner", "who should i call", "who should i trade with", "trade fits"]):
        response = _trade_partners(team_name, team, league)

    elif any(x in q for x in ["who should i move", "move on", "market-check", "market check", "shop"]):
        response = _move_candidates(team)

    elif discussed_players:
        response = _player_conversation(ctx, discussed_players[0])

    elif any(x in q for x in ["add", "target", "put me over the top", "upgrade"]):
        response = _upgrade_targets(team_name, team, league)

    else:
        response = _team_plan(team_name, team)

    if league_team_id:
        update_gm_memory(
            team_name=team_name,
            user_id=user_id,
            league_id=league_id,
            league_team_id=league_team_id,
            current_focus=_infer_focus(question, team),
            players_discussed=discussed_players,
            teams_discussed=discussed_teams,
            conversation_summary=f"User asked: {question}. Coach Condor response: {response[:600]}",
            notes=inferred_notes,
        )

    return response


def _team_plan(team_name: str, team: Dict) -> str:
    needs = team.get("position_needs") or []
    strengths = team.get("position_strengths") or []
    trade_candidates = team.get("trade_candidates") or []
    contract_problems = team.get("contract_problems") or []
    direction = friendly_label(team.get("team_direction"))

    return "\n".join([
        f"My GM read: **{team_name} is in {direction} mode**.",
        "",
        "I would not treat this like a rebuild. I would treat it like a targeted contention push.",
        "",
        "**My 3-step plan:**",
        f"1. **Protect the real foundation.** Your current build is strongest at **{', '.join(strengths) if strengths else 'your premium assets'}**.",
        f"2. **Use surplus to fix pressure points.** The roster pressure is **{', '.join(needs) if needs else 'not obvious'}**, so I’d try to turn extra QB/WR value into RB/TE help.",
        f"3. **Market-check expensive/problem contracts first.** Start with **{', '.join((contract_problems or trade_candidates)[:4]) if (contract_problems or trade_candidates) else 'your non-core depth'}**, but do not panic sell.",
        "",
        "My stance: **consolidate, but do not empty the future just to feel busy.**",
    ])


def _trade_partners(team_name: str, team: Dict, league: Dict) -> str:
    fits = league.get("trade_fits") or []
    relevant = [
        f for f in fits
        if f.get("team_a") == team_name or f.get("team_b") == team_name
    ]

    lines = ["Here are the trade conversations I’d open first:"]

    for f in relevant[:6]:
        other = f["team_b"] if f["team_a"] == team_name else f["team_a"]

        if f["team_a"] == team_name:
            you_get = f.get("team_a_needs_from_b") or []
            they_get = f.get("team_b_needs_from_a") or []
        else:
            you_get = f.get("team_b_needs_from_a") or []
            they_get = f.get("team_a_needs_from_b") or []

        lines.append(
            f"- **{other}** — you could look for **{', '.join(you_get) if you_get else 'value'}**, "
            f"and they may be interested in your **{', '.join(they_get) if they_get else 'surplus/depth'}**."
        )

    if len(lines) == 1:
        lines.append("I don’t see a clean league-fit match yet, so I’d start with teams that need QB/WR help.")

    lines.append("\nMy move: start with fit, not name value. The best deal is probably QB/WR out, RB/TE in.")
    return "\n".join(lines)


def _move_candidates(team: Dict) -> str:
    trade = team.get("trade_candidates") or []
    problems = team.get("contract_problems") or []

    lines = [
        "I would not frame this as “who do I dump?” I’d frame it as “who can I convert into a roster upgrade?”",
        "",
    ]

    if problems:
        lines.append(f"**First market-check:** {', '.join(problems[:4])}.")
    if trade:
        secondary = [x for x in trade if x not in problems]
        lines.append(f"**Secondary trade chips:** {', '.join(secondary[:6])}.")

    lines.append("")
    lines.append("My stance: move from your surplus areas, but only if the return directly helps RB/TE or improves your weekly starting lineup.")
    return "\n".join(lines)


def _player_conversation(ctx: Dict, player_name: str) -> str:
    profile = _player_lookup(ctx, player_name)
    relative = _relative_lookup(ctx, player_name)

    if not profile and not relative:
        return f"I don’t have a clean profile for **{player_name}** yet, so I’d treat this as a scouting question rather than a firm GM call."

    name = (profile or relative).get("player_name")
    label = (profile or {}).get("strategic_label")
    action = (profile or {}).get("action")
    contract = (profile or {}).get("contract_flag")
    tier = (relative or {}).get("league_value_tier")
    pct = (relative or {}).get("overall_percentile")

    lines = [f"On **{name}**, my GM read is nuanced.", ""]

    if tier:
        pct_text = f" ({float(pct):.0f}th percentile)." if pct is not None else "."
        lines.append(f"League-relative value: **{title_label(tier)}**{pct_text}")
    if label:
        lines.append(f"Current strategic profile: **{title_label(label)}**.")
    if contract:
        lines.append(f"Contract read: **{title_label(contract)}**.")

    lines.append("")

    if "josh allen" in name.lower():
        lines.append("My stance: **keep Josh Allen unless someone offers a true roster-changing overpay**. For a contender, elite QB stability is part of the foundation.")
    elif contract in ["BAD_CONTRACT", "OVERPAID"]:
        lines.append("My stance: **shop, but don’t dump**. Use him as a centerpiece or salary piece to solve RB/TE, not just to escape the contract.")
    elif action:
        lines.append(f"My stance: **{action}**.")
    else:
        lines.append("My stance: hold unless he helps you unlock a cleaner upgrade.")

    return "\n".join(lines)


def _upgrade_targets(team_name: str, team: Dict, league: Dict) -> str:
    needs = team.get("position_needs") or []
    strengths = team.get("position_strengths") or []
    fits = league.get("trade_fits") or []

    relevant = [
        f for f in fits
        if f.get("team_a") == team_name or f.get("team_b") == team_name
    ]

    lines = [
        "If the goal is to put this roster over the top, I’d focus less on adding a random name and more on adding the **right archetype**.",
        "",
        f"Your target archetype should be: **starting-caliber {', '.join(needs) if needs else 'weekly starter'} help**.",
        f"Your likely outgoing leverage: **{', '.join(strengths) if strengths else 'surplus value'}**.",
        "",
        "The conversations I’d explore first:",
    ]

    for f in relevant[:5]:
        other = f["team_b"] if f["team_a"] == team_name else f["team_a"]
        you_get = f.get("team_a_needs_from_b") if f.get("team_a") == team_name else f.get("team_b_needs_from_a")
        lines.append(f"- **{other}** for possible **{', '.join(you_get or []) or 'fit-based'}** help.")

    lines.append("")
    lines.append("My move: try to turn QB/WR surplus into one bankable RB/TE starter. That is the cleanest path to raising your title odds.")
    return "\n".join(lines)


def _player_lookup(ctx: Dict, name: str):
    q = name.lower().strip()
    for p in ctx.get("player_profiles", []):
        if q in str(p.get("player_name", "")).lower():
            return p
    return None


def _relative_lookup(ctx: Dict, name: str):
    q = name.lower().strip()
    for p in ctx.get("relative_values", []):
        if q in str(p.get("player_name", "")).lower():
            return p
    return None


def _extract_discussed_players(question: str, ctx: Dict) -> List[str]:
    found = []
    q = question.lower()
    for p in ctx.get("player_profiles", []):
        name = str(p.get("player_name") or "").strip()
        if name and name.lower() in q:
            found.append(name)
    return found


def _extract_discussed_teams(question: str, ctx: Dict) -> List[str]:
    found = []
    q = question.lower()
    league = ctx.get("league_brain") or {}
    for t in league.get("team_summaries", []) or []:
        name = str(t.get("team_name") or "").strip()
        if name and name.lower() in q:
            found.append(name)
    return found


def _infer_focus(question: str, team: Dict) -> str:
    q = question.lower()
    if "garrett" in q:
        return "Evaluate whether Garrett Wilson can be converted into RB/TE help without panic selling."
    if any(x in q for x in ["add", "target", "over the top", "upgrade"]):
        return "Find a win-now RB/TE upgrade using QB/WR surplus."
    if "josh" in q or "allen" in q:
        return "Keep Josh Allen as the foundation unless a massive overpay appears."
    return team.get("summary") or "Continue refining contender strategy."
