# -*- coding: utf-8 -*-
# Legacy App/pages/05_Contracts_Cap.py — Contracts & Cap (clean)

import os, sys
from pathlib import Path
import requests
import streamlit as st

st.set_page_config(page_title="Fantasy GM — Contracts & Cap", layout="wide")

# ---------- Path prelude ----------
PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR  = os.path.abspath(os.path.join(PAGES_DIR, ".."))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "lib"))

# ---------- Minimal env loader (no external deps) ----------
def _load_kv(path: Path) -> bool:
    if not path.exists(): return False
    for raw in path.read_text().splitlines():
        if "=" not in raw or raw.strip().startswith("#"): continue
        k, v = raw.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and os.getenv(k) is None: os.environ[k] = v
    return True

def _load_env():
    here = Path(__file__).resolve()
    root, cwd = here.parents[1], Path.cwd()
    for p in [here.with_name("fantasy_env"), here.with_name(".env"),
              cwd/"fantasy_env", cwd/".env", root/"fantasy_env", root/".env",
              cwd/"pages"/"fantasy_env", cwd/"pages"/".env"]:
        if _load_kv(p): break
    return (os.getenv("SUPABASE_URL","").strip(),
            os.getenv("SUPABASE_KEY","").strip())

SUPABASE_URL, SUPABASE_KEY = _load_env()
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Missing Supabase credentials. Add SUPABASE_URL and SUPABASE_KEY to pages/fantasy_env (or .env).")
    st.stop()

# ---------- Minimal Supabase REST client ----------
class _SBResponse:
    def __init__(self, data): self.data = data

class _SBTable:
    def __init__(self, base_url, headers, name):
        self.base = base_url.rstrip("/")
        self.h    = headers
        self.name = name
        self._select  = "*"
        self._filters = []
    def select(self, cols="*"): self._select = cols; return self
    def eq(self, col, val): self._filters.append((col, val)); return self
    def execute(self):
        import requests as _rq
        url = f"{self.base}/rest/v1/{self.name}"
        params = {"select": self._select}
        for c, v in self._filters: params[c] = f"eq.{v}"
        r = _rq.get(url, headers=self.h, params=params, timeout=20)
        r.raise_for_status()
        return _SBResponse(r.json())

class MiniSupabase:
    def __init__(self, url, key):
        self.url = url.rstrip("/")
        self.h = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "return=representation",
        }
    def table(self, name): return _SBTable(self.url, self.h, name)

sb = MiniSupabase(SUPABASE_URL, SUPABASE_KEY)

# ---------- Access context ----------
role = st.session_state.get("role")
active_team = (
    st.session_state.get("active_team_name")
    or st.session_state.get("team_name")
    or st.session_state.get("trade_from_team")
)

is_commissioner = role in ["commissioner", "host", "admin"]

if not active_team and not is_commissioner:
    st.error("No team selected. Please go back to Teams.")
    st.stop()

# ---------- Data helpers ----------
def _safe_num(x, default=0.0):
    try:
        if x is None: return default
        if isinstance(x, (int, float)): return float(x)
        return float(str(x).strip())
    except Exception:
        return default

def fetch_teams():
    try:
        rows = sb.table("teams").select("team_name,cap_limit").execute().data or []
        out = []
        for r in rows:
            out.append({
                "team_name": r.get("team_name"),
                "cap_limit": _safe_num(r.get("cap_limit"), default=22.0),  # default roster-size cap
            })
        return out
    except Exception as e:
        st.error(f"Failed to read teams: {e}")
        return []

def fetch_roster():
    try:
        # Expect columns: owner_team_name, full_name, pos, salary, years (some may be missing)
        return sb.table("roster").select("*").execute().data or []
    except Exception as e:
        st.error(f"Failed to read roster: {e}")
        return []

