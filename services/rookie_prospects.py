from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping, Sequence


FANTASY_POSITIONS = ("QB", "RB", "WR", "TE")
SYNTHETIC_PREFIX = "prospect_"


class RookieIdentity(str, Enum):
    PRE_DRAFT_PROSPECT = "PRE_DRAFT_PROSPECT"
    CURRENT_SEASON_ROOKIE = "CURRENT_SEASON_ROOKIE"
    FORMER_ROOKIE = "FORMER_ROOKIE"
    UNKNOWN_ROOKIE_STATUS = "UNKNOWN_ROOKIE_STATUS"


@dataclass(frozen=True)
class ProspectImportPlan:
    upserts: tuple[Mapping[str, Any], ...]
    synthetic_ids_to_remove: tuple[str, ...]
    merge_events: tuple[str, ...]
    ambiguous_records: tuple[str, ...]


@dataclass(frozen=True)
class RookieClassDiagnostic:
    class_year: int
    total_records: int
    fantasy_position_records: int
    prospects: int
    active_nfl_rookies: int
    missing_college: int
    missing_draft_metadata: int
    synthetic_ids: int
    canonical_ids: int
    possible_duplicate_identities: int
    flags: tuple[str, ...]


@dataclass(frozen=True)
class DraftMatchRow:
    player_name: str
    position: str
    nfl_team: str
    draft_round: int
    overall_pick: int
    sleeper_id: str | None
    universe_name: str | None
    universe_team: str | None
    match_method: str
    confidence: str
    proposed_action: str
    warnings: tuple[str, ...]
    source: str | None = None
    source_updated_at: str | None = None


@dataclass(frozen=True)
class DraftImportPlan:
    reports: tuple[DraftMatchRow, ...]
    upserts: tuple[Mapping[str, Any], ...]
    synthetic_ids_to_remove: tuple[str, ...]
    errors: tuple[str, ...]
    official_count: int
    matched_count: int
    synthetic_count: int
    ambiguous_count: int
    missing_count: int
    inserted_count: int
    updated_count: int
    merged_count: int
    unchanged_count: int

    @property
    def safe_to_apply(self) -> bool:
        return not self.errors


def normalize_prospect_name(value: Any) -> str:
    text = str(value or "").casefold().replace("’", "'")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def synthetic_prospect_id(class_year: Any, player_name: Any, position: Any) -> str:
    year = _year(class_year)
    name = normalize_prospect_name(player_name)
    pos = str(position or "").strip().upper()
    if year is None or not name or pos not in FANTASY_POSITIONS:
        raise ValueError("Synthetic prospect identity requires class year, player name, and supported position.")
    return f"{SYNTHETIC_PREFIX}{year}_{name.replace(' ', '_')}_{pos.lower()}"


def resolved_rookie_class_year(row: Mapping[str, Any]) -> int | None:
    return _year(row.get("rookie_class_year") if row.get("rookie_class_year") not in (None, "") else row.get("draft_year"))


def classify_rookie_identity(row: Mapping[str, Any], resolved_class_year: Any) -> RookieIdentity:
    current_year = _year(resolved_class_year)
    class_year = resolved_rookie_class_year(row)
    if current_year is None or class_year is None:
        return RookieIdentity.UNKNOWN_ROOKIE_STATUS
    if class_year < current_year:
        return RookieIdentity.FORMER_ROOKIE
    if class_year != current_year:
        return RookieIdentity.UNKNOWN_ROOKIE_STATUS
    status = _upper(row.get("nfl_status") or row.get("status"))
    market_pool = _upper(row.get("market_pool"))
    if status == "PROSPECT" or market_pool == "ROOKIE_PROSPECT":
        return RookieIdentity.PRE_DRAFT_PROSPECT
    canonical = _canonical_id(row)
    if canonical and (row.get("active") is True or _text(row.get("nfl_team") or row.get("team"))):
        return RookieIdentity.CURRENT_SEASON_ROOKIE
    return RookieIdentity.UNKNOWN_ROOKIE_STATUS


