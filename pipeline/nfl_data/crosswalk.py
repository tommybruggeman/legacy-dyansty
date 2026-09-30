"""DynastyProcess player ID crosswalk: sleeper <-> gsis <-> fantasypros <-> pfr <-> cfbref."""
from __future__ import annotations

from dataclasses import dataclass, field
from io import StringIO
from typing import Any

import pandas as pd

from pipeline.nfl_data.common import normalize_name, to_int, to_text

CROSSWALK_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"


@dataclass
class Crosswalk:
    frame: pd.DataFrame
    by_sleeper: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_gsis: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_fantasypros: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_pfr: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_espn: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_name: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def from_csv(cls, text: str) -> "Crosswalk":
        frame = pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)
        cw = cls(frame=frame)
        for _, row in frame.iterrows():
            rec = {k: (v if v not in ("", "NA") else None) for k, v in row.items()}
            rec["search_name"] = normalize_name(rec.get("name"))
            for key, index in (("sleeper_id", cw.by_sleeper), ("gsis_id", cw.by_gsis), ("fantasypros_id", cw.by_fantasypros), ("pfr_id", cw.by_pfr), ("espn_id", cw.by_espn)):
                value = to_text(rec.get(key))
                if value:
                    index[_norm_id(value)] = rec
            if rec["search_name"]:
                cw.by_name.setdefault(rec["search_name"], []).append(rec)
        return cw

    def sleeper_for_gsis(self, gsis_id: Any) -> str | None:
        rec = self.by_gsis.get(_norm_id(to_text(gsis_id) or ""))
        return to_text(rec.get("sleeper_id")) if rec else None

    def sleeper_for_fantasypros(self, fp_id: Any) -> str | None:
        rec = self.by_fantasypros.get(_norm_id(to_text(fp_id) or ""))
        return to_text(rec.get("sleeper_id")) if rec else None

    def gsis_for_pfr(self, pfr_id: Any) -> str | None:
        rec = self.by_pfr.get(_norm_id(to_text(pfr_id) or ""))
        return to_text(rec.get("gsis_id")) if rec else None

    def sleeper_for_espn(self, espn_id: Any) -> str | None:
        rec = self.by_espn.get(_norm_id(to_text(espn_id) or ""))
        return to_text(rec.get("sleeper_id")) if rec else None

    def record_for_sleeper(self, sleeper_id: Any) -> dict[str, Any] | None:
        return self.by_sleeper.get(_norm_id(to_text(sleeper_id) or ""))

    def sleeper_for_name(self, name: Any, position: str | None = None, team: str | None = None) -> str | None:
        candidates = self.by_name.get(normalize_name(name)) or []
        if position:
            narrowed = [c for c in candidates if (c.get("position") or "").upper() == position.upper()]
            candidates = narrowed or candidates
        if len(candidates) > 1 and team:
            narrowed = [c for c in candidates if (c.get("team") or "").upper() == team.upper()]
            candidates = narrowed or candidates
        if len(candidates) == 1:
            return to_text(candidates[0].get("sleeper_id"))
        return None


def _norm_id(value: str) -> str:
    """IDs arrive as '13269' or '13269.0' depending on the source; compare as text of the integer when numeric."""
    value = str(value).strip()
    n = to_int(value)
    return str(n) if n is not None and value.replace(".", "", 1).isdigit() else value
