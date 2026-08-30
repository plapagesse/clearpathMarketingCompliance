"""Answer-key conformance: run_checks over every fixture mock with
perfect-extractor inputs (key-derived claims, manifest-derived disclosures,
referenced offer cells, flattened artifact text).

Ground truth: fixtures/expected_findings.json (rulebook v2026.08.3 rule_ids).

- Violation mocks: emitted rule_ids must be a SUPERSET of the mock's expected
  deterministic, non-fidelity rule_ids (llm_judged ids like PL-JUDGE-001 /
  CC-JUDGE-001 are excluded by check_kind lookup in the rulebook; fidelity
  entries are asserted in test_checker_fidelity.py because the engine emits
  those with rule_id=None).
- Judgment-only violation mocks (every planted rule_id is llm_judged — the
  net-impression credit-card mock): the deterministic engine has nothing to
  prove there, so they are parametrized into the inverse guard instead —
  running them must stay CLEAN at severity medium and above.
- Compliant mocks: zero deterministic violations — nothing at severity
  medium or above (info/needs-verification chatter is allowed).
"""

from __future__ import annotations

import pytest

from backend.contracts import CheckClass, ClaimType, DisclosureType, SubmissionMode

from conftest import (
    COMPLIANT_MOCKS,
    DETERMINISTIC_VIOLATION_MOCKS,
    EXPECTED,
    JUDGMENT_ONLY_MOCKS,
    OFFER_MATRIX_VERSION,
    RULEBOOK_VERSION,
    SEV_AT_LEAST_MEDIUM,
    VIOLATION_MOCKS,
    assert_engine_invariants,
    deterministic_ids,
    emitted_rule_ids,
    expected_deterministic_ids,
    expected_rule_ids,
    findings_for_rule,
    llm_judged_ids,
    rule_by_id,
    run_mock,
)


def test_violation_mocks_split_by_check_kind_covers_the_key():
    """The two parametrizations below must together cover every violation mock
    in the answer key, and neither may be empty — a fixture rebalance that
    drops the judgment-only case would otherwise silently retire the inverse
    guard, and one that drops every deterministic case would retire the
    superset check."""
    assert set(DETERMINISTIC_VIOLATION_MOCKS) | set(JUDGMENT_ONLY_MOCKS) == set(VIOLATION_MOCKS)
    assert not (set(DETERMINISTIC_VIOLATION_MOCKS) & set(JUDGMENT_ONLY_MOCKS))
    assert DETERMINISTIC_VIOLATION_MOCKS
    assert JUDGMENT_ONLY_MOCKS


@pytest.mark.parametrize("mock_name", DETERMINISTIC_VIOLATION_MOCKS)
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


@pytest.mark.parametrize("mock_name", JUDGMENT_ONLY_MOCKS)
def test_judgment_only_mock_is_deterministically_clean(
    mock_name, rulebook, submissions_by_id, real_cells
):
    """Inverse guard for mocks whose every planted violation is llm_judged.

    The key plants a gray-area call the judge owns (CC-JUDGE-001 on the
    net-impression mock) and nothing else, which is a positive claim about the
    DETERMINISTIC engine too: every deterministic defect of the earlier draft
    was fixed in the creative, so a run must be as silent as it is on a
    certified-compliant mock. This is the assertion the superset check cannot
    make on such a mock (its expected deterministic set is empty)."""
    assert not (expected_rule_ids(mock_name) & deterministic_ids(rulebook)), (
        f"{mock_name} is no longer judgment-only — it belongs in the superset test"
    )
    assert expected_rule_ids(mock_name) <= llm_judged_ids(rulebook), mock_name

    run = run_mock(mock_name, rulebook, submissions_by_id, real_cells)
    assert_engine_invariants(run, rulebook)
    offenders = [f for f in run.findings if f.severity in SEV_AT_LEAST_MEDIUM]
    assert not offenders, (
        f"{mock_name} plants only llm_judged violations, so the deterministic "
        f"engine must stay clean, but it raised: "
        f"{[(f.rule_id, f.severity, f.summary) for f in offenders]}"
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


# Vocabulary that belongs to the engine, the contracts or the rulebook schema —
# never to a sentence a compliance officer reads. Enum values are generated from
# the contracts so a new type cannot slip past this guard.
_ENGINE_JARGON = (
    "text_plane", "text-plane", "claim_plane", "claim-plane", "decision_inputs",
    "check_type", "claim_field", "matrix_field", "condition_field", "claim_types_any",
    "claim_filter", "normalized_fields", "safety_net", "safety-net detection",
    "DisclosureType", "ClaimType", "artifact_text", "violates_when", "detection_ref",
    "anchor_patterns", "companion_patterns", "required_disclosure_types",
)
_ENUM_TOKENS = tuple(
    v for v in
    [d.value for d in DisclosureType] + [c.value for c in ClaimType]
    if "_" in v
)


@pytest.mark.parametrize("mock_name", sorted(EXPECTED))
def test_findings_never_speak_engine_jargon(mock_name, rulebook, submissions_by_id, real_cells):
    """Every finding is read by a compliance officer, not an engineer.

    The summary and the suggested redline are the two fields the reviewer acts
    on, so neither may contain an enum value, a rulebook parameter name or an
    engine-internal term; the explanation may carry a rule id (it is audit
    trail) but still never a decision-plane term. Rule ids and citations render
    as chips beside the finding, so repeating them in the prose is noise.
    """
    run = run_mock(mock_name, rulebook, submissions_by_id, real_cells)
    for f in run.findings:
        for field_name in ("summary", "suggested_redline"):
            text = getattr(f, field_name) or ""
            for token in _ENGINE_JARGON + _ENUM_TOKENS:
                assert token not in text, f"{mock_name} {f.rule_id} {field_name}: {text!r}"
            assert f.rule_id is None or f.rule_id not in text, (
                f"{mock_name}: rule id repeated in {field_name} — the UI renders it as a chip"
            )
        for token in _ENGINE_JARGON:
            assert token not in f.explanation, f"{mock_name} {f.rule_id}: {f.explanation!r}"
        assert f.summary.strip()


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