def is_current_rookie_eligible(row: Mapping[str, Any], resolved_class_year: Any) -> bool:
    if resolved_rookie_class_year(row) != _year(resolved_class_year):
        return False
    if _upper(row.get("pos") or row.get("position")) not in FANTASY_POSITIONS:
        return False
    status = _upper(row.get("nfl_status") or row.get("status"))
    if status in {"RETIRED", "DECEASED"}:
        return False
    if _positive_int(row.get("draft_pick")) is not None:
        return True
    prospect = status == "PROSPECT" or _upper(row.get("market_pool")) == "ROOKIE_PROSPECT"
    if prospect:
        return True
    if _text(row.get("nfl_team") or row.get("team")):
        return True
    if row.get("active") is not True:
        return False
    return status not in {"INACTIVE", "PERMANENTLY_INACTIVE"}


def build_prospect_import_plan(
    incoming: Sequence[Mapping[str, Any]],
    existing: Sequence[Mapping[str, Any]],
    *,
    source_aliases: Mapping[str, str] | None = None,
) -> ProspectImportPlan:
    aliases = {str(key): str(value) for key, value in (source_aliases or {}).items()}
    existing_by_id = {_row_id(row): dict(row) for row in existing if _row_id(row)}
    identity_index: dict[tuple[str, str, int], list[Mapping[str, Any]]] = {}
    for row in existing:
        key = _identity_key(row)
        if key:
            identity_index.setdefault(key, []).append(row)

    upserts: dict[str, dict[str, Any]] = {}
    removals: set[str] = set()
    events: list[str] = []
    ambiguous: list[str] = []
    for raw in incoming:
        normalized = normalize_prospect_record(raw)
        explicit_id = _text(raw.get("sleeper_id") or raw.get("sleeper_player_id") or raw.get("canonical_player_id"))
        source_key = _text(raw.get("source_id") or raw.get("source_alias"))
        canonical_id = aliases.get(source_key or "") or (explicit_id if explicit_id and not _is_synthetic(explicit_id) else None)
        key = _identity_key(normalized)
        candidates = identity_index.get(key, []) if key else []
        college = normalize_prospect_name(normalized.get("college"))
        if college:
            candidates = [
                row for row in candidates
                if not normalize_prospect_name(row.get("college")) or normalize_prospect_name(row.get("college")) == college
            ]
        canonical_candidates = [row for row in candidates if not _is_synthetic(_row_id(row))]
        if not canonical_id and len(canonical_candidates) == 1:
            canonical_id = _row_id(canonical_candidates[0])
        elif not canonical_id and len(canonical_candidates) > 1:
            ambiguous.append(normalized["player_name"])

        target_id = canonical_id or synthetic_prospect_id(normalized["rookie_class_year"], normalized["player_name"], normalized["pos"])
        base = dict(existing_by_id.get(target_id, {}))
        synthetic_id = synthetic_prospect_id(normalized["rookie_class_year"], normalized["player_name"], normalized["pos"])
        synthetic = existing_by_id.get(synthetic_id)
        if canonical_id and synthetic_id != canonical_id and synthetic:
            base = _merge_preserving_history(base, synthetic)
            removals.add(synthetic_id)
            events.append(f"merged:{synthetic_id}->{canonical_id}")
        base.update({key: value for key, value in normalized.items() if value is not None})
        base["sleeper_id"] = target_id
        base["canonical_player_id"] = canonical_id
        if canonical_id:
            base["nfl_status"] = raw.get("nfl_status") or base.get("nfl_status")
            base["active"] = raw.get("active") if raw.get("active") is not None else base.get("active")
        upserts[target_id] = base

    return ProspectImportPlan(tuple(upserts.values()), tuple(sorted(removals)), tuple(events), tuple(ambiguous))


