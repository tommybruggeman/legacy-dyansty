import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests/phase_d_prepared_cap_integration/run_phase_d_prepared_cap_certification.py"
SPEC = importlib.util.spec_from_file_location("phase_d_runner", RUNNER)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class PhaseDRunnerParsingTests(unittest.TestCase):
    def test_expected_result_shape(self):
        value = runner.parse_cardinality_result(
            '{"teams":1,"distinct_teams":1,"total_charge":2}', 1)
        self.assertEqual(value["total_charge"], 2)

    def test_blank_output_fails_helpfully(self):
        with self.assertRaisesRegex(RuntimeError, "blank SQL output"):
            runner.parse_cardinality_result("\n", 1)

    def test_known_psql_lines_do_not_displace_json(self):
        raw = 'BEGIN\n{"teams":1,"distinct_teams":1,"total_charge":2}\nROLLBACK\n'
        self.assertEqual(runner.parse_cardinality_result(raw, 1)["teams"], 1)

    def test_malformed_or_extra_output_fails_with_raw_context(self):
        with self.assertRaisesRegex(RuntimeError, "raw=.*not-json"):
            runner.parse_cardinality_result("not-json", 1)
        with self.assertRaisesRegex(RuntimeError, "exactly one JSON result"):
            runner.parse_cardinality_result(
                '{"teams":1,"distinct_teams":1,"total_charge":2}\n{}', 1)

    def test_null_values_fail_schema_validation(self):
        with self.assertRaisesRegex(RuntimeError, "total_charge must be non-null numeric"):
            runner.parse_cardinality_result(
                '{"teams":1,"distinct_teams":1,"total_charge":null}', 1)
        with self.assertRaisesRegex(RuntimeError, "teams must be a nonnegative integer"):
            runner.parse_cardinality_result(
                '{"teams":null,"distinct_teams":1,"total_charge":2}', 1)

    def test_all_certification_cardinalities_parse(self):
        for size in (1, 10, 32, 100, 2000):
            raw = ('{"teams":%d,"distinct_teams":%d,"total_charge":%d}'
                   % (size, size, size * 2))
            self.assertEqual(runner.parse_cardinality_result(raw, size)["teams"], size)

    def test_state_and_sentinel_contracts(self):
        self.assertEqual(
            runner.parse_state('{"executions":0,"cap_sets":0,"cap_rows":0}', "state"),
            {"executions": 0, "cap_sets": 0, "cap_rows": 0},
        )
        value = runner.parse_sentinel(
            '{"environment_name":"x","environment_type":"disposable_test","parent_project":"y"}')
        self.assertEqual(value["environment_type"], "disposable_test")


if __name__ == "__main__":
    unittest.main()
