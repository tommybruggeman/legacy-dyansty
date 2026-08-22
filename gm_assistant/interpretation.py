from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from gm_assistant.conversation_state import (
    ConversationState,
    ConversationStateUpdate,
)
from gm_assistant.draft_intelligence import parse_pick_references
from gm_assistant.request_context import AssistantRequestContext
from gm_assistant.scenario_simulator import is_scenario_question


INTERPRETER_VERSION = "gm_interpreter.v1"

TEAM_IDENTITY_LOOKUP_PHRASES = (
    "what team am i managing",
    "what team do i manage",
    "which team is mine",
    "who is my team",
    "what is my team name",
    "which franchise do i control",
    "tell me my team",
)

ROSTER_LIST_LOOKUP_PHRASES = (
    "who is on my team",
    "who is on my roster",
    "show me my roster",
    "show my roster",
    "list my players",
    "list my roster",
    "which players do i have",
    "who do i own",
)

LEAGUE_OWNER_INTELLIGENCE_PHRASES = (
    "who trades the most",
    "which team trades the most",
    "most active trader",
    "acquired the most future picks",
    "owns the most future first",
    "who owns the most future first",
    "have i traded with",
    "what trades has",
    "most cap space",
    "currently need a quarterback",
    "currently need an rb",
    "currently need a running back",
    "currently need a wide receiver",
    "currently need a receiver",
    "currently need a tight end",
    "assets to trade for",
)

FOOTBALL_INTELLIGENCE_PHRASES = (
    "biggest roster needs",
    "biggest needs",
    "roster needs",
    "structural needs",
    "team strengths",
    "team weaknesses",
    "best player",
    "best players",
    "three best players",
    "top five players",
    "top 5 players",
    "rank my roster",
    "rank the roster",
    "rank my entire roster",
    "rank my full roster",
    "rank all my players",
    "rank my players",
    "best-to-worst roster ranking",
    "best to worst roster ranking",
    "best players on my roster",
    "strongest players",
    "order my roster by value",
    "roster ranking",
    "biggest weakness",
    "strongest position",
    "weakest position",
    "thinnest position",
    "roster thinnest",
    "positional depth",
    "roster construction",
    "roster structure",
    "lineup coverage",
    "starter shortage",
    "contract risk",
    "contract exposure",
    "future roster holes",
    "next offseason needs",
    "age concentration",
    "salary concentration",
    "qb depth",
    "quarterback depth",
    "rb depth",
    "rb room",
    "fix my rb",
    "fix the rb",
    "running back depth",
    "wr depth",
    "receiver depth",
    "te depth",
    "tight end depth",
    "contender fit",
    "am i a contender",
    "contender",
    "championship window",
    "rebuild fit",
    "balanced roster",
    "roster balance",
    "offseason strategy",
    "market-check",
    "market check",
)


class Intent(str, Enum):
    PLAYER_EVALUATION = "player_evaluation"
    PLAYER_COMPARISON = "player_comparison"
    ROSTER_EVALUATION = "roster_evaluation"
    TRADE_EVALUATION = "trade_evaluation"
    TRADE_DISCOVERY = "trade_discovery"
    TRADE_CONSTRUCTION = "trade_construction"
    DRAFT_RECOMMENDATION = "draft_recommendation"
    DRAFT_PICK_EVALUATION = "draft_pick_evaluation"
    FREE_AGENT_RECOMMENDATION = "free_agent_recommendation"
    CONTRACT_QUESTION = "contract_question"
    SALARY_CAP_QUESTION = "salary_cap_question"
    RULES_QUESTION = "rules_question"
    LINEUP_QUESTION = "lineup_question"
    ROSTER_MOVE_QUESTION = "roster_move_question"
    LONG_TERM_PLANNING = "long_term_planning"
    TEAM_COMPARISON = "team_comparison"
    LEAGUE_ANALYSIS = "league_analysis"
    SCENARIO_SIMULATION = "scenario_simulation"
    DATA_LOOKUP = "data_lookup"
    FOLLOW_UP = "follow_up"
    GENERAL_CONVERSATION = "general_conversation"
    UNSUPPORTED = "unsupported"


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    INFERRED_FROM_CONVERSATION = "inferred_from_conversation"


@dataclass(frozen=True)
class EntityRef:
    entity_type: str
    raw_text: str
    canonical_id: str | None = None
    canonical_name: str | None = None
    resolution_status: str = ResolutionStatus.UNRESOLVED.value
    confidence: str = "low"
    candidate_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DraftPickRef:
    raw_text: str
    season: int | None = None
    round: int | None = None
    slot: int | None = None
    original_team_id: str | None = None
    current_owner_team_id: str | None = None
    canonical_pick_id: str | None = None
    resolution_status: str = ResolutionStatus.UNRESOLVED.value


@dataclass(frozen=True)
class AssetRef:
    asset_type: str
    canonical_id: str | None = None
    label: str | None = None
    ownership_context: str | None = None
    quantity: int | None = None
    season: int | None = None


@dataclass(frozen=True)
class Ambiguity:
    ambiguity_type: str
    raw_text: str
    candidates: list[str] = field(default_factory=list)
    blocking: bool = False
    explanation: str = ""


