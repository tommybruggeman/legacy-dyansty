from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class RookiePickDecision:
    decision: str
    pick_zone: str
    need_weight: float
    value_weight: float
    tier_gap: float
    explanation: str


def pick_zone_from_label(pick_label: str) -> str:
    p = (pick_label or "").replace("#", "").strip()

    if p in {"1.01", "1"}:
        return "elite"
    if p in {"1.02", "1.03"}:
        return "premium"
    if p in {"1.04", "1.05", "1.06"}:
        return "middle"
    return "late"


def weights_for_pick_zone(zone: str) -> Dict[str, float]:
    if zone == "elite":
        return {"value": 0.85, "need": 0.15}
    if zone == "premium":
        return {"value": 0.75, "need": 0.25}
    if zone == "middle":
        return {"value": 0.60, "need": 0.40}
    return {"value": 0.50, "need": 0.50}


def decide_rookie_pick_strategy(
    *,
    pick_label: str,
    best_player_name: Optional[str] = None,
    best_player_pos: Optional[str] = None,
    need_player_name: Optional[str] = None,
    need_player_pos: Optional[str] = None,
    best_player_score: float = 90.0,
    need_player_score: float = 80.0,
    team_need_score: float = 70.0,
) -> RookiePickDecision:
    zone = pick_zone_from_label(pick_label)
    weights = weights_for_pick_zone(zone)

    tier_gap = best_player_score - need_player_score

    best = best_player_name or "the best player available"
    best_pos = best_player_pos or "BPA"
    need = need_player_name or "the need-based option"
    need_pos = need_player_pos or "need"

    # Hard rule: massive tier gaps should not be overridden by need.
    if zone in {"elite", "premium"} and tier_gap >= 8:
        return RookiePickDecision(
            decision="DRAFT_BEST_PLAYER_OR_TRADE_BACK",
            pick_zone=zone,
            need_weight=weights["need"],
            value_weight=weights["value"],
            tier_gap=tier_gap,
            explanation=(
                f"At {pick_label}, the tier gap matters more than roster need. "
                f"If {best} ({best_pos}) is clearly above {need} ({need_pos}), "
                f"do not force the need pick. Draft the elite player or trade back."
            ),
        )

    # Strong need can break close tiers.
    if tier_gap <= 4 and team_need_score >= 65:
        return RookiePickDecision(
            decision="NEED_CAN_BREAK_TIE",
            pick_zone=zone,
            need_weight=weights["need"],
            value_weight=weights["value"],
            tier_gap=tier_gap,
            explanation=(
                f"The tier gap is small enough that roster need can matter. "
                f"At {pick_label}, taking {need} ({need_pos}) is reasonable if he is in the same tier as {best}."
            ),
        )

    # Middle/late picks can lean more into need.
    if zone in {"middle", "late"} and team_need_score >= 70 and tier_gap <= 7:
        return RookiePickDecision(
            decision="DRAFT_FOR_NEED_WITHIN_TIER",
            pick_zone=zone,
            need_weight=weights["need"],
            value_weight=weights["value"],
            tier_gap=tier_gap,
            explanation=(
                f"At this point in the draft, team need can carry more weight because the prospect gap is smaller. "
                f"If {need} fills a real roster hole and is close in value, drafting for need is acceptable."
            ),
        )

    return RookiePickDecision(
        decision="DRAFT_BEST_PLAYER",
        pick_zone=zone,
        need_weight=weights["need"],
        value_weight=weights["value"],
        tier_gap=tier_gap,
        explanation=(
            f"The safer move is to preserve player value. "
            f"Take {best} unless a trade offer turns the pick into better roster value."
        ),
    )
