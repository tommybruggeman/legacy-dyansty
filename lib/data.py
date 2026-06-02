# lib/data.py
from __future__ import annotations
import os
from pathlib import Path
from typing import List, Dict
import pandas as pd

# Optional imports from your codebase (they may not exist the first run)
try:
    from loaders import all_weeks_df_from_sleeper  # should accept season=...
except Exception:
    all_weeks_df_from_sleeper = None

try:
    from standings import build_standings_from_df
except Exception:
    build_standings_from_df = None

# Optional: Supabase client for transactions
try:
    from supabase import create_client
except Exception:
    create_client = None


def _supabase_client():
    if create_client is None:
        return None
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", os.getenv("SUPABASE_ANON_KEY","")).strip()
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


def fetch_weekly_scores(season: int) -> pd.DataFrame:
    """
    Returns a df with columns at least: season, week, team, pf.
    Priority: Sleeper loader → local CSV fallback.
    """
    # 1) Sleeper/ETL
    if all_weeks_df_from_sleeper is not None:
        try:
            df = all_weeks_df_from_sleeper(season=season)
            if isinstance(df, pd.DataFrame) and not df.empty:
                df2 = df.copy()
                df2.columns = [c.lower() for c in df2.columns]
                # normalize
                if "pf" not in df2.columns:
                    for g in ["points_for","points","score","pts","total_points"]:
                        if g in df2.columns:
                            df2["pf"] = df2[g]
                            break
                if "team" not in df2.columns:
                    for g in ["owner","manager","team_name","franchise","username","display_name"]:
                        if g in df2.columns:
                            df2["team"] = df2[g]
                            break
                if "season" not in df2.columns:
                    df2["season"] = season
                # require week and pf
                if "week" in df2.columns and "pf" in df2.columns and "team" in df2.columns:
                    return df2[["season","week","team","pf"]].copy()
        except Exception:
            pass

    # 2) CSV fallback if present
    for p in ["Weekly_Scores.csv", "Weekly_Scores_CSV.csv", "data/Weekly_Scores.csv"]:
        if Path(p).exists():
            try:
                csv = pd.read_csv(p)
                csv.columns = [c.lower() for c in csv.columns]
                if "pf" not in csv.columns:
                    for g in ["points_for","points","score","pts","total_points"]:
                        if g in csv.columns:
                            csv["pf"] = csv[g]
                            break
                if "team" not in csv.columns:
                    for g in ["owner","manager","team_name","franchise","username","display_name"]:
                        if g in csv.columns:
                            csv["team"] = csv[g]
                            break
                if "season" not in csv.columns:
                    csv["season"] = season
                return csv[["season","week","team","pf"]].copy()
            except Exception:
                pass

    return pd.DataFrame(columns=["season","week","team","pf"])


def standings_df(season: int) -> pd.DataFrame:
    """
    Canonical standings using your builder if available. Otherwise a safe minimal fallback.
    Expected output columns (ideal): Team, SP, PT, W, Top 5, Shotgs, Handle
    """
    wk = fetch_weekly_scores(season)
    if build_standings_from_df is not None and not wk.empty:
        try:
            s = build_standings_from_df(wk)
            # ensure a dataframe
            s = pd.DataFrame(s)
            return s
        except Exception:
            pass

    # fallback if builder not ready or no data
    if wk.empty:
        return pd.DataFrame(columns=["Team","SP","PT","W","Top 5","Shotgs","Handle"])

    agg = wk.groupby("team", as_index=False).agg(PT=("pf","sum"))
    agg["W"] = 0
    agg["Top 5"] = 0
    agg["Shotgs"] = 0
    agg["SP"] = 0
    agg["Handle"] = agg["team"]
    agg = agg.sort_values("PT", ascending=False)
    return agg.rename(columns={"team":"Team"})[["Team","SP","PT","W","Top 5","Shotgs","Handle"]]


def weekly_top_performers(season: int, limit: int = 10) -> pd.DataFrame:
    """
    Top teams by PF for the **latest week** with any >0 scores.
    """
    wk = fetch_weekly_scores(season)
    if wk.empty:
        return wk
    valid = wk.loc[wk["pf"].fillna(0) > 0]
    if valid.empty:
        last_week = wk["week"].max()
        slice_df = wk[wk["week"] == last_week]
    else:
        last_week = valid["week"].max()
        slice_df = valid[valid["week"] == last_week]
    res = (
        slice_df.groupby(["team"], as_index=False)
                .agg(pf=("pf","sum"))
                .sort_values("pf", ascending=False)
                .head(limit)
    )
    res["week"] = last_week
    return res[["week","team","pf"]]


def transactions(limit: int = 20) -> List[Dict]:
    """
    Returns the most recent transactions from Supabase table 'transactions'.
    Adjust column names if your schema differs.
    """
    sb = _supabase_client()
    if sb is None:
        return []
    try:
        r = sb.table("transactions").select("*").order("created_at", desc=True).limit(limit).execute()
        return r.data or []
    except Exception:
        return []
