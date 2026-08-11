from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from season_engine.models import LeagueSeason
from .models import CapturePlan, SourceBundle


def build_capture_plan(*, season: LeagueSeason, source: SourceBundle,
                       league_teams: list[dict], player_names: dict[str, str] | None = None,
                       existing_counts: dict[str, int] | None = None) -> CapturePlan:
    errors: list[dict] = []
    warnings: list[dict] = []
    player_names = player_names or {}
    if not season.is_active:
        errors.append(_issue("source_not_active", "The requested source LeagueSeason is not active."))
    if str(source.league.get("season")) != str(season.season):
        errors.append(_issue("source_season_mismatch", "Sleeper season does not match LeagueSeason."))
    if len(source.rosters) != 10:
        errors.append(_issue("roster_count", f"Expected 10 Sleeper rosters; found {len(source.rosters)}."))

    by_roster: dict[int, list[dict]] = {}
    by_user: dict[str, list[dict]] = {}
    for team in league_teams:
        if str(team.get("league_id")) != season.league_id:
            errors.append(_issue("cross_league_team", f"Team {team.get('id')} belongs to another league."))
            continue
        if team.get("sleeper_roster_id") is not None:
            by_roster.setdefault(int(team["sleeper_roster_id"]), []).append(team)
        if team.get("sleeper_user_id"):
            by_user.setdefault(str(team["sleeper_user_id"]), []).append(team)

    users = {str(u.get("user_id")): u for u in source.users if u.get("user_id")}
    mappings, roster_to_team = [], {}
    for roster in sorted(source.rosters, key=lambda r: int(r["roster_id"])):
        rid, owner = int(roster["roster_id"]), str(roster.get("owner_id") or "")
        candidates = by_roster.get(rid, [])
        method = "league_teams.sleeper_roster_id"
        if not candidates and owner:
            candidates, method = by_user.get(owner, []), "league_teams.sleeper_user_id"
        candidates = list({str(x["id"]): x for x in candidates}.values())
        if len(candidates) != 1:
            errors.append(_issue("team_mapping", f"Roster {rid} maps to {len(candidates)} canonical teams.", roster_id=rid))
            continue
        team = candidates[0]
        if str(team["id"]) in roster_to_team.values():
            errors.append(_issue("duplicate_team_mapping", f"Team {team['id']} maps to multiple rosters."))
            continue
        roster_to_team[rid] = str(team["id"])
        user = users.get(owner, {})
        mappings.append({
            "league_season_id": season.id, "league_team_id": str(team["id"]),
            "sleeper_roster_id": rid, "sleeper_owner_id": owner or None,
            "sleeper_user_id": owner or None,
            "team_name_snapshot": (user.get("metadata") or {}).get("team_name") or team.get("team_name"),
            "owner_name_snapshot": team.get("owner_name"), "mapping_source": method,
            "mapping_confidence": "exact",
        })

    matchups = _normalize_matchups(season, source, roster_to_team, errors, warnings)
    standings = _normalize_standings(season, source, roster_to_team)
    brackets = _normalize_brackets(season, source, roster_to_team, errors)
    assignments = _normalize_rosters(season, source, roster_to_team, player_names, errors)
    champions = {row.get("winner_league_team_id") for row in brackets if row.get("bracket_type") == "winner" and row.get("placement") == 1}
    if len(champions) != 1:
        errors.append(_issue("champion", f"Expected one derivable champion; found {len(champions)}."))

    normalized = {"league": source.league, "users": source.users, "rosters": source.rosters,
                  "matchups": source.matchups_by_week, "winner": source.winners_bracket, "loser": source.losers_bracket}
    fingerprint = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return CapturePlan(
        league_id=season.league_id, league_season_id=str(season.id), season=season.season,
        sleeper_league_id=str(season.sleeper_league_id), generated_at=datetime.now(timezone.utc).isoformat(),
        source_fingerprint=fingerprint, idempotency_key=f"pre_rollover_snapshot:{season.id}:sleeper:v1",
        team_mappings=tuple(mappings), matchups=tuple(matchups), standings=tuple(standings),
        brackets=tuple(brackets), roster_assignments=tuple(assignments), warnings=tuple(warnings),
        blocking_errors=tuple(errors), existing_counts=existing_counts or {},
    )


