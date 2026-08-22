from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from gm_assistant.repositories import ContractRepository
from gm_assistant.repositories.common import clean_id, clean_text, rows
from gm_assistant.repositories.roster import RosterRepository
from gm_assistant.request_context import AssistantRequestContext


@dataclass(frozen=True)
class PlayerEvaluation:
    league_id: str
    league_team_id: str
    player_id: str
    player_name: str
    position: str | None
    current_contribution_score: float | None
    future_outlook_score: float | None
    league_relative_score: float | None
    contract_efficiency_score: float | None
    contender_value_score: float | None
    rebuild_value_score: float | None
    risk_score: float | None
    neutral_overall_value: float | None
    confidence: float
    status: str = "evaluated"
    missing_inputs: list[str] = field(default_factory=list)
    explanation: str = ""
    fact_refs: list[str] = field(default_factory=list)
    source_rows_used: list[str] = field(default_factory=list)
    component_sources: dict[str, list[str]] = field(default_factory=dict)
    effective_weights: dict[str, float] = field(default_factory=dict)
    rookie_prospect_pathway_used: bool = False
    positional_adjustment_applied: bool = False
    positional_adjustment_source: str | None = None

    def to_evidence_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["fact_id"] = f"player_eval.{self.league_id}.{self.league_team_id}.{self.player_id}.derived.neutral_overall_value"
        return row


