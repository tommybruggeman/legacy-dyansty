from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import requests
import streamlit as st

from auth import current_user, service_client
from gm_assistant.request_context import AssistantContextError, build_assistant_request_context
from services.publication_context import publication_generation, published_cap_rows
from gm_assistant.retrieval import get_cap_summary as retrieve_cap_summary
from gm_assistant.retrieval import get_draft_picks as retrieve_draft_picks
from gm_assistant.retrieval import get_transactions as retrieve_transactions


# ------------------------------------------------------------
# Paths / Environment
# ------------------------------------------------------------

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
        (
            os.getenv("SUPABASE_KEY", "").strip()
            or os.getenv("SUPABASE_ANON_KEY", "").strip()
        ),
        os.getenv("SLEEPER_LEAGUE_ID", "").strip(),
    )


SUPABASE_URL, SUPABASE_KEY, SLEEPER_LEAGUE_ID = _load_env()


# ------------------------------------------------------------
# Supabase REST Client
# ------------------------------------------------------------

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

        r = requests.get(
            url,
            headers=self.headers,
            params=params,
            timeout=25,
        )

        if r.status_code == 404:
            return _Resp([])

        r.raise_for_status()
        return _Resp(r.json())


class SB:
    def __init__(
        self,
        url: str,
        key: str,
        access_token: Optional[str] = None,
    ):
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


def get_sb():
    access_token = st.session_state.get("sb_access_token")

    if not SUPABASE_URL or not SUPABASE_KEY:
        return None

    return SB(
        SUPABASE_URL,
        SUPABASE_KEY,
        access_token,
    )


# ------------------------------------------------------------
# Auth / Active League Helpers
# ------------------------------------------------------------

def get_user_id() -> str | None:
    user = current_user()

    if isinstance(user, dict):
        return user.get("id") or user.get("user_id")

    return getattr(user, "id", None)


def ensure_active_league_from_user() -> str | None:
    if st.session_state.get("active_league_id"):
        return st.session_state["active_league_id"]

    user_id = get_user_id()
    sb = get_sb()

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


def resolve_my_team_uncached(user_id: str) -> dict | None:
    sb = get_sb()
    league_id = ensure_active_league_from_user()

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
    league_team_id = membership.get("league_team_id")
    legacy_team_id = membership.get("team_id")

    resolved_team = _resolve_membership_league_team(
        sb,
        league_id=league_id,
        membership=membership,
    )

    if resolved_team:
        team, identity_source = resolved_team
        resolved_league_team_id = team.get("id")
        _log_assistant_identity(
            authenticated_user_id=user_id,
            requested_league_id=league_id,
            membership_id=membership.get("id"),
            membership_team_id=legacy_team_id,
            membership_league_team_id=league_team_id,
            resolved_league_team_id=resolved_league_team_id,
        )
        return {
            "league_id": league_id,
            "league_team_id": resolved_league_team_id,
            "team_id": resolved_league_team_id,
            "membership_team_id": legacy_team_id,
            "membership_league_team_id": league_team_id,
            "legacy_team_id": legacy_team_id,
            "team_identity_source": identity_source,
            "legacy_team_fallback_used": False,
            "team_name": (
                team.get("team_name")
                or team.get("owner_name")
            ),
            "owner_name": (
                team.get("owner_name")
                or team.get("team_name")
            ),
            "role": membership.get("role"),
        }

    if not legacy_team_id:
        return None

    owner_rows = (
        sb.table("owners")
        .select("*")
        .eq("id", legacy_team_id)
        .eq("league_id", league_id)
        .limit(1)
        .execute()
        .data
        or []
    )

    if not owner_rows:
        _log_assistant_identity(
            authenticated_user_id=user_id,
            requested_league_id=league_id,
            membership_id=membership.get("id"),
            membership_team_id=legacy_team_id,
            membership_league_team_id=league_team_id,
            resolved_league_team_id=None,
        )
        return None

    owner = owner_rows[0]
    _log_assistant_identity(
        authenticated_user_id=user_id,
        requested_league_id=league_id,
        membership_id=membership.get("id"),
        membership_team_id=legacy_team_id,
        membership_league_team_id=league_team_id,
        resolved_league_team_id=None,
    )

    return {
        "league_id": league_id,
        "league_team_id": None,
        "team_id": owner.get("id"),
        "legacy_team_id": owner.get("id"),
        "team_identity_source": "legacy_team_id",
        "legacy_team_fallback_used": True,
        "team_name": (
            owner.get("team_name")
            or owner.get("display_name")
            or owner.get("full_name")
        ),
        "owner_name": (
            owner.get("full_name")
            or owner.get("display_name")
            or owner.get("team_name")
        ),
        "role": membership.get("role"),
    }


