from __future__ import annotations

import requests
import pandas as pd


def _as_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _get_json(url: str):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def _current_nfl_week(max_week: int = 25) -> int:
    try:
        state = _get_json("https://api.sleeper.app/v1/state/nfl")
        wk = int(state.get("week") or 1)

        if wk <= 0:
            return 1

        return max(1, min(max_week, wk))
    except Exception:
        return 1


def _roster_id_to_name(sleeper_league_id: str) -> dict[int, str]:
    users = _get_json(f"https://api.sleeper.app/v1/league/{sleeper_league_id}/users")
    rosters = _get_json(f"https://api.sleeper.app/v1/league/{sleeper_league_id}/rosters")

    uid_to_handle: dict[str, str] = {}

    for u in users:
        uid = u.get("user_id")
        handle = (u.get("username") or "").strip()
        display = (u.get("display_name") or "").strip()
        val = handle or display or ""

        if uid and val:
            uid_to_handle[uid] = val

    rid_to_name: dict[int, str] = {}

    for r in rosters:
        rid = r.get("roster_id")
        owner_id = r.get("owner_id")

        if rid is None:
            continue

        rid_to_name[int(rid)] = uid_to_handle.get(owner_id) or f"Roster {rid}"

    return rid_to_name


def _fetch_week_pairs(sleeper_league_id: str, week: int) -> list[tuple[dict, dict]]:
    rows = (
        _get_json(f"https://api.sleeper.app/v1/league/{sleeper_league_id}/matchups/{week}")
        or []
    )

    by_matchup: dict[int, list[dict]] = {}

    for row in rows:
        matchup_id = row.get("matchup_id")

        if matchup_id is None:
            continue

        by_matchup.setdefault(matchup_id, []).append(row)

    pairs = []

    for group in by_matchup.values():
        if len(group) >= 2:
            pairs.append((group[0], group[1]))

    return pairs


def _compute_week_df(sleeper_league_id: str, week: int) -> pd.DataFrame:
    rid_to_name = _roster_id_to_name(sleeper_league_id)
    pairs = _fetch_week_pairs(sleeper_league_id, week)

    rows = []

    for a, b in pairs:
        ra = a.get("roster_id")
        rb = b.get("roster_id")

        na = rid_to_name.get(int(ra), f"Roster {ra}") if ra is not None else "Unknown"
        nb = rid_to_name.get(int(rb), f"Roster {rb}") if rb is not None else "Unknown"

        pa = _as_float(a.get("points"))
        pb = _as_float(b.get("points"))

        sa = _as_float(a.get("starters_points"))
        sb = _as_float(b.get("starters_points"))

        if pa < 10.0 <= sa:
            pa = sa

        if pb < 10.0 <= sb:
            pb = sb

        rows.append(
            {
                "week": week,
                "owner_name": na,
                "score": pa,
                "opp_score": pb,
                "win": 1 if pa > pb else 0,
            }
        )

        rows.append(
            {
                "week": week,
                "owner_name": nb,
                "score": pb,
                "opp_score": pa,
                "win": 1 if pb > pa else 0,
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    if df["score"].abs().sum() == 0 and df["opp_score"].abs().sum() == 0:
        return pd.DataFrame()

    df = df.sort_values(
        ["score", "owner_name"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)

    df["weekly_rank"] = df.index + 1

    top_n = min(5, len(df))
    df["top5"] = 0
    df.loc[: top_n - 1, "top5"] = 1

    df["standing_points_week"] = (2 * df["win"] + df["top5"]).astype(int)

    return df


def build_standings_snapshot(ctx: dict) -> pd.DataFrame:
    sleeper_league_id = str(ctx.get("sleeper_league_id") or "").strip()
    league_id = ctx.get("league_id")
    if ctx.get("season") is None:
        raise ValueError("Snapshot context requires an authoritative season.")
    season = int(ctx["season"])

    if not sleeper_league_id:
        return pd.DataFrame()

    latest_week = int(ctx.get("latest_week") or _current_nfl_week())

    frames = []

    for week in range(1, latest_week + 1):
        try:
            week_df = _compute_week_df(sleeper_league_id, week)
        except Exception:
            continue

        if not week_df.empty:
            frames.append(week_df)

    if not frames:
        return pd.DataFrame(
            columns=[
                "league_id",
                "season",
                "owner_name",
                "wins",
                "losses",
                "games",
                "pf",
                "pa",
                "ppg",
                "rank",
                "standing_points",
            ]
        )

    big = pd.concat(frames, ignore_index=True)

    out = big.groupby("owner_name", as_index=False).agg(
        wins=("win", "sum"),
        games=("score", "count"),
        pf=("score", "sum"),
        pa=("opp_score", "sum"),
        standing_points=("standing_points_week", "sum"),
    )

    out["losses"] = (out["games"] - out["wins"]).clip(lower=0)
    out["ppg"] = (out["pf"] / out["games"]).round(2)

    out = out.sort_values(
        ["standing_points", "pf"],
        ascending=[False, False],
    ).reset_index(drop=True)

    out["rank"] = out.index + 1
    out["league_id"] = league_id
    out["season"] = season

    out["pf"] = out["pf"].round(2)
    out["pa"] = out["pa"].round(2)

    return out[
        [
            "league_id",
            "season",
            "owner_name",
            "wins",
            "losses",
            "games",
            "pf",
            "pa",
            "ppg",
            "rank",
            "standing_points",
        ]
    ]
