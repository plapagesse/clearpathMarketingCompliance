"""Regression suite for defects only the LIVE path could produce.

The conformance suite feeds the engine PERFECT-extractor inputs: claims typed
straight from the answer key, disclosures typed straight from the partner
manifest, and the mock's full text as artifact_text. Every one of those is a
fact the live pipeline has to earn from a vision model, and where it earned it
differently the engine mis-fired on creative that is certifiably compliant:

* a Reg Z companion sentence physically printed on the card, typed by the
  extractor as apr_qualifier / intro_adjacency / schumer_box_link rather than
  trigger_disclosure, read as a MISSING disclosure (CC-TRIG-001, high);
* "a 4% origination fee" carrying amount_value=4, reconciled against the
  cell's $2,000–$50,000 loan-amount range (PL-TRUTH-001, critical);
* proximity measured across a concatenation of claim and disclosure fragments
  when no artifact text was supplied, emitting full severity off distances
  that mean nothing (MTG-RATE-001, high);
* a soft-pull claim the extractor did capture as a claim, where the offline
  path had no claim at all and so only ever produced a sub-medium safety-net
  finding (XP-SOFT-007, medium).

These tests reproduce each shape with crafted inputs and pin the fix. Each
also keeps its negative half, so the fix cannot pass by silencing the rule.
"""

from __future__ import annotations

from backend.contracts import ClaimType, DisclosureType, Severity

from conftest import (
    emitted_rule_ids,
    findings_for_rule,
    make_claim,
    make_disclosure,
    make_submission,
)
from test_checker_primitives import run

# The Reg Z companion fine print as it is actually printed on the compliant
# credit-card mock (fixtures/mock_cc_card_compliant.html).
COMPANION_TEXT = (
    "After the 15-month intro period ends, a variable APR of 19.24%-29.24% applies, "
    "based on your creditworthiness and the Prime Rate."
)
CARD_FEE_TEXT = "$0 annual fee. Balance transfer fee: 3% of each transfer ($5 minimum)."

CARD_TEXT = (
    "ClearPath Platinum. 0% intro APR on purchases for 15 months. "
    f"{COMPANION_TEXT} {CARD_FEE_TEXT}"
)


def card_submission():
    return make_submission(
        submission_id="SUB-LIVE-CC",
        product="credit_card",
        offer_ids=["CC-PLAT"],
        states_targeted="ALL",
    )


def intro_claim():
    return make_claim(
        "0% intro APR on purchases for 15 months",
        [ClaimType.TRIGGERING_TERM, ClaimType.PROMOTIONAL_OR_INTRODUCTORY],
        {"promo_rate_pct": 0.0, "promo_period_months": 15},
    )


# --------------------------------------------------------------------------- #
# Defect A — disclosure typed by legal function, not by the extractor's label
# --------------------------------------------------------------------------- #


def test_companion_terms_present_but_mistyped_still_satisfy_the_trigger(rulebook, real_cells):
    """The exact live shape: the companion sentence IS on the creative, but the
    vision model filed it under neighbouring types. Deriving the type from the
    disclosure's own text must clear CC-TRIG-001."""
    result = run(
        rulebook,
        submission=card_submission(),
        cells=[real_cells["CC-PLAT"]],
        claims=[intro_claim()],
        disclosures=[
            make_disclosure(DisclosureType.APR_QUALIFIER, COMPANION_TEXT),
            make_disclosure(DisclosureType.SCHUMER_BOX_LINK, CARD_FEE_TEXT),
        ],
        artifact_text=CARD_TEXT,
    )
    assert "CC-TRIG-001" not in emitted_rule_ids(result), [
        (f.rule_id, f.severity, f.summary) for f in result.findings
    ]


def test_trigger_still_fires_when_no_companion_terms_are_anywhere(rulebook, real_cells):
    """Negative half: derivation must not be a blanket amnesty. A creative that
    really carries no companion terms still fails at the rule's full severity."""
    result = run(
        rulebook,
        submission=card_submission(),
        cells=[real_cells["CC-PLAT"]],
        claims=[intro_claim()],
        disclosures=[
            make_disclosure(
                DisclosureType.APR_QUALIFIER, "Lowest rate requires excellent credit."
            )
        ],
        artifact_text="ClearPath Platinum. 0% intro APR on purchases for 15 months.",
    )
    matches = findings_for_rule(result, "CC-TRIG-001")
    assert matches, sorted(emitted_rule_ids(result))
    assert any(f.severity == Severity.HIGH for f in matches), [
        (f.severity, f.summary) for f in matches
    ]


