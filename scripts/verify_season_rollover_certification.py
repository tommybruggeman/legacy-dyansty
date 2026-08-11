#!/usr/bin/env python3
"""Read-only SHA-256 drift check for the certified rollover baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "docs/certification/season_rollover_2025_2026_certified_v1.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read certification manifest: {exc}")
        return 2

    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
        print("FAIL: unsupported or malformed certification manifest")
        return 2

    failures: list[str] = []
    for entry in manifest["files"]:
        relative = entry.get("path", "")
        expected = entry.get("sha256", "")
        path = (REPO_ROOT / relative).resolve()
        try:
            path.relative_to(REPO_ROOT)
        except ValueError:
            failures.append(f"INVALID PATH  {relative}")
            continue
        if not path.is_file():
            failures.append(f"MISSING       {relative}")
            continue
        actual = sha256(path)
        if actual != expected:
            failures.append(f"CHANGED       {relative}\n  expected {expected}\n  actual   {actual}")

    if failures:
        print("FAIL: certified season rollover baseline drift detected")
        print("\n".join(failures))
        return 1
    print(f"PASS: {len(manifest['files'])} certified files match stored SHA-256 hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
