"""Metadata-plane checks: the state-availability rule (PL-STATE-EXCL-001)
decides from Submission.states_targeted + the referenced OfferCells ALONE —
no extracted claims are needed for it to fire.
"""

from __future__ import annotations

from backend.contracts import CheckClass, Severity

from conftest import (
    emitted_rule_ids,
    findings_for_rule,
    make_submission,
    rule_by_id,
)
from test_checker_primitives import run


def test_state_leak_fires_with_zero_claims(rulebook, real_cells):
    """IL targeted while the only referenced offer (PL-60-A) excludes IL
    (Illinois PLPA 36% all-in cap): the finding must appear even when the
    claims and disclosures lists are EMPTY."""
    sub = make_submission(offer_ids=["PL-60-A"], states_targeted="IL")
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-60-A"]],
        claims=[],
        disclosures=[],
        artifact_text="Lower payments with a 60-month term.",
    )
    rule = rule_by_id(rulebook, "PL-STATE-EXCL-001")
    matches = findings_for_rule(result, "PL-STATE-EXCL-001")
    assert matches, emitted_rule_ids(result)
    assert any(f.severity == rule.severity == Severity.HIGH for f in matches)
    # Reconciliation against the offer matrix is a truthfulness finding
    # (the class the answer key uses for matrix-contradiction rows).
    assert any(f.check_class == CheckClass.TRUTHFULNESS for f in matches)


def test_state_leak_uses_all_targeting_syntax(rulebook, real_cells):
    """Same leak via the manifest's 'ALL except IA;WV' syntax: IL is inside
    the expansion and PL-60-A excludes it. (The fixture set no longer ships a
    mock with this leak — the 60-month variant's targeting was re-synced to
    PL-60-A's states_excluded — so the fact pattern lives here, crafted.)"""
    sub = make_submission(offer_ids=["PL-60-A"], states_targeted="ALL except IA;WV")
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-60-A"]],
        claims=[],
        disclosures=[],
        artifact_text="Lower payments with a 60-month term.",
    )
    assert "PL-STATE-EXCL-001" in emitted_rule_ids(result)


def test_state_available_through_any_referenced_cell_is_not_a_leak(rulebook, real_cells):
    """AMENDED PER COORDINATOR ARBITRATION (authorized edit #2): a targeted
    state excluded by SOME but not ALL referenced cells is neither silence nor
    a full violation — it emits a needs_verification-style finding BELOW
    medium naming the ambiguity (one available cell may keep the placement
    honest for that state, but per-state offer routing is unverified). A full
    violation requires EVERY referenced cell to exclude the targeted state."""
    # Targeting mirrors the post-hygiene PL-36-A footprint (fully-excluded
    # capped states removed) so ONLY partially-excluded states (IL, MD, ME,
    # NC, NJ — excluded by PL-60-A, available via PL-36-A) remain targeted.
    sub = make_submission(
        offer_ids=["PL-36-A", "PL-60-A"],
        states_targeted="ALL except AR;DC;IA;MA;NE;NY;VT;WV",
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"], real_cells["PL-60-A"]],
        claims=[],
        disclosures=[],
        artifact_text="You're prequalified for up to $50,000.",
    )
    partial = [f for f in result.findings if f.rule_id == "PL-STATE-EXCL-001"]
    assert partial, "partial exclusion must not be silent"
    for f in partial:
        assert f.severity in (Severity.LOW, Severity.INFO), f
        assert "verif" in f.summary.lower() or "verif" in f.explanation.lower(), f


def test_targeting_fully_outside_exclusions_is_clean(rulebook, real_cells):
    sub = make_submission(offer_ids=["PL-60-A"], states_targeted="CA;TX")
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-60-A"]],
        claims=[],
        disclosures=[],
        artifact_text="Lower payments with a 60-month term.",
    )
    assert "PL-STATE-EXCL-001" not in emitted_rule_ids(result)
