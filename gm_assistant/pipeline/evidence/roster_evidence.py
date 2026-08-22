from __future__ import annotations

from gm_assistant.pipeline.models import EvidencePack
from gm_assistant.pipeline.evidence.production_resolver import resolve_best_production


def _load_roster_rows(owner_team_name: str) -> list[dict]:
    try:
        from snapshot.intelligence.roster.builder import build_roster_intelligence

        data = build_roster_intelligence(owner_team_name)
        rows = []

        for key in [
            "core_players",
            "move_candidates",
            "strength_players",
            "depth_players",
            "all_players",
        ]:
            vals = data.get(key) or []
            if isinstance(vals, list):
                rows.extend([v for v in vals if isinstance(v, dict)])

        seen = set()
        deduped = []

        for r in rows:
            name = r.get("player_name") or r.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(r)

        for row in deduped:
            production = resolve_best_production(row)
            row["resolved_production"] = production
            if production.get("ppg") is not None:
                row["ppg"] = production["ppg"]
                row["production_source"] = production["source"]
                row["production_confidence"] = production["confidence"]

        return deduped

    except Exception as e:
        return [{"_evidence_error": str(e)}]


def build_roster_evidence(question: str, owner_team_name: str, understanding: dict) -> EvidencePack:
    roster = _load_roster_rows(owner_team_name)
    player_names = understanding.get("players") or []

    player = None
    if player_names:
        target = str(player_names[0]).lower()
        for row in roster:
            name = str(row.get("player_name") or row.get("name") or "").lower()
            if name == target:
                player = row
                break

    notes = []
    if not roster:
        notes.append("Roster evidence unavailable.")
    if roster and roster[0].get("_evidence_error"):
        notes.append(roster[0]["_evidence_error"])

    return EvidencePack(
        question=question,
        owner_team_name=owner_team_name,
        understanding=understanding,
        player=player,
        roster=roster,
        team_context={
            "roster_size": len(roster),
            "detected_players": player_names,
        },
        notes=notes,
    )