def test_missing_companion_finding_reads_as_plain_english(rulebook, real_cells):
    result = run(
        rulebook,
        submission=card_submission(),
        cells=[real_cells["CC-PLAT"]],
        claims=[intro_claim()],
        disclosures=[],
        artifact_text="ClearPath Platinum. 0% intro APR on purchases for 15 months.",
    )
    f = findings_for_rule(result, "CC-TRIG-001")[0]
    assert f.summary == (
        "Rate or fee claim shown without the required companion terms "
        "(APR, variable-rate statement, fees)"
    )
    assert f.suggested_redline == "Add the companion terms near the rate or fee claim."
    for banned in ("trigger_disclosure", "claim_plane", "text_plane", "DisclosureType"):
        assert banned not in f.summary and banned not in (f.suggested_redline or "")


# --------------------------------------------------------------------------- #
# Defect B — a percentage fee is not a loan amount
# --------------------------------------------------------------------------- #


def loan_submission():
    return make_submission(submission_id="SUB-LIVE-PL", offer_ids=["PL-36-A"])


def test_percentage_fee_is_not_reconciled_against_the_loan_amount_range(
    rulebook, real_cells
):
    """'The example includes a 4% origination fee' is a fee claim. Comparing its
    4 against the cell's $2,000-$50,000 loan amounts made a compliant mock look
    like a critical truthfulness defect."""
    result = run(
        rulebook,
        submission=loan_submission(),
        cells=[real_cells["PL-36-A"]],
        claims=[
            make_claim(
                "The example includes a 4% origination fee.",
                [ClaimType.FEE_OR_COST],
                {"fee_type": "origination_fee", "amount_value": 4},
            )
        ],
        disclosures=[make_disclosure(DisclosureType.TRIGGER_DISCLOSURE, "36 monthly payments")],
        artifact_text="The example includes a 4% origination fee.",
    )
    assert "PL-TRUTH-001" not in emitted_rule_ids(result), [
        (f.rule_id, f.severity, f.summary) for f in result.findings
    ]


def test_advertised_loan_amount_outside_the_range_still_fires(rulebook, real_cells):
    """Negative half: an amount advertised as a Reg Z triggering term is still
    reconciled — the scoping narrows which claims state a loan amount, it does
    not retire the check.

    Note the claim shape this requires. `amount_value` lives only in the
    fee_or_cost payload (backend/contracts.py), so the sub-check now fires on
    exactly one thing: a statement that is BOTH a Reg Z triggering term AND
    carries a stated sum — an advertised borrow amount. A pure fee claim, which
    is what "a 4% origination fee" is, no longer reaches it."""
    result = run(
        rulebook,
        submission=loan_submission(),
        cells=[real_cells["PL-36-A"]],
        claims=[
            make_claim(
                "Borrow $80,000 for just $999/mo",
                [ClaimType.TRIGGERING_TERM, ClaimType.FEE_OR_COST],
                {"amount_value": 80000},
            )
        ],
        disclosures=[make_disclosure(DisclosureType.TRIGGER_DISCLOSURE, "36 monthly payments")],
        artifact_text="Borrow $80,000 for just $999/mo.",
    )
    matches = findings_for_rule(result, "PL-TRUTH-001")
    assert matches, sorted(emitted_rule_ids(result))
    assert any(f.severity == Severity.CRITICAL for f in matches)
    f = matches[0]
    assert "advertised amount of 80000" in f.summary, f.summary
    assert "claim_field" not in f.summary and "amount_value" not in f.summary


# --------------------------------------------------------------------------- #
# Defect C — proximity findings are never full severity without artifact text
# --------------------------------------------------------------------------- #


def mortgage_submission():
    return make_submission(
        submission_id="SUB-LIVE-MTG",
        product="mortgage_prequal",
        offer_ids=["MTG-ARM"],
        states_targeted="ALL",
    )


