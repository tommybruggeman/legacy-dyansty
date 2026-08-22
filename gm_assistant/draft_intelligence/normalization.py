from __future__ import annotations

import re
from typing import Any

from gm_assistant.draft_intelligence.models import ParsedPickReference


ORDINALS = {
    "first": 1,
    "1st": 1,
    "one": 1,
    "second": 2,
    "2nd": 2,
    "two": 2,
    "third": 3,
    "3rd": 3,
    "three": 3,
    "fourth": 4,
    "4th": 4,
    "four": 4,
    "fifth": 5,
    "5th": 5,
    "five": 5,
}


def parse_pick_references(text: str, *, current_team_id: str | None = None) -> list[ParsedPickReference]:
    normalized = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    refs: list[ParsedPickReference] = []
    consumed: list[tuple[int, int]] = []

    for match in re.finditer(r"\b([1-9])\.(0?[1-9]|1[0-9]|2[0-9])\b", normalized):
        round_number = int(match.group(1))
        slot = int(match.group(2))
        refs.append(ParsedPickReference(match.group(0), "exact_slot", round=round_number, slot=slot, label=f"{round_number}.{slot:02d}"))
        consumed.append(match.span())

    for match in re.finditer(r"\b(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+(?:overall|pick)\b", normalized):
        if _inside(match.span(), consumed):
            continue
        slot = ORDINALS.get(match.group(1))
        if slot:
            refs.append(ParsedPickReference(match.group(0), "exact_slot", round=1, slot=slot, label=f"1.{slot:02d}"))
            consumed.append(match.span())

    for match in re.finditer(r"\bpick\s+(one|two|three|four|five|[1-9]|1[0-9]|2[0-9])\b", normalized):
        if _inside(match.span(), consumed):
            continue
        raw = match.group(1)
        slot = ORDINALS.get(raw) or int(raw)
        refs.append(ParsedPickReference(match.group(0), "exact_slot", round=1, slot=slot, label=f"1.{slot:02d}"))
        consumed.append(match.span())

    round_pattern = (
        r"\b(?:(my|his|her|their|a|an|one of my|without moving my)\s+)?"
        r"(?:(20\d{2}|future|early|late|mid)\s+)?"
        r"(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)(?:-|\s+)?round(?:\s+pick)?\b"
    )
    for match in re.finditer(round_pattern, normalized):
        if _inside(match.span(), consumed):
            continue
        owner_text, season_text, round_text = match.groups()
        refs.append(_round_ref(match.group(0), owner_text, season_text, round_text, current_team_id))
        consumed.append(match.span())

    compact_pattern = (
        r"\b(?:(my|his|her|their|a|an|one of my|without moving my)\s+)?"
        r"(?:(20\d{2}|future|early|late|mid)\s+)?"
        r"(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)s?\b"
    )
    for match in re.finditer(compact_pattern, normalized):
        if _inside(match.span(), consumed):
            continue
        owner_text, season_text, round_text = match.groups()
        if not owner_text and not season_text:
            continue
        refs.append(_round_ref(match.group(0), owner_text, season_text, round_text, current_team_id))
        consumed.append(match.span())

    for match in re.finditer(r"\bround\s+(one|two|three|four|five|[1-5])\b", normalized):
        if _inside(match.span(), consumed):
            continue
        raw = match.group(1)
        refs.append(ParsedPickReference(match.group(0), "round_only", round=ORDINALS.get(raw) or int(raw), resolution_status="unresolved"))
        consumed.append(match.span())

    return _dedupe_refs(refs)


def pick_labels_from_row(row: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    explicit = _clean(row.get("pick_label") or row.get("label") or row.get("canonical_pick_id") or row.get("pick_id") or row.get("id"))
    if explicit:
        labels.add(explicit)
        normalized = normalize_pick_label(explicit)
        if normalized:
            labels.add(normalized)
    season = _safe_int(row.get("season") or row.get("draft_year") or row.get("rookie_class_year"))
    round_number = _safe_int(row.get("round"))
    slot = _safe_int(row.get("slot") or row.get("pick") or row.get("original_pick_rank"))
    if round_number and slot:
        labels.add(f"{round_number}.{slot:02d}")
    if season and round_number and slot:
        labels.add(f"{season}_{round_number}.{slot:02d}")
    if season and round_number:
        labels.add(f"{season}_round_{round_number}")
    return {label for label in labels if label}


def normalize_pick_label(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    match = re.search(r"\b([1-9])\.(0?[1-9]|1[0-9]|2[0-9])\b", text)
    if match:
        return f"{int(match.group(1))}.{int(match.group(2)):02d}"
    return None


def _round_ref(raw_text: str, owner_text: str | None, season_text: str | None, round_text: str, current_team_id: str | None) -> ParsedPickReference:
    season = int(season_text) if season_text and season_text.isdigit() else None
    current_owner = current_team_id if owner_text in {"my", "one of my", "without moving my"} else None
    reference_type = "future_asset" if season else "round_only"
    status = "resolved" if current_owner else "unresolved"
    return ParsedPickReference(raw_text, reference_type, season=season, round=ORDINALS.get(round_text), current_owner_team_id=current_owner, resolution_status=status)


def _inside(span: tuple[int, int], consumed: list[tuple[int, int]]) -> bool:
    return any(span[0] >= start and span[1] <= end for start, end in consumed)


def _dedupe_refs(refs: list[ParsedPickReference]) -> list[ParsedPickReference]:
    out: list[ParsedPickReference] = []
    seen: set[tuple] = set()
    for ref in refs:
        key = (ref.reference_type, ref.season, ref.round, ref.slot, ref.current_owner_team_id, ref.raw_text)
        if key not in seen:
            out.append(ref)
            seen.add(key)
    return out


def _safe_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