def _resolve_membership_league_team(sb, *, league_id: str, membership: dict):
    candidates = [
        ("league_team_id", membership.get("league_team_id")),
        ("team_id", membership.get("team_id")),
    ]

    for source, team_id in candidates:
        if not team_id:
            continue

        rows = (
            sb.table("league_teams")
            .select("*")
            .eq("id", team_id)
            .eq("league_id", league_id)
            .limit(1)
            .execute()
            .data
            or []
        )

        if rows:
            return rows[0], source

    return None


def _log_assistant_identity(
    *,
    authenticated_user_id,
    requested_league_id,
    membership_id,
    membership_team_id,
    membership_league_team_id,
    resolved_league_team_id,
) -> None:
    print(
        "ASSISTANT_IDENTITY "
        f"authenticated_user_id={_safe_identity_value(authenticated_user_id)} "
        f"requested_league_id={_safe_identity_value(requested_league_id)} "
        f"membership_id={_safe_identity_value(membership_id)} "
        f"membership_team_id={_safe_identity_value(membership_team_id)} "
        f"membership_league_team_id={_safe_identity_value(membership_league_team_id)} "
        f"resolved_league_team_id={_safe_identity_value(resolved_league_team_id)}",
        flush=True,
    )


def _safe_identity_value(value) -> str:
    if value is None:
        return "none"
    text = str(value).strip()
    return text or "none"


def get_cached_my_team():
    user_id = get_user_id()

    if not user_id:
        return None

    cached_team = st.session_state.get("my_team_context")
    if cached_team and cached_team.get("league_team_id"):
        return cached_team

    my_team = resolve_my_team_uncached(user_id)

    if my_team:
        st.session_state["my_team_context"] = my_team
        st.session_state["active_league_id"] = my_team["league_id"]
        st.session_state["role"] = my_team.get("role")

    return my_team


# ------------------------------------------------------------
# Core League Loaders
# ------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def load_roster(league_id: str, context_generation: int = 0) -> pd.DataFrame:
    """
    Primary GM roster source.

    Uses the rebuilt snapshot table:
    team_roster_state = Sleeper roster + transactions + contracts
    """
    sb = service_client()

    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("team_roster_state")
            .select(
                "id, league_id, team_id, player_id, player_name, position, "
                "status, salary, years, contract_status"
            )
            .eq("league_id", league_id)
            .order("team_id")
            .order("player_name")
            .execute()
            .data
            or []
        )

    except Exception:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    return df.rename(
        columns={
            "team_id": "owner",
            "player_name": "player",
            "position": "pos",
            "player_id": "sleeper_id",
        }
    )


@st.cache_data(ttl=300, show_spinner=False)
def _load_caps_published(league_id: str, context_generation: int) -> pd.DataFrame:
    sb = get_sb()

    if not sb:
        return pd.DataFrame()

    try:
        rows = published_cap_rows(sb, league_id)
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def load_caps(league_id: str) -> pd.DataFrame:
    sb = get_sb()
    generation = publication_generation(sb, league_id) if sb and league_id else 0
    return _load_caps_published(league_id, generation)


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
def load_team_activity(league_id: str | None = None, limit: int = 100) -> pd.DataFrame:
    sb = get_sb()

    if not sb:
        return pd.DataFrame()

    try:
        query = sb.table("team_activity").select("*")
        if league_id:
            query = query.eq("league_id", league_id)
        rows = query.order("created_at", desc=True).limit(limit).execute().data or []
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_trade_block(
    league_id: str,
    owner_name: str,
) -> pd.DataFrame:
    sb = get_sb()

    if not sb:
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
    season: int | None = None,
) -> pd.DataFrame:
    sb = get_sb()

    if not sb:
        return pd.DataFrame()

    try:
        if season is None:
            from season_engine import SeasonResolver
            season = SeasonResolver(sb).get_active_season(league_id).season
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

    if not sb:
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