class PlayerEvaluationService:
    def __init__(self, sb: Any):
        self.sb = sb

    def evaluate_roster(
        self,
        context: AssistantRequestContext,
        *,
        league_team_id: str | None = None,
    ) -> list[PlayerEvaluation]:
        team_id = league_team_id or context.league_team_id
        roster_result = RosterRepository(self.sb).get_team_roster(context, league_team_id=team_id)
        roster_rows = [row for row in roster_result.rows if not _is_released(row)]
        roster_ids = [_player_id(row) for row in roster_rows]
        roster_ids = [player_id for player_id in roster_ids if player_id]

        profile_rows = rows(self.sb.table("player_strategic_profiles").select("*").eq("league_id", context.league_id))
        value_rows = rows(self.sb.table("league_relative_player_values").select("*").eq("league_id", context.league_id))
        contract_rows = ContractRepository(self.sb).get_contracts(
            context,
            league_team_ids=[team_id],
            player_ids=roster_ids,
        ).rows
        universe_rows = _safe_player_rows(self.sb, "player_universe", roster_ids)
        intelligence_rows = _safe_player_rows(self.sb, "player_intelligence", roster_ids)
        contract_efficiency_rows = _safe_player_rows(self.sb, "player_contract_efficiency", roster_ids)

        profiles = _by_player_id(row for row in profile_rows if clean_id(row.get("league_team_id")) == team_id)
        values = _by_player_id(row for row in value_rows if clean_id(row.get("league_team_id")) == team_id)
        contracts = _by_player_id(contract_rows)
        universe = _by_player_id(universe_rows)
        intelligence = _by_player_id(intelligence_rows)
        contract_efficiency = _by_player_id(contract_efficiency_rows)

        scoring_context = _build_scoring_context(roster_rows, profiles, universe, intelligence, contract_efficiency)

        evaluations = [
            self._evaluate_row(
                context=context,
                league_team_id=team_id,
                roster_row=roster_row,
                profile=profiles.get(_player_id(roster_row) or ""),
                relative_value=values.get(_player_id(roster_row) or ""),
                contract=contracts.get(_player_id(roster_row) or ""),
                universe=universe.get(_player_id(roster_row) or ""),
                intelligence=intelligence.get(_player_id(roster_row) or ""),
                contract_efficiency_row=contract_efficiency.get(_player_id(roster_row) or ""),
                scoring_context=scoring_context,
            )
            for roster_row in roster_rows
            if _player_id(roster_row)
        ]
        return sorted(
            evaluations,
            key=lambda item: (
                item.neutral_overall_value is None,
                -(item.neutral_overall_value or -1.0),
                -item.confidence,
                item.player_name.lower(),
                item.player_id,
            ),
        )

    def _evaluate_row(
        self,
        *,
        context: AssistantRequestContext,
        league_team_id: str,
        roster_row: dict[str, Any],
        profile: dict[str, Any] | None,
        relative_value: dict[str, Any] | None,
        contract: dict[str, Any] | None,
        universe: dict[str, Any] | None,
        intelligence: dict[str, Any] | None,
        contract_efficiency_row: dict[str, Any] | None,
        scoring_context: dict[str, Any],
    ) -> PlayerEvaluation:
        player_id = _player_id(roster_row) or ""
        name = (
            clean_text(roster_row.get("player_name") or roster_row.get("name"))
            or clean_text((profile or {}).get("player_name"))
            or clean_text((contract or {}).get("player_name"))
            or player_id
        )
        position = (
            clean_text(roster_row.get("position") or roster_row.get("player_position") or roster_row.get("pos"))
            or clean_text((profile or {}).get("position") or (profile or {}).get("pos"))
            or clean_text((contract or {}).get("player_position") or (contract or {}).get("position"))
            or clean_text((universe or {}).get("pos"))
            or clean_text((intelligence or {}).get("pos") or (intelligence or {}).get("position"))
        )

        missing: list[str] = []
        component_sources: dict[str, list[str]] = {}
        player_sources = _source_rows_used(profile, relative_value, contract, universe, intelligence, contract_efficiency_row)
        rookie_pathway = _is_rookie_or_prospect(roster_row, universe, intelligence)

        current, component_sources["current_contribution"] = _current_contribution(
            player_id, profile, universe, intelligence, contract_efficiency_row, scoring_context
        )
        future, component_sources["future_outlook"] = _future_outlook(
            profile, relative_value, universe, intelligence, rookie_pathway
        )
        relative, component_sources["league_relative_value"] = _league_relative_value(relative_value)
        if current is None:
            missing.append("current_contribution_score")
        if future is None:
            missing.append("future_outlook_score")
        if relative is None:
            missing.append("league_relative_score")
        if rookie_pathway and not any("rookie" in source or "draft" in source or "prospect" in source for source in component_sources["future_outlook"]):
            missing.append("prospect_value")

        contract_efficiency = _contract_efficiency(contract, contract_efficiency_row, universe, current, future, relative)
        component_sources["contract_efficiency"] = _contract_efficiency_sources(contract_efficiency_row, contract)
        if contract_efficiency is None:
            missing.append("contract_efficiency_score")

        contender, _contender_weights = _weighted_available_with_weights(
            [(current, 0.65), (relative, 0.25), (contract_efficiency, 0.10)]
        )
        rebuild, _rebuild_weights = _weighted_available_with_weights(
            [(future, 0.60), (relative, 0.25), (contract_efficiency, 0.15)]
        )
        risk = _risk_score(profile, contract, intelligence, universe)

        neutral, effective_weights = _weighted_available_with_weights(
            [(current, 0.25), (future, 0.30), (relative, 0.30), (contract_efficiency, 0.15)],
            labels=["current_contribution", "future_outlook", "league_relative_value", "contract_efficiency"],
        )
        available_football = sum(1 for value in (current, future, relative) if value is not None)
        status = "evaluated"
        if available_football < 2:
            neutral = None
            status = "insufficient_data"
            if "insufficient_distinct_football_inputs" not in missing:
                missing.append("insufficient_distinct_football_inputs")
        if neutral is not None and risk is not None:
            neutral = max(0.0, min(100.0, neutral - (risk * 0.08)))

        available_core = 4 - sum(1 for value in (current, future, relative, contract_efficiency) if value is None)
        confidence = max(0.2, min(1.0, 0.25 + available_core * 0.13))
        if profile:
            confidence += 0.07
        if relative_value:
            confidence += 0.07
        if contract:
            confidence += 0.06
        if universe:
            confidence += 0.08
        if intelligence:
            confidence += 0.07
        if rookie_pathway and "prospect_value" in missing:
            confidence -= 0.10
        if status == "insufficient_data":
            confidence = min(confidence, 0.45)
        confidence = round(min(confidence, 1.0), 3)

        fact_refs = _fact_refs(context.league_id, league_team_id, player_id, current, future, relative, contract_efficiency)
        explanation = _explanation(name, current, future, relative, contract_efficiency, risk, missing, status)

        return PlayerEvaluation(
            league_id=context.league_id,
            league_team_id=league_team_id,
            player_id=player_id,
            player_name=name,
            position=position,
            current_contribution_score=current,
            future_outlook_score=future,
            league_relative_score=relative,
            contract_efficiency_score=contract_efficiency,
            contender_value_score=contender,
            rebuild_value_score=rebuild,
            risk_score=risk,
            neutral_overall_value=round(neutral, 3) if neutral is not None else None,
            confidence=confidence,
            status=status,
            missing_inputs=missing,
            explanation=explanation,
            fact_refs=fact_refs,
            source_rows_used=player_sources,
            component_sources=component_sources,
            effective_weights={key: round(value, 3) for key, value in effective_weights.items()},
            rookie_prospect_pathway_used=rookie_pathway,
            positional_adjustment_applied=False,
            positional_adjustment_source="league_relative_player_values already carries league/position context; no extra position premium applied.",
        )


