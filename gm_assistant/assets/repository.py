from __future__ import annotations

from auth import service_client


class AssetRepository:
    """
    Single access layer for every asset in the GM Brain.

    No engine should query Supabase directly.
    """

    def __init__(self):
        self.sb = service_client()

    # ---------------------------------------------------------
    # Core
    # ---------------------------------------------------------

    def players(self):
        return (
            self.sb.table("player_universe")
            .select("*")
            .execute()
            .data
            or []
        )

    # ---------------------------------------------------------
    # Filters
    # ---------------------------------------------------------

    def rookies(self, rookie_class_year=None):

        q = self.sb.table("rookie_draft_board").select("*")

        if rookie_class_year is not None:
            q = q.eq("rookie_class_year", rookie_class_year)

        return q.execute().data or []

    def free_agents(self):

        return (
            self.sb.table("player_universe")
            .select("*")
            .eq("market_pool", "FA")
            .execute()
            .data
            or []
        )

    def team(self, owner_name):

        return (
            self.sb.table("player_universe")
            .select("*")
            .eq("current_owner", owner_name)
            .execute()
            .data
            or []
        )

    def by_position(self, pos):

        return (
            self.sb.table("player_universe")
            .select("*")
            .eq("pos", pos)
            .execute()
            .data
            or []
        )
