"""Owner profiles: the name, team name and picture each owner picks in Settings (owner_profiles).

They are ours, not Sleeper's: an owner's own picture wins over their Sleeper picture, and their own name wins over the
name we had for them. Display only: league_teams.owner_name stays the key the rest of the app matches on.
"""
from __future__ import annotations

from typing import Any, Mapping

from api import cache

NAME_MAX = 40


def by_team(client: Any, league_id: str) -> dict[str, dict[str, Any]]:
    """league_team_id -> profile row, shared for a minute (Settings clears it on save)."""
    def load() -> dict[str, dict[str, Any]]:
        try:
            rows = client.table("owner_profiles").select("*").eq("league_id", league_id).execute().data or []
        except Exception:  # table not created yet: everyone keeps the names and pictures they had
            return {}
        return {str(r["league_team_id"]): r for r in rows if r.get("league_team_id")}
    return cache.cached(("profiles", league_id), 60, load)


def by_roster(client: Any, league_id: str) -> dict[int, dict[str, Any]]:
    """roster id -> profile row."""
    profiles = by_team(client, league_id)
    if not profiles:
        return {}
    def load() -> list[dict[str, Any]]:
        return client.table("league_teams").select("id,sleeper_roster_id").eq("league_id", league_id).execute().data or []
    out: dict[int, dict[str, Any]] = {}
    for row in cache.cached(("team_rosters", league_id), 600, load):
        profile = profiles.get(str(row.get("id")))
        try:
            roster_id = int(row.get("sleeper_roster_id"))
        except (TypeError, ValueError):
            continue
        if profile:
            out[roster_id] = profile
    return out


def names(profiles: Mapping[int, Mapping[str, Any]], current: Mapping[int, str]) -> dict[int, str]:
    """current roster names with each owner's own name laid over."""
    return {rid: (_text(profiles.get(rid, {}).get("display_name")) or name) for rid, name in current.items()}


def pictures(profiles: Mapping[int, Mapping[str, Any]], current: Mapping[int, str | None]) -> dict[int, str | None]:
    """current roster pictures with each owner's own picture laid over (and added for rosters with none)."""
    out = dict(current)
    for rid, profile in profiles.items():
        pic = _text(profile.get("picture_url"))
        if pic:
            out[rid] = pic
    return out


def overlay_team(row: dict[str, Any], profile: Mapping[str, Any] | None) -> dict[str, Any]:
    """A team row (owner_name / team_name) with the owner's own names, in place."""
    if profile:
        if _text(profile.get("display_name")):
            row["owner_name"] = _text(profile["display_name"])
        if _text(profile.get("team_name")):
            row["team_name"] = _text(profile["team_name"])
        row["team_abbr"] = _text(profile.get("team_abbr")) or abbr(row.get("team_name") or "")
        if _text(profile.get("picture_url")):
            row["avatar"] = _text(profile["picture_url"])
    return row


def abbr(team_name: str) -> str:
    """A 2-4 letter short form of a team name when the owner hasn't picked one: initials, else the first letters."""
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in team_name).split() if w]
    if len(words) >= 2:
        return "".join(w[0] for w in words)[:4].upper()
    return (words[0][:3] if words else "").upper()


def clean(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:NAME_MAX] or None


def _text(value: Any) -> str:
    return str(value or "").strip()


def team_labels(client: Any, league_id: str) -> dict[int, dict[str, str]]:
    """roster id -> {team_name, team_abbr}: the owner's own team name from Settings, else the one we had."""
    def load() -> list[dict[str, Any]]:
        return client.table("league_teams").select("id,team_name,owner_name,sleeper_roster_id").eq("league_id", league_id).execute().data or []
    mine = by_team(client, league_id)
    out: dict[int, dict[str, str]] = {}
    for row in cache.cached(("team_labels", league_id), 600, load):
        try:
            rid = int(row.get("sleeper_roster_id"))
        except (TypeError, ValueError):
            continue
        profile = mine.get(str(row.get("id"))) or {}
        name = _text(profile.get("team_name")) or _text(row.get("team_name"))
        if name and name.casefold() != _text(row.get("owner_name")).casefold():  # no real team name yet: owner name stays
            out[rid] = {"team_name": name, "team_abbr": _text(profile.get("team_abbr")) or abbr(name)}
    return out


def label_teams(payload: Any, labels: Mapping[int, Mapping[str, str]]) -> Any:
    """Adds team_name / team_abbr to every dict in a response that names a roster (has roster_id and name), in place.
    Pages about teams competing (Standings, Teams, Matchups, Home) show these; the rest keep owner names."""
    if isinstance(payload, dict):
        rid = payload.get("roster_id")
        if "name" in payload and isinstance(rid, int) and rid in labels:
            payload["team_name"] = labels[rid]["team_name"]
            payload["team_abbr"] = labels[rid]["team_abbr"]
        for value in payload.values():
            label_teams(value, labels)
    elif isinstance(payload, list):
        for value in payload:
            label_teams(value, labels)
    return payload
