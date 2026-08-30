"""Per-primitive unit tests for the 8 deterministic primitives
(rulebook/README.md "Deterministic primitives — the Stage-4 checker
implementation spec"), exercised through real rulebook rules on crafted
claims/disclosures/offer cells/artifact text.
"""

from __future__ import annotations

from backend.contracts import CheckClass, ClaimType, DisclosureType, Severity

from conftest import (
    OFFER_MATRIX_VERSION,
    SEV_AT_LEAST_MEDIUM,
    emitted_rule_ids,
    findings_for_rule,
    make_cell,
    make_claim,
    make_disclosure,
    make_submission,
    rule_by_id,
)
from backend.engine.checker import run_checks


def run(rulebook, *, submission, cells, claims=(), disclosures=(), artifact_text=None,
        baseline=None):
    return run_checks(
        submission=submission,
        claims=list(claims),
        disclosures=list(disclosures),
        offer_cells=list(cells),
        offer_matrix_version=OFFER_MATRIX_VERSION,
        rulebook=rulebook,
        artifact_text=artifact_text,
        baseline=baseline,
    )


def payment_claim():
    return make_claim(
        "Borrow $10,000 for just $299/mo",
        [ClaimType.TRIGGERING_TERM],
        {"payment_amount": 299},
    )


# --------------------------------------------------------------------------- #
# trigger_requires_disclosures (PL-TRIG-001, XP-PREQ-002)
# --------------------------------------------------------------------------- #


def test_trigger_term_without_companion_disclosure_fires(rulebook, real_cells):
    sub = make_submission(offer_ids=["PL-36-A"])
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[payment_claim()],
        artifact_text="Borrow $10,000 for just $299/mo. Check my rate.",
    )
    rule = rule_by_id(rulebook, "PL-TRIG-001")
    matches = findings_for_rule(result, "PL-TRIG-001")
    assert matches
    assert any(
        f.severity == rule.severity and f.check_class == CheckClass.LEGALITY for f in matches
    )
    assert any(f.citation_url == rule.authorities[0].url for f in matches)


def test_trigger_term_with_companion_disclosure_is_clean(rulebook, real_cells):
    sub = make_submission(offer_ids=["PL-36-A"])
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[payment_claim()],
        disclosures=[
            make_disclosure(
                DisclosureType.TRIGGER_DISCLOSURE,
                "36 monthly payments; 13.49% APR; no downpayment is required",
            )
        ],
        artifact_text="Borrow $10,000 for just $299/mo. Check my rate.",
    )
    assert "PL-TRIG-001" not in emitted_rule_ids(result)


def test_no_downpayment_required_is_not_a_trigger(rulebook, real_cells):
    """rulebook/claim_types_legal_map.json triggering_term negative example:
    'No downpayment required' does NOT trigger (official interpretation of
    1026.24(d)(1) — absence statements only trigger when a downpayment is
    actually required). A perfect extractor types the span as a residual
    UDAAP representation; the checker must not raise a PL-TRIG-001 violation
    even though the raw safety-net pattern '\\bdown\\s?payment\\b' matches."""
    sub = make_submission(offer_ids=["PL-36-A"])
    claim = make_claim(
        "No downpayment required",
        [ClaimType.GENERAL_UDAAP_REPRESENTATION],
        {"representation_kind": "other"},
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[claim],
        artifact_text="No downpayment required. Check my rate.",
    )
    offenders = [
        f
        for f in findings_for_rule(result, "PL-TRIG-001")
        if f.severity in SEV_AT_LEAST_MEDIUM
    ]
    assert not offenders, offenders


def test_prequalified_requires_not_guaranteed_qualifier(rulebook, real_cells):
    sub = make_submission(offer_ids=["PL-36-A"])
    claim = make_claim(
        "You're prequalified for up to $50,000",
        [ClaimType.APPROVAL_OR_PREQUALIFICATION],
        {"badge_word": "prequalified", "strength": "prequalified"},
    )
    kwargs = dict(
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[claim],
        artifact_text="You're prequalified for up to $50,000. Check my rate.",
    )
    missing = run(rulebook, **kwargs)
    assert "XP-PREQ-002-personal_loan" in emitted_rule_ids(missing)

    kwargs["disclosures"] = [
        make_disclosure(DisclosureType.NOT_GUARANTEED, "Prequalification is not a guarantee of approval.")
    ]
    cured = run(rulebook, **kwargs)
    assert "XP-PREQ-002-personal_loan" not in emitted_rule_ids(cured)