# ------------------------------------------------------------
# Phase 2 Metadata Loaders
# ------------------------------------------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def load_player_metadata() -> pd.DataFrame:
    """
    Future table target: player_metadata

    Expected eventual columns:
    - sleeper_id
    - player
    - pos
    - team
    - age
    - years_pro
    - height
    - weight
    - college
    - depth_chart_position
    - injury_status
    """

    sb = get_sb()

    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("player_metadata")
            .select("*")
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=86400, show_spinner=False)
def load_player_projections() -> pd.DataFrame:
    """
    Future table target: player_projections

    Expected eventual columns:
    - sleeper_id
    - season
    - projected_points
    - projected_ppg
    - projected_games
    """

    sb = get_sb()

    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("player_projections")
            .select("*")
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=86400, show_spinner=False)
def load_player_injuries() -> pd.DataFrame:
    """
    Future table target: player_injuries

    Expected eventual columns:
    - sleeper_id
    - injury_status
    - injury_risk_score
    - games_missed_last_year
    - games_missed_last_3_years
    """

    sb = get_sb()

    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("player_injuries")
            .select("*")
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def load_player_values(league_id: str) -> pd.DataFrame:
    try:
        sb = service_client()

        rows = (
            sb.table("player_values")
            .select("*")
            .eq("league_id", league_id)
            .order("contract_value_score", desc=True)
            .execute()
            .data
            or []
        )

        return pd.DataFrame(rows)

    except Exception:
        return pd.DataFrame()
# ------------------------------------------------------------
# Enrichment Helpers
# ------------------------------------------------------------

def _normalize_join_key(df: pd.DataFrame, col: str) -> pd.DataFrame:
    df = df.copy()

    if col in df.columns:
        df[col] = df[col].astype(str).str.strip()

    return df


def _merge_optional(
    left: pd.DataFrame,
    right: pd.DataFrame,
    on: str = "sleeper_id",
    suffix: str = "",
) -> pd.DataFrame:
    if left.empty or right.empty:
        return left

    if on not in left.columns or on not in right.columns:
        return left

    left = _normalize_join_key(left, on)
    right = _normalize_join_key(right, on)

    if suffix:
        existing_cols = set(left.columns)
        rename_map = {
            c: f"{c}_{suffix}"
            for c in right.columns
            if c != on and c in existing_cols
        }
        right = right.rename(columns=rename_map)

    return left.merge(
        right,
        on=on,
        how="left",
    )


def enrich_roster(roster: pd.DataFrame) -> pd.DataFrame:
    """
    Central Phase 2 enrichment layer.

    Current behavior:
    - keeps roster working exactly as before
    - safely merges optional future metadata tables if they exist

    Future behavior:
    - sleeper metadata
    - age curves
    - production history
    - projections
    - injury risk
    - team/offensive environment
    """

    if roster.empty:
        return roster

    enriched = roster.copy()

    metadata = load_player_metadata()
    projections = load_player_projections()
    injuries = load_player_injuries()

    enriched = _merge_optional(
        enriched,
        metadata,
        on="sleeper_id",
        suffix="meta",
    )

    enriched = _merge_optional(
        enriched,
        projections,
        on="sleeper_id",
        suffix="proj",
    )

    enriched = _merge_optional(
        enriched,
        injuries,
        on="sleeper_id",
        suffix="injury",
    )

    return enriched


