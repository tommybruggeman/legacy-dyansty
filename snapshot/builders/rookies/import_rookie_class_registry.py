from __future__ import annotations

import csv
import sys
from pathlib import Path

from auth import service_client


REQUIRED_COLUMNS = {"rookie_class_year", "player_name", "pos"}


def import_rookie_registry(csv_path: str) -> None:
    path = Path(csv_path)

    if not path.exists():
        raise FileNotFoundError(path)

    sb = service_client()

    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])

        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")

        rows = []

        for r in reader:
            rows.append({
                "rookie_class_year": int(r["rookie_class_year"]),
                "player_name": r["player_name"].strip(),
                "pos": r["pos"].strip().upper(),
                "sleeper_id": (r.get("sleeper_id") or "").strip() or None,
                "gsis_id": (r.get("gsis_id") or "").strip() or None,
                "nfl_team": (r.get("nfl_team") or "").strip() or None,
                "source": (r.get("source") or "csv_import").strip(),
                "is_active": True,
            })

    if rows:
        sb.table("rookie_class_registry").upsert(
            rows,
            on_conflict="rookie_class_year,player_name,pos",
        ).execute()

    print(f"Imported {len(rows)} rookie registry rows from {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python import_rookie_class_registry.py path/to/rookies.csv")

    import_rookie_registry(sys.argv[1])