def _contract_efficiency(
    contract: dict[str, Any] | None,
    contract_efficiency_row: dict[str, Any] | None,
    universe: dict[str, Any] | None,
    current: float | None,
    future: float | None,
    relative: float | None,
) -> float | None:
    stored = _score((contract_efficiency_row or {}).get("contract_efficiency_score") or (universe or {}).get("contract_efficiency_score"))
    if stored is not None:
        return stored
    if not contract:
        return None
    salary = _float(contract.get("salary") or contract.get("contract_salary"))
    years = _float(contract.get("contract_years_left") or contract.get("years_remaining"))
    value = _weighted_available([(current, 0.35), (future, 0.25), (relative, 0.40)])
    if salary is None or years is None or value is None:
        return None
    salary_factor = max(0.35, min(1.20, 1.10 - (salary / 100.0)))
    years_factor = max(0.85, min(1.08, 0.95 + min(years, 4.0) * 0.03))
    return round(max(0.0, min(100.0, value * salary_factor * years_factor)), 3)


def _risk_score(
    profile: dict[str, Any] | None,
    contract: dict[str, Any] | None,
    intelligence: dict[str, Any] | None,
    universe: dict[str, Any] | None,
) -> float | None:
    risk = clean_text((profile or {}).get("volatility_label") or (profile or {}).get("risk"))
    flag = clean_text((profile or {}).get("contract_flag") or (contract or {}).get("contract_status"))
    score = 20.0
    text = f"{risk} {flag}".lower()
    if any(term in text for term in ("high", "volatile", "problem", "expensive", "risky")):
        score += 25.0
    if any(term in text for term in ("low", "stable", "efficient", "value")):
        score -= 10.0
    durability = _score((intelligence or {}).get("durability_score"))
    if durability is not None:
        score = (score * 0.60) + ((100.0 - durability) * 0.40)
    nfl_score = _score((universe or {}).get("nfl_intelligence_score"))
    if nfl_score is not None and nfl_score <= 25:
        score += 10.0
    return round(max(0.0, min(100.0, score)), 3)


def _weighted_available(values: list[tuple[float | None, float]]) -> float | None:
    score, _weights = _weighted_available_with_weights(values)
    return score


def _weighted_available_with_weights(
    values: list[tuple[float | None, float]],
    labels: list[str] | None = None,
) -> tuple[float | None, dict[str, float]]:
    available = [(value, weight) for value, weight in values if value is not None]
    if not available:
        return None, {}
    total_weight = sum(weight for _value, weight in available)
    score = round(sum(value * weight for value, weight in available) / total_weight, 3)
    if not labels:
        return score, {}
    weights: dict[str, float] = {}
    for (value, weight), label in zip(values, labels):
        if value is not None:
            weights[label] = weight / total_weight
    return score, weights


def _score(value: Any) -> float | None:
    number = _float(value)
    if number is None:
        return None
    if 0 <= number <= 1:
        number *= 100
    return round(max(0.0, min(100.0, number)), 3)


def _current_contribution(
    player_id: str,
    profile: dict[str, Any] | None,
    universe: dict[str, Any] | None,
    intelligence: dict[str, Any] | None,
    contract_efficiency_row: dict[str, Any] | None,
    scoring_context: dict[str, Any],
) -> tuple[float | None, list[str]]:
    sources: list[str] = []
    values: list[tuple[float | None, float]] = []

    recent = _positive_score((intelligence or {}).get("recent_production_score"))
    if recent is not None:
        values.append((recent, 0.45))
        sources.append("player_intelligence.recent_production_score")

    expected = _normalized_current_score(player_id, scoring_context)
    if expected is not None:
        values.append((expected, 0.35))
        sources.append(scoring_context.get("current_projection_source", "current_projection.position_normalized"))

    win_now_percentile = _score((profile or {}).get("win_now_percentile") or (contract_efficiency_row or {}).get("win_now_asset_score"))
    if win_now_percentile is not None:
        values.append((win_now_percentile, 0.20))
        sources.append("profile_or_contract.win_now_context")

    score, _weights = _weighted_available_with_weights(values)
    return score, sources


