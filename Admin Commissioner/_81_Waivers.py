# -*- coding: utf-8 -*-
# Legacy App/pages/11_Waivers.py — FAAB / Waivers (claims + processor)

import os, sys, re, requests
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Fantasy GM — Waivers", layout="wide")

# ---------- Path prelude ----------
PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR  = os.path.abspath(os.path.join(PAGES_DIR, ".."))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "lib"))

# ---------- Minimal env loader ----------
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
    st.error("Missing Supabase credentials. Add SUPABASE_URL and SUPABASE_KEY to fantasy_env.")
    st.stop()

# ---------- Minimal Supabase REST client ----------
class _SBResponse:
    def __init__(self, data): self.data = data

class _SBTable:
    def __init__(self, base, headers, name):
        self.base = base.rstrip("/"); self.h = headers; self.name = name
        self._method="GET"; self._sel="*"; self._filters=[]; self._payload=None; self._order=None
    def select(self, cols="*"): self._method="GET"; self._sel=cols; return self
    def eq(self, col, val): self._filters.append((col, "eq", val)); return self
    def order(self, col, desc=False): self._order=(col,desc); return self
    def insert(self, rows): self._method="POST"; self._payload=rows; return self
    def execute(self):
        url = f"{self.base}/rest/v1/{self.name}"
        if self._method=="GET":
            params={"select": self._sel}
            for c,op,v in self._filters: params[c] = f"{op}.{v}"
            if self._order:
                col, desc = self._order
                params["order"] = f"{col}.{'desc' if desc else 'asc'}"
            r=requests.get(url, headers=self.h, params=params, timeout=30)
        elif self._method=="POST":
            hdrs=dict(self.h); hdrs["Prefer"]="return=representation"
            r=requests.post(url, headers=hdrs, json=self._payload, timeout=30)
        else:
            raise RuntimeError("Unsupported method")
        r.raise_for_status()
        return _SBResponse(r.json() if r.content else None)

class SB:
    def __init__(self, url, key):
        self.url=url.rstrip("/")
        self.h={"apikey":key,"Authorization":f"Bearer {key}","Content-Type":"application/json","Accept":"application/json"}
    def table(self, name): return _SBTable(self.url, self.h, name)
    def rpc(self, fn, params=None):
        r=requests.post(f"{self.url}/rest/v1/rpc/{fn}", headers=self.h, json=params or {}, timeout=60)
        r.raise_for_status()
        return _SBResponse(r.json() if r.content else None)

sb = SB(SUPABASE_URL, SUPABASE_KEY)

# ---------- Data helpers ----------
def fetch_teams():
    try:
        rows = sb.table("teams").select("team_name").order("team_name").execute().data or []
        return [r["team_name"] for r in rows if r.get("team_name")]
    except Exception:
        return []

def fetch_claims(status=None):
    t = sb.table("waiver_claims").select("*").order("created_at", desc=True)
    if status and status != "ALL": t = t.eq("status", status)
    try:
        return t.execute().data or []
    except Exception:
        return []

def fetch_budget(team):
    try:
        row = sb.table("waiver_budgets").select("*").eq("team", team).execute().data
        if row: return float(row[0].get("budget", 0))
    except Exception: pass
    return 0.0

# ---------- UI: Place Claim ----------
st.title("💸 Waivers / FAAB")

teams = fetch_teams()
if not teams:
    st.info("No teams found.")
    st.stop()

c1, c2, c3, c4 = st.columns([1.2, 2, 1, 1])
with c1:
    team = st.selectbox("Team", teams)
with c2:
    query = st.text_input("Search Free Agents (name contains)")
with c3:
    pos = st.selectbox("Position", ["", "QB", "RB", "WR", "TE", "K", "DEF"])
with c4:
    bid = st.number_input("Bid (FAAB)", min_value=0.0, step=1.0, value=0.0)

priority = st.number_input("Priority (tiebreaker; higher = better)", min_value=0, max_value=100, value=0, step=1)

if st.button("Search"):
    try:
        resp = sb.rpc("search_free_agents", {"p_pos": pos or None, "p_name": query or None, "p_limit": 50}).execute()
        fa = resp.data or []
        for r in fa:
            r["position"] = r.get("position") or r.get("pos")
            r["nfl_team"] = r.get("nfl_team") or r.get("team")
        st.session_state["waiver_search"] = fa
    except Exception as e:
        st.error(f"Search failed: {e}")