def arm_claims():
    return [
        make_claim(
            "5/6 ARM - 6.250% rate, 6.410% APR",
            [ClaimType.RATE_OR_APR],
            {
                "value_pct": 6.41,
                "is_floor_claim": False,
                "labeled_as_apr": True,
                "rate_kind": "apr",
            },
        )
    ]


def test_proximity_without_artifact_text_is_demoted_to_needs_verification(
    rulebook, real_cells
):
    """Concatenating claim and disclosure fragments invents distances. Whatever
    proximity concludes from that may never carry the rule's full severity."""
    result = run(
        rulebook,
        submission=mortgage_submission(),
        cells=[real_cells["MTG-ARM"]],
        claims=arm_claims(),
        disclosures=[
            make_disclosure(DisclosureType.NMLS_ID, "ClearPath Financial, NMLS #1902441"),
            make_disclosure(
                DisclosureType.TAXES_INSURANCE,
                "the estimate excludes taxes and insurance, so your actual payment will be higher",
            ),
        ],
        artifact_text=None,
    )
    proximity = findings_for_rule(result, "MTG-RATE-001")
    for f in proximity:
        assert f.severity == Severity.LOW, (f.severity, f.summary)
        assert "Needs verification" in f.summary, f.summary
        assert "layout" in f.explanation.lower(), f.explanation
    assert not [f for f in result.findings if f.severity in (Severity.HIGH, Severity.CRITICAL)], [
        (f.rule_id, f.severity, f.summary) for f in result.findings
    ]


def test_proximity_with_artifact_text_keeps_full_severity(rulebook, real_cells):
    """Negative half: given the creative's real text, a genuinely unlabeled rate
    still fails at the rule's own severity."""
    result = run(
        rulebook,
        submission=mortgage_submission(),
        cells=[real_cells["MTG-ARM"]],
        claims=arm_claims(),
        disclosures=[],
        artifact_text=(
            "5/6 ARM at 6.250%. " + "Filler copy about our friendly loan officers. " * 6
            + "The annual percentage rate is disclosed in the loan estimate."
        ),
    )
    matches = findings_for_rule(result, "MTG-RATE-001")
    assert matches, sorted(emitted_rule_ids(result))
    assert any(f.severity == Severity.HIGH for f in matches), [
        (f.severity, f.summary) for f in matches
    ]


# --------------------------------------------------------------------------- #
# Defect E — soft-pull verification resolves from the partner registry
# --------------------------------------------------------------------------- #


def soft_pull_claim():
    return make_claim(
        "Check your rate in minutes - won't affect your credit score",
        [ClaimType.GENERAL_UDAAP_REPRESENTATION],
        {},
    )


def _soft_pull_run(rulebook, real_cells, partner: str):
    return run(
        rulebook,
        submission=make_submission(
            submission_id="SUB-LIVE-SOFT", partner=partner, offer_ids=["PL-36-A"]
        ),
        cells=[real_cells["PL-36-A"]],
        claims=[soft_pull_claim()],
        disclosures=[make_disclosure(DisclosureType.SOFT_PULL, "uses a soft credit pull")],
        artifact_text="Check your rate in minutes - won't affect your credit score.",
    )


def test_soft_pull_claim_clears_for_a_partner_with_a_verified_integration(
    rulebook, real_cells
):
    result = _soft_pull_run(rulebook, real_cells, "nerdwallet")
    assert "XP-SOFT-007-personal_loan" not in emitted_rule_ids(result), [
        (f.rule_id, f.severity, f.summary) for f in result.findings
    ]


def test_soft_pull_claim_still_needs_verification_for_an_unregistered_partner(
    rulebook, real_cells
):
    """Negative half: the registry answers for partners whose flow has been
    walked. Anyone else is exactly the case the rule exists to catch."""
    result = _soft_pull_run(rulebook, real_cells, "brand_new_partner")
    matches = findings_for_rule(result, "XP-SOFT-007-personal_loan")
    assert matches, sorted(emitted_rule_ids(result))
    f = matches[0]
    assert f.severity == Severity.MEDIUM, f
    assert f.summary == (
        "\"won't affect your credit score\" is unverified: the partner flow hasn't been "
        "confirmed as soft-pull only"
    ), f.summary
    assert "soft_pull_verified" not in f.summary and "condition_field" not in f.summary
