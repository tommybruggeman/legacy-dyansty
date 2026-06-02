# debug_verify.py
import os, sys, argparse, requests, pandas as pd
from collections import defaultdict

LEAGUE = os.getenv("SLEEPER_LEAGUE_ID","").strip()
assert LEAGUE, "Set SLEEPER_LEAGUE_ID"

# --- HTTP helpers
def _get(u):
    r = requests.get(u, timeout=25); r.raise_for_status(); return r.json()

def _asnum(x):
    try:
        if x is None: return 0.0
        return float(x)
    except Exception:
        return 0.0

def _rid_to_name(league_id):
    users   = _get(f"https://api.sleeper.app/v1/league/{league_id}/users")
    rosters = _get(f"https://api.sleeper.app/v1/league/{league_id}/rosters")
    uid2name = {u["user_id"]: (u.get("display_name") or u.get("username") or "").strip() for u in users}
    out = {}
    for r in rosters:
        out[r["roster_id"]] = uid2name.get(r["owner_id"], f"Roster {r['roster_id']}")
    return out

def _fetch_pairs(league_id, wk):
    rows = _get(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{wk}") or []
    by_mid = defaultdict(list)
    for r in rows:
        mid = r.get("matchup_id")
        if mid is not None:
            by_mid[mid].append(r)
    pairs = []
    for arr in by_mid.values():
        if len(arr) >= 2:
            pairs.append((arr[0], arr[1]))
    return pairs

def _current_week(max_week=25):
    try:
        st = _get("https://api.sleeper.app/v1/state/nfl")
        wk = int(st.get("week") or 1)
        return max(1, min(max_week, wk))
    except Exception:
        for wk in range(max_week, 0, -1):
            try:
                if _get(f"https://api.sleeper.app/v1/league/{LEAGUE}/matchups/{wk}"):
                    return wk
            except Exception:
                pass
        return 1

# --- Core compute (two variants so we can A/B test policy)
def compute_week_rows(league_id, wk, use_starters_fallback=True, top5_policy="first5"):
    """
    top5_policy:
      - "first5": exactly first 5 after score sort (no extra for ties at 5th)
      - "rank<=5": include all tied at 5th (can yield >5 teams)
    """
    rid2name = _rid_to_name(league_id)
    pairs    = _fetch_pairs(league_id, wk)

    rows = []
    for a, b in pairs:
        ra, rb = a.get("roster_id"), b.get("roster_id")
        na, nb = rid2name.get(ra, f"Roster {ra}"), rid2name.get(rb, f"Roster {rb}")

        pa, pb = _asnum(a.get("points")), _asnum(b.get("points"))
        sa, sb = _asnum(a.get("starters_points")), _asnum(b.get("starters_points"))

        if use_starters_fallback:
            if pa < 10.0 <= sa: pa = sa
            if pb < 10.0 <= sb: pb = sb

        rows.append({"Team": na, "Score": pa, "OppScore": pb, "Win": 1 if pa > pb else 0})
        rows.append({"Team": nb, "Score": pb, "OppScore": pa, "Win": 1 if pb > pa else 0})

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values(["Score","Team"], ascending=[False, True], kind="mergesort").reset_index(drop=True)

    if top5_policy == "first5":
        df["Rank"] = df.index + 1
        df["Top5_wk"] = 0
        df.loc[: min(5, len(df)) - 1, "Top5_wk"] = 1
    else:  # rank<=5 (tie-inclusive)
        df["Rank"] = df["Score"].rank(method="min", ascending=False).astype(int)
        df["Top5_wk"] = (df["Rank"] <= 5).astype(int)

    df["Points_wk"] = (2 * df["Win"] + df["Top5_wk"]).astype(int)
    return df

def accumulate(league_id, latest_week, **opts):
    frames = []
    for wk in range(1, latest_week + 1):
        f = compute_week_rows(league_id, wk, **opts)
        if not f.empty:
            f.insert(0, "Week", wk)
            frames.append(f)
    if not frames:
        return pd.DataFrame(), pd.DataFrame()
    big = pd.concat(frames, ignore_index=True)

    totals = big.groupby("Team", as_index=False).agg(
        StandingPoints=("Points_wk","sum"),
        PF=("Score","sum"),
        PA=("OppScore","sum"),
        Wins=("Win","sum"),
        Top5=("Top5_wk","sum"),
        Games=("Score","count")
    )
    totals["Losses"] = (totals["Games"] - totals["Wins"]).astype(int)
    totals["PF Per Game"] = (totals["PF"]/totals["Games"]).round(1)
    totals["PA Per Game"] = (totals["PA"]/totals["Games"]).round(1)
    return big, totals

def show_cutline(per_week_df, week):
    w = per_week_df[per_week_df["Week"]==week].copy()
    if w.empty: 
        print(f"No data for week {week}"); return
    w = w.sort_values("Score", ascending=False).reset_index(drop=True)
    print(f"\n=== WEEK {week} cutline ===")
    print(w.loc[:6, ["Team","Score","Rank","Top5_wk","Win"]])  # rows around cut

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--through", type=int, default=None, help="only compute up to this week")
    ap.add_argument("--policy", choices=["first5","rank<=5"], default="first5")
    ap.add_argument("--no-starters-fallback", action="store_true")
    ap.add_argument("--cutline-week", type=int, default=None)
    args = ap.parse_args()

    latest = _current_week()
    if args.through: latest = min(latest, args.through)

    per_week, totals = accumulate(
        LEAGUE, latest,
        use_starters_fallback=(not args.no_starters_fallback),
        top5_policy=args.policy,
    )

    # CSVs so you can open next to the Google Sheet
    per_week.to_csv("verify_per_week.csv", index=False)
    totals.to_csv("verify_totals.csv", index=False)

    # Mekel spotlight
    print("\n=== MekelS week-by-week ===")
    print(per_week[per_week.Team.str.contains("Mekel", case=False)][["Week","Team","Score","OppScore","Rank","Top5_wk","Win","Points_wk"]])

    if args.cutline_week:
        show_cutline(per_week, args.cutline_week)

    print("\nWrote verify_per_week.csv and verify_totals.csv")
