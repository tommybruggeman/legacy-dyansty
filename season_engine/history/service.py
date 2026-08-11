from __future__ import annotations

from typing import Any

from season_engine import SeasonResolver
from .models import CaptureResult
from .planner import build_capture_plan
from .repositories import HistoricalSeasonRepository
from .sleeper_source import HistorySource, SleeperHistorySource


class PreRolloverHistoryService:
    def __init__(self, client: Any, source: HistorySource | None = None):
        self.client = client
        self.source = source or SleeperHistorySource()

    def plan(self, league_id: str):
        season = SeasonResolver(self.client).get_active_season(league_id)
        bundle = self.source.fetch(season)
        teams = self.client.table("league_teams").select("*").eq("league_id", league_id).execute().data or []
        players = self.client.table("player_universe").select("sleeper_id,player_name").execute().data or []
        names = {str(row["sleeper_id"]): row.get("player_name") for row in players if row.get("sleeper_id")}
        try: existing = HistoricalSeasonRepository(self.client).counts(str(season.id))
        except Exception: existing = {}
        return build_capture_plan(season=season, source=bundle, league_teams=teams,
                                  player_names=names, existing_counts=existing)

    def capture(self, league_id: str, *, dry_run: bool = True) -> CaptureResult:
        plan = self.plan(league_id)
        if dry_run:
            return CaptureResult(dry_run=True, applied=False, plan=plan)
        if not plan.safe_to_apply:
            raise ValueError(f"Historical capture is blocked: {plan.blocking_errors}")
        # Re-read all source evidence. A changed fingerprint stops before the RPC.
        revalidated = self.plan(league_id)
        if revalidated.source_fingerprint != plan.source_fingerprint:
            raise ValueError("Sleeper source fingerprint changed during capture planning.")
        response = self.client.rpc("capture_pre_rollover_history", {"p_plan": plan.as_payload()}).execute()
        data = response.data[0] if isinstance(response.data, list) and response.data else response.data
        active = SeasonResolver(self.client).get_active_season(league_id)
        if active.season != plan.season or not active.is_active:
            raise RuntimeError("Active season changed during historical capture.")
        return CaptureResult(dry_run=False, applied=True, plan=plan, database_result=data or {})
