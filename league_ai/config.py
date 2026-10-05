"""Runtime configuration for League AI.

Values come from environment variables first (the .env file locally) and
Streamlit secrets second, so the same code runs on a laptop and on
Streamlit Cloud. Nothing here ever logs a secret.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_OUTPUT_TOKENS = 12000  # thinking tokens count against this (Sonnet 5.5 thinks by default)
DEFAULT_MAX_TOOL_CALLS = 15
DEFAULT_TIMEOUT_SECONDS = 60.0
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def _setting(name: str) -> str:
    value = os.getenv(name, "")
    if value and value.strip():
        return value.strip()
    try:  # Streamlit secrets are optional; never required for tests.
        import streamlit as st  # noqa: WPS433

        secret = st.secrets.get(name, "")
        return str(secret).strip() if secret else ""
    except Exception:
        return ""


def _truthy(value: str, default: bool) -> bool:
    if not value:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class LeagueAIConfig:
    enabled: bool
    api_key_present: bool
    model: str
    max_output_tokens: int
    max_tool_calls: int
    timeout_seconds: float
    effort: str | None = None  # None = the model's default; set LEAGUE_AI_EFFORT to override

    @property
    def ready(self) -> bool:
        return self.enabled and self.api_key_present


def load_config() -> LeagueAIConfig:
    def _int(name: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(float(_setting(name)))))
        except Exception:
            return default

    def _float(name: str, default: float) -> float:
        try:
            return float(_setting(name))
        except Exception:
            return default

    return LeagueAIConfig(
        enabled=_truthy(_setting("LEAGUE_AI_ENABLED"), True),
        api_key_present=bool(_setting("ANTHROPIC_API_KEY")),
        model=_setting("LEAGUE_AI_MODEL") or DEFAULT_MODEL,
        max_output_tokens=_int("LEAGUE_AI_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS, 1000, 32000),
        max_tool_calls=_int("LEAGUE_AI_MAX_TOOL_CALLS", DEFAULT_MAX_TOOL_CALLS, 0, 30),
        timeout_seconds=_float("LEAGUE_AI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        effort=(_setting("LEAGUE_AI_EFFORT").lower() if _setting("LEAGUE_AI_EFFORT").lower() in EFFORT_LEVELS else None),
    )


def api_key() -> str:
    """Return the key for the SDK. Only ever passed to the Anthropic client."""
    return _setting("ANTHROPIC_API_KEY")
