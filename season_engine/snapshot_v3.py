"""Canonical chunked evidence for rollover input snapshot v3.

The wire format deliberately uses positional arrays for all hashed material.
JSON objects may exist inside application payloads, but collection records must
already be normalized to positional values before they enter this boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

SNAPSHOT_SCHEMA = "phase3b6c-snapshot-v3"
RECORD_SCHEMA = "phase3b6c-record-v3"
CHUNK_SCHEMA = "phase3b6c-chunk-v3"
COMPONENT_SCHEMA = "phase3b6c-component-v3"
SNAPSHOT_FINGERPRINT_SCHEMA = "phase3b6c-snapshot-fingerprint-v3"
CHUNK_TARGET_BYTES = 65_536
MAX_CHUNK_BYTES = 73_728
MAX_CHUNKS_PER_COMPONENT = 1_024
MAX_TOTAL_EVIDENCE_BYTES = 67_108_864


class SnapshotV3Error(ValueError):
    """Fail-closed canonicalization or replay error."""


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False, sort_keys=True)


def _encoded(value: Any) -> bytes:
    return compact_json(value).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def record_material(key: str, value: Sequence[Any]) -> list[Any]:
    if not isinstance(key, str) or not key:
        raise SnapshotV3Error("snapshot_v3_canonical_key_invalid")
    if not isinstance(value, (list, tuple)):
        raise SnapshotV3Error("snapshot_v3_record_must_be_positional")
    return [RECORD_SCHEMA, key, list(value)]


def record_fingerprint(key: str, value: Sequence[Any]) -> str:
    return _sha256(record_material(key, value))


@dataclass(frozen=True)
class SnapshotChunk:
    index: int
    first_canonical_key: str
    last_canonical_key: str
    records: tuple[tuple[str, tuple[Any, ...]], ...]
    payload_bytes: int
    fingerprint: str

    @property
    def record_count(self) -> int:
        return len(self.records)

    @property
    def canonical_payload(self) -> list[list[Any]]:
        return [[key, list(value)] for key, value in self.records]


@dataclass(frozen=True)
class ChunkedComponent:
    name: str
    chunks: tuple[SnapshotChunk, ...]
    record_count: int
    aggregate_record_set_fingerprint: str
    component_fingerprint: str
    total_payload_bytes: int
    inline_metadata: Mapping[str, Any]
    metadata_fingerprint: str

    def manifest(self) -> dict[str, Any]:
        return {
            "storage": "ordered_chunks",
            "schema_version": COMPONENT_SCHEMA,
            "record_count": self.record_count,
            "chunk_count": len(self.chunks),
            "first_canonical_key": self.chunks[0].first_canonical_key if self.chunks else None,
            "last_canonical_key": self.chunks[-1].last_canonical_key if self.chunks else None,
            "ordered_chunk_fingerprints": [chunk.fingerprint for chunk in self.chunks],
            "aggregate_record_set_fingerprint": self.aggregate_record_set_fingerprint,
            "component_fingerprint": self.component_fingerprint,
            "total_payload_bytes": self.total_payload_bytes,
            "inline_metadata": dict(self.inline_metadata),
            "metadata_fingerprint": self.metadata_fingerprint,
        }


def _chunk_fingerprint(name: str, chunk_index: int, records: Sequence[tuple[str, Sequence[Any]]]) -> str:
    return _sha256([
        CHUNK_SCHEMA,
        name,
        chunk_index,
        records[0][0],
        records[-1][0],
        len(records),
        [[key, record_fingerprint(key, value)] for key, value in records],
    ])


def build_chunked_component(
    name: str,
    records: Iterable[tuple[str, Sequence[Any]]],
    *,
    target_bytes: int = CHUNK_TARGET_BYTES,
    max_chunk_bytes: int = MAX_CHUNK_BYTES,
    inline_metadata: Mapping[str, Any] | None = None,
) -> ChunkedComponent:
    if not name or target_bytes < 2 or max_chunk_bytes < target_bytes:
        raise SnapshotV3Error("snapshot_v3_chunk_policy_invalid")
    ordered = sorted(((key, tuple(value)) for key, value in records), key=lambda row: row[0].encode("utf-8"))
    keys = [row[0] for row in ordered]
    if len(keys) != len(set(keys)):
        raise SnapshotV3Error("snapshot_v3_duplicate_canonical_key")

    grouped: list[list[tuple[str, tuple[Any, ...]]]] = []
    current: list[tuple[str, tuple[Any, ...]]] = []
    for row in ordered:
        record_fingerprint(*row)  # validates positional shape and key
        if len(_encoded([[row[0], list(row[1])]])) > max_chunk_bytes:
            raise SnapshotV3Error("snapshot_v3_single_record_oversize")
        candidate = current + [row]
        if current and len(_encoded([[key, list(value)] for key, value in candidate])) > target_bytes:
            grouped.append(current)
            current = [row]
        else:
            current = candidate
    if current:
        grouped.append(current)
    if len(grouped) > MAX_CHUNKS_PER_COMPONENT:
        raise SnapshotV3Error("snapshot_v3_component_chunk_count_exceeded")

    chunks: list[SnapshotChunk] = []
    total_bytes = 0
    for index, group in enumerate(grouped):
        payload_bytes = len(_encoded([[key, list(value)] for key, value in group]))
        if payload_bytes > max_chunk_bytes:
            raise SnapshotV3Error("snapshot_v3_chunk_size_exceeded")
        total_bytes += payload_bytes
        chunks.append(SnapshotChunk(
            index=index,
            first_canonical_key=group[0][0],
            last_canonical_key=group[-1][0],
            records=tuple(group),
            payload_bytes=payload_bytes,
            fingerprint=_chunk_fingerprint(name, index, group),
        ))
    if total_bytes > MAX_TOTAL_EVIDENCE_BYTES:
        raise SnapshotV3Error("snapshot_v3_total_evidence_size_exceeded")

    record_set_fp = _sha256([
        "phase3b6c-record-set-v3",
        [[key, record_fingerprint(key, value)] for key, value in ordered],
    ])
    metadata = dict(inline_metadata or {})
    metadata_fp = _sha256(["phase3b6c-component-metadata-v3", metadata])
    component_fp = _sha256([
        COMPONENT_SCHEMA,
        name,
        len(ordered),
        chunks[0].first_canonical_key if chunks else None,
        chunks[-1].last_canonical_key if chunks else None,
        [chunk.fingerprint for chunk in chunks],
        record_set_fp,
        metadata_fp,
    ])
    return ChunkedComponent(name, tuple(chunks), len(ordered), record_set_fp, component_fp, total_bytes, metadata, metadata_fp)


def replay_chunked_component(manifest: Mapping[str, Any], chunks: Iterable[SnapshotChunk]) -> list[list[Any]]:
    if manifest.get("storage") != "ordered_chunks" or manifest.get("schema_version") != COMPONENT_SCHEMA:
        raise SnapshotV3Error("snapshot_v3_manifest_schema_invalid")
    ordered = sorted(chunks, key=lambda chunk: chunk.index)
    expected_count = int(manifest.get("chunk_count", -1))
    if [chunk.index for chunk in ordered] != list(range(expected_count)):
        raise SnapshotV3Error("snapshot_v3_chunk_sequence_invalid")
    if [chunk.fingerprint for chunk in ordered] != manifest.get("ordered_chunk_fingerprints"):
        raise SnapshotV3Error("snapshot_v3_chunk_fingerprint_order_mismatch")
    rebuilt = build_chunked_component(
        str(manifest.get("component_name") or "component"),
        ((key, value) for chunk in ordered for key, value in chunk.records),
        inline_metadata=manifest.get("inline_metadata") or {},
    )
    # Persisted manifests include component_name; callers using a legacy manifest
    # may provide it separately only for v1/v2 compatibility and never reach here.
    if manifest.get("component_name") is None:
        rebuilt = build_chunked_component("component", ((key, value) for chunk in ordered for key, value in chunk.records), inline_metadata=manifest.get("inline_metadata") or {})
    for field, actual in (
        ("record_count", rebuilt.record_count),
        ("first_canonical_key", rebuilt.manifest()["first_canonical_key"]),
        ("last_canonical_key", rebuilt.manifest()["last_canonical_key"]),
        ("aggregate_record_set_fingerprint", rebuilt.aggregate_record_set_fingerprint),
        ("component_fingerprint", rebuilt.component_fingerprint),
        ("total_payload_bytes", rebuilt.total_payload_bytes),
        ("metadata_fingerprint", rebuilt.metadata_fingerprint),
    ):
        if manifest.get(field) != actual:
            raise SnapshotV3Error(f"snapshot_v3_manifest_{field}_mismatch")
    return [[key, list(value)] for chunk in ordered for key, value in chunk.records]


def snapshot_fingerprint(components: Iterable[tuple[str, str, str]]) -> str:
    rows = sorted(([name, schema, fingerprint] for name, schema, fingerprint in components), key=lambda row: row[0].encode("utf-8"))
    if len(rows) != len({row[0] for row in rows}):
        raise SnapshotV3Error("snapshot_v3_duplicate_component")
    return _sha256([SNAPSHOT_FINGERPRINT_SCHEMA, rows])


def read_component(component: Mapping[str, Any], chunks: Iterable[SnapshotChunk] = ()) -> Any:
    """Read v1/v2 inline payloads or fully validate and replay v3 chunks."""
    payload = component.get("canonical_payload")
    if isinstance(payload, Mapping) and payload.get("storage") == "ordered_chunks":
        manifest = dict(payload)
        manifest.setdefault("component_name", component.get("component_name"))
        return replay_chunked_component(manifest, chunks)
    return payload