# ------------------------------------------------------------
# Context / Tab Helpers
# ------------------------------------------------------------

def get_team(ctx: dict) -> dict:
    return ctx.get("team", {}) if ctx else {}


def get_league_roster(ctx: dict) -> pd.DataFrame:
    if not ctx:
        return pd.DataFrame()

    return ctx.get("roster", pd.DataFrame())


def get_my_roster(ctx: dict) -> pd.DataFrame:
    if not ctx:
        return pd.DataFrame()

    return ctx.get("my_roster", pd.DataFrame())


def get_caps(ctx: dict) -> pd.DataFrame:
    if not ctx:
        return pd.DataFrame()

    return ctx.get("caps", pd.DataFrame())


def get_draft_picks(ctx: dict) -> pd.DataFrame:
    if not ctx:
        return pd.DataFrame()

    return ctx.get("draft_picks", pd.DataFrame())


def get_transactions(ctx: dict) -> pd.DataFrame:
    if not ctx:
        return pd.DataFrame()

    return ctx.get("transactions", pd.DataFrame())


def get_team_activity(ctx: dict) -> pd.DataFrame:
    if not ctx:
        return pd.DataFrame()

    return ctx.get("team_activity", pd.DataFrame())


def get_trade_block(ctx: dict) -> pd.DataFrame:
    if not ctx:
        return pd.DataFrame()

    return ctx.get("trade_block", pd.DataFrame())


def get_cap_adjustments(ctx: dict) -> pd.DataFrame:
    if not ctx:
        return pd.DataFrame()

    return ctx.get("cap_adjustments", pd.DataFrame())


def get_standings(ctx: dict) -> pd.DataFrame:
    if not ctx:
        return pd.DataFrame()

    return ctx.get("standings", pd.DataFrame())


def get_players_by_pos(
    ctx: dict,
    pos: str,
    mine_only: bool = True,
) -> pd.DataFrame:
    roster = get_my_roster(ctx) if mine_only else get_league_roster(ctx)

    if roster.empty or "pos" not in roster.columns:
        return pd.DataFrame()

    return roster[
        roster["pos"]
        .astype(str)
        .str.upper()
        .eq(str(pos).upper())
    ].copy()


def get_my_qbs(ctx: dict) -> pd.DataFrame:
    return get_players_by_pos(ctx, "QB")


def get_my_rbs(ctx: dict) -> pd.DataFrame:
    return get_players_by_pos(ctx, "RB")


def get_my_wrs(ctx: dict) -> pd.DataFrame:
    return get_players_by_pos(ctx, "WR")


def get_my_tes(ctx: dict) -> pd.DataFrame:
    return get_players_by_pos(ctx, "TE")


def get_my_roster_count(ctx: dict) -> int:
    return len(get_my_roster(ctx))


def get_league_roster_count(ctx: dict) -> int:
    return len(get_league_roster(ctx))

def get_player_values(ctx: dict) -> pd.DataFrame:
    if not ctx:
        return pd.DataFrame()

    return ctx.get("player_values", pd.DataFrame())

# ------------------------------------------------------------
# GM Context Builder
# ------------------------------------------------------------


def _load_table(sb, table_name: str):
    import pandas as pd

    try:
        return pd.DataFrame(sb.table(table_name).select("*").execute().data or [])
    except Exception as e:
        print(f"Unable to load {table_name}: {e}")
        return pd.DataFrame()


GLOBAL_SNAPSHOT_TABLES = {
    "player_intelligence",
    "league_intelligence",
}

LEAGUE_SCOPED_SNAPSHOT_TABLES = {
    "team_intelligence",
    "team_window_scores",
    "team_future_context",
}


def load_snapshot_table(table_name: str, league_id: str | None = None):
    import pandas as pd

    try:
        from auth import service_client
        sb = service_client()
        query = sb.table(table_name).select("*")
        if league_id and table_name in LEAGUE_SCOPED_SNAPSHOT_TABLES:
            query = query.eq("league_id", league_id)
        return pd.DataFrame(query.execute().data or [])
    except Exception as e:
        print(f"Unable to load snapshot table {table_name}: {e}")
        return pd.DataFrame()