@dataclass(frozen=True)
class InterpretedQuestion:
    raw_question: str
    primary_intent: str
    secondary_intents: list[str] = field(default_factory=list)
    player_refs: list[EntityRef] = field(default_factory=list)
    fantasy_team_refs: list[EntityRef] = field(default_factory=list)
    nfl_team_refs: list[EntityRef] = field(default_factory=list)
    owner_refs: list[EntityRef] = field(default_factory=list)
    pick_refs: list[DraftPickRef] = field(default_factory=list)
    positions: list[str] = field(default_factory=list)
    seasons: list[int] = field(default_factory=list)
    timeframe: dict[str, Any] = field(default_factory=dict)
    included_assets: list[AssetRef] = field(default_factory=list)
    excluded_assets: list[AssetRef] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    requested_count: int | None = None
    is_follow_up: bool = False
    follow_up_target: str | None = None
    ambiguities: list[Ambiguity] = field(default_factory=list)
    unresolved_text: list[str] = field(default_factory=list)
    confidence: str = "medium"
    interpreter_version: str = INTERPRETER_VERSION

    def to_packet(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Candidate:
    entity_type: str
    canonical_id: str
    canonical_name: str
    aliases: tuple[str, ...]
    league_id: str | None = None


ORDINAL_ROUNDS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
}

COUNT_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

POSITION_ALIASES = {
    "qb": "QB",
    "quarterback": "QB",
    "quarterbacks": "QB",
    "rb": "RB",
    "rbs": "RB",
    "running back": "RB",
    "running backs": "RB",
    "wr": "WR",
    "wrs": "WR",
    "receiver": "WR",
    "receivers": "WR",
    "wide receiver": "WR",
    "wide receivers": "WR",
    "te": "TE",
    "tes": "TE",
    "tight end": "TE",
    "tight ends": "TE",
    "k": "K",
    "kicker": "K",
    "def": "DST",
    "defense": "DST",
    "dst": "DST",
    "flex": "FLEX",
    "superflex": "SUPERFLEX",
    "sf": "SUPERFLEX",
}

NFL_TEAMS = {
    "ARI": ("arizona", "cardinals", "arizona cardinals", "ari"),
    "ATL": ("atlanta", "falcons", "atlanta falcons", "atl"),
    "BAL": ("baltimore", "ravens", "baltimore ravens", "bal"),
    "BUF": ("buffalo", "bills", "buffalo bills", "buf"),
    "CAR": ("carolina", "panthers", "carolina panthers", "car"),
    "CHI": ("chicago", "bears", "chicago bears", "chi"),
    "CIN": ("cincinnati", "bengals", "cincinnati bengals", "cin"),
    "CLE": ("cleveland", "browns", "cleveland browns", "cle"),
    "DAL": ("dallas", "cowboys", "dallas cowboys", "dal"),
    "DEN": ("denver", "broncos", "denver broncos", "den"),
    "DET": ("detroit", "lions", "detroit lions", "det"),
    "GB": ("green bay", "packers", "green bay packers", "gb"),
    "HOU": ("houston", "texans", "houston texans", "hou"),
    "IND": ("indianapolis", "colts", "indianapolis colts", "ind"),
    "JAX": ("jacksonville", "jaguars", "jacksonville jaguars", "jax"),
    "KC": ("kansas city", "chiefs", "kansas city chiefs", "kc"),
    "LV": ("las vegas", "raiders", "las vegas raiders", "lv"),
    "LAC": ("chargers", "los angeles chargers", "la chargers", "lac"),
    "LAR": ("rams", "los angeles rams", "la rams", "lar"),
    "MIA": ("miami", "dolphins", "miami dolphins", "mia"),
    "MIN": ("minnesota", "vikings", "minnesota vikings", "min"),
    "NE": ("new england", "patriots", "new england patriots", "ne"),
    "NO": ("new orleans", "saints", "new orleans saints", "no"),
    "NYG": ("new york giants", "giants", "nyg"),
    "NYJ": ("new york jets", "jets", "nyj"),
    "PHI": ("philadelphia", "eagles", "philadelphia eagles", "phi"),
    "PIT": ("pittsburgh", "steelers", "pittsburgh steelers", "pit"),
    "SEA": ("seattle", "seahawks", "seattle seahawks", "sea"),
    "SF": ("san francisco", "49ers", "niners", "san francisco 49ers", "sf"),
    "TB": ("tampa bay", "buccaneers", "bucs", "tampa bay buccaneers", "tb"),
    "TEN": ("tennessee", "titans", "tennessee titans", "ten"),
    "WAS": ("washington", "commanders", "washington commanders", "was"),
}


