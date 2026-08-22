from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from gm_assistant.owner_intelligence.models import OwnerMemoryScope, OwnerPreference, OwnerPreferenceStatus


class OwnerIntelligenceRepository(Protocol):
    persistent: bool

    def load_preferences(self, *, user_id: str, league_id: str, league_team_id: str) -> list[OwnerPreference]: ...
    def save_preference(self, preference: OwnerPreference) -> OwnerPreference: ...
    def supersede_preference(self, preference: OwnerPreference) -> OwnerPreference: ...


class MissingOwnerIntelligenceRepository:
    persistent = False

    def load_preferences(self, *, user_id: str, league_id: str, league_team_id: str) -> list[OwnerPreference]:
        _ = (user_id, league_id, league_team_id)
        return []

    def save_preference(self, preference: OwnerPreference) -> OwnerPreference:
        raise RuntimeError("Owner Intelligence persistence is not configured.")

    def supersede_preference(self, preference: OwnerPreference) -> OwnerPreference:
        raise RuntimeError("Owner Intelligence persistence is not configured.")


class InMemoryOwnerIntelligenceRepository:
    persistent = False

    def __init__(self, preferences: list[OwnerPreference] | None = None):
        self.preferences = list(preferences or [])

    def load_preferences(self, *, user_id: str, league_id: str, league_team_id: str) -> list[OwnerPreference]:
        out = []
        for preference in self.preferences:
            if preference.user_id != user_id:
                continue
            if preference.scope in {OwnerMemoryScope.LEAGUE.value, OwnerMemoryScope.TEAM.value} and preference.league_id != league_id:
                continue
            if preference.scope == OwnerMemoryScope.TEAM.value and preference.league_team_id != league_team_id:
                continue
            out.append(preference)
        return out

    def save_preference(self, preference: OwnerPreference) -> OwnerPreference:
        self.preferences.append(preference)
        return preference

    def supersede_preference(self, preference: OwnerPreference) -> OwnerPreference:
        updated = replace(preference, status=OwnerPreferenceStatus.SUPERSEDED.value)
        self.preferences = [updated if item == preference else item for item in self.preferences]
        return updated
