from __future__ import annotations

import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_name(name: Any) -> str:
    """'Ja'Marr Chase' -> 'jamarr chase'; strips punctuation, accents and suffixes."""
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9 ]", "", text).lower().strip()
    parts = [p for p in text.split() if p not in _SUFFIXES]
    return " ".join(parts)


def clean(value: Any) -> Any:
    """Turn pandas/NumPy NaN and 'NA' into None, numpy scalars into Python scalars."""
    if value is None:
        return None
    try:
        import math

        if isinstance(value, float) and math.isnan(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, str) and value.strip().upper() in {"NA", "NAN", ""}:
        return None
    return value


def to_int(value: Any) -> int | None:
    value = clean(value)
    if value is None:
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def to_float(value: Any) -> float | None:
    value = clean(value)
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def to_text(value: Any) -> str | None:
    value = clean(value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_local_env() -> None:
    """Locally the credentials live in the project .env; in CI they are real env vars."""
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY") and os.environ.get("CFBD_API_KEY"):
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:
        pass


def service_client():
    from supabase import create_client

    load_local_env()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required (set them in .env or the environment).")
    return create_client(url, key)


def dedupe(rows: Iterable[dict[str, Any]], on_conflict: str) -> list[dict[str, Any]]:
    """Keep the last row per conflict key; Postgres refuses two rows with the same key in one upsert."""
    keys = [k.strip() for k in on_conflict.split(",")]
    seen: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        seen[tuple(str(row.get(k)) for k in keys)] = row
    return list(seen.values())


def upsert_rows(client: Any, table: str, rows: Iterable[dict[str, Any]], *, on_conflict: str, chunk: int = 500) -> int:
    rows = dedupe(rows, on_conflict)
    written = 0
    for i in range(0, len(rows), chunk):
        batch = rows[i : i + chunk]
        client.table(table).upsert(batch, on_conflict=on_conflict).execute()
        written += len(batch)
    return written


def log_run(client: Any, loader: str, *, started_at: str, rows_written: int, ok: bool, detail: str = "") -> None:
    try:
        client.table("nfl_data_sync_log").insert(
            {"loader": loader, "started_at": started_at, "finished_at": now_iso(), "rows_written": rows_written, "ok": ok, "detail": detail[:2000]}
        ).execute()
    except Exception as exc:  # logging must never fail the run
        print(f"[nfl_data] log_run failed: {exc}", flush=True)


# Some public endpoints (ESPN) refuse non-browser user agents.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "application/json, text/csv, text/plain, */*",
}


def fetch_text(url: str, timeout: int = 120) -> str:
    import requests

    resp = requests.get(url, timeout=timeout, headers=HEADERS)
    resp.raise_for_status()
    return resp.text


def fetch_json(url: str, timeout: int = 120) -> Any:
    import requests

    resp = requests.get(url, timeout=timeout, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()
