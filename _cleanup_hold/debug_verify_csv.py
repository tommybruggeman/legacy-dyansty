# debug_verify_csv.py
# Compare live (Sleeper) weekly rows + season totals to a "truth" CSV

import argparse, os
from typing import Dict, List, Tuple
import pandas as pd
import requests

LEAGUE_ID_ENV = "SLEEPER_LEAGUE_ID"

# ---------- HTTP + helpers ----------
def _get(url: str):
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.json()

def _as_number(x):
    try:
        if x is None: return 0.0
        if isinstance(x, (int, float)): return float(x)
        return float(str(x))
    except Exception:
        return 0.0

def _current_nfl_week(max_week: int = 25) -> int:
    league_id = os.getenv(LEAGUE_ID_ENV, "").strip()
    try:
        state = _get("https://api.sleeper.app/v1/state/nfl")
        wk = int(state.get("week") or 1)
        return max(1, min(max_week, wk))
    except Exception:
        for wk in range(max_week, 0, -1):
            try:
                if _get(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{wk}"):
                    return wk
            except Exception:
                pass
        return 1

def roster_id_to_name(league_id: str) -> Dict[int, str]:
    users   = _get(f"https://api.sleeper.app/v1/league/{league_id}/users")
    rosters = _get(f"https://api.sleeper.app/v1/league/{league_id}/rosters")
    uid_to_disp = {u["user_id"]: (u.get("display_name") or u.get("username") or "").strip()
                   for u in users}
    out: Dict[int, str] = {}
    for r in rosters:
        rid = r.get("roster_id")
        own = r.get("owner_id")
        out[rid] = uid_to_disp.get(own, f"Roster {rid}")
    return out

# ---------- weekly rows ----------
def fetch_week_pairs(league_id: str, week: int) -> List[Tuple[dict, dict]]:
    rows = _get(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{week}") or []
    by_mid: Dict[int, list] = {}
    for r in rows:
        mid = r.get("matchup_id")
        if mid is None:
            continue
        by_mid.setdefault(mid, []).append(r)
    pairs: List[Tuple[dict, dict]] = []
    for arr in by_mid.values():
        if len(arr) >= 2:
            pairs.append((arr[0], arr[1]))
    return pairs

def compute_week_df(league_id: str, week: int) -> pd.DataFrame:
    rid2name = roster_id_to_name(league_id)
    pairs = fetch_week_pairs(league_id, week)
    rows: List[Dict] = []

    for a, b in pairs:
        ra, rb = a.get("roster_id"), b.get("roster_id")
        na, nb = rid2name.get(ra, f"Roster {ra}"), rid2name.get(rb, f"Roster {rb}")

        pa = _as_number(a.get("points"));  pb = _as_number(b.get("points"))
        sa = _as_number(a.get("starters_points")); sb = _as_number(b.get("starters_points"))
        if pa < 10.0 <= sa: pa = sa
        if pb < 10.0 <= sb: pb = sb

        rows.append({"Week": week, "Team": na, "Score": round(pa, 2), "OppScore": round(pb, 2)})
        rows.append({"Week": week, "Team": nb, "Score": round(pb, 2), "OppScore": round(pa, 2)})

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values(["Score", "Team"], ascending=[False, True], kind="mergesort").reset_index(drop=True)
    df["Rank"] = df.index + 1
    top_n = min(5, len(df))
    df["Top5_wk"] = 0
    df.loc[: top_n - 1, "Top5_wk"] = 1
    df["Win"] = (df["Score"] > df["OppScore"]).astype(int)
    df["Points_wk"] = (2 * df["Win"] + df["Top5_wk"]).astype(int)
    return df[["Week", "Team", "Score", "OppScore", "Rank", "Top5_wk", "Win", "Points_wk"]]

# ---------- season totals ----------
def build_standings_from_weekly(league_id: str) -> pd.DataFrame:
    latest_wk = _current_nfl_week(max_week=25)
    frames: List[pd.DataFrame] = []
    for wk in range(1, latest_wk + 1):
        df_w = compute_week_df(league_id, wk)
        if not df_w.empty:
            frames.append(df_w)
    if not frames:
        return pd.DataFrame()

    big = pd.concat(frames, ignore_index=True)
    agg = big.groupby("Team", as_index=False).agg(
        StandingPoints=("Points_wk", "sum"),
        PF=("Score", "sum"),
        PA=("OppScore", "sum"),
        Wins=("Win", "sum"),
        Top5=("Top5_wk", "sum"),
        Games=("Score", "count"),
    )
    agg["Losses"] = (agg["Games"] - agg["Wins"]).clip(lower=0).astype(int)
    agg["PF Per Game"] = (agg["PF"] / agg["Games"]).round(1)
    agg["PA Per Game"] = (agg["PA"] / agg["Games"]).round(1)
    agg.rename(columns={"StandingPoints": "Standing Points"}, inplace=True)
    return agg

# ---------- CSV utilities ----------
def _lc_map(cols) -> Dict[str, str]:
    return {str(c).lower().strip(): c for c in cols}

def _find_like(cols_map: Dict[str, str], *needles: str) -> str | None:
    n = [s.lower().strip() for s in needles]
    for lc, orig in cols_map.items():
        for q in n:
            if lc == q or q in lc:
                return orig
    return None

def _coerce_num(s) -> float:
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return float("nan")

def normalize_truth(truth_raw: pd.DataFrame) -> pd.DataFrame:
    """Return a per-team table with columns: Week, Team, Score, (optional) OppScore."""
    cols_lc = _lc_map(truth_raw.columns)

    # Try matchup layout first (Team 1/Team 2 with their points)
    c_week = _find_like(cols_lc, "week", "wk")
    c_t1   = _find_like(cols_lc, "team 1", "owner 1")
    c_t2   = _find_like(cols_lc, "team 2", "owner 2")
    c_p1   = _find_like(cols_lc, "team 1 points", "points 1", "pf 1")
    c_p2   = _find_like(cols_lc, "team 2 points", "points 2", "pf 2")

    if c_week and c_t1 and c_t2 and c_p1 and c_p2:
        df = truth_raw[[c_week, c_t1, c_t2, c_p1, c_p2]].copy()
        df.columns = ["Week", "Team1", "Team2", "Pts1", "Pts2"]

        a = df[["Week", "Team1", "Pts1", "Pts2"]].rename(
            columns={"Team1": "Team", "Pts1": "Score", "Pts2": "OppScore"}
        )
        b = df[["Week", "Team2", "Pts2", "Pts1"]].rename(
            columns={"Team2": "Team", "Pts2": "Score", "Pts1": "OppScore"}
        )
        truth = pd.concat([a, b], ignore_index=True)

    else:
        # Per-team layout (Team/Owner + Week + Score [+ OppScore])
        lm = _lc_map(truth_raw.columns)
        team_col  = _find_like(lm, "team", "owner", "owner name", "display name")
        week_col  = _find_like(lm, "week", "wk")
        score_col = _find_like(lm, "score", "points", "points for", "pf")
        opp_col   = _find_like(lm, "oppscore", "opp score", "against", "pa", "points against")

        missing = []
        if not team_col:  missing.append("Team/Owner")
        if not week_col:  missing.append("Week/Wk")
        if not score_col: missing.append("Score/Points/PF")
        if missing:
            raise KeyError(
                f"CSV is missing required column(s): {', '.join(missing)}. "
                f"Found columns: {list(truth_raw.columns)}"
            )

        truth = truth_raw.rename(columns={
            team_col:  "Team",
            week_col:  "Week",
            score_col: "Score",
            **({opp_col: "OppScore"} if opp_col else {})
        })

    # Clean numeric/text robustly
    truth["Team"] = truth["Team"].astype(str).str.strip()
    truth["Week"] = pd.to_numeric(truth["Week"], errors="coerce")
    truth = truth[truth["Week"].notna()].copy()
    truth["Week"] = truth["Week"].astype(int)

    truth["Score"] = pd.to_numeric(
        truth["Score"].astype(str).str.replace(",", "").str.strip(),
        errors="coerce"
    ).round(2)

    if "OppScore" in truth.columns:
        truth["OppScore"] = pd.to_numeric(
            truth["OppScore"].astype(str).str.replace(",", "").str.strip(),
            errors="coerce"
        ).round(2)

    return truth

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", required=True, help="Sleeper league id")
    ap.add_argument("--csv", required=True, help="Path to gold CSV (Weekly Scores)")
    ap.add_argument("--through", type=int, default=0, help="Audit through week N (0 = auto)")
    args = ap.parse_args()

    league_id = args.league.strip()
    truth_raw = pd.read_csv(args.csv)
    truth = normalize_truth(truth_raw)

    max_week = args.through or _current_nfl_week(max_week=25)

    # ---------- Weekly diffs ----------
    mismatches: List[Tuple[str, int, pd.DataFrame]] = []
    for wk in range(1, max_week + 1):
        live = compute_week_df(league_id, wk)
        if live.empty:
            print(f"[WARN] Week {wk}: no live rows")
            continue

        t_wk = truth[truth["Week"] == wk].copy()
        if t_wk.empty:
            print(f"[WARN] Week {wk}: no rows in CSV")
            continue

        # Score comparison
        m = live.merge(
            t_wk[["Team", "Score"]].rename(columns={"Score": "Score_truth"}),
            on="Team", how="left", validate="many_to_one"
        )
        tol = 0.01
        bad = m[(m["Score_truth"].isna()) | ((m["Score"] - m["Score_truth"]).abs() > tol)]
        if not bad.empty:
            mismatches.append(("Score", wk, bad[["Team", "Score", "Score_truth"]]))

        # OppScore comparison (if present)
        if "OppScore" in t_wk.columns:
            m2 = live.merge(
                t_wk[["Team", "OppScore"]].rename(columns={"OppScore": "OppScore_truth"}),
                on="Team", how="left", validate="many_to_one"
            )
            bad2 = m2[(m2["OppScore_truth"].isna()) | ((m2["OppScore"] - m2["OppScore_truth"]).abs() > tol)]
            if not bad2.empty:
                mismatches.append(("OppScore", wk, bad2[["Team", "OppScore", "OppScore_truth"]]))

    if mismatches:
        print("\n=== WEEKLY MISMATCHES ===")
        for kind, wk, dfm in mismatches:
            print(f"\n-- Week {wk} :: {kind} diffs --")
            print(dfm.to_string(index=False))
    else:
        print("\nNo weekly score/OppScore mismatches 👍")

    # ---------- Season totals compare ----------
    per_week = []
    for wk, t_wk in truth.groupby("Week"):
        dfw = t_wk.copy().sort_values(["Score", "Team"], ascending=[False, True], kind="mergesort").reset_index(drop=True)
        dfw["Rank"] = dfw.index + 1
        top_n = min(5, len(dfw))
        dfw["Top5_wk"] = 0
        dfw.loc[: top_n - 1, "Top5_wk"] = 1
        if "OppScore" in dfw.columns:
            dfw["Win"] = (dfw["Score"] > dfw["OppScore"]).astype(int)
        else:
            dfw["Win"] = 0
        dfw["Points_wk"] = (2 * dfw["Win"] + dfw["Top5_wk"]).astype(int)
        per_week.append(dfw)

    csv_big = pd.concat(per_week, ignore_index=True)
    csv_totals = csv_big.groupby("Team", as_index=False).agg(
        StandingPoints=("Points_wk","sum"),
        PF=("Score","sum"),
        PA=("OppScore","sum") if "OppScore" in truth.columns else ("Score","sum"),
        Wins=("Win","sum"),
        Top5=("Top5_wk","sum"),
        Games=("Score","count")
    )

    live_totals = build_standings_from_weekly(league_id)
    merged = live_totals.merge(csv_totals, on="Team", suffixes=("_live","_csv"))

    def diff(df, col, tol=0.01):
        if df.empty or col not in df: return pd.DataFrame()
        if df[col+"_csv"].dtype.kind in "if":
            m = df[(df[col+"_live"] - df[col+"_csv"]).abs() > tol][["Team", col+"_live", col+"_csv"]]
        else:
            m = df[df[col+"_live"] != df[col+"_csv"]][["Team", col+"_live", col+"_csv"]]
        if not m.empty: m.insert(1, "Metric", col)
        return m

    diffs = pd.concat([
        diff(merged, "Standing Points", 0.01),
        diff(merged, "PF", 0.01),
        diff(merged, "PA", 0.01) if "OppScore" in truth.columns else pd.DataFrame(),
        diff(merged, "Wins", 0.0),
        diff(merged, "Top5", 0.0),
        diff(merged, "Games", 0.0),
    ], ignore_index=True)

    if not diffs.empty:
        print("\n=== SEASON TOTALS MISMATCHES ===")
        print(diffs.to_string(index=False))
    else:
        print("\nSeason totals match the CSV computation 👍")

if __name__ == "__main__":
    main()
