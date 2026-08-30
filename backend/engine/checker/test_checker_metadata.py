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
    # The answer key classes this reconciliation against the offer matrix as
    # a truthfulness finding (mock_pl_card_il_leak.html entry).
    assert any(f.check_class == CheckClass.TRUTHFULNESS for f in matches)


def test_state_leak_uses_all_targeting_syntax(rulebook, real_cells):
    """Same leak via the manifest's 'ALL except IA;WV' syntax (the
    mock_pl_card_il_leak fact pattern): IL is inside the expansion and
    PL-60-A excludes it."""
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
    """Answer-key-derived semantics: SUB-2026-0142 (certified compliant,
    zero expected findings) targets 'ALL except IA;WV' — which includes IL —
    while referencing BOTH PL-36-A (available in IL) and PL-60-A (IL
    excluded). Therefore a targeted state counts as a leak only when EVERY
    referenced offer cell excludes it; one available cell keeps the
    placement honest for that state."""
    sub = make_submission(
        offer_ids=["PL-36-A", "PL-60-A"], states_targeted="ALL except IA;WV"
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"], real_cells["PL-60-A"]],
        claims=[],
        disclosures=[],
        artifact_text="You're prequalified for up to $50,000.",
    )
    assert "PL-STATE-EXCL-001" not in emitted_rule_ids(result)


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
