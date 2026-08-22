from __future__ import annotations

from gm_assistant.roster_loader import rows_for_owner
from gm_assistant.production_context import get_production_context

from auth import service_client
from gm_assistant.context_builder import build_team_context


def _num(row: dict, key: str, default: float = 0.0) -> float:
    try:
        value = row.get(key)
        return float(value) if value is not None else default
    except Exception:
        return default


def _roster_rows(owner_team_name: str) -> list[dict]:
    return rows_for_owner(owner_team_name) or []


def _move_score(row: dict) -> float:
    salary = _num(row, "salary")
    years = _num(row, "years")

    asset = (
        _num(row, "dynasty_asset_score")
        or _num(row, "asset_score")
        or _num(row, "trade_value_score")
    )

    ppg = (
        _num(row, "primary_ppg")
        or _num(row, "season_ppg")
        or _num(row, "expected_ppg")
    )

    age_risk = _num(row, "age_risk")

    contract_efficiency = _num(row, "contract_efficiency_score")
    contract_risk = _num(row, "contract_risk")

    if contract_risk <= 0 and contract_efficiency > 0:
        contract_risk = max(0, 100 - contract_efficiency)

    # Meaningful salary/years matter more than abstract risk on cheap depth.
    contract_weight = 0.25 if salary >= 8 or years >= 2 else 0.04

    score = 0.0
    score += salary * 1.15
    score += years * 1.75
    score += contract_risk * contract_weight
    score += age_risk * 0.8
    score -= asset * 0.22
    score -= ppg * 0.75

    # Do not rank $1 one-year depth as a priority exit unless they produce nothing.
    if salary <= 1 and years <= 1 and ppg > 3:
        score -= 10

    # Superflex anchor protection: elite QBs are market-check assets, not default exits.
    pos = str(row.get("pos") or "").upper()
    if pos == "QB" and ppg >= 20 and asset >= 65:
        score -= 25

    return score

def _apply_authoritative_production(row: dict) -> dict:
    from gm_assistant.pipeline.evidence.production_resolver import resolve_best_production

    out = dict(row or {})
    production = resolve_best_production(out)

    out["resolved_production"] = production

    if production.get("ppg") is not None:
        out["primary_ppg"] = production["ppg"]
        out["season_ppg"] = production["ppg"]
        out["ppg"] = production["ppg"]
        out["production_source"] = production["source"]
        out["production_confidence"] = production["confidence"]
    else:
        out["production_source"] = "production_unavailable"
        out["production_confidence"] = 0

    return out


def _display_ppg(row: dict) -> str:
    source = str(row.get("production_source") or "")
    ppg = row.get("primary_ppg") or row.get("season_ppg") or row.get("ppg")

    if source == "production_unavailable" or ppg is None:
        return "Production unavailable"

    try:
        return f"PPG {float(ppg):.2f}"
    except Exception:
        return "Production unavailable"


def _format_player(row: dict) -> str:
    row = _apply_authoritative_production(row)

    asset = (
        row.get("dynasty_asset_score")
        or row.get("asset_score")
        or row.get("trade_value_score")
        or 0
    )

    ppg = row.get("primary_ppg") or row.get("season_ppg") or row.get("ppg")
    ppg_text = "Production unavailable" if ppg is None else f"ppg {float(ppg):.2f}"

    raw_risk = row.get("contract_risk") or 0
    efficiency = row.get("contract_efficiency_score") or 0
    signal = raw_risk if raw_risk else max(0, 100 - float(efficiency or 0))

    return (
        f"{row.get('player_name')} ({row.get('pos','-')}) — "
        f"salary ${row.get('salary')}, "
        f"years {row.get('years')}, "
        f"asset {round(float(asset),1)}, "
        f"{ppg_text}, "
        f"contract risk {round(float(signal),1)}, "
        f"source {row.get('production_source')}"
    )


def answer_roster_exit_decision(question: str, owner_team_name: str, understanding: dict | None = None) -> dict:
    rows = _roster_rows(owner_team_name)

    if not rows:
        return {
            "answer_type": "roster_understanding",
            "decision": "NO_ROSTER_FOUND",
            "summary": f"I could not find roster rows for {owner_team_name}.",
            "understanding": understanding,
        }

    ranked = sorted(rows, key=_move_score, reverse=True)[:8]

    lines = []
    for i, row in enumerate(ranked):
        lines.append(f"{i+1}. {_format_player(row)}")

    return {
        "answer_type": "roster_understanding",
        "decision": "ROSTER_EXIT_DECISION",
        "summary": (
            "I understood this as a roster-exit question, so I ranked your actual roster by move pressure: "
            "contract burden, age/contract risk, production, and asset value.\n\n"
            + "\n".join(lines)
            + "\n\nLean: start market checks with the top names, but do not dump them blindly. "
            "The goal is to convert expensive or fragile value into cleaner production, picks, or younger flexibility."
        ),
        "players": ranked,
        "understanding": understanding,
    }


