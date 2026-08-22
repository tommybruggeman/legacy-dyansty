from typing import Dict, Any, List


def route_data_needs(open_needs: List[Dict[str, Any]]) -> Dict[str, Any]:
    routes = {
        "identity": [],
        "draft_profile": [],
        "situation": [],
        "production": [],
        "market": [],
        "contract": [],
        "risk": [],
        "general": [],
    }

    for need in open_needs or []:
        name = need.get("need") or ""

        if name == "identity_complete":
            routes["identity"].append(need)
        elif name == "draft_profile_complete":
            routes["draft_profile"].append(need)
        elif name == "situation_complete":
            routes["situation"].append(need)
        elif name == "production_complete":
            routes["production"].append(need)
        elif name == "market_complete":
            routes["market"].append(need)
        elif name == "contract_complete":
            routes["contract"].append(need)
        elif name == "risk_complete":
            routes["risk"].append(need)
        else:
            routes["general"].append(need)

    next_route = None
    for key in ["identity", "draft_profile", "situation", "production", "market", "contract", "risk", "general"]:
        if routes[key]:
            next_route = key
            break

    return {
        "next_route": next_route,
        "routes": routes,
        "summary": f"Next resolver: {next_route}" if next_route else "No open resolver needs.",
    }