def _future_outlook(
    profile: dict[str, Any] | None,
    relative_value: dict[str, Any] | None,
    universe: dict[str, Any] | None,
    intelligence: dict[str, Any] | None,
    rookie_pathway: bool,
) -> tuple[float | None, list[str]]:
    sources: list[str] = []
    weighted: list[tuple[float | None, float]] = []

    if rookie_pathway:
        candidates = [
            (_positive_score((universe or {}).get("rookie_asset_score")), 0.30, "player_universe.rookie_asset_score"),
            (_positive_score((universe or {}).get("future_projection_score")), 0.25, "player_universe.future_projection_score"),
            (_positive_score((universe or {}).get("market_consensus_score")), 0.20, "player_universe.market_consensus_score"),
            (_score((profile or {}).get("asset_score") or (relative_value or {}).get("asset_score")), 0.15, "strategic_or_relative.asset_score"),
            (_positive_score((intelligence or {}).get("trade_value_score")), 0.10, "player_intelligence.trade_value_score"),
        ]
    else:
        candidates = [
            (_positive_score((universe or {}).get("dynasty_asset_score")), 0.25, "player_universe.dynasty_asset_score"),
            (_positive_score((universe or {}).get("future_projection_score")), 0.20, "player_universe.future_projection_score"),
            (_positive_score((universe or {}).get("market_consensus_score")), 0.20, "player_universe.market_consensus_score"),
            (_score((profile or {}).get("asset_score") or (relative_value or {}).get("asset_score")), 0.20, "strategic_or_relative.asset_score"),
            (_positive_score((intelligence or {}).get("trade_value_score")), 0.10, "player_intelligence.trade_value_score"),
            (_positive_score((intelligence or {}).get("rank_score")), 0.05, "player_intelligence.rank_score"),
        ]

    for value, weight, source in candidates:
        if value is not None:
            weighted.append((value, weight))
            sources.append(source)
    score, _weights = _weighted_available_with_weights(weighted)
    return score, sources


def _league_relative_value(relative_value: dict[str, Any] | None) -> tuple[float | None, list[str]]:
    sources: list[str] = []
    weighted: list[tuple[float | None, float]] = []
    candidates = [
        (_score((relative_value or {}).get("overall_value_score") or (relative_value or {}).get("value_score")), 0.60, "league_relative_player_values.overall_value_score"),
        (_score((relative_value or {}).get("overall_percentile")), 0.25, "league_relative_player_values.overall_percentile"),
        (_score((relative_value or {}).get("position_overall_percentile") or (relative_value or {}).get("position_percentile")), 0.15, "league_relative_player_values.position_overall_percentile"),
    ]
    for value, weight, source in candidates:
        if value is not None:
            weighted.append((value, weight))
            sources.append(source)
    score, _weights = _weighted_available_with_weights(weighted)
    return score, sources


