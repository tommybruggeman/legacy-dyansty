"""Tool registry for League AI.

A tool is a plain Python function plus a JSON schema. Tools are read-only
over league and NFL data, except `remember`, which writes memory notes.
Implementations are registered by later build steps; the registry and the
loop that runs them live here so the client is testable on its own.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., Any]

    def as_api(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class ToolRegistry:
    tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec

    def specs(self) -> list[dict[str, Any]]:
        return [t.as_api() for t in self.tools.values()]

    def __len__(self) -> int:
        return len(self.tools)

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Run a tool and return its result as text for the model. Never raises."""
        spec = self.tools.get(name)
        if spec is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            result = spec.fn(**(arguments or {}))
        except TypeError as exc:
            return json.dumps({"error": f"bad arguments for {name}: {exc}"})
        except Exception as exc:  # tool failures are reported to the model, not raised
            return json.dumps({"error": f"{name} failed: {type(exc).__name__}: {str(exc)[:300]}"})
        if isinstance(result, str):
            return result
        try:
            # compact: no padding, and no empty fields. Every tool result is re-read on each later round
            # of the answer, so its size is paid several times over.
            return json.dumps(_compact(result), default=str, separators=(",", ":"))
        except Exception:
            return str(result)


def _compact(value: Any) -> Any:
    """Drop None, empty strings and empty containers; zeros and False stay (they carry information)."""
    if isinstance(value, dict):
        out = {k: _compact(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v is not None and v != "" and v != [] and v != {}}
    if isinstance(value, (list, tuple)):
        return [_compact(v) for v in value]
    return value
