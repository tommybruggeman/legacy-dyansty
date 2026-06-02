#!/usr/bin/env python3
import os, argparse, requests, pandas as pd
from typing import Dict, List, Tuple

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

def _current_week(league_id: str, cap: int = 25) -> int:
    try:
        state = _get("https://api.sleeper.app/v1/state/nfl")
        wk = int(state.get("week") or 1)
        return max(1, min(cap, wk))
    except Exception:
        for wk in range(cap, 0, -1):
            try:
                if _get(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{wk}"):
                    return wk
            except Exception:
                pass
        return 1

def _rid_to_name(league_id: str) -> Dict[int,str]:
    users   = _get(f"https://api.sleeper.app/v1/league/{league_id}/users")
    rosters = _get(f"https://api.sleeper.app/v1/league/{league_id}/rosters")
    uid_to_disp = {u["user_id"]: (u.get("display_name") or u.get("username") or "").strip() for u in users}
    out={}
    for r in rosters:
        out[r.get("roster_id")] = uid_to_disp.get(r.get("owner_id"), f"Roster {r.get('roster_id')}")
    return out

def _pairs(league_id: str, week: int) -> List[Tuple[dict,dict]]:
    rows = _get(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{week}") or []
    by_mid={}
    for r in rows:
        mid = r.get("matchup_id")
        if mid is None: continue
        by_mid.setdefault(mid, []).append(r)
    out=[]
    for arr in by_mid.values():
        if len(arr)>=2:
            out.append((arr[0], arr[1]))
    return out

def compute_week(league_id: str, week: int) -> pd.DataFrame:
    rid2name = _rid_to_name(league_id)
    pairs = _pairs(league_id, week)

    rows=[]
    for a,b in pairs:
        ra, rb = a.get("roster_id"), b.get("roster_id")
        na, nb = rid2name.get(ra, f"Roster {ra}"), rid2name.get(rb, f"Roster {rb}")

        pa, pb = _as_number(a.get("points")), _as_number(b.get("points"))
        sa, sb = _as_number(a.get("starters_points")), _as_number(b.get("starters_points"))

        # guard for Sleeper oddity
        if pa < 10.0 <= sa: pa = sa
        if pb < 10.0 <= sb: pb = sb

        rows.append({"Team":na,"Score":pa,"OppScore":pb,"Win": 1 if pa>pb else 0})
        rows.append({"Team":nb,"Score":pb,"OppScore":pa,"Win": 1 if pb>pa else 0})

    if not rows: return pd.DataFrame()

    df = pd.DataFrame(rows)
    # **Deterministic rank** (no tie sharing): sort then assign index+1
    df = df.sort_values(["Score","Team"], ascending=[False,True], kind="mergesort").reset_index(drop=True)
    df["Rank"] = df.index + 1

    # mark exactly top-5 (or fewer if fewer played)
    top_n = min(5, len(df))
    df["Top5_wk"] = 0
    df.loc[:top_n-1, "Top5_wk"] = 1

    # league Standing Points for the week
    df["Points_wk"] = (2*df["Win"] + df["Top5_wk"]).astype(int)
    df.insert(0, "Week", week)
    return df

def compute_season(league_id: str, through: int) -> Tuple[pd.DataFrame, pd.DataFrame]:
    weeks=[]
    for wk in range(1, through+1):
        dw = compute_week(league_id, wk)
        if not dw.empty:
            weeks.append(dw)
    if not weeks:
        return pd.DataFrame(), pd.DataFrame()
    per_week = pd.concat(weeks, ignore_index=True)

    totals = per_week.groupby("Team", as_index=False).agg(
        **{
            "Standing Points": ("Points_wk","sum"),
            "PF": ("Score","sum"),
            "PA": ("OppScore","sum"),
            "Wins": ("Win","sum"),
            "Top 5": ("Top5_wk","sum"),
            "Games": ("Score","count"),
        }
    )
    totals["Losses"] = (totals["Games"] - totals["Wins"]).clip(lower=0).astype(int)
    totals["PF Per Game"] = (totals["PF"]/totals["Games"]).round(1)
    totals["PA Per Game"] = (totals["PA"]/totals["Games"]).round(1)
    totals = totals.sort_values(["Standing Points","PF","Wins","Top 5"], ascending=[False,False,False,False])
    return per_week, totals

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default=os.getenv("SLEEPER_LEAGUE_ID","").strip(), help="Sleeper league id")
    ap.add_argument("--through", type=int, default=0, help="audit through week N (0 = auto current)")
    ap.add_argument("--team", default="", help="optional team filter to print only that team’s weekly rows")
    args = ap.parse_args()

    if not args.league:
        raise SystemExit("Set --league or SLEEPER_LEAGUE_ID")

    through = args.through or _current_week(args.league, cap=25)

    per_week, totals = compute_season(args.league, through)
    if per_week.empty:
        print("No data.")
        return

    print(f"\n=== AUDIT: through Week {through} ===\n")
    if args.team:
        df = per_week[per_week["Team"].str.contains(args.team, case=False)].copy()
        print(df.to_string(index=False))
    else:
        # compact per-week view
        cols = ["Week","Team","Score","OppScore","Win","Rank","Top5_wk","Points_wk"]
        print(per_week[cols].sort_values(["Week","Rank"]).to_string(index=False))

    print("\n=== SEASON TOTALS ===\n")
    keep = ["Team","Standing Points","PF","Wins","Top 5","Losses","PA","PF Per Game","PA Per Game","Games"]
    print(totals[keep].to_string(index=False))

    # Optional CSVs for deeper comparison
    per_week.to_csv("audit_per_week.csv", index=False)
    totals.to_csv("audit_totals.csv", index=False)
    print("\nWrote audit_per_week.csv and audit_totals.csv")

if __name__ == "__main__":
    main()
