from __future__ import annotations

import re


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().strip())


def classify_gm_intent(question: str) -> str:
    q = normalize(question)

    if any(x in q for x in [
        "market-check", "market check", "shop first", "who should i shop",
        "who should i move", "sell high", "test the market"
    ]):
        return "market_check"

    if any(x in q for x in [
        "secondary trade", "trade target", "trade targets", "who should i trade for",
        "who can i get", "upgrade at", "upgrade rb", "upgrade te", "acquire"
    ]):
        return "trade_target"

    if any(x in q for x in [
        "package", "offer", "trade proposal", "4 team trade", "three team trade",
        "multi-team", "multi team"
    ]):
        return "trade_package"

    if any(x in q for x in [
        "what should i do with", "should i trade", "should i drop", "should i cut",
        "should i keep", "hold", "sell", "buy"
    ]):
        return "player_decision"

    if any(x in q for x in [
        "offensive line", "how much does", "how much should", "draft capital matter",
        "coaching influence", "scheme influence", "why does", "football context"
    ]):
        return "football_concept"

    if any(x in q for x in [
        "contract", "salary", "cap", "dead cap", "overpaid", "underpaid",
        "restructure", "drop penalty", "worth $", "worth it", "worth the money"
    ]):
        return "contract"

    if any(x in q for x in [
        "league", "contenders", "rebuilders", "who needs", "team needs"
    ]):
        return "league_analysis"

    if any(x in q for x in [
        "draft", "rookie", "pick", "picks", "1.01", "1.02", "1.03",
        "hampton or jeanty", "jeanty or hampton", "safest profile", "highest ceiling"
    ]):
        return "draft"

    if any(x in q for x in [
        "waiver", "free agent", "fa ", "available"
    ]):
        return "waiver"

    if any(x in q for x in [
        "lineup", "start", "sit", "starter"
    ]):
        return "lineup"

    if any(x in q for x in [
        "plan", "strategy", "direction", "contend", "rebuild", "retool",
        "strength", "weakness", "weaknesses", "next move", "3-step", "three-step"
    ]):
        return "team_strategy"

    if any(x in q for x in [
        "risky", "risk", "profile", "what do you think of", "thoughts on",
        "tell me about", "break down"
    ]):
        return "player_profile"

    return "general"
