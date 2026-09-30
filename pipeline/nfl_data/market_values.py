"""DynastyProcess values -> nfl_market_values and nfl_pick_values rows."""
from __future__ import annotations

import math
import re
from io import StringIO
from typing import Any

import pandas as pd

from pipeline.nfl_data.common import now_iso, to_float, to_int, to_text
from pipeline.nfl_data.crosswalk import Crosswalk

VALUES_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/values-players.csv"
PICKS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/values-picks.csv"


def read_csv(text: str) -> pd.DataFrame:
    return pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)


def ecr_to_value(ecr: float | None) -> int | None:
    """DynastyProcess's published value curve: 10500 * e^(-0.0232 * ECR)."""
    if ecr is None:
        return None
    return int(round(10500 * math.exp(-0.0232 * float(ecr))))


def transform_values(values: pd.DataFrame, crosswalk: Crosswalk) -> list[dict[str, Any]]:
    stamp = now_iso()
    rows: list[dict[str, Any]] = []
    for _, r in values.iterrows():
        fp_id = to_text(r.get("fp_id"))
        name = to_text(r.get("player"))
        scrape = to_text(r.get("scrape_date"))
        if not fp_id or not name or not scrape:
            continue
        sleeper_id = crosswalk.sleeper_for_fantasypros(fp_id) or crosswalk.sleeper_for_name(name, to_text(r.get("pos")), to_text(r.get("team")))
        rows.append(
            {
                "fantasypros_id": fp_id,
                "scrape_date": scrape,
                "sleeper_id": sleeper_id,
                "player_name": name,
                "position": to_text(r.get("pos")),
                "team": to_text(r.get("team")),
                "age": to_float(r.get("age")),
                "draft_year": to_int(r.get("draft_year")),
                "ecr_1qb": to_float(r.get("ecr_1qb")),
                "ecr_2qb": to_float(r.get("ecr_2qb")),
                "ecr_pos": to_float(r.get("ecr_pos")),
                "value_1qb": to_int(r.get("value_1qb")),
                "value_2qb": to_int(r.get("value_2qb")),
                "source": "dynastyprocess",
                "refreshed_at": stamp,
            }
        )
    return rows


_PICK_RE = re.compile(r"^(?P<year>\d{4})\s+(?:Pick\s+(?P<round>\d)\.(?P<slot>\d{2})|(?P<tier>Early|Mid|Late)?\s*(?P<round2>\d)(?:st|nd|rd|th))$", re.I)


def parse_pick_label(label: str) -> tuple[int | None, int | None, str | None]:
    m = _PICK_RE.match(str(label or "").strip())
    if not m:
        return None, None, None
    year = int(m.group("year"))
    if m.group("round"):
        return year, int(m.group("round")), f"{int(m.group('round'))}.{m.group('slot')}"
    tier = (m.group("tier") or "").lower() or None
    return year, int(m.group("round2")), tier


def transform_picks(picks: pd.DataFrame) -> list[dict[str, Any]]:
    stamp = now_iso()
    rows: list[dict[str, Any]] = []
    for _, r in picks.iterrows():
        label = to_text(r.get("player"))
        scrape = to_text(r.get("scrape_date"))
        if not label or not scrape:
            continue
        year, rnd, slot = parse_pick_label(label)
        ecr1, ecr2 = to_float(r.get("ecr_1qb")), to_float(r.get("ecr_2qb"))
        rows.append(
            {
                "pick_label": label,
                "scrape_date": scrape,
                "draft_year": year,
                "round": rnd,
                "slot": slot,
                "ecr_1qb": ecr1,
                "ecr_2qb": ecr2,
                "value_1qb": ecr_to_value(ecr1),
                "value_2qb": ecr_to_value(ecr2),
                "source": "dynastyprocess",
                "refreshed_at": stamp,
            }
        )
    return rows