def answer_qb_surplus_to_rb_strategy(question: str, owner_team_name: str, understanding: dict | None = None) -> dict:
    rows = _roster_rows(owner_team_name)

    qbs = [r for r in rows if str(r.get("pos", "")).upper() == "QB"]
    rbs = [r for r in rows if str(r.get("pos", "")).upper() == "RB"]

    qbs_ranked = sorted(
        qbs,
        key=lambda r: (
            _num(r, "asset_score"),
            _num(r, "season_ppg"),
            -_num(r, "contract_risk"),
        ),
        reverse=True,
    )

    foundation = qbs_ranked[:2]
    movable = qbs_ranked[2:]

    qb_lines = []
    if foundation:
        qb_lines.append("Foundation QBs:")
        qb_lines.extend([f"- {_format_player(r)}" for r in foundation])

    if movable:
        qb_lines.append("\nMovable QB depth:")
        qb_lines.extend([f"- {_format_player(r)}" for r in movable])

    rb_lines = []
    if rbs:
        weak_rbs = sorted(
            rbs,
            key=lambda r: (_num(r, "season_ppg"), _num(r, "asset_score")),
        )[:5]
        rb_lines.append("\nCurrent RB pressure points:")
        rb_lines.extend([f"- {_format_player(r)}" for r in weak_rbs])

    return {
        "answer_type": "roster_understanding",
        "decision": "QB_SURPLUS_TO_RB_STRATEGY",
        "summary": (
            "I understood this as a roster-construction question: use QB surplus without damaging your superflex foundation.\n\n"
            + "\n".join(qb_lines + rb_lines)
            + "\n\nLean: protect the top two QBs unless the return is a true weekly RB difference-maker. "
            "Shop the movable QB tier first, ideally paired with a lesser asset, for a starting RB or RB-plus pick return."
        ),
        "foundation_qbs": foundation,
        "movable_qbs": movable,
        "running_backs": rbs,
        "understanding": understanding,
    }


def answer_roster_understanding_question(question: str, owner_team_name: str, understanding: dict) -> dict:
    intent = understanding.get("intent")

    if intent == "ROSTER_EXIT_DECISION":
        return answer_roster_exit_decision(question, owner_team_name, understanding)

    if intent == "QB_SURPLUS_TO_RB_STRATEGY":
        return answer_qb_surplus_to_rb_strategy(question, owner_team_name, understanding)

    return {
        "answer_type": "roster_understanding",
        "decision": "UNHANDLED_ROSTER_INTENT",
        "summary": "I understood this as a roster question, but this roster intent is not handled yet.",
        "understanding": understanding,
    }


def answer_position_review(question: str, owner_team_name: str, understanding: dict | None = None) -> dict:
    rows = _roster_rows(owner_team_name)
    q = question.lower()

    pos = None
    for p in ["QB", "RB", "WR", "TE"]:
        if p.lower() in q or f"{p.lower()}s" in q:
            pos = p
            break

    group = [r for r in rows if not pos or str(r.get("pos", "")).upper() == pos]
    group = sorted(
        group,
        key=lambda r: (
            float(r.get("primary_ppg") or r.get("season_ppg") or 0),
            float(r.get("dynasty_asset_score") or 0),
        ),
        reverse=True,
    )

    label = f"{pos} room" if pos else "roster"
    lines = [f"My read on your {label}:"]

    for r in group[:8]:
        lines.append(f"- {_format_player(r)}")

    lines.append("\nLean: use this group to identify where you have weekly strength, movable depth, and replacement-level roster spots.")

    return {
        "answer_type": "position_review",
        "decision": "POSITION_REVIEW",
        "summary": "\n".join(lines),
        "players": group[:8],
        "understanding": understanding,
    }


def answer_team_review(question: str, owner_team_name: str, understanding: dict | None = None) -> dict:
    rows = _roster_rows(owner_team_name)

    by_pos = {}
    for r in rows:
        by_pos.setdefault(str(r.get("pos") or "-").upper(), []).append(r)

    lines = ["Here is my GM read on this roster using the current graph context."]

    for pos in ["QB", "RB", "WR", "TE"]:
        group = by_pos.get(pos, [])
        if not group:
            continue

        top = sorted(
            group,
            key=lambda r: float(r.get("primary_ppg") or r.get("season_ppg") or 0),
            reverse=True,
        )[:3]

        avg_ppg = sum(float(r.get("primary_ppg") or r.get("season_ppg") or 0) for r in group) / max(1, len(group))
        lines.append(f"\n{pos}: {len(group)} players, avg usable PPG {avg_ppg:.1f}.")
        for r in top:
            lines.append(f"- {_format_player(r)}")

    lines.append(
        "\nMy move: protect your true weekly anchors, market-check expensive inefficient contracts, and use surplus QB/WR value to improve RB or TE without emptying the future."
    )

    return {
        "answer_type": "team_review",
        "decision": "TEAM_REVIEW",
        "summary": "\n".join(lines),
        "understanding": understanding,
    }


