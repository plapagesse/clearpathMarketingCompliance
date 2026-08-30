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


def test_coverage_marker_says_what_happened_in_plain_english(rulebook, real_cells):
    """The marker is what a reviewer sees when a run degraded, so it has to say
    what was and was not checked — not name the engine's internal planes."""
    sub = make_submission(offer_ids=["PL-36-A"])
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[guaranteed_claim()],
        artifact_text=None,
    )
    f = coverage_markers(result)[0]
    assert f.summary == (
        "Layout and spacing checks ran on extracted text only for this run; "
        "proximity and required-element results may be incomplete"
    )
    for jargon in ("text_plane", "text-plane", "claim_plane", "artifact text", "Token-bound"):
        assert jargon not in f.summary and jargon not in f.explanation


def test_empty_artifact_text_degrades_like_a_missing_one(rulebook, real_cells):
    """A model that returns a blank transcription must not read as coverage."""
    sub = make_submission(offer_ids=["PL-36-A"])
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[guaranteed_claim()],
        artifact_text="   ",
    )
    assert coverage_markers(result)


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


def test_degraded_required_element_absence_demotes_to_needs_verification(rulebook, real_cells):
    """Concluding a required element is ABSENT from concatenated fragments is
    exactly as unsupported as a proximity distance measured on them, so in
    degraded mode element_required never carries the rule's full severity
    (MTG-NMLS-001 is HIGH with full text; here it must flag LOW)."""
    sub = make_submission(
        product="mortgage_prequal", surface="mortgage_rate_table",
        template_id="CK-MTG-TABLE", offer_ids=["MTG-30F"],
        states_targeted="ALL except AK;HI;NY;VT;WV",
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["MTG-30F"]],
        artifact_text=None,
    )
    matches = [f for f in result.findings if f.rule_id == "MTG-NMLS-001"]
    assert matches
    for f in matches:
        assert f.severity == Severity.LOW
        assert f.summary.startswith("Needs verification:")
        assert "could not confirm" in f.summary