def build_completed_draft_import_plan(
    official_rows: Sequence[Mapping[str, Any]],
    sleeper_players: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
    existing_universe: Sequence[Mapping[str, Any]],
    *,
    source_aliases: Mapping[str, str] | None = None,
) -> DraftImportPlan:
    """Validate and plan a completed-draft import without performing writes."""
    aliases = {str(key): str(value) for key, value in (source_aliases or {}).items()}
    sleeper_by_id = _sleeper_by_id(sleeper_players)
    existing_by_id = {_row_id(row): dict(row) for row in existing_universe if _row_id(row)}
    name_pos: dict[tuple[str, str], list[tuple[str, Mapping[str, Any]]]] = {}
    gsis: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for sleeper_id, row in sleeper_by_id.items():
        key = (normalize_prospect_name(row.get("search_name") or row.get("full_name") or row.get("player_name")), _upper(row.get("position") or row.get("pos")))
        if key[0] and key[1]:
            name_pos.setdefault(key, []).append((sleeper_id, row))
        if _text(row.get("gsis_id")):
            gsis.setdefault(str(row["gsis_id"]), []).append((sleeper_id, row))

    errors: list[str] = []
    picks: dict[int, str] = {}
    resolved_ids: dict[str, str] = {}
    reports: list[DraftMatchRow] = []
    upserts: list[Mapping[str, Any]] = []
    removals: set[str] = set()
    counts = {"matched": 0, "synthetic": 0, "ambiguous": 0, "missing": 0, "inserted": 0, "updated": 0, "merged": 0, "unchanged": 0}

    for raw in official_rows:
        name = _text(raw.get("player_name")) or ""
        pos = _upper(raw.get("pos") or raw.get("position"))
        pick = _positive_int(raw.get("draft_pick"))
        round_number = _positive_int(raw.get("draft_round"))
        team = _upper(raw.get("nfl_team"))
        warnings: list[str] = []
        if pos not in FANTASY_POSITIONS:
            errors.append(f"unsupported_position:{name}:{pos}")
        if pick is None:
            errors.append(f"missing_overall_pick:{name}")
        elif pick in picks:
            errors.append(f"duplicate_overall_pick:{pick}:{picks[pick]}:{name}")
        else:
            picks[pick] = name
        if round_number is None:
            errors.append(f"missing_draft_round:{name}")

        candidates: list[tuple[str, Mapping[str, Any]]] = []
        method = "unmatched"
        confidence = "none"
        explicit_id = _text(raw.get("sleeper_id") or raw.get("sleeper_player_id"))
        canonical_id = _text(raw.get("canonical_player_id"))
        gsis_id = _text(raw.get("gsis_id"))
        alias_key = _text(raw.get("source_id") or raw.get("source_alias") or raw.get("search_name"))
        if explicit_id and explicit_id in sleeper_by_id:
            candidates = [(explicit_id, sleeper_by_id[explicit_id])]
            method, confidence = "explicit_sleeper_id", "exact"
        elif canonical_id and canonical_id in sleeper_by_id:
            candidates = [(canonical_id, sleeper_by_id[canonical_id])]
            method, confidence = "canonical_player_id", "exact"
        elif gsis_id and len(gsis.get(gsis_id, [])) == 1:
            candidates = gsis[gsis_id]
            method, confidence = "gsis_id", "exact"
        else:
            key = (normalize_prospect_name(raw.get("search_name") or name), pos)
            candidates = list(name_pos.get(key, []))
            if len(candidates) == 1:
                method, confidence = "normalized_name_position", "high"
            elif len(candidates) > 1:
                college = normalize_prospect_name(raw.get("college"))
                college_matches = [(sid, row) for sid, row in candidates if college and normalize_prospect_name(row.get("college")) == college]
                if len(college_matches) == 1:
                    candidates = college_matches
                    method, confidence = "normalized_name_position_college", "high"
            if len(candidates) != 1 and alias_key and aliases.get(alias_key) in sleeper_by_id:
                alias_id = aliases[alias_key]
                candidates = [(alias_id, sleeper_by_id[alias_id])]
                method, confidence = "explicit_alias", "exact"

        if len(candidates) > 1:
            counts["ambiguous"] += 1
            errors.append(f"ambiguous_identity:{name}:{','.join(sid for sid, _row in candidates)}")
            reports.append(DraftMatchRow(name, pos, team, round_number or 0, pick or 0, None, None, None, "ambiguous", "none", "abort", tuple(warnings), _text(raw.get("source")), _text(raw.get("source_updated_at"))))
            continue

        if len(candidates) == 1:
            sleeper_id, sleeper = candidates[0]
            sleeper_pos = _upper(sleeper.get("position") or sleeper.get("pos"))
            if sleeper_pos != pos:
                errors.append(f"conflicting_position:{name}:{pos}:{sleeper_pos}")
                warnings.append("Sleeper position conflicts with official draft position.")
            if sleeper_id in resolved_ids:
                errors.append(f"duplicate_sleeper_match:{sleeper_id}:{resolved_ids[sleeper_id]}:{name}")
            resolved_ids[sleeper_id] = name
            counts["matched"] += 1
            existing = existing_by_id.get(sleeper_id, {})
            synthetic_id = synthetic_prospect_id(raw.get("rookie_class_year") or raw.get("draft_year"), name, pos)
            synthetic = existing_by_id.get(synthetic_id)
            base = _merge_preserving_history(existing, synthetic or {})
            row = _completed_rookie_row(raw, sleeper_id, sleeper, base)
            if synthetic and synthetic_id != sleeper_id:
                removals.add(synthetic_id)
                counts["merged"] += 1
            action = "unchanged" if existing and _rookie_fields_equal(existing, row) and not synthetic else ("update" if existing else "insert")
            counts["unchanged" if action == "unchanged" else "updated" if action == "update" else "inserted"] += 1
            if action != "unchanged" or synthetic:
                upserts.append(row)
            reports.append(DraftMatchRow(name, pos, team, round_number or 0, pick or 0, sleeper_id, _text(existing.get("player_name")), _text(existing.get("nfl_team")), method, confidence, action if not synthetic else "merge", tuple(warnings), _text(raw.get("source")), _text(raw.get("source_updated_at"))))
            continue

        counts["missing"] += 1
        counts["synthetic"] += 1
        synthetic_id = synthetic_prospect_id(raw.get("rookie_class_year") or raw.get("draft_year"), name, pos)
        existing = existing_by_id.get(synthetic_id, {})
        row = _completed_synthetic_row(raw, synthetic_id, existing)
        action = "unchanged" if existing and _rookie_fields_equal(existing, row) else ("update" if existing else "insert")
        counts["unchanged" if action == "unchanged" else "updated" if action == "update" else "inserted"] += 1
        if action != "unchanged":
            upserts.append(row)
        reports.append(DraftMatchRow(name, pos, team, round_number or 0, pick or 0, None, _text(existing.get("player_name")), _text(existing.get("nfl_team")), "unmatched", "none", f"synthetic_{action}", ("No secure Sleeper match; deterministic synthetic fallback proposed.",), _text(raw.get("source")), _text(raw.get("source_updated_at"))))

    return DraftImportPlan(
        tuple(reports), tuple(upserts), tuple(sorted(removals)), tuple(dict.fromkeys(errors)), len(official_rows),
        counts["matched"], counts["synthetic"], counts["ambiguous"], counts["missing"], counts["inserted"],
        counts["updated"], counts["merged"], counts["unchanged"],
    )


