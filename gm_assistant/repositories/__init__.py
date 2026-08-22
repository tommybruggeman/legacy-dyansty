"""Scoped domain repositories for the canonical GM Assistant path.

Repositories own raw data access and production-schema normalization. They do
not render answers, make recommendations, import Streamlit, or authorize by
owner/team names alone.
"""

from gm_assistant.repositories.cap import CapRepository
from gm_assistant.repositories.contracts import ContractRepository
from gm_assistant.repositories.draft_picks import DraftPickRepository
from gm_assistant.repositories.league import LeagueRepository
from gm_assistant.repositories.players import PlayerRepository
from gm_assistant.repositories.roster import RosterRepository
from gm_assistant.repositories.team import TeamRepository
from gm_assistant.repositories.transactions import TransactionRepository

__all__ = [
    "CapRepository",
    "ContractRepository",
    "DraftPickRepository",
    "LeagueRepository",
    "PlayerRepository",
    "RosterRepository",
    "TeamRepository",
    "TransactionRepository",
]