def _build_scoring_context(
    roster_rows: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    universe: dict[str, dict[str, Any]],
    intelligence: dict[str, dict[str, Any]],
    contract_efficiency: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    projection_by_player: dict[str, float] = {}
    position_by_player: dict[str, str] = {}
    for row in roster_rows:
        player_id = _player_id(row)
        if not player_id:
            continue
        position = clean_text(row.get("position") or row.get("player_position") or row.get("pos"))
        position_by_player[player_id] = position or "UNK"
        value = (
            _positive_float((universe.get(player_id) or {}).get("expected_ppg"))
            or _positive_float((contract_efficiency.get(player_id) or {}).get("expected_ppg"))
            or _positive_float((profiles.get(player_id) or {}).get("median_projection"))
            or _positive_float((intelligence.get(player_id) or {}).get("recent_avg_ppg_ppr"))
        )
        if value is not None:
            projection_by_player[player_id] = value
    by_position: dict[str, list[float]] = {}
    for player_id, value in projection_by_player.items():
        by_position.setdefault(position_by_player.get(player_id) or "UNK", []).append(value)
    return {
        "projection_by_player": projection_by_player,
        "position_by_player": position_by_player,
        "projection_by_position": by_position,
        "current_projection_source": "player_universe.expected_ppg.position_normalized",
    }


def _normalized_current_score(player_id: str, scoring_context: dict[str, Any]) -> float | None:
    raw = (scoring_context.get("projection_by_player") or {}).get(player_id)
    if raw is None:
        return None
    position = (scoring_context.get("position_by_player") or {}).get(player_id) or "UNK"
    peers = list((scoring_context.get("projection_by_position") or {}).get(position) or [])
    if len(peers) < 2 or min(peers) == max(peers):
        all_values = list((scoring_context.get("projection_by_player") or {}).values())
        peers = all_values if len(all_values) >= 2 else peers
    if not peers or min(peers) == max(peers):
        return _score(raw)
    return round(((raw - min(peers)) / (max(peers) - min(peers))) * 100.0, 3)


def _positive_score(value: Any) -> float | None:
    score = _score(value)
    if score is None or score <= 0:
        return None
    return score


def _positive_float(value: Any) -> float | None:
    number = _float(value)
    if number is None or number <= 0:
        return None
    return number


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _by_player_id(row_iter: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in row_iter:
        player_id = _player_id(row)
        if player_id:
            out[player_id] = dict(row)
    return out


def _safe_player_rows(sb: Any, table_name: str, player_ids: list[str]) -> list[dict[str, Any]]:
    if not player_ids:
        return []
    wanted = set(player_ids)
    try:
        query = sb.table(table_name).select("*").in_("sleeper_id", list(wanted))
        return rows(query)
    except Exception:
        try:
            data = rows(sb.table(table_name).select("*"))
        except Exception:
            return []
        return [row for row in data if _player_id(row) in wanted]


def _player_id(row: dict[str, Any]) -> str | None:
    return clean_id(row.get("sleeper_id") or row.get("sleeper_player_id") or row.get("player_id"))


def _is_released(row: dict[str, Any]) -> bool:
    status = clean_text(row.get("status") or row.get("roster_status") or row.get("contract_status"))
    return bool(status and status.lower() in {"released", "cut", "dropped", "waived"})


def _is_rookie_or_prospect(
    roster_row: dict[str, Any],
    universe: dict[str, Any] | None,
    intelligence: dict[str, Any] | None,
) -> bool:
    if roster_row.get("is_rookie") is True or str(roster_row.get("is_rookie")).strip().lower() == "true":
        return True
    for value in (roster_row.get("experience"), (universe or {}).get("years_exp"), (intelligence or {}).get("seasons_played")):
        number = _float(value)
        if number is not None and number <= 1:
            return True
    return False


def _source_rows_used(
    profile: dict[str, Any] | None,
    relative_value: dict[str, Any] | None,
    contract: dict[str, Any] | None,
    universe: dict[str, Any] | None,
    intelligence: dict[str, Any] | None,
    contract_efficiency_row: dict[str, Any] | None,
) -> list[str]:
    sources: list[str] = []
    if profile:
        sources.append("player_strategic_profiles")
    if relative_value:
        sources.append("league_relative_player_values")
    if contract:
        sources.append("contracts")
    if universe:
        sources.append("player_universe")
    if intelligence:
        sources.append("player_intelligence")
    if contract_efficiency_row:
        sources.append("player_contract_efficiency")
    return sources


def _contract_efficiency_sources(
    contract_efficiency_row: dict[str, Any] | None,
    contract: dict[str, Any] | None,
) -> list[str]:
    if contract_efficiency_row and _score(contract_efficiency_row.get("contract_efficiency_score")) is not None:
        return ["player_contract_efficiency.contract_efficiency_score"]
    if contract:
        return ["contracts.salary", "contracts.contract_years_left", "derived.available_player_value"]
    return []


def _fact_refs(
    league_id: str,
    league_team_id: str,
    player_id: str,
    current: float | None,
    future: float | None,
    relative: float | None,
    contract_efficiency: float | None,
) -> list[str]:
    prefix = f"player_eval.{league_id}.{league_team_id}.{player_id}"
    refs = [f"{prefix}.derived.neutral_overall_value"]
    if current is not None:
        refs.append(f"{prefix}.component.current_contribution")
    if future is not None:
        refs.append(f"{prefix}.component.future_outlook")
    if relative is not None:
        refs.append(f"{prefix}.component.league_relative_value")
    if contract_efficiency is not None:
        refs.append(f"{prefix}.component.contract_efficiency")
    return refs


def _explanation(
    name: str,
    current: float | None,
    future: float | None,
    relative: float | None,
    contract_efficiency: float | None,
    risk: float | None,
    missing: list[str],
    status: str,
) -> str:
    parts = [f"{name} is evaluated from scoped roster, strategic profile, league-relative value, and contract evidence where available."]
    if status == "insufficient_data":
        parts.append("Evaluation is incomplete because there are not enough distinct football value inputs.")
    if current is not None and future is not None:
        parts.append(f"Current contribution is {current:g} and future outlook is {future:g}.")
    if relative is not None:
        parts.append(f"League-relative value is {relative:g}.")
    if contract_efficiency is not None:
        parts.append(f"Contract efficiency is {contract_efficiency:g}; salary is used only against value, not as player quality.")
    if risk is not None:
        parts.append(f"Risk penalty input is {risk:g}.")
    if missing:
        parts.append("Missing inputs lowered confidence: " + ", ".join(missing) + ".")
    return " ".join(parts)
