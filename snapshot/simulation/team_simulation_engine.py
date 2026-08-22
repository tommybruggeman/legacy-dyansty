
import numpy as np
import pandas as pd


class TeamSimulationEngine:

    def __init__(self):
        pass

    # -------------------------------------------------
    # CORE TEAM VALUE CURVE
    # -------------------------------------------------
    def simulate_team_curve(self, roster_df, outcome_df):
        """
        Produces team-level 5-year projection curve
        """

        if roster_df is None or len(roster_df) == 0:
            return {"error": "no roster"}

        results = []

        for year in range(1, 6):

            year_value = 0
            volatility = 0

            for _, r in roster_df.iterrows():

                sid = r.get("sleeper_id")

                match = outcome_df[outcome_df["sleeper_id"] == sid]

                if match.empty:
                    continue

                # expected value curve
                base = float(match.iloc[0].get(f"projection_{min(year,3)}yr", 50))

                # aging decay factor (soft, not rule-based logic)
                age = float(r.get("age", 27))
                decay = max(0, (age + year - 27) * 0.8)

                value = base - decay

                year_value += value
                volatility += abs(value * 0.1)

            results.append({
                "year": year,
                "team_value": float(year_value),
                "risk": float(volatility)
            })

        return {
            "curve": results,
            "peak_year": max(results, key=lambda x: x["team_value"])["year"],
            "total_value": sum(x["team_value"] for x in results)
        }


    # -------------------------------------------------
    # TRADE IMPACT SIMULATION
    # -------------------------------------------------
    def simulate_trade(self, roster_df, outcome_df, add_players=None, remove_players=None):

        add_players = add_players or []
        remove_players = remove_players or []

        base = self.simulate_team_curve(roster_df, outcome_df)
        base_value = base["total_value"]

        modified_roster = roster_df.copy()

        if remove_players:
            modified_roster = modified_roster[~modified_roster["sleeper_id"].isin(remove_players)]

        if add_players:
            modified_roster = pd.concat([modified_roster, pd.DataFrame(add_players)])

        new = self.simulate_team_curve(modified_roster, outcome_df)
        new_value = new["total_value"]

        return {
            "baseline_value": base_value,
            "new_value": new_value,
            "delta": new_value - base_value,
            "baseline_peak_year": base["peak_year"],
            "new_peak_year": new["peak_year"],
        }
