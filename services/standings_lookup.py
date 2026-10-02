from __future__ import annotations

from typing import Iterable, Mapping

import pandas as pd


def build_sleeper_team_identities(
    users: Iterable[Mapping],
    rosters: Iterable[Mapping],
) -> dict[int, dict[str, object]]:
    """Index Sleeper identities by roster id without using display-name matching."""
    users_by_id = {
        str(user.get("user_id")): user
        for user in users
        if user.get("user_id") is not None
    }
    identities: dict[int, dict[str, object]] = {}

    for roster in rosters:
        raw_roster_id = roster.get("roster_id")
        if raw_roster_id is None:
            continue
        try:
            roster_id = int(raw_roster_id)
        except (TypeError, ValueError):
            continue

        owner_id = roster.get("owner_id")
        user = users_by_id.get(str(owner_id), {})
        identities[roster_id] = {
            "roster_id": roster_id,
            "owner_id": str(owner_id) if owner_id is not None else None,
            # Sleeper's league-users endpoint currently exposes the account
            # handle as display_name and commonly omits username entirely.
            "username": str(
                user.get("username") or user.get("display_name") or ""
            ).strip() or None,
        }

    return identities


def resolve_team_standings_row(
    standings: pd.DataFrame,
    *,
    sleeper_roster_id: object = None,
    sleeper_owner_id: object = None,
    identities_by_roster_id: Mapping[int, Mapping] | None = None,
) -> pd.DataFrame:
    """Resolve one standings row through Sleeper roster/owner identity only.

    Standings snapshots may contain the identifiers directly or may use the
    Sleeper username as their row key. In the latter case the username is
    derived from the requested roster/owner id via Sleeper's roster mapping;
    contract owner and team display names are deliberately never considered.
    """
    if standings.empty:
        return pd.DataFrame()

    work = standings.copy()
    roster_id = None
    if sleeper_roster_id is not None:
        try:
            roster_id = int(sleeper_roster_id)
        except (TypeError, ValueError):
            pass
    owner_id = str(sleeper_owner_id) if sleeper_owner_id is not None else None

    for column in ("sleeper_roster_id", "roster_id"):
        if roster_id is not None and column in work.columns:
            values = pd.to_numeric(work[column], errors="coerce")
            hit = work[values.eq(roster_id)]
            if not hit.empty:
                return hit.iloc[:1]

    for column in ("sleeper_owner_id", "owner_id", "sleeper_user_id"):
        if owner_id and column in work.columns:
            hit = work[work[column].astype(str).eq(owner_id)]
            if not hit.empty:
                return hit.iloc[:1]

    identity = None
    identities = identities_by_roster_id or {}
    if roster_id is not None:
        identity = identities.get(roster_id)
    if identity is None and owner_id:
        identity = next(
            (item for item in identities.values() if str(item.get("owner_id")) == owner_id),
            None,
        )

    username = str((identity or {}).get("username") or "").strip()
    if not username:
        return pd.DataFrame()

    for column in (
        "owner", "Team", "owner_name", "username", "sleeper_username",
        "sleeper_handle", "handle", "user_name",
    ):
        if column not in work.columns:
            continue
        hit = work[work[column].astype(str).str.strip().str.casefold().eq(username.casefold())]
        if not hit.empty:
            return hit.iloc[:1]

    return pd.DataFrame()


def standings_for_team_cards(standings: pd.DataFrame) -> pd.DataFrame:
    """Normalize the Standings-page snapshot for Team/My Team metric cards."""
    if standings.empty:
        return pd.DataFrame()
    return standings.rename(
        columns={
            "Wins": "wins",
            "Losses": "losses",
            "PF": "pf",
            "PF Per Game": "ppg",
            "Standing Points": "standing_points",
            "Rank": "rank",
        }
    ).copy()
