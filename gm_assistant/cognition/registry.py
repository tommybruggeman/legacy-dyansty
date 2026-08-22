from __future__ import annotations

from gm_assistant.engines.rookie_engine import RookieDraftEngine
from gm_assistant.engines.fa_engine import FreeAgentEngine
from gm_assistant.engines.contract_engine import ContractEngine
from gm_assistant.engines.team_engine import TeamEngine


class EngineRegistry:
    def __init__(self):
        self.engines = [
            RookieDraftEngine(),
            FreeAgentEngine(),
            ContractEngine(),
            TeamEngine(),
        ]

    def select(self, route: dict):
        candidates = []

        for engine in self.engines:
            score = engine.can_handle(route)
            if score > 0:
                candidates.append((score, engine))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
