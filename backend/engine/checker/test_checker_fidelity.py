"""Fidelity mode: verification-mode runs diff against the approved baseline;
drift surfaces as findings with check_class=fidelity and rule_id=None.
pre_publication runs ignore any baseline handed to them.

Two halves, because the fixture set carries only the positive one:

* POSITIVE (fixture-backed): seed_screenshot_pl_card_verified.html is a
  seed-account capture that MATCHES its approved baseline
  (mock_pl_card_compliant.html, SUB-2026-0142) — the 8.99% matrix floor and
  the full qualifier fine print are intact. The mode runs, the baseline join
  key is populated, and the correct answer is an empty fidelity list.

* NEGATIVE (synthetic): drift is constructed IN-TEST rather than from a
  fixture. The baseline's claims/disclosures are built from the approved
  creative, then a copy is perturbed one axis at a time — the floor claim's
  value_pct 8.99 -> 7.99, or a dropped approved disclosure — and the fidelity
  diff must emit findings for exactly those perturbations. An unperturbed
  control (same claims, same disclosures) must emit none, so the assertions
  are about the perturbation and not about the mode merely being on.

Building drift in-test rather than committing a drifted mock keeps the
fixture set at its rebalanced shape while still covering every branch of the
engine's fidelity diff: dropped disclosure, changed rate, and newly
introduced deterministic violation.
"""

from __future__ import annotations

import pytest

from backend.contracts import CheckClass, ClaimType, DisclosureType, SubmissionMode
from backend.engine.checker import run_checks

from conftest import (
    OFFER_MATRIX_VERSION,
    SEV_AT_LEAST_MEDIUM,
    claims_from_key,
    disclosures_from_manifest,
    emitted_rule_ids,
    make_claim,
    mock_text,
    run_mock,
)

VERIFIED = "seed_screenshot_pl_card_verified.html"
BASELINE = "mock_pl_card_compliant.html"

VERIFIED_SUB_ID = "SUB-2026-0151"
BASELINE_SUB_ID = "SUB-2026-0142"

# The approved creative's advertised floor, verbatim from the baseline mock
# ("APR as low as 8.99%") and equal to PL-36-A's apr_min in the offer matrix.
BASELINE_FLOOR_PCT = 8.99
# A perturbed floor that is ALSO matrix-invalid: 7.99 is neither PL-36-A's
# (8.99) nor PL-60-A's (11.49) apr_min, so PL-TRUTH-001's floor sub-check
# fails on it — that is what makes drift a new violation, not just a diff.
DRIFTED_FLOOR_PCT = 7.99


def fidelity_findings(run):
    return [f for f in run.findings if f.check_class == CheckClass.FIDELITY]


def floor_claim(value_pct: float):
    """The approved creative's floor-rate claim, at a given value."""
    return make_claim(
        f"APR as low as {value_pct}%",
        [ClaimType.RATE_OR_APR],
        {
            "value_pct": value_pct,
            "is_floor_claim": True,
            "labeled_as_apr": True,
            "rate_kind": "apr",
        },
        location="subheadline",
    )


@pytest.fixture()
def baseline_run(rulebook, submissions_by_id, real_cells):
    return run_mock(BASELINE, rulebook, submissions_by_id, real_cells)


@pytest.fixture()
def verified_run(rulebook, submissions_by_id, real_cells):
    # run_mock builds the approved-baseline CheckRun and passes it as
    # `baseline=` for verification-mode mocks (key: approved_baseline).
    return run_mock(VERIFIED, rulebook, submissions_by_id, real_cells)


@pytest.fixture()
def baseline_disclosures(submissions_by_id):
    """What the approved placement disclosed (SUB-2026-0142's manifest)."""
    return disclosures_from_manifest(submissions_by_id[BASELINE_SUB_ID])