def _normalize_matchups(season, source, roster_to_team, errors, warnings):
    rows = []
    playoff_start = int((source.league.get("settings") or {}).get("playoff_week_start") or 99)
    for week, entries in sorted(source.matchups_by_week.items()):
        groups: dict[int, list[dict]] = {}
        for entry in entries:
            if entry.get("matchup_id") is None:
                rid = int(entry["roster_id"])
                if rid not in roster_to_team:
                    errors.append(_issue("matchup_mapping", f"Week {week} bye entry has unresolved roster {rid}."))
                else:
                    warnings.append(_issue("postseason_bye", f"Week {week} roster {rid} has no official Sleeper matchup ID and was not fabricated into a matchup.", roster_id=rid, week=week))
                continue
            groups.setdefault(int(entry["matchup_id"]), []).append(entry)
        for mid, pair in sorted(groups.items()):
            if mid <= 0 or len(pair) != 2:
                errors.append(_issue("matchup_shape", f"Week {week} matchup {mid} has {len(pair)} participants.")); continue
            pair.sort(key=lambda x: int(x["roster_id"]))
            r1, r2 = int(pair[0]["roster_id"]), int(pair[1]["roster_id"])
            if r1 not in roster_to_team or r2 not in roster_to_team:
                errors.append(_issue("matchup_mapping", f"Week {week} matchup {mid} has unresolved participants.")); continue
            p1, p2 = float(pair[0].get("points") or 0), float(pair[1].get("points") or 0)
            rows.append({"league_season_id": season.id, "week": week, "sleeper_matchup_id": mid,
                "league_team_1_id": roster_to_team[r1], "league_team_2_id": roster_to_team[r2],
                "sleeper_roster_1_id": r1, "sleeper_roster_2_id": r2, "team_1_points": p1, "team_2_points": p2,
                "winner_league_team_id": None if p1 == p2 else roster_to_team[r1 if p1 > p2 else r2],
                "result": "tie" if p1 == p2 else "decided", "phase": "regular" if week < playoff_start else "playoff",
                "source_payload": pair})
    return rows


def _normalize_standings(season, source, roster_to_team):
    ranked = sorted(source.rosters, key=lambda r: (-int((r.get("settings") or {}).get("wins") or 0),
        -_decimal(r.get("settings") or {}, "fpts")))
    rank = {int(r["roster_id"]): i + 1 for i, r in enumerate(ranked)}
    return [{"league_season_id": season.id, "league_team_id": roster_to_team[int(r["roster_id"])],
        "wins": int((r.get("settings") or {}).get("wins") or 0), "losses": int((r.get("settings") or {}).get("losses") or 0),
        "ties": int((r.get("settings") or {}).get("ties") or 0), "points_for": _decimal(r.get("settings") or {}, "fpts"),
        "points_against": _decimal(r.get("settings") or {}, "fpts_against"), "regular_season_rank": rank[int(r["roster_id"])],
        "streak": (r.get("metadata") or {}).get("streak"), "source_payload": r.get("settings") or {}}
        for r in source.rosters if int(r["roster_id"]) in roster_to_team]


def _normalize_brackets(season, source, roster_to_team, errors):
    rows = []
    for kind, bracket in (("winner", source.winners_bracket), ("consolation", source.losers_bracket)):
        for item in bracket:
            participants = [item.get("t1"), item.get("t2")]
            if any(x is not None and int(x) not in roster_to_team for x in participants):
                errors.append(_issue("bracket_mapping", f"Unresolved participant in {kind} bracket match {item.get('m')}.")); continue
            rows.append({"league_season_id": season.id, "bracket_type": kind, "round": int(item.get("r") or 0),
                "sleeper_bracket_match_id": int(item.get("m") or 0),
                "team_1_id": roster_to_team.get(int(item["t1"])) if item.get("t1") is not None else None,
                "team_2_id": roster_to_team.get(int(item["t2"])) if item.get("t2") is not None else None,
                "winner_league_team_id": roster_to_team.get(int(item["w"])) if item.get("w") is not None else None,
                "loser_league_team_id": roster_to_team.get(int(item["l"])) if item.get("l") is not None else None,
                "placement": item.get("p"), "source_payload": item})
    return rows


def _normalize_rosters(season, source, roster_to_team, player_names, errors):
    rows, seen = [], set()
    for roster in source.rosters:
        rid = int(roster["roster_id"]); team = roster_to_team.get(rid)
        if not team: continue
        taxi, reserve, starters = set(roster.get("taxi") or []), set(roster.get("reserve") or []), set(roster.get("starters") or [])
        if taxi & reserve: errors.append(_issue("designation_conflict", f"Roster {rid} has taxi/reserve overlap."))
        for pid in roster.get("players") or []:
            if pid in seen: errors.append(_issue("duplicate_player", f"Player {pid} appears on multiple rosters.")); continue
            seen.add(pid)
            designation = "taxi" if pid in taxi else "ir" if pid in reserve else "active" if pid in starters else "bench"
            rows.append({"league_season_id": season.id, "league_team_id": team, "sleeper_player_id": str(pid),
                "player_name_snapshot": player_names.get(str(pid)), "roster_designation": designation, "source": "sleeper"})
    return rows


def _decimal(settings, field):
    return float(settings.get(field) or 0) + float(settings.get(f"{field}_decimal") or 0) / 100


def _issue(code, message, **context): return {"code": code, "message": message, "context": context}
