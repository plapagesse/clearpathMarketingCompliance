"""Tests for scripts/generate_contracts_ts.py — the contracts.py -> contracts.gen.ts
codegen that replaced the hand-maintained frontend mirror."""

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GEN_PATH = REPO_ROOT / "frontend" / "src" / "contracts.gen.ts"

spec = importlib.util.spec_from_file_location(
    "generate_contracts_ts", REPO_ROOT / "scripts" / "generate_contracts_ts.py"
)
codegen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codegen)


def test_generation_is_deterministic():
    assert codegen.generate() == codegen.generate()


def test_committed_file_is_fresh():
    assert GEN_PATH.exists(), "frontend/src/contracts.gen.ts missing — run the generator"
    assert GEN_PATH.read_text() == codegen.generate(), (
        "contracts.gen.ts is stale relative to backend/contracts.py — "
        "run: python scripts/generate_contracts_ts.py"
    )


def test_check_mode_catches_mutation(tmp_path, monkeypatch):
    out = tmp_path / "contracts.gen.ts"
    monkeypatch.setattr(codegen, "OUT_PATH", out)
    monkeypatch.setattr(codegen.sys, "argv", ["generate_contracts_ts.py"])
    assert codegen.main() == 0  # writes fresh file
    monkeypatch.setattr(codegen.sys, "argv", ["generate_contracts_ts.py", "--check"])
    assert codegen.main() == 0  # fresh -> passes
    out.write_text(out.read_text() + "\n// tampered\n")
    assert codegen.main() == 1  # stale -> fails


def test_claim_type_union_matches_enum():
    from backend.contracts import ClaimType

    output = codegen.generate()
    match = re.search(r"export type ClaimType = (.+?);", output)
    assert match, "ClaimType union not emitted"
    emitted = re.findall(r'"([^"]+)"', match.group(1))
    assert emitted == [ct.value for ct in ClaimType]


def test_finding_interface_matches_model_fields():
    from backend.contracts import Finding

    output = codegen.generate()
    match = re.search(r"export interface Finding \{(.*?)\}", output, re.S)
    assert match, "Finding interface not emitted"
    emitted_fields = re.findall(r"^\s{2}(\w+)\??:", match.group(1), re.M)
    assert emitted_fields == list(Finding.model_fields)


def test_payload_interfaces_cover_registry():
    from backend.contracts import CLAIM_TYPE_PAYLOADS

    output = codegen.generate()
    for model in CLAIM_TYPE_PAYLOADS.values():
        assert f"export interface {model.__name__} " in output