# --------------------------------------------------------------------------- #
# phrase_prohibited (XP-UDAAP-001, MTG-COUNSEL-001)
# --------------------------------------------------------------------------- #


def test_prohibited_phrase_on_claim_fires_with_rule_metadata(rulebook, real_cells):
    sub = make_submission(offer_ids=["PL-36-A"])
    claim = make_claim(
        "Guaranteed approval — regardless of credit history",
        [ClaimType.APPROVAL_OR_PREQUALIFICATION],
        {"badge_word": "guaranteed approval", "strength": "guaranteed"},
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[claim],
        artifact_text="Guaranteed approval — regardless of credit history. Claim my loan.",
    )
    rule = rule_by_id(rulebook, "XP-UDAAP-001-personal_loan")
    matches = findings_for_rule(result, "XP-UDAAP-001-personal_loan")
    assert matches
    assert any(f.severity == rule.severity == Severity.CRITICAL for f in matches)
    assert any(f.citation_url == rule.authorities[0].url for f in matches)


def test_prohibited_phrase_matching_is_case_insensitive(rulebook, real_cells):
    sub = make_submission(offer_ids=["PL-36-A"])
    claim = make_claim(
        "GUARANTEED APPROVAL FOR EVERY APPLICANT",
        [ClaimType.APPROVAL_OR_PREQUALIFICATION],
        {"badge_word": "GUARANTEED APPROVAL", "strength": "guaranteed"},
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[claim],
        artifact_text="GUARANTEED APPROVAL FOR EVERY APPLICANT",
    )
    assert "XP-UDAAP-001-personal_loan" in emitted_rule_ids(result)


def test_counselor_phrase_fires_on_mortgage_text(rulebook, real_cells):
    sub = make_submission(
        product="mortgage_prequal", surface="mortgage_rate_table",
        template_id="CK-MTG-TABLE", offer_ids=["MTG-30F"],
        states_targeted="ALL except AK;HI;NY;VT;WV",
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["MTG-30F"]],
        artifact_text="Talk to a ClearPath counselor about your options today. NMLS #1902441.",
    )
    assert "MTG-COUNSEL-001" in emitted_rule_ids(result)


def test_phrase_matching_never_implies_plurals(rulebook, real_cells):
    """rulebook/README.md normalization spec: matching is word-boundary
    aware and 'pluralization is never implied' — the lexicon phrase
    'counselor' must NOT match 'counselors'."""
    sub = make_submission(
        product="mortgage_prequal", surface="mortgage_rate_table",
        template_id="CK-MTG-TABLE", offer_ids=["MTG-30F"],
        states_targeted="ALL except AK;HI;NY;VT;WV",
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["MTG-30F"]],
        artifact_text="Our loan counselors are ready to help. NMLS #1902441.",
    )
    assert "MTG-COUNSEL-001" not in emitted_rule_ids(result)


# --------------------------------------------------------------------------- #
# phrase_conditional (PL-BADGE-001, MTG-FIXED-001)
# --------------------------------------------------------------------------- #


def preapproved_claim():
    return make_claim(
        "You're pre-approved for up to $50,000",
        [ClaimType.APPROVAL_OR_PREQUALIFICATION],
        {"badge_word": "pre-approved", "strength": "pre_approved"},
    )


def test_preapproved_fires_when_offer_is_not_a_firm_offer(rulebook, real_cells):
    # PL-36-A: is_firm_offer FALSE, badge_designation_allowed 'prequalified'.
    sub = make_submission(offer_ids=["PL-36-A"], badge_text="Pre-approved")
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[preapproved_claim()],
        artifact_text="You're pre-approved for up to $50,000.",
    )
    rule = rule_by_id(rulebook, "PL-BADGE-001")
    matches = findings_for_rule(result, "PL-BADGE-001")
    assert matches
    assert any(f.severity == rule.severity == Severity.CRITICAL for f in matches)