def normalize_prospect_record(row: Mapping[str, Any]) -> dict[str, Any]:
    name = _text(row.get("player_name") or row.get("full_name"))
    pos = _upper(row.get("pos") or row.get("position"))
    year = resolved_rookie_class_year(row)
    if not name or pos not in FANTASY_POSITIONS or year is None:
        raise ValueError("Prospect records require player_name, supported pos, and rookie class year.")
    identity_value = _text(row.get("canonical_player_id") or row.get("sleeper_id") or row.get("sleeper_player_id"))
    canonical_id = identity_value if identity_value and not _is_synthetic(identity_value) else None
    return {
        "player_name": name,
        "search_name": normalize_prospect_name(row.get("search_name") or name),
        "pos": pos,
        "college": _text(row.get("college")),
        "rookie_class_year": year,
        "draft_year": _year(row.get("draft_year")) or year,
        "draft_round": _positive_int(row.get("draft_round")),
        "draft_pick": _positive_int(row.get("draft_pick")),
        "nfl_team": _text(row.get("nfl_team")),
        "canonical_player_id": canonical_id,
        "nfl_status": _text(row.get("nfl_status")) or "PROSPECT",
        "active": row.get("active") if row.get("active") is not None else False,
        "market_pool": _text(row.get("market_pool")) or "ROOKIE_PROSPECT",
        "years_exp": None,
        "has_contract": False,
    }


