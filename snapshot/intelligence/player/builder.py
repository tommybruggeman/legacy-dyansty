from __future__ import annotations

from snapshot.intelligence.player.identity import build_identity
from snapshot.intelligence.player.production import build_production
from snapshot.intelligence.player.market import build_market
from snapshot.intelligence.player.contract import build_contract
from snapshot.intelligence.player.dynasty import build_dynasty
from snapshot.intelligence.player.situation import build_situation
from snapshot.intelligence.player.opinion import build_opinion
from snapshot.intelligence.player.evidence import build_evidence


def build_player_intelligence(
    player_name: str,
    owner_team_name: str | None = None,
    season: int | None = None,
    include_raw: bool = False,
) -> dict:
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    identity = build_identity(player_name, owner_team_name)
    production = build_production(player_name, owner_team_name)
    market = build_market(player_name, owner_team_name)
    contract = build_contract(player_name, owner_team_name)
    dynasty = build_dynasty(player_name, owner_team_name)
    situation = build_situation(player_name, owner_team_name)
    opinion = build_opinion(player_name, owner_team_name)
    evidence = build_evidence(identity.get("name") or player_name, season)

    player = {
        "player_name": identity.get("name") or player_name,
        "identity": identity,
        "production": production,
        "market": market,
        "contract": contract,
        "dynasty": dynasty,
        "situation": situation,
        "opinion": opinion,
        "evidence": evidence,
        "summary": build_player_intelligence_summary(
            identity,
            production,
            market,
            contract,
            dynasty,
            situation,
            opinion,
            evidence,
        ),
    }

    if not include_raw:
        _strip_raw(player)

    return player


def _strip_raw(obj):
    if isinstance(obj, dict):
        obj.pop("_raw", None)
        for value in obj.values():
            _strip_raw(value)
    elif isinstance(obj, list):
        for item in obj:
            _strip_raw(item)


def build_player_intelligence_summary(
    identity: dict,
    production: dict,
    market: dict,
    contract: dict,
    dynasty: dict,
    situation: dict,
    opinion: dict,
    evidence: dict,
) -> str:
    name = identity.get("name")
    pos = identity.get("pos")
    team = identity.get("nfl_team")
    salary = contract.get("salary")
    years = contract.get("years")
    ppg = production.get("season_ppg") or production.get("expected_ppg") or production.get("historical_ppg")
    asset = dynasty.get("dynasty_asset_score") or dynasty.get("asset_value_score") or dynasty.get("final_rookie_score")
    rec = opinion.get("recommendation") or opinion.get("asset_recommendation") or "NO_RECOMMENDATION"
    conf = evidence.get("confidence")

    return (
        f"{name} ({pos}, {team}) — salary ${salary}, years {years}, "
        f"PPG {ppg}, asset {asset}, recommendation {rec}, evidence confidence {conf}."
    )


if __name__ == "__main__":
    tests = [
        ("Josh Allen", "Tommy Bruggeman"),
        ("Garrett Wilson", "Tommy Bruggeman"),
        ("Fernando Mendoza", "Tommy Bruggeman"),
        ("Chandler Morris", "Tommy Bruggeman"),
    ]

    for name, owner in tests:
        print("\n" + "=" * 100)
        pi = build_player_intelligence(name, owner)
        print(pi["summary"])
        print("IDENTITY:", pi["identity"])
        print("PRODUCTION:", pi["production"])
        print("CONTRACT:", pi["contract"])
        print("DYNASTY:", pi["dynasty"])
        print("OPINION:", pi["opinion"])
        print("EVIDENCE:", {
            "confidence": pi["evidence"]["confidence"],
            "open_task_count": pi["evidence"]["open_task_count"],
            "missing_fields": pi["evidence"]["missing_fields"][:10],
        })
