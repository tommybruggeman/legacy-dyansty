from dataclasses import dataclass

@dataclass
class LeagueSettings:
    rookie_contract_years: int = 3
    salary_cap: int = 225
    rookie_scale_multiplier: float = 1.0


def get_league_settings() -> LeagueSettings:
    """
    Central config for dynasty engine.
    Later this will come from Supabase league_rules table.
    """
    return LeagueSettings()
