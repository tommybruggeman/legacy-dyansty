"""This week's win odds for a team, as the Front Office saved them: the same number the app's team pages show."""
from __future__ import annotations

import streamlit as st


@st.cache_data(ttl=600, show_spinner=False)
def _saved(league_id: str, season: int, week: int) -> dict:
    from auth import auth_client
    from league_ai import matchup_odds

    return matchup_odds.saved_week(auth_client(), league_id, season, week)[1]


def win_odds_text(league_id: str | None, season: int | None, owner_name: str | None) -> str | None:
    """'62% to win vs Grady' for this week, or None before the Front Office has saved this week's odds."""
    if not league_id or not season or not owner_name:
        return None
    try:
        from api import league as league_api

        rows = _saved(str(league_id), int(season), int(league_api.current_week()))
    except Exception:
        return None
    row = next((r for o, r in rows.items() if o.casefold() == str(owner_name).casefold()), None)
    if not row or row.get("win_pct") is None:
        return None
    opp = str(row.get("opp_owner") or "").split(" ")[0]
    return f"{round(float(row['win_pct']))}% to win" + (f" vs {opp}" if opp else "")
