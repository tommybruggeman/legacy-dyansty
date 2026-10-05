"""League AI service: assemble the pack, run Claude, return an answer.

The page calls `answer()`. Everything Streamlit-specific stays on the page;
this module only needs a Supabase client, the request context and callables
for the data it cannot load itself (standings).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from league_ai.client import ClaudeClient, ClientResult, Trace, human_message
from league_ai.config import LeagueAIConfig, load_config
from league_ai.context_pack import Asker, PackInputs, build_league_pack
from league_ai.prompt import system_blocks
from league_ai.settings_store import apply_sleeper_scoring, fetch_sleeper_scoring, load_settings
from league_ai.tools import ToolRegistry
from services.team_roster_state import CanonicalTeamStateError, load_team_state


@dataclass
class AnswerResult:
    ok: bool
    text: str
    trace: Trace
    error_code: str | None = None
    pack_chars: int = 0

    @property
    def human_error(self) -> str:
        return human_message(self.error_code)

    def trace_line(self) -> str:
        payload = self.trace.as_dict()
        payload["pack_chars"] = self.pack_chars
        payload["ok"] = self.ok
        return "LEAGUE_AI_TRACE " + json.dumps(payload, sort_keys=True, default=str)


@dataclass
class LeagueDataSources:
    """Everything the service needs to build the pack, injectable for tests."""
    client: Any
    league_id: str
    season: int
    league_name: str = "the league"
    sleeper_league_id: str | None = None
    league_rules: Mapping[str, Any] | None = None
    standings_loader: Callable[[], list[dict[str, Any]]] | None = None
    history_loader: Callable[[], list[str]] | None = None
    state_loader: Callable[[], Mapping[str, Any]] | None = None
    private_memory_loader: Callable[[], list[str]] | None = None
    league_notebook_loader: Callable[[], list[str]] | None = None
    learning_loader: Callable[[], list[str]] | None = None
    data_freshness: dict[str, str] = field(default_factory=dict)


def _safe(loader: Callable[[], Any] | None, default: Any) -> Any:
    if loader is None:
        return default
    try:
        return loader()
    except Exception:
        return default


def load_league_rules(client: Any, league_id: str) -> dict[str, Any]:
    try:
        rows = client.table("league_rules").select("*").eq("league_id", league_id).limit(1).execute().data or []
        return dict(rows[0]) if rows else {}
    except Exception:
        return {}


def build_pack(sources: LeagueDataSources, asker: Asker) -> str:
    state = sources.state_loader() if sources.state_loader else load_team_state(sources.client, sources.league_id, sources.season)
    rules = sources.league_rules if sources.league_rules is not None else load_league_rules(sources.client, sources.league_id)
    ai_settings = load_settings(sources.client, sources.league_id)
    if not ai_settings.roster_positions and sources.sleeper_league_id:
        try:  # nothing saved yet: use Sleeper's lineup and scoring for this answer (not persisted)
            apply_sleeper_scoring(ai_settings, fetch_sleeper_scoring(sources.sleeper_league_id))
        except Exception:
            pass
    inputs = PackInputs(
        league_name=sources.league_name,
        season=sources.season,
        state=state,
        league_rules=rules,
        ai_settings=ai_settings,
        asker=asker,
        standings=_safe(sources.standings_loader, []),
        past_seasons=_safe(sources.history_loader, []),
        private_memory=_safe(sources.private_memory_loader, []),
        league_notebook=_safe(sources.league_notebook_loader, []),
        learning=_safe(sources.learning_loader, []),
        data_freshness=dict(sources.data_freshness),
    )
    return build_league_pack(inputs)


def _history_messages(history: list[dict[str, str]], limit: int = 20) -> list[dict[str, Any]]:
    """Convert on-screen chat history to API messages: alternating roles, text only."""
    out: list[dict[str, Any]] = []
    for msg in history[-limit:]:
        role = "user" if msg.get("role") == "user" else "assistant"
        text = str(msg.get("content") or "").strip()
        if not text:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":  # API history must start with the user
        out.pop(0)
    return out


def answer(
    *,
    question: str,
    history: list[dict[str, str]],
    asker: Asker,
    sources: LeagueDataSources,
    tools: ToolRegistry | None = None,
    client: ClaudeClient | None = None,
    config: LeagueAIConfig | None = None,
    pack: str | None = None,
    on_event: Callable[[str, Any], None] | None = None,
    week_context: str | None = None,
    turn_effort: str | None = None,
) -> AnswerResult:
    config = config or load_config()
    if not config.enabled:
        return AnswerResult(False, "", Trace(model=config.model, error_code="disabled"), "disabled")
    if not config.api_key_present and client is None:
        return AnswerResult(False, "", Trace(model=config.model, error_code="missing_api_key"), "missing_api_key")

    try:
        league_pack = pack if pack is not None else build_pack(sources, asker)
    except CanonicalTeamStateError as exc:
        trace = Trace(model=config.model, error_code="league_state_unavailable")
        return AnswerResult(False, str(exc), trace, "league_state_unavailable")

    messages = _history_messages(history)
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n\n" + question.strip()
    else:
        messages.append({"role": "user", "content": question.strip()})

    runner = client or ClaudeClient(config)
    result: ClientResult = runner.run(system=system_blocks(league_pack, week_context), messages=messages, tools=tools, on_event=on_event, turn_effort=turn_effort)
    return AnswerResult(result.ok, result.text, result.trace, result.error_code, pack_chars=len(league_pack))
