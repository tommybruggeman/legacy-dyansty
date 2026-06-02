# -*- coding: utf-8 -*-
# Legacy App/pages/04_Owner_Map.py — Owner Map Editor (clean, no extra deps)

import os, sys
from pathlib import Path
import requests
import streamlit as st

st.set_page_config(page_title="Fantasy GM — Owner Map", layout="wide")

# ---------- Path prelude ----------
PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR  = os.path.abspath(os.path.join(PAGES_DIR, ".."))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "lib"))

# ---------- Minimal .env loader ----------
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
        self._method  = "GET"
        self._payload = None
        self._on_conflict = None
        self._as_upsert   = False

    def select(self, cols="*"):
        self._method = "GET"; self._select = cols; return self

    def eq(self, col, value):
        self._filters.append((col, value)); return self

    def insert(self, rows):
        self._method = "POST"; self._payload = rows; return self

    def upsert(self, rows, on_conflict: str):
        self._method = "POST"; self._payload = rows
        self._as_upsert = True; self._on_conflict = on_conflict
        return self

    def update(self, values):
        self._method = "PATCH"; self._payload = values; return self

    def delete(self):
        self._method = "DELETE"; self._payload = None; return self

    def execute(self):
        url = f"{self.base}/rest/v1/{self.name}"
        params = {}
        headers = dict(self.h)

        if self._method == "GET":
            params["select"] = self._select
            for c, v in self._filters: params[c] = f"eq.{v}"
            r = requests.get(url, headers=headers, params=params, timeout=20)

        elif self._method == "POST":
            if self._as_upsert:
                headers["Prefer"] = "resolution=merge-duplicates,return=representation"
                if self._on_conflict: params["on_conflict"] = self._on_conflict
            r = requests.post(url, headers=headers, params=params, json=self._payload, timeout=20)

        elif self._method == "PATCH":
            for c, v in self._filters: params[c] = f"eq.{v}"
            r = requests.patch(url, headers=headers, params=params, json=self._payload, timeout=20)

        elif self._method == "DELETE":
            for c, v in self._filters: params[c] = f"eq.{v}"
            r = requests.delete(url, headers=headers, params=params, timeout=20)

        else:
            raise RuntimeError("Unsupported method")

        r.raise_for_status()
        data = r.json() if r.content else None
        return _SBResponse(data)

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

# ---------- Helpers ----------
def read_owner_map():
    try:
        return sb.table("owner_map").select("*").execute().data or []
    except Exception as e:
        st.error(f"Failed to read owner_map: {e}")
        return []

def detect_cols(rows):
    """Return (raw_col, display_col, id_col or None)."""
    if not rows:
        # default to preferred schema
        return ("raw_name", "display_name", None)
    sample = rows[0].keys()
    id_col = "id" if "id" in sample else None
    candidates = [
        ("raw_name", "display_name"),
        ("handle", "full_name"),
        ("team_name", "full_name"),
    ]
    for a, b in candidates:
        if a in sample and b in sample:
            return (a, b, id_col)
    # fallback: pick two columns
    cols = list(sample)
    if len(cols) >= 2:
        return (cols[0], cols[1], id_col)
    return ("raw_name", "display_name", id_col)

def upsert_mapping(raw_col, display_col, raw_val, display_val, id_val=None):
    payload = [{raw_col: raw_val, display_col: display_val}]
    try:
        sb.table("owner_map").upsert(payload, on_conflict=raw_col).execute()
        return True, None
    except Exception as e:
        return False, str(e)

def delete_mapping(id_col, id_val, raw_col, raw_val):
    try:
        tbl = sb.table("owner_map").delete()
        if id_col and id_val is not None:
            tbl = tbl.eq(id_col, id_val)
        else:
            tbl = tbl.eq(raw_col, raw_val)
        tbl.execute()
        return True, None
    except Exception as e:
        return False, str(e)

# ---------- UI ----------
st.title("👥 Owner Map")

rows = read_owner_map()
raw_col, display_col, id_col = detect_cols(rows)

# Current mappings
st.subheader("Current Mappings")
st.dataframe(rows, use_container_width=True)

# Add / Update
st.markdown("---")
st.subheader("Add / Update Mapping")
c1, c2 = st.columns([1, 1])
with c1:
    raw_val = st.text_input(f"Raw/Sleeper name ({raw_col})", "")
with c2:
    display_val = st.text_input(f"Display name ({display_col})", "")

if st.button("Save Mapping"):
    if not raw_val or not display_val:
        st.warning("Please enter both fields.")
    else:
        ok, err = upsert_mapping(raw_col, display_col, raw_val.strip(), display_val.strip())
        if ok:
            st.success("Saved!")
            rows = read_owner_map()  # refresh
            st.dataframe(rows, use_container_width=True)
        else:
            st.error(f"Save failed: {err}")

# Delete
st.markdown("---")
st.subheader("Delete Mapping")
if rows:
    # Build label list
    labels = []
    for r in rows:
        rid = r.get(id_col) if id_col else None
        labels.append((rid, r.get(raw_col, ""), r.get(display_col, "")))
    label_strs = [f"{i+1}. {raw} → {disp}" for i, (_, raw, disp) in enumerate(labels)]
    pick = st.selectbox("Select a mapping to delete", label_strs, index=0)
    if st.button("Delete Selected"):
        idx = label_strs.index(pick)
        rid, raw, disp = labels[idx]
        ok, err = delete_mapping(id_col, rid, raw_col, raw)
        if ok:
            st.success("Deleted.")
            rows = read_owner_map()
            st.dataframe(rows, use_container_width=True)
        else:
            st.error(f"Delete failed: {err}")
else:
    st.info("No mappings yet. Add one above.")
