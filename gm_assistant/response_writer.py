from __future__ import annotations

import re

from gm_assistant.gm_answer_helpers import player_nfl_context_note, player_contract_efficiency_note, player_gm_arguments


def _extract_player(summary: str) -> str:
    try:
        if "On **" in summary:
            return summary.split("On **", 1)[1].split("**", 1)[0].strip()
    except Exception:
        pass
    return ""


def _extract_num(pattern: str, text: str):
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def _extract_pos(summary: str) -> str:
    m = re.search(r"He is a \*\*([A-Z]+)\*\*", summary)
    return m.group(1) if m else "player"


def _extract_read(summary: str) -> str:
    m = re.search(r"my read is: \*\*(.*?)\*\*", summary, re.I)
    return m.group(1) if m else ""


def _extract_signal(summary: str, label: str):
    return _extract_num(rf"{label} \*\*(\d+\.?\d*)\*\*", summary)


def _nfl_context_plain(player: str, owner_team_name: str) -> str:
    note = player_nfl_context_note(player, owner_team_name)

    if not note:
        return ""

    low = note.lower()
    reasons = []

    if "buried_depth_chart" in low:
        reasons.append("he is buried on the depth chart")
    if "injury" in low or "dnr" in low:
        reasons.append("there is injury/availability risk")
    if "bad_context" in low or "risky_context" in low:
        reasons.append("his current NFL situation is unstable")

    if not reasons:
        return ""

    return " The football context matters here too: " + ", and ".join(dict.fromkeys(reasons)) + "."


def _nfl_note_once(player: str, owner_team_name: str, raw_summary: str) -> str:
    if "NFL context warning:" in raw_summary or "NFL context:" in raw_summary:
        return ""
    return player_nfl_context_note(player, owner_team_name)


def _contract_human_take(player, pos, salary, years, contract_value, engine, recent, trade, nfl_plain):
    salary = salary or 0
    years = years or 0
    cv = contract_value

    if cv is None:
        return f"I do not have enough contract context to give a clean take on **{player}** yet."

    # Elite QB / superflex override.
    if pos == "QB" and salary >= 35 and (engine or 0) >= 80 and (recent or 0) >= 70:
        return (
            f"Yeah, it is expensive — but I think it is justified. **${salary:g}** is a huge number, "
            f"but elite QB production in superflex is different from paying up at RB or WR. "
            f"You are buying a weekly positional advantage that is extremely hard to replace. "
            f"So I would not call it a bargain, but I also would not call it a bad contract."
            f"{nfl_plain}"
        )

    # Expensive and inefficient non-elite profile.
    if salary >= 30 and cv < 40:
        return (
            f"Honestly, I do not like it. **${salary:g}** is premium-player money, and the current profile is not giving you enough back for that cost. "
            f"You are paying like he is a stable weekly difference-maker, but the contract is eating flexibility."
            f"{nfl_plain}"
        )

    # Mid-price but risky.
    if salary >= 10 and cv < 55:
        return (
            f"I am pretty uneasy about it. **${salary:g} for {years:g} years** is not impossible to carry, "
            f"but it only feels good if the player is healthy, stable, and clearly useful. Right now there is too much fragility baked in."
            f"{nfl_plain}"
        )

    # Mid-price, decent contract, bad NFL context.
    if salary >= 10 and cv >= 55 and "football context matters" in nfl_plain:
        return (
            f"The number itself is not terrible — **${salary:g} for {years:g} years** is workable for a talented {pos}. "
            f"But I would not call it clean. You are paying for the version of **{player}** that needs role and health to rebound."
            f"{nfl_plain}"
        )

    if salary <= 8 and cv >= 45:
        return (
            f"I actually like it. At **${salary:g}**, the bar is lower, and you are not tying up much cap. "
            f"Even if the player is not perfect, the contract gives you flexibility."
            f"{nfl_plain}"
        )

    if cv >= 65:
        return (
            f"I think it is fine. Not some screaming bargain, but playable. "
            f"The cost is reasonable enough that you are not boxed in by the deal."
            f"{nfl_plain}"
        )

    return (
        f"I would call it shaky more than awful. The player may still be useful, but the contract is not giving you enough safety."
        f"{nfl_plain}"
    )



