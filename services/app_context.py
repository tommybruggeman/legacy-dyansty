from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import requests
import streamlit as st

from auth import current_user


# ---------- timing ----------
CTX_START = time.perf_counter()


def tick(label: str):
    print(
        f"[APP CONTEXT] {label}: {time.perf_counter() - CTX_START:.2f}s",
        flush=True,
    )


# ---------- env ----------
ROOT_DIR = Path(__file__).resolve().parents[1]


def _load_kv(path: Path) -> bool:
    if not path.exists():
        return False

    for raw in path.read_text().splitlines():
        if "=" not in raw or raw.strip().startswith("#"):
            continue

        k, v = raw.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")

        if k and os.getenv(k) is None:
            os.environ[k] = v

    return True


def _load_env() -> Tuple[str, str, str]:
    for p in [
        ROOT_DIR / "fantasy_env",
        ROOT_DIR / ".env",
        ROOT_DIR / "pages" / "fantasy_env",
        ROOT_DIR / "pages" / ".env",
        Path.cwd() / "fantasy_env",
        Path.cwd() / ".env",
    ]:
        if _load_kv(p):
            break

    return (
        os.getenv("SUPABASE_URL", "").strip(),
        os.getenv("SUPABASE_KEY", "").strip(),
        os.getenv("SLEEPER_LEAGUE_ID", "").strip(),
    )


SUPABASE_URL, SUPABASE_KEY, SLEEPER_LEAGUE_ID = _load_env()


# ---------- minimal Supabase REST ----------
class _Resp:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, base: str, headers: dict, name: str):
        self.base = base.rstrip("/")
        self.headers = headers
        self.name = name
        self._select = "*"
        self._filters = []
        self._order = None
        self._limit = None

    def select(self, cols="*"):
        self._select = cols
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def execute(self):
        url = f"{self.base}/rest/v1/{self.name}"
        params = {"select": self._select}

        for c, v in self._filters:
            params[c] = f"eq.{v}"

        if self._order:
            params["order"] = f"{self._order[0]}.{'desc' if self._order[1] else 'asc'}"

        if self._limit:
            params["limit"] = self._limit

        r = requests.get(url, headers=self.headers, params=params, timeout=25)

        if r.status_code == 404:
            return _Resp([])

        r.raise_for_status()
        return _Resp(r.json())


class SB:
    def __init__(self, url: str, key: str, access_token: Optional[str] = None):
        self.url = url.rstrip("/")
        token = access_token or key
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def table(self, name: str):
        return _Table(self.url, self.headers, name)


def get_secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def get_sb():
    access_token = st.session_state.get("sb_access_token")

    url = SUPABASE_URL or get_secret("SUPABASE_URL")
    key = (
        SUPABASE_KEY
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
        or get_secret("SUPABASE_KEY")
        or get_secret("SUPABASE_ANON_KEY")
    )

    if url and key:
        return SB(url, key, access_token)

    return None


# ---------- user/team context ----------
def get_user_id() -> Optional[str]:
    user = current_user()

    if isinstance(user, dict):
        return user.get("id") or user.get("user_id")

    return getattr(user, "id", None)


@st.cache_data(ttl=300, show_spinner=False)
def ensure_active_league_from_user(user_id: str | None) -> Optional[str]:
    sb = get_sb()

    if st.session_state.get("active_league_id"):
        return st.session_state["active_league_id"]

    if not sb or not user_id:
        return None

    rows = (
        sb.table("league_memberships")
        .select("league_id, role")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )

    if not rows:
        return None

    st.session_state["active_league_id"] = rows[0].get("league_id")
    st.session_state["role"] = rows[0].get("role")

    return st.session_state["active_league_id"]

