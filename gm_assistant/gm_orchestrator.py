from __future__ import annotations

from gm_assistant.intent_router import classify_gm_intent
from gm_assistant.conversation_mode import classify_conversation_mode
from gm_assistant.response_writer import write_response
from gm_assistant.reasoning.draft_reasoning import answer_draft_question
from gm_assistant.reasoning.football_concepts import answer_football_concept
from gm_assistant.gm_answer_helpers import market_check_candidates, trade_target_candidates, format_market_check, format_trade_targets, player_nfl_context_note


def _safe_summary(value):
    if isinstance(value, dict):
        return value.get("summary") or str(value)
    return str(value)


def _extract_player_from_summary(summary: str) -> str:
    try:
        if "On **" in summary:
            return summary.split("On **", 1)[1].split("**", 1)[0]
    except Exception:
        pass
    return ""


def answer_market_check(question, owner_team_name, *, answer_asset_question, answer_team_strategy, team_future_summary, team_directive, team_future):
    candidates = market_check_candidates(owner_team_name)

    return {
        "answer_type": "market_check",
        "owner_team_name": owner_team_name,
        "summary": (
            "My first market-check would be:\n\n"
            f"{format_market_check(candidates)}\n\n"
            "My move: start with the highest-name-value player whose contract is pressuring flexibility. "
            "You are not forced to sell; you are testing whether another manager still prices the player above your internal contract-adjusted value."
        ),
        "team_future_context": team_future,
    }


def answer_trade_target(question, owner_team_name, *, answer_asset_question, answer_team_strategy, team_future_summary, team_directive, team_future):
    targets = trade_target_candidates(owner_team_name, question)

    return {
        "answer_type": "trade_target",
        "owner_team_name": owner_team_name,
        "summary": (
            "Best secondary trade targets based on your roster pressure points:\n\n"
            f"{format_trade_targets(targets)}\n\n"
            "My move: use QB/WR surplus or expensive WR money to hunt RB/TE value. "
            "This should be a secondary trade, not an all-in star chase."
        ),
        "team_future_context": team_future,
    }


def answer_trade_package(question, owner_team_name, *, answer_asset_question, answer_team_strategy, team_future_summary, team_directive, team_future):
    base = answer_team_strategy(question, owner_team_name)
    summary = _safe_summary(base)

    return {
        "answer_type": "trade_package",
        "owner_team_name": owner_team_name,
        "summary": (
            f"{team_future_summary}\n\n"
            f"{team_directive}\n\n"
            "Trade-package lens: build from need-fit, not equal-name value. The package should solve another manager’s roster problem while moving money or surplus off yours.\n\n"
            f"{summary}\n\n"
            "My move: structure offers as 2-for-1 or player-plus-pick deals where your outgoing asset has market appeal but is not central to your title path."
        ),
        "team_future_context": team_future,
    }


def answer_contract(question, owner_team_name, *, answer_asset_question, answer_team_strategy, team_future_summary, team_directive, team_future):
    asset = answer_asset_question(question, owner_team_name)
    summary = _safe_summary(asset)

    # Contract-specific rewrite layer.
    lower = summary.lower()

    player = "This player"
    if "on **" in summary:
        try:
            player = summary.split("On **", 1)[1].split("**", 1)[0]
        except Exception:
            player = "This player"

    contract_value = None
    salary = None
    years = None

    import re

    cv_match = re.search(r"contract value \*\*(\d+\.?\d*)\*\*", summary, re.I)
    salary_match = re.search(r"at \*\*\$(\d+\.?\d*)\*\*", summary, re.I)
    years_match = re.search(r"with \*\*(\d+\.?\d*)\s+years?\*\*", summary, re.I)

    if cv_match:
        contract_value = float(cv_match.group(1))
    if salary_match:
        salary = float(salary_match.group(1))
    if years_match:
        years = float(years_match.group(1))

    if contract_value is not None:
        if contract_value < 35:
            verdict = "Yes"
            label = "bad contract"
            why = "The contract value score is low enough that the salary is hurting roster flexibility."
        elif contract_value < 55:
            verdict = "Mostly yes"
            label = "inefficient contract"
            why = "The contract is not a disaster, but it is below where you want a clean value contract to be."
        elif contract_value < 70:
            verdict = "Not really"
            label = "acceptable contract"
            why = "The contract is workable, though not a major edge."
        else:
            verdict = "No"
            label = "good contract"
            why = "The contract is creating positive value relative to the player profile."
    else:
        verdict = "Unclear"
        label = "contract that needs more context"
        why = "I could not find a contract value score in the current player answer."

    details = []
    if salary is not None and years is not None:
        details.append(f"salary **${salary:g}** for **{years:g} years**")
    if contract_value is not None:
        details.append(f"contract value score **{contract_value:g}**")

    detail_text = ", ".join(details) if details else "available contract signals"

    return {
        "answer_type": "contract",
        "owner_team_name": owner_team_name,
        "summary": (
            f"{team_future_summary}\n\n"
            f"{team_directive}\n\n"
            f"**{verdict} — {player} is on a {label}.**\n\n"
            f"The read is based on {detail_text}. {why}\n\n"
            "Important distinction: this is a contract answer, not a player-talent answer. "
            "A player can still be worth holding while also being on a bad or inefficient contract.\n\n"
            f"Original player-context read:\n\n{summary}"
        ),
        "team_future_context": team_future,
    }


