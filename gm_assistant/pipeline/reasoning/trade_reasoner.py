from __future__ import annotations

from gm_assistant.pipeline.models import EvidencePack, ReasoningResult


def _num(row: dict, *keys: str, default: float = 0.0) -> float:
    for k in keys:
        try:
            val = row.get(k)
            if val is not None:
                return float(val)
        except Exception:
            pass
    return default


def reason_trade_return(evidence: EvidencePack) -> ReasoningResult:
    player = evidence.player or {}
    name = (
        player.get("player_name")
        or player.get("name")
        or (evidence.understanding.get("players") or ["that player"])[0]
    )

    salary = _num(player, "salary", "contract_salary")
    years = _num(player, "years", "contract_years")
    asset = _num(player, "asset", "asset_score", "dynasty_value")
    ppg = _num(player, "ppg", "season_ppg", "projected_ppg")
    risk = _num(player, "contract_risk")

    actions = [
        "Ask first for a starting-caliber RB or TE upgrade.",
        "If the other manager will not pay that, ask for a cheaper productive starter plus a pick.",
        "Do not accept a pure cap dump unless the pick/young asset return is meaningful.",
    ]

    reasons = []
    risks = []

    if salary >= 25:
        reasons.append(f"{name} carries meaningful cap weight at roughly ${salary:.0f}.")
    if asset >= 55:
        reasons.append(f"{name} still has real dynasty/market value, so the return needs to be substantial.")
    if ppg > 0:
        reasons.append(f"The production profile is usable at about {ppg:.1f} PPG.")
    if risk >= 60:
        risks.append("Contract risk is elevated, so waiting too long could reduce leverage.")
    if not player:
        risks.append("Player evidence was not fully loaded, so treat this as a framework rather than a final price.")

    if not reasons:
        reasons.append("The main idea is to set the price before shopping, not shop first and react later.")

    return ReasoningResult(
        decision="TRADE_RETURN_VALUE",
        recommendation="SHOP_ONLY_AT_PRICE",
        confidence=0.82 if player else 0.65,
        reasons=reasons,
        risks=risks,
        actions=actions,
        evidence={
            "player": name,
            "salary": salary,
            "years": years,
            "asset": asset,
            "ppg": ppg,
            "contract_risk": risk,
        },
    )