def interpret_question(
    question: str,
    context: AssistantRequestContext,
    conversation_state: ConversationState | None = None,
    *,
    sb: Any | None = None,
) -> InterpretedQuestion:
    raw_question = str(question or "").strip()
    normalized = _normalize(raw_question)
    player_candidates = _load_player_candidates(sb, context)
    team_candidates = _load_fantasy_team_candidates(sb, context)

    positions = _extract_positions(normalized)
    seasons, timeframe = _extract_seasons_and_timeframe(normalized, context)
    requested_count = _extract_requested_count(normalized)
    pick_refs = _parse_draft_picks(normalized, context)
    player_refs, player_ambiguities, unresolved = _resolve_players(raw_question, normalized, player_candidates)
    fantasy_team_refs, owner_refs, team_ambiguities = _resolve_fantasy_teams(normalized, context, team_candidates)
    nfl_team_refs, nfl_ambiguities = _resolve_nfl_teams(normalized, fantasy_team_refs)
    is_follow_up, follow_up_target, follow_up_refs, follow_up_ambiguities = _resolve_follow_up(
        normalized,
        conversation_state,
        player_refs,
    )
    player_refs = _merge_entity_refs(player_refs, follow_up_refs)
    constraints, constraint_ambiguities = _extract_constraints(normalized, positions, requested_count)
    included_assets, excluded_assets = _extract_assets(player_refs, pick_refs, constraints, normalized)
    primary_intent, secondary_intents = _classify_intents(
        normalized,
        player_refs=player_refs,
        team_refs=fantasy_team_refs,
        pick_refs=pick_refs,
        positions=positions,
        is_follow_up=is_follow_up,
    )
    if is_team_identity_question(normalized) or is_roster_list_question(normalized):
        player_refs = []
        player_ambiguities = []
        unresolved = []
        included_assets = []
        excluded_assets = []
    ambiguities = player_ambiguities + team_ambiguities + nfl_ambiguities + follow_up_ambiguities + constraint_ambiguities
    ambiguities += _intent_blocking_ambiguities(primary_intent, player_refs, pick_refs, normalized)
    confidence = _confidence(primary_intent, ambiguities, unresolved)

    return InterpretedQuestion(
        raw_question=raw_question,
        primary_intent=primary_intent.value,
        secondary_intents=[intent.value for intent in secondary_intents],
        player_refs=player_refs,
        fantasy_team_refs=fantasy_team_refs,
        nfl_team_refs=nfl_team_refs,
        owner_refs=owner_refs,
        pick_refs=pick_refs,
        positions=positions,
        seasons=seasons,
        timeframe=timeframe,
        included_assets=included_assets,
        excluded_assets=excluded_assets,
        constraints=constraints,
        requested_count=requested_count,
        is_follow_up=is_follow_up,
        follow_up_target=follow_up_target,
        ambiguities=ambiguities,
        unresolved_text=unresolved,
        confidence=confidence,
    )


def conversation_update_from_interpretation(
    interpreted: InterpretedQuestion,
    *,
    message_id: str | None = None,
) -> ConversationStateUpdate:
    update = ConversationStateUpdate(last_message_id=message_id)
    update.add_player_ids = [
        ref.canonical_id
        for ref in interpreted.player_refs
        if ref.canonical_id and ref.resolution_status in {ResolutionStatus.RESOLVED.value, ResolutionStatus.INFERRED_FROM_CONVERSATION.value}
    ]
    update.add_team_ids = [
        ref.canonical_id
        for ref in interpreted.fantasy_team_refs
        if ref.canonical_id and ref.resolution_status == ResolutionStatus.RESOLVED.value
    ]
    update.add_pick_ids = [
        pick.canonical_pick_id
        for pick in interpreted.pick_refs
        if pick.canonical_pick_id
    ]
    update.add_assets = [
        asdict(asset)
        for asset in interpreted.included_assets
        if asset.canonical_id or asset.label
    ]
    update.add_constraints = dict(interpreted.constraints)
    update.add_ambiguities = [
        ambiguity.ambiguity_type
        for ambiguity in interpreted.ambiguities
        if ambiguity.blocking
    ]
    if interpreted.timeframe.get("label"):
        update.replace_timeframe = str(interpreted.timeframe["label"])
    if interpreted.primary_intent in {
        Intent.TRADE_EVALUATION.value,
        Intent.TRADE_CONSTRUCTION.value,
    }:
        update.replace_current_scenario = {
            "type": interpreted.primary_intent,
            "summary": interpreted.raw_question[:240],
        }
    return update


def build_interpretation_packet(interpreted: InterpretedQuestion | None) -> dict[str, Any]:
    if not interpreted:
        return {}
    packet = interpreted.to_packet()
    packet["raw_question"] = packet["raw_question"][:500]
    return packet


def _load_player_candidates(sb: Any | None, context: AssistantRequestContext) -> list[_Candidate]:
    if not sb:
        return []
    rows: list[dict[str, Any]] = []
    for table_name, id_field in (
        ("player_strategic_profiles", "sleeper_id"),
        ("league_relative_player_values", "sleeper_id"),
        ("contracts", "sleeper_player_id"),
    ):
        try:
            result = (
                sb.table(table_name)
                .select("*")
                .eq("league_id", context.league_id)
                .limit(2000)
                .execute()
            )
            for row in result.data or []:
                rows.append({**row, "_id_field": id_field})
        except Exception:
            continue
    candidates: dict[str, _Candidate] = {}
    for row in rows:
        player_id = _clean_id(row.get(row.get("_id_field")) or row.get("sleeper_id") or row.get("player_id"))
        name = _clean_name(row.get("player_name") or row.get("full_name"))
        if not player_id or not name:
            continue
        aliases = _player_aliases(name)
        candidates[player_id] = _Candidate("player", player_id, name, aliases, context.league_id)
    return list(candidates.values())


