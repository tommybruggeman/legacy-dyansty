"""Read-only validator for the Phase 3B.5J JSON-compatible YAML catalog."""
from __future__ import annotations
import json
from pathlib import Path

CATALOG=Path(__file__).resolve().parents[1]/"config"/"rollover_operation_catalog.yaml"
VALID_PHASES={"preflight","mutation","reconciliation","validation","finalization","publication"}
VALID_OWNERS={"execution","publication"}

def main():
 data=json.loads(CATALOG.read_text())
 operations=data.get("operations") or []
 codes=[op.get("code") for op in operations];orders=[op.get("order") for op in operations]
 assert len(codes)==len(set(codes)) and all(codes),"operation codes must be unique and nonempty"
 assert len(orders)==len(set(orders)) and all(isinstance(x,int) for x in orders),"orders must be unique integers"
 by_code={op["code"]:op for op in operations}
 for op in operations:
  assert op.get("phase") in VALID_PHASES,f"invalid phase: {op['code']}"
  assert op.get("domain"),f"missing domain: {op['code']}"
  assert op.get("owner") in VALID_OWNERS,f"invalid owner: {op['code']}"
  assert op.get("postconditions"),f"missing postcondition: {op['code']}"
  assert op.get("tests"),f"missing tests: {op['code']}"
  assert op.get("blocking_validations"),f"missing validation: {op['code']}"
  assert op.get("affected_data_objects"),f"missing data objects: {op['code']}"
  for dependency in op.get("dependencies",[]):
   assert dependency in by_code,f"unknown dependency {dependency}: {op['code']}"
   assert by_code[dependency]["order"]<op["order"],f"dependency order violation: {dependency}->{op['code']}"
 visiting=set();visited=set()
 def visit(code):
  if code in visiting:raise AssertionError(f"dependency cycle at {code}")
  if code in visited:return
  visiting.add(code)
  for dependency in by_code[code].get("dependencies",[]):visit(dependency)
  visiting.remove(code);visited.add(code)
 for code in codes:visit(code)
 print(f"catalog=PASS operations={len(operations)} unique_codes={len(codes)} unique_orders={len(orders)} acyclic=true")

if __name__=="__main__":main()