def test_preapproved_allowed_on_true_firm_offer(rulebook):
    firm = make_cell(
        offer_id="PL-FIRM",
        is_firm_offer=True,
        badge_designation_allowed="pre-approved",
    )
    sub = make_submission(offer_ids=["PL-FIRM"], badge_text="Pre-approved")
    result = run(
        rulebook,
        submission=sub,
        cells=[firm],
        claims=[preapproved_claim()],
        artifact_text="You're pre-approved for up to $50,000.",
    )
    assert "PL-BADGE-001" not in emitted_rule_ids(result)


def test_fixed_wording_fires_against_variable_mortgage_cell(rulebook, real_cells):
    sub = make_submission(
        product="mortgage_prequal", surface="mortgage_rate_module",
        template_id="CK-MTG-TABLE", offer_ids=["MTG-ARM"],
        states_targeted="ALL except AK;HI;NY;VT;WV",
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["MTG-ARM"]],
        artifact_text="Lock in a fixed low rate of 6.250% APR today. NMLS #1902441.",
    )
    rule = rule_by_id(rulebook, "MTG-FIXED-001")
    matches = findings_for_rule(result, "MTG-FIXED-001")
    assert matches
    assert any(f.severity == rule.severity == Severity.CRITICAL for f in matches)


def test_fixed_wording_allowed_against_fixed_rate_cell(rulebook, real_cells):
    # MTG-30F: apr_type 'fixed' — condition apr_type == 'variable' not met.
    sub = make_submission(
        product="mortgage_prequal", surface="mortgage_rate_module",
        template_id="CK-MTG-TABLE", offer_ids=["MTG-30F"],
        states_targeted="ALL except AK;HI;NY;VT;WV",
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["MTG-30F"]],
        artifact_text="Lock in a fixed low rate of 6.625% APR today. NMLS #1902441.",
    )
    assert "MTG-FIXED-001" not in emitted_rule_ids(result)


# --------------------------------------------------------------------------- #
# proximity_required (CC-INTRO-001)
# --------------------------------------------------------------------------- #


def cc_submission(**overrides):
    base = dict(
        product="credit_card", template_id="CK-CC-CARD", template_version="v4",
        offer_ids=["CC-PLAT"], states_targeted="ALL",
    )
    base.update(overrides)
    return make_submission(**base)


def test_promo_rate_without_adjacent_intro_word_fires(rulebook, real_cells):
    result = run(
        rulebook,
        submission=cc_submission(),
        cells=[real_cells["CC-PLAT"]],
        artifact_text="0% APR for 15 months. Get the card.",
    )
    rule = rule_by_id(rulebook, "CC-INTRO-001")
    matches = findings_for_rule(result, "CC-INTRO-001")
    assert matches
    assert any(f.severity == rule.severity == Severity.HIGH for f in matches)


def test_promo_rate_with_immediate_intro_word_is_clean(rulebook, real_cells):
    result = run(
        rulebook,
        submission=cc_submission(),
        cells=[real_cells["CC-PLAT"]],
        artifact_text=(
            "0% intro APR on purchases for 15 months; after the intro period "
            "ends, a variable APR of 19.24%-29.24% applies."
        ),
    )
    assert "CC-INTRO-001" not in emitted_rule_ids(result)


# --------------------------------------------------------------------------- #
# element_required (MTG-NMLS-001, CC-PRESCREEN-001 incl. applies_when gate)
# --------------------------------------------------------------------------- #


def test_missing_nmls_id_fires_on_any_mortgage_creative(rulebook, real_cells):
    sub = make_submission(
        product="mortgage_prequal", surface="mortgage_rate_table",
        template_id="CK-MTG-TABLE", offer_ids=["MTG-30F"],
        states_targeted="ALL except AK;HI;NY;VT;WV",
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["MTG-30F"]],
        artifact_text="See your personalized mortgage rate in minutes.",
    )
    rule = rule_by_id(rulebook, "MTG-NMLS-001")
    matches = findings_for_rule(result, "MTG-NMLS-001")
    assert matches
    assert any(f.severity == rule.severity == Severity.HIGH for f in matches)


