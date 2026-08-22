import copy
import json
import os
import unittest
from unittest.mock import patch

from scripts import audit_season_rollover_production_parity as audit


class ProductionParityAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(audit.CONTRACT.read_text())

    def snapshot(self):
        tables = [{"table_name": name, "rls": True} for name in self.contract["required_tables"]]
        types = self.contract.get("required_column_types", {})
        columns = [{"table_name": table, "column_name": column, "data_type": types.get(f"{table}.{column}", "text"), "is_nullable": "NO"}
                   for table, names in self.contract["required_tables"].items() for column in names]
        functions = []
        for identity, expected in self.contract["required_functions"].items():
            markers = self.contract.get("definition_markers", {}).get(identity, [])
            functions.append({"identity": identity, "security_definer": expected["security_definer"],
                              "search_path": "search_path=pg_catalog, public",
                              "authenticated_execute": expected["authenticated_execute"],
                              "definition": " ".join(markers)})
        constraints = [{"table_name": x["table"], "name": "expected", "definition": x["contains"]}
                       for x in self.contract["required_constraints"]]
        return {"tables": tables, "columns": columns, "functions": functions,
                "constraints": constraints, "handlers": audit.expected_operations(),
                "authenticated_table_writes": [], "migration_history": None,
                "production_state": None, "identity": {}}

    def required_failures(self, snapshot):
        return [x for x in audit.assess(snapshot, self.contract) if x.required and x.status == "FAIL"]

    def test_complete_parity_passes(self):
        results = audit.assess(self.snapshot(), self.contract)
        self.assertEqual([], [x for x in results if x.required and x.status == "FAIL"])
        self.assertEqual(0, audit.exit_code(results))

    def test_missing_required_table_fails(self):
        value = self.snapshot(); value["tables"].pop()
        self.assertTrue(self.required_failures(value))

    def test_missing_required_function_fails(self):
        value = self.snapshot(); value["functions"].pop()
        self.assertTrue(self.required_failures(value))

    def test_wrong_function_security_posture_fails(self):
        value = self.snapshot(); value["functions"][0]["security_definer"] = False
        self.assertTrue(self.required_failures(value))

    def test_wrong_handler_order_or_count_fails(self):
        value = self.snapshot(); value["handlers"] = value["handlers"][:-1]
        self.assertTrue(self.required_failures(value))

    def test_missing_each_final_correction_fails(self):
        for marker in ("preserve_active_liability", "'[]'::jsonb", "cap_hit"):
            value = self.snapshot()
            for function in value["functions"]:
                function["definition"] = function["definition"].replace(marker, "")
            self.assertTrue(self.required_failures(value), marker)

    def test_publication_only_differences_do_not_fail(self):
        value = self.snapshot(); value["publication_handlers"] = [{"operation_order": 99}]
        self.assertEqual([], self.required_failures(value))

    def test_warning_does_not_mask_required_failure(self):
        value = self.snapshot(); value["tables"].pop()
        results = audit.assess(value, self.contract)
        self.assertTrue(any(x.status == "WARN" for x in results))
        self.assertTrue(any(x.status == "FAIL" and x.required for x in results))
        self.assertEqual(1, audit.exit_code(results))

    def test_acknowledgment_required_before_connection(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "LEGACY_PRODUCTION_PARITY_AUDIT"):
                audit.collect(None)

    def test_query_safety_rejects_mutation(self):
        for sql in ("delete from public.rollover_executions", "with x as (update t set a=1 returning *) select * from x"):
            with self.assertRaises(ValueError): audit.validate_read_only_query(sql)
        audit.validate_read_only_query("select * from pg_catalog.pg_class")

    def test_errors_do_not_expose_password(self):
        secret = "never-print-this-password"
        env = {"LEGACY_PRODUCTION_PARITY_AUDIT":"1", "LEGACY_PROD_DB_HOST":"host",
               "LEGACY_PROD_DB_PORT":"5432", "LEGACY_PROD_DB_NAME":"db",
               "LEGACY_PROD_DB_USER":"auditor", "LEGACY_PROD_DB_PASSWORD":secret}
        failed = type("P", (), {"returncode": 1, "stderr": "authentication failed", "stdout": ""})()
        with patch.dict(os.environ, env, clear=True), patch("subprocess.run", return_value=failed):
            with self.assertRaises(RuntimeError) as caught: audit.collect(None)
        self.assertNotIn(secret, str(caught.exception))


if __name__ == "__main__": unittest.main()
