"""Fixture canonical team state for League AI tests (schema canonical-team-state-v1)."""
from __future__ import annotations

LEAGUE_ID = "11111111-1111-1111-1111-111111111111"
SEASON = 2026
TOMMY = "t-tommy"
CHASE = "t-chase"
DYLAN = "t-dylan"


def _player(team, pid, name, pos, cap_hit, years, *, rookie=False, designation=None, ctype="veteran", source="fa_auction", salary=None):
    return {
        "contract_agreement_id": f"c-{pid}",
        "league_id": LEAGUE_ID,
        "league_team_id": team,
        "player_id": pid,
        "sleeper_player_id": pid,
        "player_name": name,
        "pos": pos,
        "owner_name": {TOMMY: "Tommy Bruggeman", CHASE: "Chase Seyforth", DYLAN: "Dylan Burruel"}[team],
        "team_name": {TOMMY: "Tommy Bruggeman", CHASE: "Chase's Chumps", DYLAN: "Dylan Burruel"}[team],
        "salary": salary if salary is not None else cap_hit,
        "cap_hit": cap_hit,
        "contract_type": ctype,
        "is_rookie": rookie,
        "status": "active",
        "season": SEASON,
        "contract_years_left": years,
        "roster_designation": designation,
        "initial_acquisition_type": source,
    }


def fixture_state() -> dict:
    roster = [
        _player(TOMMY, "p1", "Josh Allen", "QB", 60, 2),
        _player(TOMMY, "p2", "Bijan Robinson", "RB", 45, 3),
        _player(TOMMY, "p3", "Rome Odunze", "WR", 12, 2, rookie=True, ctype="rookie", source="rookie_draft"),
        _player(TOMMY, "p4", "Taxi Rookie", "WR", 4, 3, rookie=True, designation="taxi", ctype="rookie", source="rookie_draft", salary=8),
        _player(TOMMY, "p5", "Hurt Guy", "TE", 5, 1, designation="ir", salary=10),
        _player(CHASE, "p6", "Lamar Jackson", "QB", 55, 1),
        _player(CHASE, "p7", "CeeDee Lamb", "WR", 50, 4),
        _player(CHASE, "p8", "Waiver Guy", "RB", 1, 1, source="waiver"),
        _player(DYLAN, "p9", "Jalen Hurts", "QB", 48, 3),
        _player(DYLAN, "p10", "Retained Guy", "WR", 30, 2),
    ]
    teams = [
        {"league_team_id": TOMMY, "owner_name": "Tommy Bruggeman", "team_name": "Tommy Bruggeman"},
        {"league_team_id": CHASE, "owner_name": "Chase Seyforth", "team_name": "Chase's Chumps"},
        {"league_team_id": DYLAN, "owner_name": "Dylan Burruel", "team_name": "Dylan Burruel"},
    ]
    dead_cap = [
        {"id": "d1", "league_id": LEAGUE_ID, "league_team_id": TOMMY, "player_id": "x1", "player_name": "Dropped Vet",
         "owner_name": "Tommy Bruggeman", "team_name": "Tommy Bruggeman", "season": SEASON, "amount": 7.5, "adjustment_type": "dropped_player_charge"},
    ]
    retained = [
        {"contract_agreement_id": "c-p10", "retaining_league_team_id": CHASE, "player_name": "Retained Guy", "season": SEASON, "amount": 10},
    ]
    cap_adjustments = [
        {"league_id": LEAGUE_ID, "league_team_id": DYLAN, "owner_name": "Dylan Burruel", "team_name": "Dylan Burruel",
         "season": SEASON, "amount": 5, "adjustment_type": "trade_carryover", "counterparty_owner": "Chase Seyforth", "created_at": "2026-09-01"},
        {"league_id": LEAGUE_ID, "league_team_id": CHASE, "owner_name": "Chase Seyforth", "team_name": "Chase's Chumps",
         "season": SEASON, "amount": -5, "adjustment_type": "trade_carryover", "counterparty_owner": "Dylan Burruel", "created_at": "2026-09-01"},
    ]
    activity = [
        {"id": "e1", "league_team_id": CHASE, "player_id": "p8", "player_name": "Waiver Guy", "owner_name": "Chase Seyforth",
         "team_name": "Chase's Chumps", "action": "add", "event_type": "signed", "effective_at": "2026-09-16T10:00:00Z", "created_at": "2026-09-16T10:00:00Z"},
        {"id": "e2", "league_team_id": TOMMY, "player_id": "x1", "player_name": "Dropped Vet", "owner_name": "Tommy Bruggeman",
         "team_name": "Tommy Bruggeman", "action": "drop", "event_type": "released", "effective_at": "2026-09-10T10:00:00Z", "created_at": "2026-09-10T10:00:00Z"},
    ]
    draft_picks = [
        {"stable_pick_id": "2027-1-t-tommy", "draft_year": 2027, "round_number": 1, "asset_status": "available", "original_league_team_id": TOMMY, "current_owner_league_team_id": TOMMY,
         "original_team_name": "Tommy Bruggeman", "current_team_name": "Tommy Bruggeman"},
        {"stable_pick_id": "2027-1-t-chase", "draft_year": 2027, "round": 1, "original_league_team_id": CHASE, "current_owner_league_team_id": TOMMY,
         "original_team_name": "Chase's Chumps", "current_team_name": "Tommy Bruggeman"},
        {"stable_pick_id": "2027-2-t-tommy", "draft_year": 2027, "round": 2, "original_league_team_id": TOMMY, "current_owner_league_team_id": DYLAN,
         "original_team_name": "Tommy Bruggeman", "current_team_name": "Dylan Burruel"},
        {"stable_pick_id": "2028-1-t-chase", "draft_year": 2028, "round": 1, "original_league_team_id": CHASE, "current_owner_league_team_id": CHASE,
         "original_team_name": "Chase's Chumps", "current_team_name": "Chase's Chumps"},
    ]
    return {
        "schema": "canonical-team-state-v1",
        "league_id": LEAGUE_ID,
        "season": SEASON,
        "scope_team_id": None,
        "teams": teams,
        "roster": roster,
        "dead_cap": dead_cap,
        "retained_salary": retained,
        "activity": activity,
        "cap_adjustments": cap_adjustments,
        "draft_picks": draft_picks,
    }


def fixture_standings() -> list[dict]:
    return [
        {"Team": "Chase Seyforth", "Standing Points": 9, "PF": 410.5, "PA": 350.2, "Wins": 3, "Losses": 0, "Top 5": 3, "Games": 3, "rank": 1},
        {"Team": "Tommy Bruggeman", "Standing Points": 5, "PF": 380.0, "PA": 372.1, "Wins": 2, "Losses": 1, "Top 5": 1, "Games": 3, "rank": 2},
        {"Team": "Dylan Burruel", "Standing Points": 0, "PF": 290.3, "PA": 401.0, "Wins": 0, "Losses": 3, "Top 5": 0, "Games": 3, "rank": 3},
    ]


LEAGUE_RULES = {"league_id": LEAGUE_ID, "salary_cap": 225, "default_dead_cap_pct": 50, "max_contract_years": 4,
                "league_min_salary": 1, "rookie_contract_years": 3, "rookie_option_years": 1, "rookie_scale_enabled": True}