def answer_player_decision(question, owner_team_name, *, answer_asset_question, answer_team_strategy, team_future_summary, team_directive, team_future):
    asset = answer_asset_question(question, owner_team_name)
    if isinstance(asset, dict):
        asset["answer_type"] = asset.get("answer_type", "player_decision")
        asset["team_future_context"] = team_future
        asset["team_future_summary"] = team_future_summary
        asset["team_directive"] = team_directive
        summary = asset.get("summary") or ""
        player = _extract_player_from_summary(summary)
        if summary and player:
            asset["summary"] = summary + player_nfl_context_note(player, owner_team_name)
        return asset

    return {
        "answer_type": "player_decision",
        "owner_team_name": owner_team_name,
        "summary": f"{team_future_summary}\n\n{team_directive}\n\n{asset}" + player_nfl_context_note(_extract_player_from_summary(str(asset)), owner_team_name),
        "team_future_context": team_future,
    }


def orchestrate_gm_answer(
    question,
    owner_team_name,
    *,
    answer_asset_question,
    answer_team_strategy,
    team_future_summary,
    team_directive,
    team_future,
):
    intent = classify_gm_intent(question)
    mode = classify_conversation_mode(question)

    common = dict(
        answer_asset_question=answer_asset_question,
        answer_team_strategy=answer_team_strategy,
        team_future_summary=team_future_summary,
        team_directive=team_directive,
        team_future=team_future,
    )

    if intent == "market_check":
        raw = answer_market_check(question, owner_team_name, **common)
        raw["conversation_mode"] = mode
        return raw

    if intent == "trade_target":
        raw = answer_trade_target(question, owner_team_name, **common)
        raw["conversation_mode"] = mode
        return raw

    if intent == "trade_package":
        raw = answer_trade_package(question, owner_team_name, **common)
        raw["conversation_mode"] = mode
        return raw

    if intent == "contract":
        raw = answer_contract(question, owner_team_name, **common)
        return write_response(intent, mode, owner_team_name, raw)

    if intent == "player_decision":
        raw = answer_player_decision(question, owner_team_name, **common)
        return write_response(intent, mode, owner_team_name, raw)

    if intent == "player_profile":
        raw = answer_player_decision(question, owner_team_name, **common)
        return write_response("player_profile", mode, owner_team_name, raw)

    if intent == "draft":
        raw = answer_draft_question(question, owner_team_name)
        return raw

    if intent == "football_concept":
        raw = answer_football_concept(question, owner_team_name)
        return raw

    if intent in {"team_strategy", "league_analysis", "waiver", "lineup"}:
        raw = answer_team_strategy(question, owner_team_name)
        if isinstance(raw, dict):
            raw["answer_type"] = intent
        return write_response(intent, mode, owner_team_name, raw)

    asset = answer_asset_question(question, owner_team_name)
    raw = asset
    if isinstance(asset, dict):
        asset["answer_type"] = "general"
        asset["team_future_context"] = team_future
        asset["team_future_summary"] = team_future_summary
        asset["team_directive"] = team_directive
        raw = asset
    else:
        raw = {
            "answer_type": "general",
            "owner_team_name": owner_team_name,
            "summary": f"{team_future_summary}\n\n{team_directive}\n\n{asset}",
            "team_future_context": team_future,
        }

    return write_response("general", mode, owner_team_name, raw)

