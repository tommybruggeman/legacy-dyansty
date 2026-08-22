from dataclasses import replace
import unittest

from season_engine.snapshot_v3 import (
    CHUNK_TARGET_BYTES, MAX_CHUNK_BYTES, SnapshotV3Error,
    build_chunked_component, compact_json, read_component, record_fingerprint, replay_chunked_component,
    snapshot_fingerprint,
)


def records(n: int, width: int = 32):
    return [(f"team:{i:05d}", [i, "x" * width]) for i in range(n)]


class SnapshotV3Tests(unittest.TestCase):
    def test_empty_small_one_chunk_and_legacy_reads(self):
        empty = build_chunked_component("teams", [])
        self.assertEqual(empty.record_count, 0)
        one = build_chunked_component("teams", records(1))
        self.assertEqual(len(one.chunks), 1)
        self.assertEqual(read_component({"canonical_payload": {"legacy": True}}), {"legacy": True})
        self.assertEqual(read_component({"component_schema_version": "phase3b6c-owner_options-v1", "canonical_payload": {"v": 1}}), {"v": 1})
        self.assertEqual(read_component({"component_schema_version": "phase3b6c-owner_option_decisions-v2", "canonical_payload": {"v": 2}}), {"v": 2})

    def test_exact_boundary_and_multi_chunk(self):
        seed = build_chunked_component("teams", [("team:00000", [0, ""])])
        exact = build_chunked_component("teams", [("team:00000", [0, "x" * (CHUNK_TARGET_BYTES-seed.chunks[0].payload_bytes)])])
        self.assertEqual(exact.chunks[0].payload_bytes, CHUNK_TARGET_BYTES)
        multi = build_chunked_component("teams", records(2000, 80))
        self.assertGreater(len(multi.chunks), 1)
        self.assertEqual(replay_chunked_component({**multi.manifest(), "component_name": "teams"}, multi.chunks)[0][0], "team:00000")

    def test_100_and_2000_deterministic_under_shuffle(self):
        for size in (100, 2000):
            forward = build_chunked_component("teams", records(size))
            reverse = build_chunked_component("teams", reversed(records(size)))
            self.assertEqual(forward.component_fingerprint, reverse.component_fingerprint)
            self.assertEqual(forward.chunks, reverse.chunks)

    def test_oversized_single_record_rejected(self):
        with self.assertRaisesRegex(SnapshotV3Error, "single_record_oversize"):
            build_chunked_component("teams", [("team:1", ["x" * MAX_CHUNK_BYTES])])

    def test_missing_duplicate_reordered_and_modified_chunks_reject(self):
        component = build_chunked_component("teams", records(2000, 80))
        manifest = {**component.manifest(), "component_name": "teams"}
        with self.assertRaises(SnapshotV3Error):
            replay_chunked_component(manifest, component.chunks[1:])
        with self.assertRaises(SnapshotV3Error):
            replay_chunked_component(manifest, component.chunks + (component.chunks[-1],))
        swapped = (replace(component.chunks[1], index=0), replace(component.chunks[0], index=1)) + component.chunks[2:]
        with self.assertRaises(SnapshotV3Error):
            replay_chunked_component(manifest, swapped)
        changed = replace(component.chunks[0], records=(("team:00000", (0, "changed")),))
        with self.assertRaises(SnapshotV3Error):
            replay_chunked_component(manifest, (changed,) + component.chunks[1:])
        bad_fp = replace(component.chunks[0], fingerprint="0" * 64)
        with self.assertRaises(SnapshotV3Error):
            replay_chunked_component(manifest, (bad_fp,) + component.chunks[1:])

    def test_altered_manifest_component_and_snapshot_fingerprints_reject(self):
        component = build_chunked_component("teams", records(100))
        for field, value in (("record_count", 99), ("component_fingerprint", "0" * 64),
                             ("aggregate_record_set_fingerprint", "1" * 64)):
            manifest = {**component.manifest(), "component_name": "teams", field: value}
            with self.assertRaises(SnapshotV3Error):
                replay_chunked_component(manifest, component.chunks)
        first = snapshot_fingerprint([("teams", "v3", component.component_fingerprint)])
        second = snapshot_fingerprint([("teams", "v3", "0" * 64)])
        self.assertNotEqual(first, second)

    def test_fixed_snapshot_cross_language_vector(self):
        component = build_chunked_component("teams", records(100, 80))
        self.assertEqual(component.component_fingerprint, "dd71b3241fa5bc795e1c4aa9cdb6f1a6f7ee3a144841a61699f42e0af498e34e")
        self.assertEqual(
            snapshot_fingerprint([("teams", "phase3b6c-team_mapping-v3", component.component_fingerprint)]),
            "70e3fbd4a40f403c00c828ea6fbe580a32252d8261149bf97149366bc1adac69",
        )

    def test_record_chunk_component_hierarchy_fixed_vector(self):
        value = [0, "x" * 80]
        component = build_chunked_component("teams", [("team:00000", value)])
        self.assertEqual(record_fingerprint("team:00000", value), "f4ac4259b0138139943d157142ad7af4d59dc2a4d8056f2d7c29005ffc7b07f5")
        self.assertEqual(component.chunks[0].fingerprint, "f58ebf01a165ac9035f383333978f6f26eb7a37c52b68b590ff9b5ab7bea343a")
        self.assertEqual(component.aggregate_record_set_fingerprint, "4142ba5b8c933fe1b35dbc6907ba95768554e1a5a967040793f782c8117157ea")
        self.assertEqual(component.component_fingerprint, "4296bb81a08cc99fc12f567a5f9fb39adb53020e6b464bebc6f86b4a719cc986")
        self.assertEqual(component.chunks[0].payload_bytes, len(compact_json(component.chunks[0].canonical_payload).encode("utf-8")))

    def test_mixed_unicode_null_boolean_integer_and_decimal_string_vector(self):
        mixed = [("a:null", [None]), ("b:false", [False]), ("c:true", [True]),
                 ("d:int", [-42]), ("e:decimal", ["10.00"]),
                 ("é:key", ["雪", "café", None, True, 7])]
        component = build_chunked_component("mixed", mixed)
        self.assertEqual(component.chunks[0].fingerprint, "97d8db03260f7ff0513e1306bf9c653acd71a4ea1e3cdfd72b9654893fc86392")
        self.assertEqual(component.aggregate_record_set_fingerprint, "8760c297093f9aff509f536a516316b438fad581d193531d47d3c9b5cfc7b147")
        self.assertEqual(component.component_fingerprint, "c8d309008a9427dd57a2e71861874279a3187a6e3818c4d6a16e1a1b0ce5647f")

    def test_exact_and_one_byte_over_chunk_target_vectors(self):
        exact = build_chunked_component("boundary", [("a", ["x" * 65513]), ("b", [""])])
        self.assertEqual([chunk.payload_bytes for chunk in exact.chunks], [65536])
        self.assertEqual(exact.component_fingerprint, "d3e215dc6dc4d2a55256ba81565f3668456cf64692ae2810ce834f07bf2481a3")
        over = build_chunked_component("boundary", [("a", ["x" * 65514]), ("b", [""])])
        self.assertEqual([chunk.payload_bytes for chunk in over.chunks], [65526, 12])
        self.assertEqual(over.component_fingerprint, "5b59e9d3d09d21c0166859e422af7ac150a702cc855e0b390ba18d1521fdfbd8")


if __name__ == "__main__":
    unittest.main()
