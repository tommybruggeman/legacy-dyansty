from __future__ import annotations

# ============================================================
# Engine Context
# ============================================================

import pandas as pd

from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows
from engine.player_enrichment import enrich_roster
from engine.roster_fit import attach_roster_fit_scores


# ============================================================
# Supabase Client
# ============================================================

supabase = service_client()


# ============================================================
# Build Engine Context
# ============================================================

def build_engine_context(league_id: str) -> dict:
    roster = pd.DataFrame(load_internal_contract_rows(supabase, league_id))

    if not roster.empty:
        roster = roster.rename(
            columns={
                "player_name": "player",
                "player_position": "pos",
                "contract_years_left": "years",
            }
        )

        roster = enrich_roster(roster)

        roster = attach_roster_fit_scores(roster)

    return {
        "league_id": league_id,
        "my_roster": roster,
    }
