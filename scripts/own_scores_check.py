"""Our own scores vs Sleeper's, every week of each active season (what api.own_league.sync now shows everywhere).
Prints counts only (the repo and its Actions logs are public): teams whose total differs, games whose winner flips,
and weekly top-5 changes. Saves nothing.

    python -m scripts.own_scores_check
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _winners(rows):
    games = {}
    for r in rows:
        if r.get("matchup_id"):
            games.setdefault(r["matchup_id"], []).append(r)
    out = {}
    for mid, pair in games.items():
        if len(pair) == 2:
            a, b = pair
            pa, pb = float(a.get("points") or 0), float(b.get("points") or 0)
            out[mid] = None if abs(pa - pb) < 0.005 else (a["roster_id"] if pa > pb else b["roster_id"])
    return out


def _top5(rows):
    ranked = sorted(rows, key=lambda r: -float(r.get("points") or 0))
    return {r["roster_id"] for r in ranked[:5]}


def _stub_streamlit() -> None:
    """api.league imports website helpers that import streamlit; the checks runner doesn't install it."""
    import types
    from unittest.mock import MagicMock
    if "streamlit" in sys.modules:
        return
    try:
        import streamlit  # noqa: F401
        return
    except ImportError:
        pass

    def cache(*args, **kwargs):
        return args[0] if args and callable(args[0]) else (lambda f: f)
    st = types.ModuleType("streamlit")
    st.cache_data = st.cache_resource = cache
    st.session_state = {}
    st.secrets = {}
    st.__getattr__ = lambda name: MagicMock()
    sys.modules["streamlit"] = st


def main() -> int:
    _stub_streamlit()
    from api import league, own_league, sleeper
    from league_ai.settings_store import load_settings
    from pipeline.nfl_data.common import service_client

    client = service_client()
    seasons = (client.table("league_seasons").select("league_id,season,sleeper_league_id,is_active")
               .eq("is_active", True).execute().data or [])
    current = league.current_week()
    for n, s in enumerate(seasons, 1):
        sid = s.get("sleeper_league_id")
        if not sid:
            continue
        scoring = load_settings(client, s["league_id"]).scoring_settings or (sleeper.raw(f"league/{sid}") or {}).get("scoring_settings") or {}
        raw = {w: sleeper.raw(f"league/{sid}/matchups/{w}") or [] for w in range(1, current + 1)}
        ours = own_league.own_scores(int(s["season"]), current, scoring, rows_for=lambda w: raw[w],
                                     lines_for=lambda w: own_league.week_lines(client, int(s["season"]), w, current))
        print(f"league {n} (season {s['season']}): weeks scored by us {len(ours)} of {current}", flush=True)
        for w in range(1, current + 1):
            mine = ours.get(f"matchups/{w}")
            if not mine:
                print(f"  week {w}: Sleeper's (no lines)", flush=True)
                continue
            theirs = {r["roster_id"]: float(r.get("points") or 0) for r in raw[w]}
            diffs = [abs(float(r["points"]) - theirs.get(r["roster_id"], 0)) for r in mine]
            changed = sum(d > 0.005 for d in diffs)
            a, b = _winners(raw[w]), _winners(mine)
            flips = sum(a.get(m) != b.get(m) for m in a)
            top = len(_top5(raw[w]) ^ _top5(mine)) // 2
            print(f"  week {w}: {changed} of {len(mine)} team totals differ (max {max(diffs, default=0):.2f}), "
                  f"{flips} winners flip, {top} top-5 changes", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
