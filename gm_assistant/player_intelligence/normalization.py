from __future__ import annotations

import math
import re
from typing import Any


MISSING_ID_VALUES = {"", "none", "null", "nan", "n/a", "na"}
MAX_EXACT_FLOAT_INT = 9_007_199_254_740_991


def normalize_player_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer() or abs(value) > MAX_EXACT_FLOAT_INT:
            return None
        return str(int(value))
    text = str(value).strip()
    if text.lower() in MISSING_ID_VALUES:
        return None
    return text or None


def normalize_player_name(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = re.sub(r"\s+", " ", text)
    return normalized or None


def player_name_key(value: Any) -> str | None:
    text = normalize_player_name(value)
    if not text:
        return None
    lowered = text.lower().replace("'", "")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered or None


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip().replace("$", "").replace(",", ""))
    except Exception:
        return None


def clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