def _load_fantasy_team_candidates(sb: Any | None, context: AssistantRequestContext) -> list[_Candidate]:
    candidates = [
        _Candidate(
            "fantasy_team",
            context.league_team_id,
            _clean_name(context.team_name or context.owner_name or "my team"),
            ("my team", "my roster", "my squad"),
            context.league_id,
        )
    ]
    if not sb:
        return candidates
    try:
        result = (
            sb.table("league_teams")
            .select("id,league_id,team_name,owner_name")
            .eq("league_id", context.league_id)
            .limit(200)
            .execute()
        )
    except Exception:
        return candidates
    seen = {context.league_team_id}
    for row in result.data or []:
        team_id = _clean_id(row.get("id"))
        if not team_id or team_id in seen:
            continue
        team_name = _clean_name(row.get("team_name") or row.get("owner_name"))
        owner_name = _clean_name(row.get("owner_name") or row.get("team_name"))
        aliases = tuple(alias for alias in {team_name, owner_name} if alias)
        candidates.append(_Candidate("fantasy_team", team_id, team_name or owner_name, aliases, context.league_id))
        seen.add(team_id)
    return candidates


def _resolve_players(
    raw_question: str,
    normalized: str,
    candidates: list[_Candidate],
) -> tuple[list[EntityRef], list[Ambiguity], list[str]]:
    refs: list[EntityRef] = []
    ambiguities: list[Ambiguity] = []
    matched_ids: set[str] = set()
    for raw_name in _proper_name_spans(raw_question):
        matches = _candidate_matches(raw_name, normalized, candidates, require_phrase=False)
        if not matches:
            continue
        if len(matches) == 1:
            candidate = matches[0]
            if candidate.canonical_id not in matched_ids:
                refs.append(_entity_ref(candidate, raw_name, ResolutionStatus.RESOLVED.value, "high"))
                matched_ids.add(candidate.canonical_id)
        else:
            ids = [candidate.canonical_id for candidate in matches]
            ambiguities.append(Ambiguity("player_reference", raw_name, ids, True, "Multiple scoped players match this name."))
            refs.append(EntityRef("player", raw_name, resolution_status=ResolutionStatus.AMBIGUOUS.value, candidate_ids=ids))

    alias_hits: dict[str, list[_Candidate]] = {}
    resolved_phrases = {
        _normalize(value)
        for ref in refs
        for value in (ref.raw_text, ref.canonical_name)
        if value
    }
    for candidate in candidates:
        if candidate.canonical_id in matched_ids:
            continue
        for alias in candidate.aliases:
            if any(alias != phrase and _phrase_present(alias, phrase) for phrase in resolved_phrases):
                continue
            if _phrase_present(alias, normalized):
                alias_hits.setdefault(alias, []).append(candidate)
    for alias in sorted(alias_hits, key=len, reverse=True):
        matches = [candidate for candidate in alias_hits[alias] if candidate.canonical_id not in matched_ids]
        if not matches:
            continue
        if len(matches) == 1:
            candidate = matches[0]
            refs.append(_entity_ref(candidate, alias, ResolutionStatus.RESOLVED.value, "high"))
            matched_ids.add(candidate.canonical_id)
            continue
        ids = [candidate.canonical_id for candidate in matches]
        ambiguities.append(Ambiguity("player_reference", alias, ids, True, "Multiple scoped players match this name."))
        refs.append(EntityRef("player", alias, resolution_status=ResolutionStatus.AMBIGUOUS.value, candidate_ids=ids))

    unresolved = []
    if _needs_player_entity(normalized) and not refs:
        unresolved.append(raw_question[:120])
    return refs, ambiguities, unresolved


def _resolve_fantasy_teams(
    normalized: str,
    context: AssistantRequestContext,
    candidates: list[_Candidate],
) -> tuple[list[EntityRef], list[EntityRef], list[Ambiguity]]:
    refs: list[EntityRef] = []
    owner_refs: list[EntityRef] = []
    ambiguities: list[Ambiguity] = []
    if any(phrase in normalized for phrase in ("my team", "my roster", "my squad")):
        refs.append(EntityRef(
            "fantasy_team",
            "my team",
            canonical_id=context.league_team_id,
            canonical_name=context.team_name or context.owner_name,
            resolution_status=ResolutionStatus.RESOLVED.value,
            confidence="high",
        ))
    for candidate in candidates:
        for alias in candidate.aliases:
            if alias and _phrase_present(alias, normalized):
                if candidate.canonical_id not in {ref.canonical_id for ref in refs}:
                    refs.append(_entity_ref(candidate, alias, ResolutionStatus.RESOLVED.value, "high"))
                    owner_refs.append(EntityRef(
                        "owner",
                        alias,
                        canonical_id=candidate.canonical_id,
                        canonical_name=candidate.canonical_name,
                        resolution_status=ResolutionStatus.RESOLVED.value,
                        confidence="medium",
                    ))
                break
    if "commissioner" in normalized and not refs:
        ambiguities.append(Ambiguity("fantasy_team_reference", "commissioner", [], False, "Commissioner team requires league data."))
    return refs, owner_refs, ambiguities


def _resolve_nfl_teams(normalized: str, fantasy_team_refs: list[EntityRef]) -> tuple[list[EntityRef], list[Ambiguity]]:
    refs: list[EntityRef] = []
    ambiguities: list[Ambiguity] = []
    fantasy_raw = {_normalize(ref.raw_text) for ref in fantasy_team_refs}
    seen: set[str] = set()
    for abbr, aliases in NFL_TEAMS.items():
        for alias in aliases:
            if _phrase_present(alias, normalized):
                if alias in fantasy_raw:
                    ambiguities.append(Ambiguity(
                        "team_type",
                        alias,
                        [abbr] + [ref.canonical_id or "" for ref in fantasy_team_refs if _normalize(ref.raw_text) == alias],
                        True,
                        "Reference could mean an NFL franchise or a fantasy league team.",
                    ))
                if abbr not in seen:
                    refs.append(EntityRef(
                        "nfl_team",
                        alias,
                        canonical_id=abbr,
                        canonical_name=abbr,
                        resolution_status=ResolutionStatus.RESOLVED.value,
                        confidence="high",
                    ))
                    seen.add(abbr)
                break
    return refs, ambiguities