def load_gm_context() -> dict | None:
    my_team = get_cached_my_team()

    if not my_team:
        return None

    league_id = my_team["league_id"]
    owner_name = my_team["owner_name"]
    user = current_user() or {}

    try:
        sb_service = service_client()
        assistant_context = build_assistant_request_context(
            sb=sb_service,
            user=user,
            active_league_id=league_id,
        )
    except AssistantContextError as exc:
        return {
            "team": my_team,
            "assistant_context_error": str(exc),
        }

    cap_result = retrieve_cap_summary(sb_service, assistant_context)
    draft_pick_result = retrieve_draft_picks(sb_service, assistant_context)
    transaction_result = retrieve_transactions(sb_service, assistant_context)
    retrieval_errors = [
        {"table": result.source.table, "error": result.error}
        for result in (cap_result, draft_pick_result, transaction_result)
        if not result.ok
    ]

    roster_df = load_roster(league_id, assistant_context.context_generation)
    roster_df = enrich_roster(roster_df)

    my_roster = pd.DataFrame()

    if not roster_df.empty and owner_name and "owner" in roster_df.columns:
        my_roster = roster_df[
            roster_df["owner"]
            .astype(str)
            .str.strip()
            .str.lower()
            .eq(str(owner_name).strip().lower())
        ].copy()

    return {
        "team": my_team,
        "assistant_context": assistant_context,
        "assistant_retrieval_errors": retrieval_errors,
        "roster": roster_df,
        "my_roster": my_roster,
        "player_values": load_player_values(league_id),
        "caps": pd.DataFrame(cap_result.rows),
        "draft_picks": pd.DataFrame(draft_pick_result.rows),
        "transactions": pd.DataFrame(transaction_result.rows),
        "team_activity": load_team_activity(league_id),
        "trade_block": load_trade_block(league_id, owner_name),
        "cap_adjustments": load_cap_adjustments(league_id, owner_name),
        "standings": load_cached_standings(league_id),

        # Canonical FantasyDB brain tables
        "player_intelligence": load_snapshot_table("player_intelligence", league_id),
        "team_intelligence": load_snapshot_table("team_intelligence", league_id),
        "league_intelligence": load_snapshot_table("league_intelligence", league_id),

        # Legacy fallback / raw team feature table
        "team_window_scores": load_snapshot_table("team_window_scores", league_id),

        # Team future / window context
        "team_future_context": load_snapshot_table("team_future_context", league_id),
    }


# ------------------------------------------------------------
# Debug / Summary Helpers
# ------------------------------------------------------------

def summarize_context(ctx: dict) -> dict:
    if not ctx:
        return {}

    return {
        "team": ctx.get("team", {}),
        "league_roster_rows": len(ctx.get("roster", pd.DataFrame())),
        "my_roster_rows": len(ctx.get("my_roster", pd.DataFrame())),
        "player_values_rows": len(ctx.get("player_values", pd.DataFrame())),
        "caps_rows": len(ctx.get("caps", pd.DataFrame())),
        "draft_pick_rows": len(ctx.get("draft_picks", pd.DataFrame())),
        "transaction_rows": len(ctx.get("transactions", pd.DataFrame())),
        "team_activity_rows": len(ctx.get("team_activity", pd.DataFrame())),
        "trade_block_rows": len(ctx.get("trade_block", pd.DataFrame())),
        "cap_adjustment_rows": len(ctx.get("cap_adjustments", pd.DataFrame())),
        "standings_rows": len(ctx.get("standings", pd.DataFrame())),
        "player_intelligence_rows": len(ctx.get("player_intelligence", pd.DataFrame())),
        "team_intelligence_rows": len(ctx.get("team_intelligence", pd.DataFrame())),
        "league_intelligence_rows": len(ctx.get("league_intelligence", pd.DataFrame())),
        "team_future_context_rows": len(ctx.get("team_future_context", pd.DataFrame())),
    }
