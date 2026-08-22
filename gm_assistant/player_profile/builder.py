from __future__ import annotations

from gm_assistant.player_profile.models import PlayerProfile


class PlayerProfileBuilder:

    def build(self, row):

        if not row:
            return None

        return PlayerProfile(

            sleeper_id=row.get("sleeper_id"),
            player_name=row.get("player_name"),
            position=row.get("pos"),

            age=row.get("age"),
            years_exp=row.get("years_exp"),
            career_stage=row.get("age_curve_stage"),

            expected_ppg=row.get("expected_ppg"),
            historical_ppg=row.get("historical_ppg"),

            salary=row.get("salary"),
            years=row.get("years"),
            contract_score=row.get("contract_efficiency_score"),

            market_score=row.get("market_consensus_score"),
            rookie_asset_score=row.get("rookie_asset_score"),

            role_score=row.get("role_score"),
            situation_score=row.get("situation_score"),
            opportunity_score=row.get("opportunity_score"),

            future_score=row.get("future_projection_score"),

            asset_subtype=row.get("asset_subtype"),
            market_pool=row.get("market_pool"),

            summary=row.get("player_universe_summary"),
        )