def _argument_based_contract_take(player: str, owner_team_name: str, fallback_take: str) -> str:
    args = player_gm_arguments(player, owner_team_name, "contract")

    if not args:
        return fallback_take

    pros = [a for a in args if a.get("polarity") == "pro"]
    cons = [a for a in args if a.get("polarity") == "con"]
    neutrals = [a for a in args if a.get("polarity") == "neutral"]

    top_pros = pros[:3]
    top_cons = cons[:2]
    top_neutral = neutrals[:1]

    pro_texts = [a.get("argument_text") for a in top_pros if a.get("argument_text")]
    con_texts = [a.get("argument_text") for a in top_cons if a.get("argument_text")]
    neutral_texts = [a.get("argument_text") for a in top_neutral if a.get("argument_text")]

    pro_weight = sum(float(a.get("weight") or 0) for a in pros[:3])
    con_weight = sum(float(a.get("weight") or 0) for a in cons[:3])

    # Opening opinion
    if pro_weight >= con_weight + 50:
        opener = f"I actually like the **{player}** contract."
    elif con_weight >= pro_weight + 40:
        opener = f"I do not love the **{player}** contract."
    else:
        opener = f"I think the **{player}** contract is pretty balanced."

    lines = [opener]

    if pro_texts:
        lines.append("The case for it: " + " ".join(pro_texts))

    if con_texts:
        lines.append("The pushback: " + " ".join(con_texts))

    if neutral_texts:
        lines.append("Context: " + " ".join(neutral_texts))

    return "\n\n".join(lines)

def write_contract_response(question, owner_team_name, raw_summary, mode):
    player = _extract_player(raw_summary) or "this player"
    pos = _extract_pos(raw_summary)

    salary = _extract_num(r"at \*\*\$(\d+\.?\d*)\*\*", raw_summary)
    years = _extract_num(r"with \*\*(\d+\.?\d*)\s+years?\*\*", raw_summary)
    contract_value = _extract_signal(raw_summary, "contract value")
    engine = _extract_signal(raw_summary, "engine score")
    recent = _extract_signal(raw_summary, "recent production")
    trade = _extract_signal(raw_summary, "trade value")

    nfl_plain = _nfl_context_plain(player, owner_team_name)
    ce_note = player_contract_efficiency_note(player, owner_team_name)
    fallback_take = _contract_human_take(player, pos, salary, years, contract_value, engine, recent, trade, nfl_plain)
    take = _argument_based_contract_take(player, owner_team_name, fallback_take)

    if mode == "opinion":
        return take + ce_note

    if mode == "explain":
        return (
            f"The way I am thinking about **{player}'s** contract is pretty simple: "
            f"does the salary match the role, health, production, scarcity, and replacement value?\n\n"
            f"{take}{ce_note}\n\n"
            "The score is only the internal shorthand. The human version is: are you paying for a player who still gives you an edge, "
            "or are you paying for a version of the player that may not exist anymore?"
        )

    return (
        f"{take}\n\n"
        "Action lens: now that we have the opinion, the next step would be comparing hold, trade, and cut outcomes."
    )


def write_player_profile_response(owner_team_name, raw_summary, mode):
    player = _extract_player(raw_summary) or "this player"
    read = _extract_read(raw_summary)
    nfl_plain = _nfl_context_plain(player, owner_team_name)

    if mode == "explain":
        return (
            f"**{player} is risky because the football situation is doing a lot of damage to the asset profile.**\n\n"
            f"The model still sees the player talent/name value, which is why the base read can land around **{read or 'hold'}**. "
            f"But the context is where the concern comes from."
            f"{nfl_plain}\n\n"
            "That means I would separate two ideas: the player can still be talented, but the asset is fragile because the role and availability are not clean."
        )

    return (
        f"My take on **{player}** is that the player is more complicated than the simple label suggests."
        f"{nfl_plain}"
    )


def write_player_response(question, owner_team_name, raw_summary, mode):
    player = _extract_player(raw_summary) or "this player"

    if mode == "opinion":
        nfl_plain = _nfl_context_plain(player, owner_team_name)
        return (
            f"My honest take on **{player}**: I am not just looking at whether he is talented. "
            f"I am looking at whether the current version of the player still matches the price and role you need from him."
            f"{nfl_plain}"
        )

    if mode == "explain":
        nfl_plain = _nfl_context_plain(player, owner_team_name)
        return (
            f"Here is why **{player}** is complicated:\n\n"
            f"{raw_summary}\n\n"
            f"{nfl_plain}\n\n"
            "The important distinction is talent versus asset quality. A player can still be good at football while becoming a risky fantasy asset."
        )

    return raw_summary + _nfl_note_once(player, owner_team_name, raw_summary)


def write_team_response(raw_summary, mode):
    if mode == "opinion":
        return raw_summary.replace("Next moves:", "My honest read:")
    return raw_summary


def write_response(intent, mode, owner_team_name, raw_answer):
    if isinstance(raw_answer, dict):
        raw_summary = raw_answer.get("summary") or str(raw_answer)
    else:
        raw_summary = str(raw_answer)

    if intent == "contract":
        summary = write_contract_response("", owner_team_name, raw_summary, mode)
    elif intent == "player_profile":
        summary = write_player_profile_response(owner_team_name, raw_summary, mode)
    elif intent == "player_decision":
        summary = write_player_response("", owner_team_name, raw_summary, mode)
    elif intent in {"team_strategy", "league_analysis"}:
        summary = write_team_response(raw_summary, mode)
    else:
        summary = raw_summary

    result = dict(raw_answer) if isinstance(raw_answer, dict) else {}
    result["answer_type"] = intent
    result["conversation_mode"] = mode
    result["summary"] = summary
    return result