def resolve_rookie_stage(row: Mapping[str, Any], resolved_class_year: Any) -> str:
    if resolved_rookie_class_year(row) != _year(resolved_class_year):
        return RookieIdentity.FORMER_ROOKIE.value if (resolved_rookie_class_year(row) or 9999) < (_year(resolved_class_year) or 0) else RookieIdentity.UNKNOWN_ROOKIE_STATUS.value
    if _positive_int(row.get("draft_pick")) is not None:
        return "DRAFTED"
    prospect = _upper(row.get("nfl_status")) == "PROSPECT" or _upper(row.get("market_pool")) == "ROOKIE_PROSPECT"
    if _text(row.get("nfl_team") or row.get("team")) and not prospect:
        return "UDFA"
    if prospect:
        return "PROSPECT"
    return RookieIdentity.UNKNOWN_ROOKIE_STATUS.value


def build_rookie_class_diagnostics(rows: Sequence[Mapping[str, Any]], *, minimum_expected_fantasy_records: int = 3) -> tuple[RookieClassDiagnostic, ...]:
    by_year: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        year = resolved_rookie_class_year(row)
        if year is not None:
            by_year.setdefault(year, []).append(row)
    reports = []
    for year, year_rows in sorted(by_year.items()):
        identity_counts: dict[tuple[str, str, int], int] = {}
        for row in year_rows:
            key = _identity_key(row)
            if key:
                identity_counts[key] = identity_counts.get(key, 0) + 1
        fantasy_count = sum(_upper(row.get("pos") or row.get("position")) in FANTASY_POSITIONS for row in year_rows)
        flags = ("INCOMPLETE_CLASS_TOO_FEW_FANTASY_RECORDS",) if fantasy_count < minimum_expected_fantasy_records else ()
        reports.append(RookieClassDiagnostic(
            class_year=year,
            total_records=len(year_rows),
            fantasy_position_records=fantasy_count,
            prospects=sum(classify_rookie_identity(row, year) == RookieIdentity.PRE_DRAFT_PROSPECT for row in year_rows),
            active_nfl_rookies=sum(classify_rookie_identity(row, year) == RookieIdentity.CURRENT_SEASON_ROOKIE for row in year_rows),
            missing_college=sum(not _text(row.get("college")) for row in year_rows),
            missing_draft_metadata=sum(not _positive_int(row.get("draft_round")) or not _positive_int(row.get("draft_pick")) for row in year_rows),
            synthetic_ids=sum(_is_synthetic(_row_id(row)) for row in year_rows),
            canonical_ids=sum(bool(_canonical_id(row)) for row in year_rows),
            possible_duplicate_identities=sum(count - 1 for count in identity_counts.values() if count > 1),
            flags=flags,
        ))
    return tuple(reports)