@pytest.fixture()
def capture_run(rulebook, submissions_by_id, real_cells, baseline_disclosures):
    """Run the verification-mode capture against the approved baseline with
    caller-supplied claims/disclosures, so a test can perturb exactly one axis.

    Returns a callable(claims, disclosures, *, baseline=None, artifact_text=None).
    """

    sub = submissions_by_id[VERIFIED_SUB_ID]
    cells = [real_cells[oid] for oid in sub.offer_ids]

    def _run(claims, disclosures, *, baseline=None, artifact_text=None):
        assert sub.mode == SubmissionMode.VERIFICATION
        return run_checks(
            submission=sub,
            claims=claims,
            disclosures=disclosures,
            offer_cells=cells,
            offer_matrix_version=OFFER_MATRIX_VERSION,
            rulebook=rulebook,
            artifact_text=artifact_text if artifact_text is not None else mock_text(VERIFIED),
            baseline=baseline,
            baseline_claims=[floor_claim(BASELINE_FLOOR_PCT)],
            baseline_disclosures=baseline_disclosures,
        )

    return _run


# --------------------------------------------------------------------------- #
# Positive half: the verified capture matches its baseline
# --------------------------------------------------------------------------- #


def test_verified_capture_emits_no_fidelity_findings(verified_run):
    """The seed capture reproduces the approved baseline, so the fidelity diff
    must be empty — the answer key's expected_findings for it is []."""
    assert fidelity_findings(verified_run) == [], [
        (f.severity, f.summary) for f in fidelity_findings(verified_run)
    ]


def test_verified_run_records_verification_mode(verified_run):
    assert verified_run.mode == SubmissionMode.VERIFICATION
    assert verified_run.submission_id == VERIFIED_SUB_ID


def test_verified_capture_is_clean_deterministically_too(verified_run):
    """Not merely fidelity-clean: the capture raises no deterministic
    violation either, matching its empty answer-key entry. Same bar as the
    compliant mocks in the conformance suite — nothing at medium or above
    (info / needs-verification chatter is allowed)."""
    assert claims_from_key(VERIFIED) == []  # no planted claim-anchored findings
    offenders = [f for f in verified_run.findings if f.severity in SEV_AT_LEAST_MEDIUM]
    assert not offenders, [(f.rule_id, f.severity, f.summary) for f in offenders]


# --------------------------------------------------------------------------- #
# Negative half: synthetic drift (no drifted fixture on disk)
# --------------------------------------------------------------------------- #


def test_unperturbed_control_emits_no_fidelity_findings(capture_run, baseline_disclosures):
    """Control for the two perturbation tests: the same claims and the same
    disclosures as the baseline, run through the same fidelity diff, produce
    nothing. Anything the perturbed runs emit is therefore the perturbation."""
    run = capture_run([floor_claim(BASELINE_FLOOR_PCT)], list(baseline_disclosures))
    assert fidelity_findings(run) == [], [(f.summary) for f in fidelity_findings(run)]


def test_changed_rate_claim_emits_one_fidelity_finding(capture_run, baseline_disclosures):
    """Perturbation 1 — the advertised floor drifted 8.99% -> 7.99% after
    approval, disclosures untouched."""
    drifted = floor_claim(DRIFTED_FLOOR_PCT)
    run = capture_run(
        [drifted],
        list(baseline_disclosures),
        artifact_text=mock_text(VERIFIED).replace("8.99%", "7.99%"),
    )

    fid = fidelity_findings(run)
    assert len(fid) == 1, [(f.rule_id, f.summary) for f in fid]
    f = fid[0]
    assert f.rule_id is None  # fidelity is engine-level, not a rulebook rule
    assert f.claim_id == drifted.id  # anchored to the claim that moved
    assert "8.99" in f.summary and "7.99" in f.summary, f.summary
    # ...and nothing about the disclosures, which did not move
    assert "disclosure" not in f.summary.lower(), f.summary


def test_dropped_disclosure_emits_one_fidelity_finding(capture_run, baseline_disclosures):
    """Perturbation 2 — the approved not-guaranteed qualifier is gone from the
    live placement, rate claim untouched."""
    kept = [d for d in baseline_disclosures if d.disclosure_type != DisclosureType.NOT_GUARANTEED]
    assert len(kept) == len(baseline_disclosures) - 1  # the baseline really carried it

    run = capture_run([floor_claim(BASELINE_FLOOR_PCT)], kept)

    fid = fidelity_findings(run)
    assert len(fid) == 1, [(f.rule_id, f.summary) for f in fid]
    f = fid[0]
    assert f.rule_id is None
    assert DisclosureType.NOT_GUARANTEED.value in f.summary, f.summary
    # ...and nothing about the rate, which did not move
    assert "8.99" not in f.summary, f.summary


