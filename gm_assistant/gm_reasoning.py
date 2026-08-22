from __future__ import annotations

from collections import defaultdict


def _num(value, default=0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _bool(value):
    return bool(value)


def _name(r):
    return (r.get("player_name") or "").strip()


def _sort(rows, key, reverse=True):
    return sorted(rows, key=lambda r: _num(r.get(key)), reverse=reverse)


def build_team_reasoning(rows: list[dict]) -> dict:
    rows = [r for r in rows if r.get("player_name")]

    by_asset = _sort(rows, "asset_value_score")
    by_dynasty = _sort(rows, "dynasty_asset_score")
    by_win_now = _sort(rows, "win_now_asset_score")
    by_contract = _sort(rows, "contract_value_score")

    cornerstone = [
        r for r in by_dynasty
        if _bool(r.get("cornerstone_flag"))
        or (
            _num(r.get("dynasty_asset_score")) >= 70
            and _num(r.get("dynasty_window_score")) >= 70
        )
    ][:6]

    win_now_values = [
        r for r in by_win_now
        if _num(r.get("win_now_asset_score")) >= 55
    ][:6]

    dynasty_holds = [
        r for r in by_dynasty
        if _num(r.get("dynasty_asset_score")) >= 55
    ][:8]

    sell_highs = [
        r for r in by_asset
        if _bool(r.get("sell_high_flag"))
    ][:6]

    buy_lows = [
        r for r in by_dynasty
        if _bool(r.get("buy_low_flag"))
    ][:6]

    expensive_holds = [
        r for r in by_dynasty
        if r.get("asset_recommendation") == "EXPENSIVE HOLD"
    ][:5]

    sells = [
        r for r in sorted(rows, key=lambda r: _num(r.get("asset_value_score")))
        if r.get("asset_recommendation") == "SELL"
    ][:8]

    cheap_values = [
        r for r in by_contract
        if _num(r.get("salary")) <= 8
        and _num(r.get("engine_player_score")) >= 60
    ][:5]

    pos = defaultdict(list)
    for r in rows:
        pos[r.get("pos") or "UNK"].append(r)

    position_reads = {}

    for p, players in pos.items():
        avg_asset = sum(_num(x.get("asset_value_score")) for x in players) / max(len(players), 1)
        avg_dynasty = sum(_num(x.get("dynasty_asset_score")) for x in players) / max(len(players), 1)
        avg_win_now = sum(_num(x.get("win_now_asset_score")) for x in players) / max(len(players), 1)
        avg_window = sum(_num(x.get("dynasty_window_score")) for x in players) / max(len(players), 1)
        avg_engine = sum(_num(x.get("engine_player_score")) for x in players) / max(len(players), 1)
        total_salary = sum(_num(x.get("salary")) for x in players)

        best_dynasty = _sort(players, "dynasty_asset_score")[0]
        best_win_now = _sort(players, "win_now_asset_score")[0]
        worst = sorted(players, key=lambda x: _num(x.get("asset_value_score")))[0]

        if avg_dynasty >= 58:
            grade = "dynasty strength"
        elif avg_win_now >= 55:
            grade = "win-now strength"
        elif avg_asset >= 45:
            grade = "mixed"
        else:
            grade = "weakness"

        if total_salary >= 50 and avg_asset < 50:
            cap_note = "expensive relative to value"
        elif total_salary <= 15 and avg_asset >= 45:
            cap_note = "cheap and efficient"
        else:
            cap_note = "neutral"

        position_reads[p] = {
            "grade": grade,
            "count": len(players),
            "avg_asset_value_score": round(avg_asset, 2),
            "avg_dynasty_asset_score": round(avg_dynasty, 2),
            "avg_win_now_asset_score": round(avg_win_now, 2),
            "avg_dynasty_window_score": round(avg_window, 2),
            "avg_engine_player_score": round(avg_engine, 2),
            "total_salary": round(total_salary, 2),
            "cap_note": cap_note,
            "best_dynasty_asset": _name(best_dynasty),
            "best_win_now_asset": _name(best_win_now),
            "worst_asset": _name(worst),
        }

    strengths = []
    weaknesses = []
    next_moves = []
    strategy_notes = []

    qb = position_reads.get("QB")
    rb = position_reads.get("RB")
    wr = position_reads.get("WR")
    te = position_reads.get("TE")

    if qb:
        if qb["avg_dynasty_window_score"] >= 70:
            strengths.append("Superflex QB foundation is the biggest long-term strength.")
            strategy_notes.append("Because elite and usable QBs hold value well, this roster does not need a full teardown.")
        if qb["total_salary"] >= 70:
            weaknesses.append("QB room is expensive, so the rest of the roster needs cheaper value contracts.")

    if rb:
        if rb["avg_dynasty_asset_score"] < 45:
            weaknesses.append("RB is the clearest dynasty and contract-adjusted weakness.")
            next_moves.append("Avoid adding expensive RB contracts; target rookie RB upside, cheap rentals, or short-term production.")
        elif rb["avg_win_now_asset_score"] >= 55:
            strengths.append("RB has enough win-now value to compete.")

    if wr:
        if wr["avg_dynasty_asset_score"] >= 55:
            strengths.append("WR has usable dynasty value.")
        elif wr["total_salary"] >= 60 and wr["avg_dynasty_asset_score"] < 50:
            weaknesses.append("WR has too much salary tied to non-elite dynasty assets.")
            next_moves.append("Try to consolidate WR depth or expensive mid-tier WRs into one better long-term asset.")

    if te:
        if te["avg_dynasty_asset_score"] < 45:
            weaknesses.append("TE is not creating a meaningful weekly or dynasty advantage.")
            next_moves.append("Treat TE as a value-shopping position unless a true difference-maker becomes available.")

    if cornerstone:
        names = ", ".join(_name(r) for r in cornerstone[:3])
        strengths.append(f"True dynasty anchors: {names}.")

    if dynasty_holds:
        names = ", ".join(_name(r) for r in dynasty_holds[:4])
        strategy_notes.append(f"Best long-term holds: {names}.")

    if win_now_values:
        names = ", ".join(_name(r) for r in win_now_values[:4])
        strategy_notes.append(f"Best win-now values: {names}.")

    if expensive_holds:
        names = ", ".join(_name(r) for r in expensive_holds[:3])
        strategy_notes.append(f"{names} are valuable but expensive, so they reduce flexibility.")

    if sell_highs:
        names = ", ".join(_name(r) for r in sell_highs[:3])
        next_moves.append(f"Explore sell-high markets on: {names}.")

    if buy_lows:
        names = ", ".join(_name(r) for r in buy_lows[:3])
        next_moves.append(f"Consider buying low or holding through volatility on: {names}.")

    if sells:
        names = ", ".join(_name(r) for r in sells[:3])
        next_moves.append(f"Shop or churn the lowest-value contracts first: {names}.")

    total_salary = sum(_num(r.get("salary")) for r in rows)
    avg_asset = sum(_num(r.get("asset_value_score")) for r in rows) / max(len(rows), 1)
    avg_dynasty = sum(_num(r.get("dynasty_asset_score")) for r in rows) / max(len(rows), 1)
    avg_win_now = sum(_num(r.get("win_now_asset_score")) for r in rows) / max(len(rows), 1)

    if avg_dynasty >= 55 and avg_win_now >= 55:
        roster_direction = "contend"
        strategic_summary = "This roster has enough present production and dynasty value to play aggressively."
    elif qb and qb["avg_dynasty_window_score"] >= 70 and avg_dynasty >= 45:
        roster_direction = "soft retool"
        strategic_summary = "This is not a teardown. Keep the QB foundation and rebuild value around it."
    elif avg_dynasty >= 48:
        roster_direction = "selective buyer"
        strategic_summary = "This roster is in the middle: keep long-term assets, but avoid paying for marginal short-term upgrades."
    else:
        roster_direction = "retool"
        strategic_summary = "This roster should prioritize flexibility, younger value contracts, and moving inefficient salary."

    return {
        "roster_direction": roster_direction,
        "strategic_summary": strategic_summary,
        "total_salary": round(total_salary, 2),
        "avg_asset_value_score": round(avg_asset, 2),
        "avg_dynasty_asset_score": round(avg_dynasty, 2),
        "avg_win_now_asset_score": round(avg_win_now, 2),
        "cornerstone_players": [_name(r) for r in cornerstone],
        "dynasty_holds": [_name(r) for r in dynasty_holds],
        "win_now_values": [_name(r) for r in win_now_values],
        "sell_highs": [_name(r) for r in sell_highs],
        "buy_lows": [_name(r) for r in buy_lows],
        "sell_candidates": [_name(r) for r in sells],
        "cheap_values": [_name(r) for r in cheap_values],
        "position_reads": position_reads,
        "strengths": strengths or ["No clear roster strength detected yet."],
        "weaknesses": weaknesses or ["No glaring weakness detected from asset scores alone."],
        "strategy_notes": strategy_notes or ["Keep collecting value and avoid overpaying for marginal upgrades."],
        "next_moves": next_moves or ["Hold core players and look for value upgrades around the edges."],
    }


def summarize_reasoning_as_text(reasoning: dict) -> str:
    lines = []

    lines.append(f"Roster direction: {reasoning['roster_direction'].upper()}.")
    lines.append(reasoning["strategic_summary"])

    if reasoning.get("strengths"):
        lines.append("Strengths: " + " ".join(reasoning["strengths"]))

    if reasoning.get("weaknesses"):
        lines.append("Weaknesses: " + " ".join(reasoning["weaknesses"]))

    if reasoning.get("strategy_notes"):
        lines.append("Strategy: " + " ".join(reasoning["strategy_notes"]))

    if reasoning.get("next_moves"):
        lines.append("Next moves: " + " ".join(reasoning["next_moves"]))

    return "\n\n".join(lines)
