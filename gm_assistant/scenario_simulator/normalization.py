from __future__ import annotations

import re
from typing import Any

from gm_assistant.scenario_simulator.models import (
    MovePlayerToIR,
    MovePlayerToTaxi,
    ReleasePlayer,
    ScenarioAction,
    TradePickOut,
    TradePlayerIn,
    TradePlayerOut,
)


SCENARIO_TRIGGERS = (
    "what happens if",
    "what would happen if",
    "what would my cap be if",
    "what would my roster look like if",
    "simulate",
)


def is_scenario_question(text: Any) -> bool:
    normalized = normalize_text(text)
    if not any(trigger in normalized for trigger in SCENARIO_TRIGGERS):
        return False
    return bool(parse_scenario_actions(text))


def parse_scenario_actions(text: Any) -> list[ScenarioAction]:
    raw = str(text or "").strip()
    normalized = normalize_text(raw)
    if not normalized:
        return []

    trade_match = re.search(r"\btrad(?:e|ed|ing)\s+(.+?)\s+for\s+(.+?)(?:[?.!]|$)", raw, flags=re.IGNORECASE)
    if trade_match:
        out_name = _clean_asset_text(trade_match.group(1))
        in_name = _clean_asset_text(trade_match.group(2))
        if out_name and in_name:
            return [TradePlayerOut(player_name=out_name), TradePlayerIn(player_name=in_name)]

    pick_match = re.search(r"\btrad(?:e|ed|ing)\s+my\s+(20\d{2})\s+(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\b", normalized)
    if pick_match:
        return [TradePickOut(season=int(pick_match.group(1)), round=_round_number(pick_match.group(2)))]

    release_match = re.search(r"\b(?:cut|drop|release|releasing)\s+(.+?)(?:[?.!]|$)", raw, flags=re.IGNORECASE)
    if release_match:
        name = _clean_asset_text(release_match.group(1))
        if name:
            return [ReleasePlayer(player_name=name)]

    trade_out_match = re.search(r"\btrad(?:e|ed|ing)\s+(.+?)(?:[?.!]|$)", raw, flags=re.IGNORECASE)
    if trade_out_match:
        name = _clean_asset_text(trade_out_match.group(1))
        if name and not _looks_like_pick(name):
            return [TradePlayerOut(player_name=name)]

    ir_match = re.search(r"\bmove\s+(.+?)\s+to\s+(?:ir|injured reserve)\b", raw, flags=re.IGNORECASE)
    if ir_match:
        name = _clean_asset_text(ir_match.group(1))
        if name:
            return [MovePlayerToIR(player_name=name)]

    taxi_match = re.search(r"\bmove\s+(.+?)\s+to\s+taxi\b", raw, flags=re.IGNORECASE)
    if taxi_match:
        name = _clean_asset_text(taxi_match.group(1))
        if name:
            return [MovePlayerToTaxi(player_name=name)]

    return []


def normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9. ]+", " ", str(text or "").lower())).strip()


def normalize_player_name(text: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())).strip()


def _clean_asset_text(text: Any) -> str:
    raw = str(text or "").strip(" ?.!,")
    raw = re.sub(r"^(?:i|we)\s+", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"^(?:my|our)\s+", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s+", " ", raw).strip(" ?.!,")
    return raw


def _round_number(text: str) -> int:
    return {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5}[text]


def _looks_like_pick(text: str) -> bool:
    return any(word in normalize_text(text).split() for word in ("first", "1st", "second", "2nd", "third", "3rd", "pick"))