def resolve_my_team(user_id: str | None, league_id: str | None) -> dict | None:
    sb = get_sb()
    print("[CTX DEBUG] user_id =", user_id, flush=True)

    if not sb or not league_id or not user_id:
        return None
    membership_rows = (
        sb.table("league_memberships")
        .select("*")
        .eq("league_id", league_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )

    if not membership_rows:
        return None

    membership = membership_rows[0]
    team_id = membership.get("team_id") or membership.get("league_team_id")

    if team_id:
        team_rows = (
            sb.table("league_teams")
            .select("*")
            .eq("id", team_id)
            .eq("league_id", league_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if team_rows:
            team = team_rows[0]
            return {
                "league_id": league_id,
                "team_id": team.get("id"),
                "team_name": team.get("team_name") or team.get("owner_name"),
                "owner_name": team.get("owner_name"),
                "role": membership.get("role"),
            }

    owner_name = membership.get("owner_name") or membership.get("sleeper_username")
    team_name = membership.get("team_name")

    if owner_name:
        team_rows = (
            sb.table("league_teams")
            .select("*")
            .eq("league_id", league_id)
            .eq("owner_name", owner_name)
            .limit(1)
            .execute()
            .data
            or []
        )

        if team_rows:
            team = team_rows[0]
            return {
                "league_id": league_id,
                "team_id": team.get("id"),
                "team_name": team.get("team_name") or team_name or team.get("owner_name"),
                "owner_name": team.get("owner_name"),
                "role": membership.get("role"),
            }

    return None



# ---------- shared data loaders ----------
@st.cache_data(ttl=300, show_spinner=False)
def load_roster(league_id: str) -> pd.DataFrame:
    sb = get_sb()

    if not sb or not league_id:
        return pd.DataFrame()

    rows = (
        sb.table("contracts")
        .select(
            "id, league_id, owner_name, player_name, player_position, "
            "contract_years_left, contract_total_years, salary, sleeper_player_id"
        )
        .eq("league_id", league_id)
        .order("player_position")
        .order("player_name")
        .execute()
        .data
        or []
    )

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    return df.rename(
        columns={
            "owner_name": "owner",
            "player_name": "player",
            "player_position": "pos",
            "contract_years_left": "years",
            "sleeper_player_id": "sleeper_id",
        }
    )


@st.cache_data(ttl=300, show_spinner=False)
def load_caps() -> pd.DataFrame:
    sb = get_sb()

    if not sb:
        return pd.DataFrame()

    try:
        rows = sb.table("v_team_caps").select("*").execute().data or []
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_draft_picks() -> pd.DataFrame:
    sb = get_sb()

    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("draft_picks")
            .select("*")
            .order("season")
            .order("round")
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_transactions(limit: int = 300) -> pd.DataFrame:
    sb = get_sb()

    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("transactions_enriched")
            .select("*")
            .order("ts", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_trade_block(league_id: str, owner_name: str) -> pd.DataFrame:
    sb = get_sb()

    if not sb or not league_id or not owner_name:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("trade_block")
            .select("*")
            .eq("league_id", league_id)
            .eq("owner", owner_name)
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_cap_adjustments(
    league_id: str,
    owner_name: str,
    season: int = 2026,
) -> pd.DataFrame:
    sb = get_sb()

    if not sb or not league_id or not owner_name:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("cap_adjustments")
            .select("*")
            .eq("league_id", league_id)
            .eq("owner_name", owner_name)
            .eq("season", season)
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_cached_standings(league_id: str) -> pd.DataFrame:
    sb = get_sb()

    if not sb or not league_id:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("league_standings")
            .select("*")
            .eq("league_id", league_id)
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


# ---------- Sleeper standings ----------
def get_json(url: str):
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.json()


def as_number(x) -> float:
    try:
        if x is None:
            return 0.0
        return float(x)
    except Exception:
        return 0.0


@st.cache_data(ttl=300, show_spinner=False)
def load_owner_display_map() -> dict:
    sb = get_sb()

    if not sb:
        return {}

    try:
        rows = (
            sb.table("owner_map")
            .select("sleeper_username,team_name")
            .execute()
            .data
            or []
        )
    except Exception:
        return {}

    mapping = {}

    for row in rows:
        uname = str(row.get("sleeper_username") or "").strip()
        disp = str(row.get("team_name") or "").strip()

        if uname and disp:
            mapping[uname] = disp

    return mapping


@st.cache_data(ttl=300, show_spinner=False)
def current_nfl_week() -> int:
    try:
        state = get_json("https://api.sleeper.app/v1/state/nfl")
        wk = int(state.get("week") or 1)
        return max(1, min(wk, 25))
    except Exception:
        return 1


@st.cache_data(ttl=300, show_spinner=False)
def roster_id_to_name(sleeper_league_id: str) -> dict:
    users = get_json(f"https://api.sleeper.app/v1/league/{sleeper_league_id}/users")
    rosters = get_json(f"https://api.sleeper.app/v1/league/{sleeper_league_id}/rosters")

    uid_to_name = {
        u.get("user_id"): (u.get("display_name") or u.get("username") or "").strip()
        for u in users
    }

    out = {}

    for r in rosters:
        rid = r.get("roster_id")
        owner_id = r.get("owner_id")

        if rid is not None:
            out[rid] = uid_to_name.get(owner_id, f"Roster {rid}")

    return out


@st.cache_data(ttl=300, show_spinner=False)
def build_standings_from_sleeper(sleeper_league_id: str) -> pd.DataFrame:
    if not sleeper_league_id:
        return pd.DataFrame()

    try:
        rid_to_name = roster_id_to_name(sleeper_league_id)
        owner_map = load_owner_display_map()
        latest_week = current_nfl_week()
    except Exception as e:
        print(f"[APP CONTEXT] build_standings setup failed: {e}", flush=True)
        return pd.DataFrame()

    frames = []

    for week in range(1, latest_week + 1):
        try:
            rows = (
                get_json(
                    f"https://api.sleeper.app/v1/league/{sleeper_league_id}/matchups/{week}"
                )
                or []
            )
        except Exception:
            continue

        by_mid = {}

        for row in rows:
            mid = row.get("matchup_id")
            if mid is not None:
                by_mid.setdefault(mid, []).append(row)

        week_rows = []

        for pair in by_mid.values():
            if len(pair) < 2:
                continue

            a, b = pair[0], pair[1]

            pa = as_number(a.get("points"))
            pb = as_number(b.get("points"))
            sa = as_number(a.get("starters_points"))
            sbp = as_number(b.get("starters_points"))

            if pa < 10 <= sa:
                pa = sa
            if pb < 10 <= sbp:
                pb = sbp

            na = rid_to_name.get(a.get("roster_id"), f"Roster {a.get('roster_id')}")
            nb = rid_to_name.get(b.get("roster_id"), f"Roster {b.get('roster_id')}")

            na = owner_map.get(na, na)
            nb = owner_map.get(nb, nb)

            week_rows.append(
                {
                    "Team": na,
                    "Score": pa,
                    "OppScore": pb,
                    "Win": 1 if pa > pb else 0,
                }
            )
            week_rows.append(
                {
                    "Team": nb,
                    "Score": pb,
                    "OppScore": pa,
                    "Win": 1 if pb > pa else 0,
                }
            )

        if not week_rows:
            continue

        df_w = pd.DataFrame(week_rows)

        if df_w["Score"].abs().sum() == 0 and df_w["OppScore"].abs().sum() == 0:
            continue

        df_w = df_w.sort_values(
            ["Score", "Team"],
            ascending=[False, True],
            kind="mergesort",
        ).reset_index(drop=True)

        df_w["Top 5"] = 0
        df_w.loc[: min(4, len(df_w) - 1), "Top 5"] = 1
        df_w["Standing Points"] = (2 * df_w["Win"] + df_w["Top 5"]).astype(int)

        frames.append(df_w)

    if not frames:
        return pd.DataFrame()

    big = pd.concat(frames, ignore_index=True)

    out = big.groupby("Team", as_index=False).agg(
        **{
            "Standing Points": ("Standing Points", "sum"),
            "PF": ("Score", "sum"),
            "PA": ("OppScore", "sum"),
            "Wins": ("Win", "sum"),
            "Top 5": ("Top 5", "sum"),
            "Games": ("Score", "count"),
        }
    )

    out["Losses"] = out["Games"] - out["Wins"]
    out["PF Per Game"] = (out["PF"] / out["Games"]).round(1)
    out["PA Per Game"] = (out["PA"] / out["Games"]).round(1)

    return out


def preseason_standings() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Team": [
                "Chase Seyforth",
                "Chasen Hardy",
                "Connor Cassidy",
                "Dylan Burruel",
                "Grady Graham",
                "Kevin Wells",
                "Mekel Sanchez",
                "Nando Munoz",
                "Nick Salafia",
                "Tommy Bruggeman",
            ],
            "Standing Points": [0] * 10,
            "PF": [0] * 10,
            "PA": [0] * 10,
            "Wins": [0] * 10,
            "Top 5": [0] * 10,
            "Games": [0] * 10,
            "Losses": [0] * 10,
            "PF Per Game": [0] * 10,
            "PA Per Game": [0] * 10,
        }
    )


# ---------- public API ----------
def clear_app_context():
    st.session_state.pop("app_context", None)
    st.cache_data.clear()


def get_app_context(force_refresh: bool = False) -> dict:
    global CTX_START
    CTX_START = time.perf_counter()

    if force_refresh:
        clear_app_context()

    if "app_context" in st.session_state:
        tick("returned cached app_context")
        return st.session_state["app_context"]

    tick("start")

    user_id = get_user_id()
    tick("after get_user_id")

    league_id = ensure_active_league_from_user(user_id)
    tick("after ensure_active_league_from_user")

    my_team = resolve_my_team(user_id, league_id)
    tick("after resolve_my_team")

    team_name = None
    owner_name = None
    role = st.session_state.get("role")

    if my_team:
        league_id = my_team.get("league_id") or league_id
        team_name = my_team.get("team_name")
        owner_name = my_team.get("owner_name") or team_name
        role = my_team.get("role") or role

    roster_df = load_roster(league_id)
    tick("after load_roster")

    caps_df = load_caps()
    tick("after load_caps")

    picks_df = load_draft_picks()
    tick("after load_draft_picks")

    tx_df = load_transactions()
    tick("after load_transactions")

    trade_df = load_trade_block(league_id, owner_name)
    tick("after load_trade_block")

    cap_adj_df = load_cap_adjustments(league_id, owner_name)
    tick("after load_cap_adjustments")

    cached_stand_df = load_cached_standings(league_id)
    tick("after load_cached_standings")

    sleeper_stand_df = build_standings_from_sleeper(SLEEPER_LEAGUE_ID)
    tick("after build_standings_from_sleeper")

    stand_df = sleeper_stand_df
    if stand_df.empty:
        stand_df = cached_stand_df

    if stand_df.empty:
        stand_df = preseason_standings()

    tick("after choose_stand_df")

    ctx = {
        "loaded": True,
        "user_id": user_id,
        "my_team": my_team,
        "league_id": league_id,
        "team_name": team_name,
        "owner_name": owner_name,
        "role": role,
        "roster_df": roster_df,
        "caps_df": caps_df,
        "picks_df": picks_df,
        "tx_df": tx_df,
        "trade_df": trade_df,
        "cap_adj_df": cap_adj_df,
        "stand_df": stand_df,
    }

    st.session_state["app_context"] = ctx

    tick("finished app_context")

    return ctx