def _resolve_follow_up(
    normalized: str,
    conversation_state: ConversationState | None,
    existing_player_refs: list[EntityRef],
) -> tuple[bool, str | None, list[EntityRef], list[Ambiguity]]:
    is_follow_up = (
        _has_any(normalized, ("what about", "what if they add", "would you still", "other guy", "add my", "how does that"))
        or re.search(r"\b(that|him|his|he|her)\b", normalized) is not None
    )
    if not is_follow_up:
        return False, None, [], []
    if existing_player_refs:
        return True, "explicit_entity", [], []
    if not conversation_state:
        return True, None, [], [Ambiguity("follow_up_reference", normalized[:80], [], True, "No scoped conversation state is available.")]
    if any(term in normalized.split() for term in ("him", "his", "he", "her")):
        player_ids = list(conversation_state.discussed_player_ids or [])
        if len(player_ids) == 1:
            return True, "player", [EntityRef(
                "player",
                "him",
                canonical_id=player_ids[0],
                resolution_status=ResolutionStatus.INFERRED_FROM_CONVERSATION.value,
                confidence="medium",
            )], []
        if len(player_ids) > 1:
            return True, "player", [], [Ambiguity("current_player_reference", "him", player_ids, True, "Multiple scoped players are active in this conversation.")]
    if "second option" in normalized and conversation_state.prior_recommendation_ref:
        return True, "prior_recommendation", [], []
    if ("that" in normalized or "would you still" in normalized) and conversation_state.current_scenario:
        return True, str(conversation_state.current_scenario.get("type") or "current_scenario"), [], []
    return True, None, [], [Ambiguity("follow_up_reference", normalized[:80], [], True, "Follow-up target is unclear.")]


def _parse_draft_picks(normalized: str, context: AssistantRequestContext) -> list[DraftPickRef]:
    refs = []
    for parsed in parse_pick_references(normalized, current_team_id=context.league_team_id):
        refs.append(DraftPickRef(
            raw_text=parsed.raw_text,
            season=parsed.season,
            round=parsed.round,
            slot=parsed.slot,
            current_owner_team_id=parsed.current_owner_team_id,
            resolution_status=parsed.resolution_status,
        ))
    return _dedupe_picks(refs)


def _extract_constraints(normalized: str, positions: list[str], requested_count: int | None) -> tuple[dict[str, Any], list[Ambiguity]]:
    constraints: dict[str, Any] = {}
    ambiguities: list[Ambiguity] = []
    if positions:
        constraints["positions"] = positions
    if requested_count:
        constraints["requested_count"] = requested_count
    if any(x in normalized for x in ("last season","in 2025","was his salary","was he under contract")):
        constraints["contract_query_type"]="contract_history"
    elif any(x in normalized for x in ("2027","next year","future commitment","run through")) and any(x in normalized for x in ("contract","salary","cost","owe","commitment")):
        constraints["contract_query_type"]="contract_future"
    elif any(x in normalized for x in ("free agent","is he free","available to sign")):
        constraints["contract_query_type"]="contract_free_agent_status"
    elif any(x in normalized for x in ("on my roster but expired","off my roster","why am i paying","roster but")):
        constraints["contract_query_type"]="contract_roster_mismatch"
    elif any(x in normalized for x in ("expire","expiration")):
        constraints["contract_query_type"]="contract_expiration"
    elif any(x in normalized for x in ("contract","deal","salary","making","years left")):
        constraints["contract_query_type"]="contract_current"
    if "under 25" in normalized or "younger than 25" in normalized:
        constraints["max_age"] = 24
    elif "young" in normalized or "younger" in normalized:
        constraints["prefers_younger_players"] = True
        ambiguities.append(Ambiguity("age_constraint", "young", [], False, "Young is a broad age preference."))
    salary_match = re.search(r"(?:under|below|max|maximum|less than)\s+\$?(\d+)", normalized)
    if salary_match and ("salary" in normalized or "cap" in normalized or "contract" in normalized):
        constraints["max_salary"] = int(salary_match.group(1))
    year_match = re.search(r"(?:contract|deal).{0,18}(?:under|max|maximum|less than)\s+(\d+)\s+years?", normalized)
    if year_match:
        constraints["max_contract_years"] = int(year_match.group(1))
    years_left_match = re.search(r"\b(one|two|three|four|five|[1-5])\s+years?\s+(?:left|remaining)\b", normalized)
    if years_left_match:
        value = COUNT_WORDS.get(years_left_match.group(1), years_left_match.group(1))
        constraints["contract_years_left"] = int(value)
    if "without moving my first" in normalized or "do not trade a first" in normalized or "don't trade a first" in normalized:
        constraints["do_not_trade_first_round_pick"] = True
    if "preserve cap flexibility" in normalized or "keep cap flexibility" in normalized:
        constraints["preserve_cap_flexibility"] = True
    if "only free agents" in normalized or "free agents only" in normalized:
        constraints["only_free_agents"] = True
    if "only players from other rosters" in normalized or "other rosters" in normalized:
        constraints["only_other_rosters"] = True
    if "avoid injured" in normalized or "no injured" in normalized:
        constraints["avoid_injured_players"] = True
    if "cheap" in normalized and "max_salary" not in constraints:
        constraints["prefers_cheap_assets"] = True
        ambiguities.append(Ambiguity("salary_constraint", "cheap", [], False, "Cheap is not a precise salary limit."))
    return constraints, ambiguities


