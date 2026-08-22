from __future__ import annotations


def position_bands(pos: str, rank: int) -> dict:
    pos = (pos or "").upper()

    if pos == "QB":
        if rank <= 3:
            return {
                "expected": "Elite QB1 / top-3 QB",
                "minimum": "Top-6 QB",
                "bust": "Outside top-10 QB",
                "league_winner": "QB1 overall ceiling",
            }
        if rank <= 8:
            return {
                "expected": "QB1 starter",
                "minimum": "Top-10 QB",
                "bust": "Outside top-15 QB",
                "league_winner": "Top-3 QB season",
            }
        if rank <= 15:
            return {
                "expected": "Superflex starter",
                "minimum": "Top-18 QB",
                "bust": "Non-starting fantasy QB",
                "league_winner": "Top-8 QB season",
            }

    if pos == "RB":
        if rank <= 5:
            return {
                "expected": "Elite RB1",
                "minimum": "Top-10 RB",
                "bust": "Outside top-18 RB",
                "league_winner": "Top-3 RB season",
            }
        if rank <= 12:
            return {
                "expected": "RB1 / high-end RB2",
                "minimum": "Top-20 RB",
                "bust": "Outside top-30 RB",
                "league_winner": "Top-6 RB season",
            }
        if rank <= 24:
            return {
                "expected": "Usable RB2 / flex",
                "minimum": "Top-36 RB",
                "bust": "Outside top-45 RB",
                "league_winner": "Top-12 RB season",
            }

    if pos == "WR":
        if rank <= 5:
            return {
                "expected": "Elite WR1",
                "minimum": "Top-10 WR",
                "bust": "Outside top-18 WR",
                "league_winner": "Top-3 WR season",
            }
        if rank <= 12:
            return {
                "expected": "WR1",
                "minimum": "Top-18 WR",
                "bust": "Outside top-30 WR",
                "league_winner": "Top-5 WR season",
            }
        if rank <= 24:
            return {
                "expected": "WR2 / strong starter",
                "minimum": "Top-36 WR",
                "bust": "Outside top-45 WR",
                "league_winner": "Top-12 WR season",
            }

    if pos == "TE":
        if rank <= 3:
            return {
                "expected": "Elite TE advantage",
                "minimum": "Top-5 TE",
                "bust": "Outside top-10 TE",
                "league_winner": "TE1 overall ceiling",
            }
        if rank <= 8:
            return {
                "expected": "Starting TE",
                "minimum": "Top-12 TE",
                "bust": "Outside top-18 TE",
                "league_winner": "Top-3 TE season",
            }

    return {
        "expected": "Useful fantasy contributor",
        "minimum": "Playable depth",
        "bust": "Replacement-level output",
        "league_winner": "Unexpected positional spike",
    }


def verdict(expectation_fit: float, underperformance_risk: float, pressure: float) -> str:
    if expectation_fit >= 75 and underperformance_risk <= 35:
        return "MEETING / BEATING EXPECTATION"
    if expectation_fit >= 60:
        return "ACCEPTABLE EXPECTATION FIT"
    if underperformance_risk >= 75 and pressure >= 75:
        return "MAJOR UNDERPERFORMANCE RISK"
    if underperformance_risk >= 60:
        return "UNDERPERFORMANCE WATCH"
    return "EXPECTATION MONITOR"