def test_present_nmls_id_satisfies_element(rulebook, real_cells):
    sub = make_submission(
        product="mortgage_prequal", surface="mortgage_rate_table",
        template_id="CK-MTG-TABLE", offer_ids=["MTG-30F"],
        states_targeted="ALL except AK;HI;NY;VT;WV",
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["MTG-30F"]],
        artifact_text="See your personalized mortgage rate in minutes. NMLS #1902441.",
    )
    assert "MTG-NMLS-001" not in emitted_rule_ids(result)


PRESCREEN_BODY = (
    "Sarah, here is the ClearPath Platinum card. 0% intro APR on purchases "
    "for 15 months; after the intro period a variable APR of 19.24%-29.24% applies."
)


def test_prescreen_notice_rule_gated_off_for_non_firm_offer(rulebook, real_cells):
    # applies_when {offer_field: is_firm_offer, equals: true}: CC-PLAT is not
    # a firm offer, so a missing opt-out notice raises nothing.
    sub = cc_submission(surface="prescreen_email", template_id="CK-CC-PRESCREEN-EM")
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["CC-PLAT"]],
        disclosures=[make_disclosure(DisclosureType.SCHUMER_BOX_LINK, "See rates and fees")],
        artifact_text=PRESCREEN_BODY,
    )
    assert "CC-PRESCREEN-001" not in emitted_rule_ids(result)


def test_prescreen_notice_missing_on_firm_offer_fires(rulebook, real_cells):
    sub = cc_submission(
        surface="prescreen_email", template_id="CK-CC-PRESCREEN-EM",
        offer_ids=["CC-PLAT-PS"],
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["CC-PLAT-PS"]],
        disclosures=[make_disclosure(DisclosureType.SCHUMER_BOX_LINK, "See rates and fees")],
        artifact_text=PRESCREEN_BODY,
    )
    rule = rule_by_id(rulebook, "CC-PRESCREEN-001")
    matches = findings_for_rule(result, "CC-PRESCREEN-001")
    assert matches
    assert any(f.severity == rule.severity == Severity.CRITICAL for f in matches)


def test_prescreen_notice_present_on_firm_offer_is_clean(rulebook, real_cells):
    sub = cc_submission(
        surface="prescreen_email", template_id="CK-CC-PRESCREEN-EM",
        offer_ids=["CC-PLAT-PS"],
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["CC-PLAT-PS"]],
        disclosures=[make_disclosure(DisclosureType.SCHUMER_BOX_LINK, "See rates and fees")],
        artifact_text=(
            PRESCREEN_BODY
            + " PRESCREEN & OPT-OUT NOTICE: You can choose to stop receiving "
            "prescreened offers of credit by calling 1-888-5-OPTOUT."
        ),
    )
    assert "CC-PRESCREEN-001" not in emitted_rule_ids(result)


# --------------------------------------------------------------------------- #
# ground_truth_consistency + composite_all (PL-TRUTH-001)
# --------------------------------------------------------------------------- #


def rate_floor_claim(value_pct: float):
    return make_claim(
        f"APR as low as {value_pct}%",
        [ClaimType.RATE_OR_APR],
        {
            "value_pct": value_pct,
            "is_floor_claim": True,
            "labeled_as_apr": True,
            "rate_kind": "apr",
        },
    )


def test_rate_absent_from_offer_matrix_fires_truthfulness(rulebook, real_cells):
    # 7.49% floor does not exist in PL-36-A (floor 8.99) — Amerisave pattern.
    sub = make_submission(offer_ids=["PL-36-A"])
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[rate_floor_claim(7.49)],
        disclosures=[make_disclosure(DisclosureType.APR_QUALIFIER, "Lowest APR requires excellent credit")],
        artifact_text="APR as low as 7.49% for qualified borrowers.",
    )
    rule = rule_by_id(rulebook, "PL-TRUTH-001")
    matches = findings_for_rule(result, "PL-TRUTH-001")
    assert matches
    assert any(
        f.severity == rule.severity == Severity.CRITICAL
        and f.check_class == CheckClass.TRUTHFULNESS
        for f in matches
    )