def _extract_assets(
    player_refs: list[EntityRef],
    pick_refs: list[DraftPickRef],
    constraints: dict[str, Any],
    normalized: str,
) -> tuple[list[AssetRef], list[AssetRef]]:
    included: list[AssetRef] = []
    excluded: list[AssetRef] = []
    for ref in player_refs:
        asset = AssetRef("player", ref.canonical_id, ref.canonical_name or ref.raw_text)
        if _near_exclusion(ref.raw_text, normalized):
            excluded.append(asset)
        else:
            included.append(asset)
    for pick in pick_refs:
        label = _pick_label(pick)
        asset = AssetRef("draft_pick", pick.canonical_pick_id, label, pick.current_owner_team_id, season=pick.season)
        if "without" in pick.raw_text or "do_not_trade_first_round_pick" in constraints:
            excluded.append(asset)
        else:
            included.append(asset)
    return included, excluded


def _classify_intents(
    normalized: str,
    *,
    player_refs: list[EntityRef],
    team_refs: list[EntityRef],
    pick_refs: list[DraftPickRef],
    positions: list[str],
    is_follow_up: bool,
) -> tuple[Intent, list[Intent]]:
    secondary: list[Intent] = []
    if _has_any(normalized, ("submit this trade", "set my lineup", "release player", "release this player")):
        return Intent.UNSUPPORTED, []
    if _has_any(normalized, ("another owner's private", "another owners private", "ignore my current league", "pretend i own", "another owner's team data")):
        return Intent.UNSUPPORTED, []
    if is_scenario_question(normalized):
        return Intent.SCENARIO_SIMULATION, []
    if is_football_intelligence_question(normalized):
        return Intent.DATA_LOOKUP, _dedupe_intents(secondary)
    if any(term in normalized for term in ("salary cap", "cap space", "clear salary", "clear cap", "cap room", "afford")):
        secondary.append(Intent.SALARY_CAP_QUESTION)
    if any(term in normalized for term in ("contract", "deal")) or (
        "salary" in normalized and not any(term in normalized for term in ("clear salary", "salary cap"))
    ) or (
        "years" in normalized and _has_any(normalized, ("left", "remaining"))
    ):
        secondary.append(Intent.CONTRACT_QUESTION)
    if secondary and secondary[0] == Intent.CONTRACT_QUESTION and _has_any(normalized, ("years", "contract", "deal", "salary")):
        return Intent.CONTRACT_QUESTION, _dedupe_intents(secondary[1:])
    if is_team_identity_question(normalized) or is_roster_list_question(normalized) or is_league_owner_intelligence_question(normalized) or is_football_intelligence_question(normalized) or _has_any(normalized, ("which team am i managing", "what league am i in", "who is on ir", "which players are on ir", "what picks do i own", "who is on the roster")):
        return Intent.DATA_LOOKUP, _dedupe_intents(secondary)
    if positions and _has_any(normalized, ("who are my", "which players are my", "show me my", "list my")):
        return Intent.ROSTER_EVALUATION, _dedupe_intents(secondary)
    if _has_any(normalized, ("taxi", "legal", "allowed", "rule", "deadline")):
        return Intent.RULES_QUESTION, _dedupe_intents(secondary)
    if is_follow_up and not any(term in normalized for term in ("trade", "draft", "contract", "cap", "lineup", "start", "sit", "taxi", "legal", "rule")):
        return Intent.FOLLOW_UP, _dedupe_intents(secondary)
    if _has_any(normalized, ("find me", "find ", "who can i target", "could i acquire", "can i acquire", "trade targets", "targets")):
        return Intent.TRADE_DISCOVERY, _dedupe_intents(secondary)
    if _has_any(normalized, ("what should i offer", "build me a trade", "build a trade", "trade package", "package for")):
        return Intent.TRADE_CONSTRUCTION, _dedupe_intents(secondary)
    if _has_any(normalized, ("should i trade", "accept this", "accept the trade", "deal fair", "trade ")) and (
        " for " in normalized or player_refs or pick_refs
    ):
        return Intent.TRADE_EVALUATION, _dedupe_intents(secondary)
    if _has_any(normalized, ("who should i take", "who should we target in the draft", "who should i draft", "draft at")):
        return Intent.DRAFT_RECOMMENDATION, _dedupe_intents(secondary)
    if pick_refs and _has_any(normalized, ("worth", "value", "evaluate", "trade my", "use my")):
        return Intent.DRAFT_PICK_EVALUATION, _dedupe_intents(secondary)
    if _has_any(normalized, ("free agent", "free-agent", "waiver", "pick up", "pickup", "available players", "add ")) and not "trade" in normalized:
        return Intent.FREE_AGENT_RECOMMENDATION, _dedupe_intents(secondary)
    if _has_any(normalized, ("lineup", "start", "sit")):
        return Intent.LINEUP_QUESTION, _dedupe_intents(secondary)
    if _has_any(normalized, ("cut", "drop", "release", "promote", "roster move", "move on from")):
        return Intent.ROSTER_MOVE_QUESTION, _dedupe_intents(secondary)
    if _has_any(normalized, ("rebuild", "three-year", "3 year", "long term", "next three years", "win now", "plan")):
        return Intent.LONG_TERM_PLANNING, _dedupe_intents(secondary)
    if _has_any(normalized, ("compare teams", "compare my team", "team better", "which team")) and len(team_refs) >= 2:
        return Intent.TEAM_COMPARISON, _dedupe_intents(secondary)
    if _has_any(normalized, ("league", "contenders", "rebuilders", "league-wide", "strongest teams", "rankings")):
        return Intent.LEAGUE_ANALYSIS, _dedupe_intents(secondary)
    if _has_any(normalized, ("who has", "show me", "list", "where is")):
        return Intent.DATA_LOOKUP, _dedupe_intents(secondary)
    if len(player_refs) >= 2 or _has_any(normalized, (" vs ", "versus", "compare players", "rather have", " or ")):
        return Intent.PLAYER_COMPARISON, _dedupe_intents(secondary)
    if _has_any(normalized, ("roster", "my team", "strengths", "weaknesses", "team look", "best player", "best players", "contender")) and not player_refs:
        return Intent.ROSTER_EVALUATION, _dedupe_intents(secondary)
    if secondary:
        return secondary[0], _dedupe_intents(secondary[1:])
    if player_refs or _has_any(normalized, ("what do you think", "thoughts on", "outlook", "buy", "sell", "hold", "profile")):
        return Intent.PLAYER_EVALUATION, _dedupe_intents(secondary)
    if _has_any(normalized, ("hello", "thanks", "thank you", "hey")):
        return Intent.GENERAL_CONVERSATION, []
    if _has_any(normalized, ("baseball", "basketball", "weather", "stock market")):
        return Intent.UNSUPPORTED, []
    return Intent.GENERAL_CONVERSATION, []


