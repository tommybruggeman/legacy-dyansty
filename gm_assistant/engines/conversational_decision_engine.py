from __future__ import annotations

import re
from typing import Any

from gm_assistant.engines.roster_decision_engine import (
    roster_liability_report,
    cut_decision,
    next_move,
)


def _clean_question(q: str) -> str:
    return (q or "").strip()


def _extract_player_name(question: str) -> str | None:
    q = _clean_question(question)

    patterns = [
        r"should i (?:cut|drop|release|dump|move on from)\s+(.+)",
        r"would you (?:cut|drop|release|dump|move on from)\s+(.+)",
        r"what should i do with\s+(.+)",
        r"do i (?:cut|drop|release|dump)\s+(.+)",
    ]

    for pat in patterns:
        m = re.search(pat, q, flags=re.I)
        if m:
            name = m.group(1).strip(" ?.!").strip()
            return name if name else None

    return None


def _looks_like_cut_question(q: str) -> bool:
    q = q.lower()
    return any(x in q for x in [
        "should i cut",
        "should i drop",
        "would you cut",
        "would you drop",
        "do i cut",
        "do i drop",
        "move on from",
        "release",
        "dump",
    ])


def _looks_like_liability_question(q: str) -> bool:
    q = q.lower()
    return any(x in q for x in [
        "who should i cut",
        "who should i drop",
        "who is hurting",
        "hurting my roster",
        "worst contracts",
        "bad contracts",
        "roster liabilities",
        "get rid of",
        "clean up my roster",
    ])


def _looks_like_next_move_question(q: str) -> bool:
    q = q.lower()
    return any(x in q for x in [
        "next move",
        "what should i do next",
        "where do we go from here",
        "what now",
        "next step",
        "how do i improve",
        "how does team look",
    ])


def answer_conversational_decision(
    question: str,
    owner_team_name: str,
    team_goal: str | None = None,
) -> dict[str, Any] | None:
    q = _clean_question(question)

    if not q:
        return None

    if _looks_like_cut_question(q):
        player_name = _extract_player_name(q)

        if player_name:
            raw = cut_decision(owner_team_name, player_name, team_goal)
            return _make_conversational(raw, question)

        raw = roster_liability_report(owner_team_name, limit=6)
        return _make_conversational(raw, question)

    if _looks_like_liability_question(q):
        raw = roster_liability_report(owner_team_name, limit=6)
        return _make_conversational(raw, question)

    if _looks_like_next_move_question(q):
        raw = next_move(owner_team_name, team_goal)
        return _make_conversational(raw, question)

    return None


def _make_conversational(raw: dict[str, Any], question: str) -> dict[str, Any]:
    answer_type = raw.get("answer_type")

    if answer_type == "cut_decision":
        return _write_cut_answer(raw, question)

    if answer_type == "roster_liability":
        return _write_liability_answer(raw, question)

    if answer_type == "next_move":
        return _write_next_move_answer(raw, question)

    return raw


def _write_cut_answer(raw: dict[str, Any], question: str) -> dict[str, Any]:
    p = raw.get("player") or {}
    rec = raw.get("recommendation", "MONITOR")

    name = p.get("player_name", "that player")
    salary = p.get("salary")
    years = p.get("years")
    contract = p.get("contract_efficiency_score")
    dynasty = p.get("dynasty_asset_score")
    ppg = p.get("expected_ppg") or p.get("season_ppg")
    dead = raw.get("dead_cap_estimate")

    if "CUT / CHURN" in rec:
        lead = f"Yeah — I would be comfortable cutting **{name}**."
        tone = "That is the type of roster spot where the flexibility is probably worth more than waiting on the player."
    elif "DO NOT CUT" in rec:
        lead = f"No — I would not cut **{name}**."
        tone = "The contract may be annoying, but there is still enough player value that cutting him would probably be selling the asset for zero."
    elif "SHOP FIRST" in rec:
        lead = f"I would not start with a cut on **{name}**. I would shop him first."
        tone = "This is exactly the kind of player where the contract is the problem, not necessarily the name value."
    else:
        lead = f"I would hold **{name}** for now, but I would market-check him."
        tone = "I do not think this is an automatic cut, but I also would not ignore the contract pressure."

    summary = (
        f"{lead}\n\n"
        f"{tone}\n\n"
        f"The quick read: **${float(salary or 0):g}**, **{float(years or 0):g} years**, "
        f"contract score **{float(contract or 0):.1f}**, dynasty score **{float(dynasty or 0):.1f}**, "
        f"and expected PPG around **{float(ppg or 0):.1f}**. "
        f"If you cut him, the rough dead-cap hit is about **${float(dead or 0):g}** total before we split it by season.\n\n"
        f"My GM answer: **{rec}**. If another manager will still pay for the name, take the trade path first. "
        f"Only cut if the market is dead and the roster spot/cap flexibility clearly helps you more than holding the asset."
    )

    raw["summary"] = summary
    raw["conversation_style"] = "natural_decision"
    return raw


def _write_liability_answer(raw: dict[str, Any], question: str) -> dict[str, Any]:
    players = raw.get("players") or []
    owner = raw.get("owner_team_name", "your team")

    if not players:
        raw["summary"] = "I do not see enough roster data to confidently identify your biggest roster liabilities yet."
        return raw

    top = players[0]
    lines = [
        f"The first place I would look on **{owner}** is **{top['player']}**.",
        "",
        "Not because he has to be cut immediately — because he is the clearest spot where contract, production, and roster flexibility need to be questioned.",
        "",
        "The main pressure points:"
    ]

    for r in players[:5]:
        lines.append(
            f"- **{r['player']}** ({r['pos']}): ${r['salary']:g}/{r['years']:g} yrs, "
            f"contract {r['contract']}, dynasty {r['dynasty']}, PPG {r['ppg']} → **{r['action']}**"
        )

    lines.append("")
    lines.append(
        "My move would be: market-check the expensive names first, churn the low-value bench spots second, "
        "and only take a dead-cap hit when the player has no real trade market left."
    )

    raw["summary"] = "\n".join(lines)
    raw["conversation_style"] = "natural_roster_audit"
    return raw


def _write_next_move_answer(raw: dict[str, Any], question: str) -> dict[str, Any]:
    player = raw.get("player")

    if not player:
        raw["summary"] = "I do not see enough roster data to give you a confident next move yet."
        return raw

    summary = (
        f"Your next move should be pretty simple: **market-check {player['player']}**.\n\n"
        f"I would not frame it as a panic sell. I would frame it as a value test. "
        f"He is sitting at **${player['salary']:g}/{player['years']:g} yrs**, with contract score **{player['contract']}**, "
        f"dynasty score **{player['dynasty']}**, and PPG around **{player['ppg']}**.\n\n"
        f"That means the question is not just, “is he good?” The question is, "
        f"“does someone else value him more than your contract-adjusted model does?”\n\n"
        f"I would try to turn that into **RB/TE help, cheaper production, or cap flexibility**. "
        f"If the market is weak, hold. If someone prices the name aggressively, that is your opening."
    )

    raw["summary"] = summary
    raw["conversation_style"] = "natural_next_move"
    return raw