def _merge_preserving_history(canonical: Mapping[str, Any], synthetic: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(synthetic)
    merged.update({key: value for key, value in canonical.items() if value is not None})
    for key, value in synthetic.items():
        if ("rank" in key or "projection" in key or "history" in key or key in {"draft_round", "draft_pick", "college"}) and merged.get(key) in (None, "", [], {}):
            merged[key] = value
    return merged


def _sleeper_by_id(rows: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    if isinstance(rows, Mapping):
        return {str(key): dict(value) for key, value in rows.items() if isinstance(value, Mapping)}
    return {
        str(player_id): dict(row)
        for row in rows
        if (player_id := row.get("sleeper_player_id") or row.get("sleeper_id") or row.get("player_id"))
    }


def _completed_rookie_row(official: Mapping[str, Any], sleeper_id: str, sleeper: Mapping[str, Any], existing: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(existing)
    canonical_name = _text(sleeper.get("full_name") or sleeper.get("player_name")) or _text(official.get("player_name"))
    row.update({
        "sleeper_id": sleeper_id,
        "canonical_player_id": sleeper_id,
        "gsis_id": _text(sleeper.get("gsis_id")) or row.get("gsis_id"),
        "player_name": canonical_name,
        "search_name": normalize_prospect_name(canonical_name),
        "pos": _upper(official.get("pos")),
        "nfl_team": _text(sleeper.get("team")) or _text(official.get("nfl_team")),
        "nfl_status": _text(sleeper.get("status")) or row.get("nfl_status"),
        "active": sleeper.get("active") if sleeper.get("active") is not None else row.get("active"),
        "rookie_class_year": _year(official.get("rookie_class_year") or official.get("draft_year")),
        "draft_year": _year(official.get("draft_year")),
        "draft_round": _positive_int(official.get("draft_round")),
        "draft_pick": _positive_int(official.get("draft_pick")),
        "years_exp": sleeper.get("years_exp") if sleeper.get("years_exp") is not None else 0,
        "college": _text(official.get("college")) or _text(sleeper.get("college")) or row.get("college"),
        "source": _text(official.get("source")),
        "source_updated_at": _text(official.get("source_updated_at")),
    })
    return row


def _completed_synthetic_row(official: Mapping[str, Any], synthetic_id: str, existing: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(existing)
    name = _text(official.get("player_name"))
    row.update({
        "sleeper_id": synthetic_id,
        "canonical_player_id": None,
        "player_name": name,
        "search_name": normalize_prospect_name(official.get("search_name") or name),
        "pos": _upper(official.get("pos")),
        "nfl_team": _text(official.get("nfl_team")),
        "nfl_status": "PROSPECT",
        "active": False,
        "market_pool": "ROOKIE_PROSPECT",
        "rookie_class_year": _year(official.get("rookie_class_year") or official.get("draft_year")),
        "draft_year": _year(official.get("draft_year")),
        "draft_round": _positive_int(official.get("draft_round")),
        "draft_pick": _positive_int(official.get("draft_pick")),
        "years_exp": None,
        "has_contract": bool(row.get("has_contract", False)),
        "college": _text(official.get("college")),
        "source": _text(official.get("source")),
        "source_updated_at": _text(official.get("source_updated_at")),
    })
    return row


def _rookie_fields_equal(existing: Mapping[str, Any], proposed: Mapping[str, Any]) -> bool:
    keys = (
        "player_name", "search_name", "pos", "nfl_team", "nfl_status", "active", "rookie_class_year",
        "draft_year", "draft_round", "draft_pick", "years_exp", "college", "source", "source_updated_at",
    )
    return all(existing.get(key) == proposed.get(key) for key in keys)


def _identity_key(row: Mapping[str, Any]) -> tuple[str, str, int] | None:
    name = normalize_prospect_name(row.get("search_name") or row.get("player_name") or row.get("full_name"))
    pos = _upper(row.get("pos") or row.get("position"))
    year = resolved_rookie_class_year(row)
    return (name, pos, year) if name and pos in FANTASY_POSITIONS and year is not None else None


def _row_id(row: Mapping[str, Any]) -> str | None:
    return _text(row.get("sleeper_id") or row.get("sleeper_player_id"))


def _canonical_id(row: Mapping[str, Any]) -> str | None:
    explicit = _text(row.get("canonical_player_id"))
    row_id = _row_id(row)
    return explicit or (row_id if row_id and not _is_synthetic(row_id) else None)


def _is_synthetic(value: Any) -> bool:
    return str(value or "").startswith(SYNTHETIC_PREFIX)


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _year(value: Any) -> int | None:
    try:
        year = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return year if 2000 <= year <= 2100 else None


def _positive_int(value: Any) -> int | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return int(number) if number > 0 and number.is_integer() else None