def _extract_positions(normalized: str) -> list[str]:
    found: list[str] = []
    for alias, value in POSITION_ALIASES.items():
        if _phrase_present(alias, normalized) and value not in found:
            found.append(value)
    return found


def is_league_owner_intelligence_question(normalized_question: str) -> bool:
    normalized = _normalize(normalized_question)
    return _has_any(normalized, LEAGUE_OWNER_INTELLIGENCE_PHRASES)


def is_football_intelligence_question(normalized_question: str) -> bool:
    normalized = _normalize(normalized_question)
    if _has_any(normalized, FOOTBALL_INTELLIGENCE_PHRASES):
        return True
    if re.search(r"\bwho (?:is|are) my (?:best|top|strongest) (?:player|players)\b", normalized):
        return True
    if re.search(r"\brank (?:my|the|all) (?:entire |full )?(?:roster|players|team)\b", normalized):
        return True
    if re.search(r"\b(?:show me|list|give me) (?:my )?(?:top|best|strongest|best to worst|best-to-worst).*(?:players|roster)\b", normalized):
        return True
    if "order my roster by value" in normalized:
        return True
    if re.search(r"\bwhat (?:is|are) my (?:biggest|top|clearest) (?:weakness|weaknesses|strength|strengths)\b", normalized):
        return True
    if "what does" in normalized and "roster" in normalized and "mean" in normalized:
        return True
    if "how does my team look" in normalized or "how does this roster look" in normalized:
        return True
    return False


def _extract_seasons_and_timeframe(normalized: str, context: AssistantRequestContext) -> tuple[list[int], dict[str, Any]]:
    seasons = [int(value) for value in re.findall(r"\b(20\d{2})\b", normalized)]
    timeframe: dict[str, Any] = {}
    if "this season" in normalized or "this year" in normalized:
        timeframe["label"] = "current_season"
        seasons.append(context.current_season)
    if "next season" in normalized or "next year" in normalized:
        timeframe["label"] = "next_season"
        seasons.append(context.current_season + 1)
    if "next three years" in normalized or "three-year" in normalized or "3 year" in normalized:
        timeframe["label"] = "next_three_years"
        timeframe["horizon_years"] = 3
    if "long term" in normalized or "future" in normalized:
        timeframe.setdefault("label", "future")
    if "win now" in normalized:
        timeframe["label"] = "win_now"
    if "rookie draft" in normalized:
        timeframe["event"] = "rookie_draft"
    if "deadline" in normalized:
        timeframe["event"] = "trade_deadline"
    return sorted(set(seasons)), timeframe


def _extract_requested_count(normalized: str) -> int | None:
    digit_match = re.search(r"\b(\d{1,2})\s+(?:players?|targets?|options?|receivers?|running backs?|quarterbacks?|tight ends?)\b", normalized)
    if digit_match:
        return int(digit_match.group(1))
    for word, value in COUNT_WORDS.items():
        if re.search(rf"\b{word}\s+(?:players?|targets?|options?|receivers?|running backs?|quarterbacks?|tight ends?)\b", normalized):
            return value
    return None


def _intent_blocking_ambiguities(
    intent: Intent,
    player_refs: list[EntityRef],
    pick_refs: list[DraftPickRef],
    normalized: str,
) -> list[Ambiguity]:
    needs_player = intent in {Intent.PLAYER_EVALUATION, Intent.PLAYER_COMPARISON, Intent.TRADE_EVALUATION}
    if needs_player and _needs_player_entity(normalized) and not player_refs and not pick_refs:
        return [Ambiguity("missing_player_reference", normalized[:80], [], True, "This intent needs a player or asset reference.")]
    return []