fa_list = st.session_state.get("waiver_search", [])
if fa_list:
    st.dataframe(fa_list, use_container_width=True)

    # place a claim
    names = [f"{r['full_name']} ({r.get('position','?')}) [{r['sleeper_id']}]" for r in fa_list]
    pick = st.selectbox("Select Player for Claim", names, key="claim_pick")
    note  = st.text_input("Notes (optional)", "")

    if st.button("Place Claim"):
        m = re.search(r"\[(.+?)\]$", pick)
        sleeper_id = m.group(1) if m else None
        chosen = next((r for r in fa_list if str(r.get("sleeper_id")) == str(sleeper_id)), None)
        if not chosen:
            st.warning("Could not resolve player.")
        else:
            # basic budget check (client-side)
            budget = fetch_budget(team)
            if bid > budget:
                st.warning(f"Bid exceeds budget (${budget:.0f}).")
            else:
                try:
                    sb.table("waiver_claims").insert([{
                        "team": team,
                        "sleeper_id": sleeper_id,
                        "player_name": chosen.get("full_name"),
                        "pos": chosen.get("position"),
                        "nfl_team": chosen.get("nfl_team"),
                        "bid": float(bid),
                        "priority": int(priority),
                        "status": "PENDING",
                        "notes": note or ""
                    }]).execute()
                    st.success("✅ Claim placed.")
                except Exception as e:
                    st.error(f"Failed to place claim: {e}")

st.markdown("---")

# ---------- UI: Claims Admin / Processing ----------
st.subheader("Claims")

filter_status = st.selectbox("Filter", ["ALL", "PENDING", "WON", "LOST", "CANCELLED"])
claims = fetch_claims(filter_status)
if claims:
    st.dataframe(claims, use_container_width=True, hide_index=True)
else:
    st.info("No claims.")

cA, cB, cC = st.columns([1,1,1])
with cA:
    cancel_id = st.text_input("Cancel Claim ID", "")
    if st.button("Cancel Claim"):
        if not cancel_id.strip():
            st.warning("Enter a claim id (numeric).")
        else:
            try:
                # set to CANCELLED only if still pending
                url = f"{sb.url}/rest/v1/waiver_claims?id=eq.{cancel_id}&status=eq.PENDING"
                hdrs = dict(sb.h); hdrs["Prefer"] = "return=representation"
                r = requests.patch(url, headers=hdrs, json={"status":"CANCELLED"}, timeout=20)
                r.raise_for_status()
                st.success("Cancelled (if it was still pending).")
            except Exception as e:
                st.error(f"Cancel failed: {e}")

with cB:
    if st.button("Process Waivers (Admin)"):
        try:
            res = sb.rpc("process_waivers_now").execute()
            st.success(f"Processed. Result: {res.data}")
        except Exception as e:
            st.error(f"Processing failed: {e}")

with cC:
    if st.button("Finalize Winners (Sign to Rosters)"):
        try:
            winners = sb.table("waiver_claims").select("*").eq("status","WON").execute().data or []
            count = 0
            for w in winners:
                try:
                    # add to roster via your existing RPC
                    sb.rpc("sign_free_agent", {
                        "p_team": w["team"],
                        "p_sleeper_id": w["sleeper_id"],
                        "p_salary": 1,         # default waiver contract; adjust later if needed
                        "p_years": 1,
                        "p_pos": None,
                        "p_nfl_team": None
                    }).execute()
                    count += 1
                except Exception:
                    pass
            # mark as processed_note to avoid double-signing
            url = f"{sb.url}/rest/v1/waiver_claims?status=eq.WON"
            hdrs = dict(sb.h); hdrs["Prefer"] = "return=representation"
            requests.patch(url, headers=hdrs, json={"status":"PROCESSED_NOTE"}, timeout=30).raise_for_status()
            st.success(f"Signed {count} players to winning teams.")
        except Exception as e:
            st.error(f"Finalize failed: {e}")

st.caption("Flow: Place claims → Process Waivers → Finalize Winners (sign to rosters).")
