from __future__ import annotations

from .common import find_one, pick, num


def build_contract(player_name: str, owner_team_name: str | None = None) -> dict:
    universe = find_one("player_universe", player_name, owner_team_name)
    identity = find_one("player_identity_context", player_name)
    asset = find_one("roster_asset_values", player_name, owner_team_name)
    rec = find_one("player_recommendations", player_name, owner_team_name)

    return {
        "salary": num(pick(universe.get("salary"), asset.get("salary"), rec.get("salary"))),
        "years": num(pick(universe.get("years"), asset.get("years"), rec.get("years"))),
        "has_contract": universe.get("has_contract"),
        "contract_total_years": num(universe.get("contract_total_years")),
        "recommended_years": num(universe.get("recommended_years")),
        "contract_score": num(identity.get("contract_score")),
        "contract_efficiency_score": num(universe.get("contract_efficiency_score")),
        "contract_efficiency_grade": universe.get("contract_efficiency_grade"),
        "contract_cost_score": num(asset.get("contract_cost_score")),
        "contract_value_score": num(asset.get("contract_value_score")),
        "contract_risk_score": num(asset.get("contract_risk_score")),
        "term_risk_score": num(asset.get("term_risk_score")),
        "position_contract_rank": universe.get("position_contract_rank"),
        "position_contract_percentile": num(universe.get("position_contract_percentile")),
        "_raw": {
            "player_universe": universe,
            "player_identity_context": identity,
            "roster_asset_values": asset,
            "player_recommendations": rec,
        },
    }