def test_both_perturbations_emit_both_fidelity_findings(capture_run, baseline_disclosures):
    """The two diffs are independent: perturb both axes at once and both
    findings appear (the shape the retired drift fixture used to assert)."""
    kept = [d for d in baseline_disclosures if d.disclosure_type != DisclosureType.NOT_GUARANTEED]
    run = capture_run(
        [floor_claim(DRIFTED_FLOOR_PCT)],
        kept,
        artifact_text=mock_text(VERIFIED).replace("8.99%", "7.99%"),
    )

    fid = fidelity_findings(run)
    assert len(fid) == 2, [(f.rule_id, f.summary) for f in fid]
    assert all(f.rule_id is None for f in fid), fid
    summaries = " | ".join(f.summary for f in fid)
    assert "7.99" in summaries
    assert DisclosureType.NOT_GUARANTEED.value in summaries


def test_drifted_values_still_hit_deterministic_rules(capture_run, baseline_disclosures):
    """The drifted 7.99% floor is not a valid matrix rate (PL-TRUTH-001): the
    ordinary deterministic engines keep firing alongside the fidelity diff,
    they are not suppressed by verification mode."""
    run = capture_run(
        [floor_claim(DRIFTED_FLOOR_PCT)],
        list(baseline_disclosures),
        artifact_text=mock_text(VERIFIED).replace("8.99%", "7.99%"),
    )
    assert "PL-TRUTH-001" in emitted_rule_ids(run), sorted(emitted_rule_ids(run))


def test_newly_introduced_violation_is_reported_as_drift(
    capture_run, baseline_disclosures, baseline_run
):
    """Third fidelity branch: handed the approved CheckRun as well, the engine
    diffs findings and reports each violation the approved version did NOT
    have. The baseline is clean, so the drift-induced PL-TRUTH-001 is new."""
    assert "PL-TRUTH-001" not in emitted_rule_ids(baseline_run)

    run = capture_run(
        [floor_claim(DRIFTED_FLOOR_PCT)],
        list(baseline_disclosures),
        baseline=baseline_run,
        artifact_text=mock_text(VERIFIED).replace("8.99%", "7.99%"),
    )

    drift = [f for f in fidelity_findings(run) if "PL-TRUTH-001" in f.explanation]
    assert drift, [(f.rule_id, f.summary) for f in fidelity_findings(run)]
    assert all(f.rule_id is None for f in fidelity_findings(run))


# --------------------------------------------------------------------------- #
# Mode guards (unchanged intent)
# --------------------------------------------------------------------------- #


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
    sub = submissions_by_id[BASELINE_SUB_ID]  # pre_publication
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


def test_pre_publication_mode_ignores_supplied_baseline_claims_and_drift(
    rulebook, submissions_by_id, real_cells, baseline_disclosures
):
    """Same guard against the claim/disclosure diff branches: a pre_publication
    submission handed a perturbed baseline still emits no fidelity findings."""
    sub = submissions_by_id[BASELINE_SUB_ID]
    cells = [real_cells[oid] for oid in sub.offer_ids]
    result = run_checks(
        submission=sub,
        claims=[floor_claim(DRIFTED_FLOOR_PCT)],
        disclosures=[
            d for d in baseline_disclosures if d.disclosure_type != DisclosureType.NOT_GUARANTEED
        ],
        offer_cells=cells,
        offer_matrix_version=OFFER_MATRIX_VERSION,
        rulebook=rulebook,
        artifact_text=mock_text(BASELINE),
        baseline_claims=[floor_claim(BASELINE_FLOOR_PCT)],
        baseline_disclosures=baseline_disclosures,
    )
    assert fidelity_findings(result) == []
    assert result.mode == SubmissionMode.PRE_PUBLICATION