def _confidence(intent: Intent, ambiguities: list[Ambiguity], unresolved: list[str]) -> str:
    if intent in {Intent.UNSUPPORTED, Intent.GENERAL_CONVERSATION} and unresolved:
        return "low"
    if any(ambiguity.blocking for ambiguity in ambiguities):
        return "low"
    if ambiguities or unresolved:
        return "medium"
    return "high"


def _candidate_matches(raw_text: str, normalized_question: str, candidates: list[_Candidate], *, require_phrase: bool) -> list[_Candidate]:
    raw_norm = _normalize(raw_text)
    matches = []
    for candidate in candidates:
        aliases = set(candidate.aliases)
        aliases.add(_normalize(candidate.canonical_name))
        if any(alias == raw_norm for alias in aliases if alias):
            matches.append(candidate)
            continue
        if not require_phrase and any(_minor_match(raw_norm, alias) for alias in aliases if alias):
            matches.append(candidate)
            continue
    return matches


def _proper_name_spans(raw_question: str) -> list[str]:
    spans = re.findall(r"\b[A-Z][a-zA-Z'.-]+(?:\s+[A-Z][a-zA-Z'.-]+){0,2}\b", raw_question or "")
    question_words = {"should", "what", "who", "which", "can", "find", "build", "how", "why", "when", "where"}
    return [span.strip(" ?.,!") for span in spans if span.strip(" ?.,!").lower() not in question_words]


def _player_aliases(name: str) -> tuple[str, ...]:
    norm = _normalize(name)
    parts = norm.split()
    aliases = {norm}
    if len(parts) >= 2:
        aliases.add(parts[-1])
        aliases.add(f"{parts[0][0]} {parts[-1]}")
        aliases.add(f"{parts[0][0]}. {parts[-1]}")
    return tuple(sorted(aliases))


def _entity_ref(candidate: _Candidate, raw_text: str, status: str, confidence: str) -> EntityRef:
    return EntityRef(
        candidate.entity_type,
        raw_text,
        canonical_id=candidate.canonical_id,
        canonical_name=candidate.canonical_name,
        resolution_status=status,
        confidence=confidence,
        candidate_ids=[candidate.canonical_id],
    )


def _merge_entity_refs(base: list[EntityRef], additions: list[EntityRef]) -> list[EntityRef]:
    out = list(base)
    seen = {(ref.entity_type, ref.canonical_id, ref.raw_text) for ref in out}
    for ref in additions:
        key = (ref.entity_type, ref.canonical_id, ref.raw_text)
        if key not in seen:
            out.append(ref)
            seen.add(key)
    return out


def _dedupe_picks(picks: list[DraftPickRef]) -> list[DraftPickRef]:
    out: list[DraftPickRef] = []
    seen: set[tuple] = set()
    for pick in picks:
        key = (pick.raw_text, pick.season, pick.round, pick.slot, pick.current_owner_team_id)
        if key not in seen:
            out.append(pick)
            seen.add(key)
    return out


def _dedupe_intents(intents: list[Intent]) -> list[Intent]:
    out: list[Intent] = []
    for intent in intents:
        if intent not in out:
            out.append(intent)
    return out


def _pick_label(pick: DraftPickRef) -> str:
    if pick.slot:
        return f"{pick.round}.{pick.slot:02d}"
    label = f"round {pick.round}" if pick.round else "draft pick"
    if pick.season:
        return f"{pick.season} {label}"
    return label


def _needs_player_entity(normalized: str) -> bool:
    return any(term in normalized for term in ("trade", "compare", "versus", " vs ", "thoughts on", "what do you think", "contract", "hold", "sell", "buy"))


def _near_exclusion(raw_text: str, normalized: str) -> bool:
    raw = _normalize(raw_text)
    if not raw:
        return False
    return any(phrase in normalized for phrase in (f"without moving {raw}", f"do not move {raw}", f"don't move {raw}", f"exclude {raw}"))


def _minor_match(left: str, right: str) -> bool:
    if not left or not right or abs(len(left) - len(right)) > 2:
        return False
    return _levenshtein(left, right) <= 2


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for i, lc in enumerate(left, start=1):
        current = [i]
        for j, rc in enumerate(right, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (lc != rc),
            ))
        previous = current
    return previous[-1]


def _has_any(normalized: str, markers: tuple[str, ...]) -> bool:
    return any(marker in normalized for marker in markers)


def is_team_identity_question(text: Any) -> bool:
    normalized = _normalize(text)
    if any(_phrase_present(phrase, normalized) for phrase in TEAM_IDENTITY_LOOKUP_PHRASES):
        return True
    return bool(
        re.search(r"(?<![a-z0-9])what team (?:do )?i manage(?![a-z0-9])", normalized)
        or re.search(r"(?<![a-z0-9])what team (?:am )?i managing(?![a-z0-9])", normalized)
    )


def is_roster_list_question(text: Any) -> bool:
    normalized = _normalize(text)
    return any(_phrase_present(phrase, normalized) for phrase in ROSTER_LIST_LOOKUP_PHRASES)


def _phrase_present(phrase: str, normalized: str) -> bool:
    phrase = _normalize(phrase)
    if not phrase:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", normalized) is not None


def _normalize(text: Any) -> str:
    text = str(text or "").lower().replace("&", " and ")
    text = re.sub(r"[’']", "", text)
    text = re.sub(r"[^a-z0-9.$\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _clean_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    return text
