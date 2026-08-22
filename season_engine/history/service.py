from __future__ import annotations

from typing import Any

from season_engine import SeasonResolver
from .models import CaptureResult
from .planner import build_capture_plan
from .repositories import HistoricalSeasonRepository, paginated_rows
from .sleeper_source import HistorySource, SleeperHistorySource


class PreRolloverHistoryService:
    def __init__(self, client: Any, source: HistorySource | None = None):
        self.client = client
        self.source = source or SleeperHistorySource()

    def plan(self, league_id: str):
        season = SeasonResolver(self.client).get_active_season(league_id)
        bundle = self.source.fetch(season)
        teams = paginated_rows(self.client, "league_teams", filters={"league_id": league_id})
        referenced_players = sorted({str(player) for roster in bundle.rosters for player in (roster.get("players") or ())})
        players = []
        for start in range(0, len(referenced_players), 200):
            batch = referenced_players[start:start + 200]
            if not batch:
                continue
            query = self.client.table("player_universe").select("sleeper_id,player_name", count="exact").in_("sleeper_id", batch).order("sleeper_id").range(0, len(batch)-1)
            response = query.execute(); count = getattr(response, "count", None)
            if count is None or len(response.data or []) != count:
                raise RuntimeError("Player-name evidence was truncated or its exact count was unavailable.")
            players.extend(response.data or [])
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
