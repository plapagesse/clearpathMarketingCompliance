"""Fidelity mode: verification-mode runs diff against the approved baseline
CheckRun; drift surfaces as findings with check_class=fidelity and
rule_id=None. pre_publication runs ignore any baseline handed to them.

Fact pattern (fixtures): seed_screenshot_pl_card_drift.html vs its approved
baseline mock_pl_card_compliant.html — the APR floor drifted 8.99 -> 7.99
(also matrix-invalid) and the approved qualifier fine-print paragraph was
dropped. The answer key plants TWO fidelity findings for it.
"""

from __future__ import annotations

import pytest

from backend.contracts import CheckClass, SubmissionMode

from conftest import (
    emitted_rule_ids,
    run_mock,
)

DRIFT = "seed_screenshot_pl_card_drift.html"
BASELINE = "mock_pl_card_compliant.html"


@pytest.fixture()
def baseline_run(rulebook, submissions_by_id, real_cells):
    return run_mock(BASELINE, rulebook, submissions_by_id, real_cells)


@pytest.fixture()
def drift_run(rulebook, submissions_by_id, real_cells):
    # run_mock builds the approved-baseline CheckRun and passes it as
    # `baseline=` for verification-mode mocks (key: approved_baseline).
    return run_mock(DRIFT, rulebook, submissions_by_id, real_cells)


def fidelity_findings(run):
    return [f for f in run.findings if f.check_class == CheckClass.FIDELITY]


def test_drift_emits_fidelity_findings_with_null_rule_id(drift_run):
    fid = fidelity_findings(drift_run)
    # Two independent drifts are planted: the changed APR floor and the
    # dropped qualifier disclosures (fixtures/expected_findings.json).
    assert len(fid) >= 2, [(f.rule_id, f.summary) for f in drift_run.findings]
    assert all(f.rule_id is None for f in fid), fid


def test_drift_run_records_verification_mode(drift_run):
    assert drift_run.mode == SubmissionMode.VERIFICATION
    assert drift_run.submission_id == "SUB-2026-0151"


def test_drifted_values_still_hit_deterministic_rules(drift_run):
    """The drifted 7.99% floor is not a valid matrix rate (PL-TRUTH-001) and
    the payment example lost its companion disclosures (PL-TRIG-001) — the
    ordinary deterministic engines keep firing alongside the fidelity diff."""
    ids = emitted_rule_ids(drift_run)
    assert "PL-TRUTH-001" in ids
    assert "PL-TRIG-001" in ids


def test_baseline_run_itself_has_no_fidelity_findings(baseline_run):
    # The approved baseline is a pre_publication run with no baseline of its
    # own — nothing to diff.
    assert fidelity_findings(baseline_run) == []
    assert baseline_run.mode == SubmissionMode.PRE_PUBLICATION


def test_pre_publication_mode_ignores_a_supplied_baseline(
    rulebook, submissions_by_id, real_cells, baseline_run
):
    """Handing a baseline to a pre_publication submission must not produce
    fidelity findings: fidelity is a verification-mode behavior."""
    from conftest import (
        OFFER_MATRIX_VERSION,
        claims_from_key,
        disclosures_from_manifest,
        mock_text,
    )
    from backend.engine.checker import run_checks

    sub = submissions_by_id["SUB-2026-0142"]  # pre_publication
    cells = [real_cells[oid] for oid in sub.offer_ids]
    result = run_checks(
        submission=sub,
        claims=claims_from_key(BASELINE),
        disclosures=disclosures_from_manifest(sub),
        offer_cells=cells,
        offer_matrix_version=OFFER_MATRIX_VERSION,
        rulebook=rulebook,
        artifact_text=mock_text(BASELINE),
        baseline=baseline_run,
    )
    assert fidelity_findings(result) == []
    assert result.mode == SubmissionMode.PRE_PUBLICATION
