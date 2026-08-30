"""Text-plane degradation: artifact_text=None puts the engine in degraded
mode — token/text_plane rules fall back to scanning the extracted claim/
disclosure texts, and the run carries exactly the pinned marker: one
info-severity coverage finding with rule_id=None flagging that raw-text
coverage was unavailable.
"""

from __future__ import annotations

from backend.contracts import ClaimType, Severity

from conftest import emitted_rule_ids, make_claim, make_submission
from test_checker_primitives import run


def guaranteed_claim():
    return make_claim(
        "Guaranteed approval — regardless of credit history",
        [ClaimType.APPROVAL_OR_PREQUALIFICATION],
        {},
    )


def coverage_markers(result):
    return [
        f
        for f in result.findings
        if f.rule_id is None and f.severity == Severity.INFO
    ]


def test_missing_artifact_text_emits_info_coverage_finding(rulebook, real_cells):
    sub = make_submission(offer_ids=["PL-36-A"])
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[guaranteed_claim()],
        artifact_text=None,
    )
    assert coverage_markers(result), [
        (f.rule_id, f.severity, f.summary) for f in result.findings
    ]


def test_claim_plane_rules_still_decide_without_artifact_text(rulebook, real_cells):
    # Degraded scan runs over claim/disclosure texts; the claim-plane
    # decision for a prohibited phrase needs no raw artifact at all.
    sub = make_submission(offer_ids=["PL-36-A"])
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[guaranteed_claim()],
        artifact_text=None,
    )
    assert "XP-UDAAP-001-personal_loan" in emitted_rule_ids(result)


def test_no_coverage_marker_when_artifact_text_is_supplied(rulebook, real_cells):
    sub = make_submission(offer_ids=["PL-36-A"])
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[guaranteed_claim()],
        artifact_text="Guaranteed approval — regardless of credit history. Claim my loan.",
    )
    assert not coverage_markers(result)
