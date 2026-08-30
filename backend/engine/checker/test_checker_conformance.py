"""Answer-key conformance: run_checks over every fixture mock with
perfect-extractor inputs (key-derived claims, manifest-derived disclosures,
referenced offer cells, flattened artifact text).

Ground truth: fixtures/expected_findings.json (rulebook v2026.08.3 rule_ids).

- Violation mocks: emitted rule_ids must be a SUPERSET of the mock's expected
  deterministic, non-fidelity rule_ids (llm_judged ids like PL-JUDGE-001 /
  CC-JUDGE-001 are excluded by check_kind lookup in the rulebook; fidelity
  entries are asserted in test_checker_fidelity.py because the engine emits
  those with rule_id=None).
- Compliant mocks: zero deterministic violations — nothing at severity
  medium or above (info/needs-verification chatter is allowed).
"""

from __future__ import annotations

import pytest

from backend.contracts import CheckClass, SubmissionMode

from conftest import (
    COMPLIANT_MOCKS,
    EXPECTED,
    OFFER_MATRIX_VERSION,
    RULEBOOK_VERSION,
    SEV_AT_LEAST_MEDIUM,
    VIOLATION_MOCKS,
    assert_engine_invariants,
    emitted_rule_ids,
    expected_deterministic_ids,
    findings_for_rule,
    rule_by_id,
    run_mock,
)


@pytest.mark.parametrize("mock_name", VIOLATION_MOCKS)
def test_violation_mock_fires_expected_deterministic_rules(
    mock_name, rulebook, submissions_by_id, real_cells
):
    run = run_mock(mock_name, rulebook, submissions_by_id, real_cells)
    assert_engine_invariants(run, rulebook)
    expected = expected_deterministic_ids(mock_name, rulebook)
    assert expected, f"key entry for {mock_name} lost its deterministic ids"
    missing = expected - emitted_rule_ids(run)
    assert not missing, (
        f"{mock_name}: expected deterministic rules did not fire: {sorted(missing)}; "
        f"emitted={sorted(emitted_rule_ids(run))}"
    )


@pytest.mark.parametrize("mock_name", COMPLIANT_MOCKS)
def test_compliant_mock_yields_no_finding_at_or_above_medium(
    mock_name, rulebook, submissions_by_id, real_cells
):
    run = run_mock(mock_name, rulebook, submissions_by_id, real_cells)
    assert_engine_invariants(run, rulebook)
    offenders = [f for f in run.findings if f.severity in SEV_AT_LEAST_MEDIUM]
    assert not offenders, (
        f"{mock_name} is certified compliant by the answer key, but the engine "
        f"raised: {[(f.rule_id, f.severity, f.summary) for f in offenders]}"
    )


def test_finding_severity_and_citation_come_from_the_rule(
    rulebook, submissions_by_id, real_cells
):
    """PL-BADGE-001 and XP-UDAAP-001-personal_loan on the pre-approved/
    guaranteed mock: findings inherit the rule's severity and surface its
    PRIMARY (first-listed) authority's url (rulebook/README.md)."""
    run = run_mock(
        "mock_pl_card_preapproved_guaranteed.html", rulebook, submissions_by_id, real_cells
    )
    for rid in ("PL-BADGE-001", "XP-UDAAP-001-personal_loan"):
        rule = rule_by_id(rulebook, rid)
        matches = findings_for_rule(run, rid)
        assert matches, rid
        assert any(f.severity == rule.severity for f in matches), rid
        assert any(f.citation_url == rule.authorities[0].url for f in matches), rid


def test_checkrun_records_versions_and_mode(rulebook, submissions_by_id, real_cells):
    """CheckRun must record the rulebook_version and offer_matrix_version it
    executed against, plus the submission's mode (CONTRACTS.md CheckRun)."""
    run = run_mock("mock_pl_card_compliant.html", rulebook, submissions_by_id, real_cells)
    assert run.rulebook_version == RULEBOOK_VERSION
    assert run.offer_matrix_version == OFFER_MATRIX_VERSION
    assert run.mode == SubmissionMode.PRE_PUBLICATION
    assert run.submission_id == EXPECTED["mock_pl_card_compliant.html"]["submission_id"]
    assert run.findings is not None  # contract default_factory list


def test_pre_publication_runs_emit_no_fidelity_findings(
    rulebook, submissions_by_id, real_cells
):
    for mock_name in VIOLATION_MOCKS + COMPLIANT_MOCKS:
        if EXPECTED[mock_name]["mode"] != "pre_publication":
            continue
        run = run_mock(mock_name, rulebook, submissions_by_id, real_cells)
        assert not [f for f in run.findings if f.check_class == CheckClass.FIDELITY], mock_name
