from __future__ import annotations


def rookie_board_quality(ctx: dict) -> list[str]:
    flags = []

    prospects = ctx.get("prospect_quality", [])
    board = ctx.get("rookie_board", [])

    if len(board) < 12:
        flags.append("Rookie board is thin; likely missing real 2026 source boards.")

    placeholder_rows = [
        r for r in prospects
        if "Auto-detected from Sleeper" in str(r.get("risk_notes") or "")
    ]

    if placeholder_rows:
        flags.append("Prospect context still contains Sleeper placeholder rows.")

    if prospects:
        source_rows = [
            r for r in prospects
            if "Consensus from" in str(r.get("risk_notes") or "")
        ]
        if not source_rows:
            flags.append("No consensus-source prospect rows detected.")

    return flags


def build_quality_flags(ctx: dict) -> list[str]:
    flags = []
    flags.extend(rookie_board_quality(ctx))
    return flags
