import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "season_rollover_gate3_mutation_paths_v1.json"
MANIFEST_PATH = ROOT / "docs" / "certification" / "season_rollover_2025_2026_certified_v1.json"
RUNTIME_ROOTS = ("pages", "services", "season_engine", "contract_engine", "Admin Commissioner")
WRITE_METHODS = {"insert", "update", "delete", "upsert"}
APPROVED_NON_UI_DIRECT_WRITES = {
    "season_engine/commissioner_policy_approval.py:58:insert:league_rollover_policies",
}


def registry():
    return json.loads(REGISTRY_PATH.read_text())


def table_receiver(call):
    for node in ast.walk(call.func.value):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value
    return None


def protected(table, data):
    return (
        table in set(data["protected_exact_tables"])
        or table.startswith("rollover_")
        or table.startswith("prepared_")
    )


class Gate3MutationPathRegistryTests(unittest.TestCase):
    def test_registry_is_complete_and_uses_only_declared_classifications(self):
        data = registry()
        declared = set(data["classifications"])
        paths = data["paths"]
        self.assertEqual(len({item["id"] for item in paths}), len(paths))
        self.assertTrue(paths)
        for item in paths:
            self.assertIn(item["classification"], declared)
            self.assertTrue(item["surfaces"])
            self.assertTrue(item["mechanism"])
            self.assertTrue(item["locations"])
            self.assertTrue(item["notes"])

    def test_gate3_files_are_outside_frozen_gate1_manifest(self):
        certified = {item["path"] for item in json.loads(MANIFEST_PATH.read_text())["files"]}
        for path in (
            REGISTRY_PATH,
            Path(__file__).resolve(),
            ROOT / "tests" / "test_season_rollover_gate3_application_wiring.py",
            ROOT / "services" / "season_rollover_ui.py",
            ROOT / "services" / "season_rollover_owner_ui.py",
            ROOT / "pages" / "90_Settings.py",
            ROOT / "docs" / "season_rollover_gate3_disposable_ui_acceptance.md",
        ):
            self.assertNotIn(str(path.relative_to(ROOT)), certified)

    def test_runtime_has_no_direct_protected_table_writes(self):
        data = registry()
        violations = []
        for root_name in RUNTIME_ROOTS:
            for path in (ROOT / root_name).rglob("*.py"):
                try:
                    tree = ast.parse(path.read_text())
                except SyntaxError as exc:
                    self.fail(f"cannot audit {path.relative_to(ROOT)}: {exc}")
                for call in ast.walk(tree):
                    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                        continue
                    if call.func.attr not in WRITE_METHODS:
                        continue
                    table = table_receiver(call)
                    if table and protected(table, data):
                        violations.append(f"{path.relative_to(ROOT)}:{call.lineno}:{call.func.attr}:{table}")
        self.assertEqual(set(violations), APPROVED_NON_UI_DIRECT_WRITES)

    def test_ui_does_not_hold_service_role_or_directly_mutate_protected_tables(self):
        ui = (ROOT / "services" / "season_rollover_ui.py").read_text()
        page = (ROOT / "pages" / "90_Settings.py").read_text()
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", ui)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", page)
        self.assertIn("SeasonRolloverControlService", ui)
        self.assertIn("render_season_rollover_control", page)

    def test_service_rpc_allowlist_covers_registry_publication_and_execution_boundaries(self):
        source = (ROOT / "services" / "season_rollover_control.py").read_text()
        required = {
            "approve_canonical_rollover_policy_authenticated",
            "create_rollover_execution_authenticated",
            "open_rollover_notice_window_authenticated",
            "close_rollover_decision_window_authenticated",
            "initialize_rollover_commissioner_reviews_authenticated",
            "prepare_rollover_authorities_authenticated",
            "approve_rollover_execution_plan_authenticated",
            "revoke_rollover_execution_plan_approval_authenticated",
            "execute_rollover_plan_authenticated",
            "publish_target_season_authority_authenticated",
            "activate_target_cap_authority_authenticated",
            "enable_target_free_agent_visibility_authenticated",
            "release_cutover_restrictions_authenticated",
            "refresh_published_ui_and_ai_context_authenticated",
        }
        self.assertTrue(all(f'"{name}"' in source for name in required))
        self.assertIn("if name not in AUTHENTICATED_RPCS", source)

    def test_admin_only_direct_policy_insert_is_not_referenced_by_ui(self):
        application = "\n".join(
            path.read_text(errors="ignore")
            for root_name in ("pages", "services")
            for path in (ROOT / root_name).rglob("*.py")
        )
        self.assertNotIn("CommissionerPolicyApprovalService", application)
        self.assertIn(
            "CommissionerPolicyApprovalService",
            (ROOT / "scripts" / "approve_commissioner_rollover_policy.py").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