def build_team_summary(teams, roster):
    # Index team caps
    caps = {t["team_name"]: t.get("cap_limit", 22.0) for t in teams}
    # Init accumulator
    summary = {}
    for t in teams:
        summary[t["team_name"]] = {
            "Team": t["team_name"],
            "CapLimit": _safe_num(t["cap_limit"], 22.0),
            "RosterSize": 0,
            "SlotsLeft": 0,
            "TotalSalary": 0.0,
            "AvgSalary": 0.0,
            "TotalYears": 0.0,
            "AvgYears": 0.0,
        }
    # Aggregate roster
    by_team = {}
    for r in roster:
        team = r.get("owner_team_name")
        if not team: continue
        by_team.setdefault(team, []).append(r)

    for team, rows in by_team.items():
        size = len(rows)
        total_salary = sum(_safe_num(r.get("salary"), 0.0) for r in rows)
        total_years  = sum(_safe_num(r.get("years"), 0.0)  for r in rows)
        avg_salary = (total_salary / size) if size else 0.0
        avg_years  = (total_years / size)  if size else 0.0
        cap_limit  = _safe_num(caps.get(team, 22.0), 22.0)

        if team not in summary:
            summary[team] = {
                "Team": team, "CapLimit": cap_limit,
                "RosterSize": 0, "SlotsLeft": 0,
                "TotalSalary": 0.0, "AvgSalary": 0.0,
                "TotalYears": 0.0, "AvgYears": 0.0,
            }
        summary[team]["RosterSize"]  = size
        summary[team]["SlotsLeft"]   = int(cap_limit) - size
        summary[team]["TotalSalary"] = round(total_salary, 2)
        summary[team]["AvgSalary"]   = round(avg_salary, 2)
        summary[team]["TotalYears"]  = round(total_years, 2)
        summary[team]["AvgYears"]    = round(avg_years, 2)

    # Return as list sorted by SlotsLeft (asc) then TotalSalary (desc)
    rows = list(summary.values())
    rows.sort(key=lambda r: (r["SlotsLeft"], -r["TotalSalary"]))
    return rows, by_team

# ---------- UI ----------
st.title("💼 Contracts & Cap")

# Filters
colA, colB = st.columns([1, 1])

with colA:
    sort_by = st.selectbox(
        "Sort by",
        ["Slots Left", "Total Salary", "Roster Size"],
        index=0,
    )

with colB:
    if is_commissioner:
        view_mode = st.selectbox(
            "View mode",
            ["My Team", "League Overview"],
            index=0,
        )
    else:
        view_mode = "My Team"

teams  = fetch_teams()
roster = fetch_roster()
summary_rows, roster_by_team = build_team_summary(teams, roster)

# Sorting
if sort_by == "Total Salary":
    summary_rows.sort(key=lambda r: -r["TotalSalary"])
elif sort_by == "Roster Size":
    summary_rows.sort(key=lambda r: -r["RosterSize"])
else:  # Slots Left
    summary_rows.sort(key=lambda r: r["SlotsLeft"])

if is_commissioner and view_mode == "League Overview":
    st.subheader("League Contract Summary")
    st.dataframe(
        summary_rows,
        use_container_width=True,
        column_order=[
            "Team",
            "RosterSize",
            "CapLimit",
            "SlotsLeft",
            "TotalSalary",
            "AvgSalary",
            "TotalYears",
            "AvgYears",
        ],
    )
else:
    my_summary = [r for r in summary_rows if r["Team"] == active_team]

    st.subheader(f"My Contract Summary — {active_team}")

    if my_summary:
        st.dataframe(
            my_summary,
            use_container_width=True,
            column_order=[
                "Team",
                "RosterSize",
                "CapLimit",
                "SlotsLeft",
                "TotalSalary",
                "AvgSalary",
                "TotalYears",
                "AvgYears",
            ],
        )
    else:
        st.info("No contract summary found for your team yet.")

st.markdown("---")

# Drill-down
if is_commissioner and view_mode == "League Overview":
    options = [r["Team"] for r in summary_rows]
    picked = st.selectbox("View contracts for team", options) if options else None
else:
    picked = active_team

if picked:
    st.subheader(f"Contracts — {picked}")
    rows = roster_by_team.get(picked, [])
    # Normalize for view
    table = []
    for r in rows:
        table.append({
            "Player": r.get("full_name") or r.get("player") or r.get("display_name") or "-",
            "Pos": r.get("pos") or r.get("position") or "-",
            "NFL": r.get("nfl_team") or r.get("team") or "-",
            "Salary": _safe_num(r.get("salary"), 0.0),
            "Years": _safe_num(r.get("years"), 0.0),
            "Slot": r.get("slot") or r.get("status") or "ACTIVE",
        })
    if table:
        st.dataframe(table, use_container_width=True)
    else:
        st.info("No contract data for this team yet.")