def answer_data_quality_review(question: str, owner_team_name: str, understanding: dict | None = None) -> dict:
    roster = _roster_rows(owner_team_name)

    rows = []
    for r in roster:
        r = _apply_authoritative_production(r)
        source = r.get("production_source") or "production_unavailable"
        conf = float(r.get("production_confidence") or 0)

        if source in {"production_unavailable", "no_production_source", "player_universe_latest_week_fallback"} or conf < 70:
            rows.append((conf, source, r))

    rows = sorted(rows, key=lambda x: x[0])[:10]

    lines = ["The players with the least reliable production context are:"]

    for conf, source, r in rows:
        lines.append(
            f"- {r.get('player_name')} ({r.get('pos')}) — "
            f"confidence {int(conf)}, source {source}, {_display_ppg(r)}."
        )

    lines.append("")
    lines.append("Lean: treat these as source-work priorities before making hard trade/cut decisions from production alone.")

    return {
        "answer_type": "roster_understanding",
        "decision": "DATA_QUALITY_REVIEW",
        "summary": "\n".join(lines),
        "players": [r for _, _, r in rows],
        "understanding": understanding,
    }


def answer_trade_strategy(question: str, owner_team_name: str, understanding: dict | None = None) -> dict:
    rows = _roster_rows(owner_team_name)

    expensive = sorted(
        [r for r in rows if float(r.get("salary") or 0) >= 10],
        key=lambda r: (
            float(r.get("contract_risk") or max(0, 100 - float(r.get("contract_efficiency_score") or 0))),
            float(r.get("salary") or 0),
        ),
        reverse=True,
    )[:5]

    leverage = sorted(
        rows,
        key=lambda r: (
            float(r.get("dynasty_asset_score") or 0),
            float(r.get("primary_ppg") or r.get("season_ppg") or 0),
        ),
        reverse=True,
    )[:6]

    lines = ["Trade strategy: I would build from leverage first, not from panic."]

    lines.append("\nBest leverage pieces to discuss:")
    for r in leverage:
        lines.append(f"- {_format_player(r)}")

    lines.append("\nContracts/players to market-check:")
    for r in expensive:
        lines.append(f"- {_format_player(r)}")

    lines.append(
        "\nLean: shop from the movable tier first. Package one useful name plus a smaller piece for RB/TE help, but protect elite QB value unless the return is a weekly difference-maker."
    )

    return {
        "answer_type": "trade_strategy",
        "decision": "TRADE_STRATEGY",
        "summary": "\n".join(lines),
        "understanding": understanding,
    }


def answer_player_trade_decision(question: str, owner_team_name: str, understanding: dict | None = None) -> dict:
    rows = _roster_rows(owner_team_name)
    players = understanding.get("players") if understanding else []

    target = None
    for name in players or []:
        n = str(name).lower()
        target = next((r for r in rows if n in str(r.get("player_name") or "").lower()), None)
        if target:
            break

    if not target:
        return answer_roster_exit_decision(question, owner_team_name, understanding)

    salary = float(target.get("salary") or 0)
    years = float(target.get("years") or 0)
    ppg = float(target.get("primary_ppg") or target.get("season_ppg") or 0)
    asset = float(target.get("dynasty_asset_score") or 0)
    risk = float(target.get("contract_risk") or max(0, 100 - float(target.get("contract_efficiency_score") or 0)))

    move_pressure = salary * 1.1 + years * 2 + risk * 0.25 - asset * 0.2 - ppg * 0.6

    if move_pressure >= 25:
        stance = "shop"
    elif move_pressure >= 10:
        stance = "market-check"
    else:
        stance = "hold unless overpaid"

    summary = (
        f"My read on {target.get('player_name')}: {stance}.\n\n"
        f"{_format_player(target)}\n\n"
        f"Why: salary, years, contract risk, production, and dynasty asset value combine to a move-pressure score of {move_pressure:.1f}.\n\n"
        "Lean: do not dump the player blindly. Set a price first, then only move if the return improves weekly lineup strength, cap flexibility, or future value."
    )

    return {
        "answer_type": "player_trade_decision",
        "decision": "PLAYER_TRADE_DECISION",
        "summary": summary,
        "player": target,
        "understanding": understanding,
    }