def test_rate_present_in_offer_matrix_is_clean(rulebook, real_cells):
    sub = make_submission(offer_ids=["PL-36-A"])
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[rate_floor_claim(8.99)],
        disclosures=[make_disclosure(DisclosureType.APR_QUALIFIER, "Lowest APR requires excellent credit")],
        artifact_text="APR as low as 8.99% for qualified borrowers.",
    )
    assert "PL-TRUTH-001" not in emitted_rule_ids(result)


def test_composite_flags_term_not_offered_by_referenced_cells(rulebook, real_cells):
    # composite_all sub-check: term_months exists_in referenced cells {36, 60}.
    sub = make_submission(offer_ids=["PL-36-A", "PL-60-A"])
    cells = [real_cells["PL-36-A"], real_cells["PL-60-A"]]
    trigger_ok = make_disclosure(
        DisclosureType.TRIGGER_DISCLOSURE, "Repayment terms and APR disclosed"
    )
    bad = run(
        rulebook,
        submission=sub,
        cells=cells,
        claims=[make_claim("Repay over 48 months", [ClaimType.TRIGGERING_TERM], {"term_months": 48})],
        disclosures=[trigger_ok],
        artifact_text="Repay over 48 months.",
    )
    assert "PL-TRUTH-001" in emitted_rule_ids(bad)

    good = run(
        rulebook,
        submission=sub,
        cells=cells,
        claims=[make_claim("Repay over 60 months", [ClaimType.TRIGGERING_TERM], {"term_months": 60})],
        disclosures=[trigger_ok],
        artifact_text="Repay over 60 months.",
    )
    assert "PL-TRUTH-001" not in emitted_rule_ids(good)


# --------------------------------------------------------------------------- #
# numeric_cap_by_state (PL-STATE-CAP-001)
# --------------------------------------------------------------------------- #


def apr_range_claim():
    return make_claim(
        "APR from 8.99% to 29.99%",
        [ClaimType.RATE_OR_APR],
        {
            "range_min_pct": 8.99,
            "range_max_pct": 29.99,
            "is_floor_claim": False,
            "labeled_as_apr": True,
            "rate_kind": "apr",
        },
    )


def _cap_run(rulebook, real_cells, states: str):
    sub = make_submission(offer_ids=["PL-36-A"], states_targeted=states)
    return run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[apr_range_claim()],
        disclosures=[make_disclosure(DisclosureType.APR_QUALIFIER, "based on creditworthiness")],
        artifact_text="APR from 8.99% to 29.99% based on creditworthiness.",
    )


def test_apr_max_above_targeted_state_cap_fires(rulebook, real_cells):
    # AR caps consumer-loan APR at 17.0 (data/state_apr_caps.json); 29.99 > 17.
    result = _cap_run(rulebook, real_cells, "AR;AZ")
    rule = rule_by_id(rulebook, "PL-STATE-CAP-001")
    matches = findings_for_rule(result, "PL-STATE-CAP-001")
    assert matches
    assert any(f.severity == rule.severity == Severity.CRITICAL for f in matches)


def test_apr_max_within_targeted_state_cap_is_clean(rulebook, real_cells):
    # AZ cap 36.0; 29.99 <= 36.
    result = _cap_run(rulebook, real_cells, "AZ")
    assert "PL-STATE-CAP-001" not in emitted_rule_ids(result)


def test_states_absent_from_caps_table_are_ignored(rulebook, real_cells):
    # README: "For every targeted state PRESENT in the caps table" — TX has
    # no entry, so nothing fires.
    result = _cap_run(rulebook, real_cells, "TX")
    assert "PL-STATE-CAP-001" not in emitted_rule_ids(result)
