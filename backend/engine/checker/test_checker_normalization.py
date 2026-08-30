"""Normalization pipeline (rulebook/README.md, normative for all text_plane
checks), applied in order: (1) Unicode NFKC; (2) HTML-entity decode
(numeric entities included); (3) curly->straight quote/apostrophe
normalization; (4) lowercase; (5) whitespace collapse incl. NBSP;
(6) word-boundary-aware matching.
"""

from __future__ import annotations

from backend.contracts import ClaimType, DisclosureType

from conftest import (
    emitted_rule_ids,
    make_claim,
    make_disclosure,
    make_submission,
)
from test_checker_primitives import PRESCREEN_BODY, cc_submission, run


def mtg_submission(**overrides):
    base = dict(
        product="mortgage_prequal", surface="mortgage_rate_module",
        template_id="CK-MTG-TABLE", offer_ids=["MTG-ARM"],
        states_targeted="ALL except AK;HI;NY;VT;WV",
    )
    base.update(overrides)
    return make_submission(**base)


def test_nfkc_folds_ligatures_before_matching(rulebook, real_cells):
    # U+FB01 'fi' ligature: NFKC("ﬁxed") == "fixed" — the MTG-FIXED-001
    # lexicon phrase must match the ligature spelling on a variable-rate cell.
    result = run(
        rulebook,
        submission=mtg_submission(),
        cells=[real_cells["MTG-ARM"]],
        artifact_text="Lock in a ﬁxed low rate of 6.250% APR. NMLS #1902441.",
    )
    assert "MTG-FIXED-001" in emitted_rule_ids(result)


def test_matching_is_case_insensitive_after_lowercasing(rulebook, real_cells):
    result = run(
        rulebook,
        submission=mtg_submission(offer_ids=["MTG-30F"]),
        cells=[real_cells["MTG-30F"]],
        artifact_text="SPEAK TO A COUNSELOR TODAY. NMLS #1902441.",
    )
    assert "MTG-COUNSEL-001" in emitted_rule_ids(result)


def test_pattern_capitals_are_style_not_case_sensitivity(rulebook, real_cells):
    # The prescreen detection patterns are written in capitals
    # ("PRESCREEN & OPT-OUT NOTICE"); a lowercase rendering must still
    # satisfy the element (README step 4).
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
            + " prescreen & opt-out notice: you may opt out of prescreened "
            "offers by calling 1-888-5-optout."
        ),
    )
    assert "CC-PRESCREEN-001" not in emitted_rule_ids(result)


def test_html_entities_are_decoded_before_matching(rulebook, real_cells):
    # "&amp;" -> "&" inside the notice heading, and the numeric entity
    # "&#48;" -> "0" inside the promo rate (README step 2: numeric entities
    # included).
    sub = cc_submission(
        surface="prescreen_email", template_id="CK-CC-PRESCREEN-EM",
        offer_ids=["CC-PLAT-PS"],
    )
    with_entity_heading = run(
        rulebook,
        submission=sub,
        cells=[real_cells["CC-PLAT-PS"]],
        disclosures=[make_disclosure(DisclosureType.SCHUMER_BOX_LINK, "See rates and fees")],
        artifact_text=(
            PRESCREEN_BODY
            + " PRESCREEN &amp; OPT-OUT NOTICE: call 1-888-5-OPTOUT to opt out."
        ),
    )
    assert "CC-PRESCREEN-001" not in emitted_rule_ids(with_entity_heading)

    promo = run(
        rulebook,
        submission=cc_submission(),
        cells=[real_cells["CC-PLAT"]],
        artifact_text="&#48;% APR for 15 months. Get the card.",
    )
    assert "CC-INTRO-001" in emitted_rule_ids(promo)


def test_curly_apostrophes_normalize_to_straight(rulebook, real_cells):
    # Lexicon phrase "can't be denied" (straight apostrophe) vs rendered
    # curly U+2019 — README step 3. Fixture claim texts carry curly
    # apostrophes verbatim, so this is load-bearing for the answer key.
    sub = make_submission(offer_ids=["PL-36-A"])
    claim = make_claim(
        "Your application can’t be denied",
        [ClaimType.APPROVAL_OR_PREQUALIFICATION, ClaimType.GENERAL_UDAAP_REPRESENTATION],
        # arbitrated: claim must satisfy the payload contract; normalization
        # intent unchanged.
        {"badge_word": "can’t be denied", "strength": "guaranteed",
         "representation_kind": "other"},
    )
    result = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        claims=[claim],
        artifact_text="Your application can’t be denied. Apply today.",
    )
    assert "XP-UDAAP-001-personal_loan" in emitted_rule_ids(result)


def test_nbsp_collapses_to_plain_space_inside_matches(rulebook, real_cells):
    # "9.99 %" must match the rate anchor \d{1,2}(\.\d{1,3})?\s?% after
    # NBSP -> space collapse (README step 5); with no APR label anywhere the
    # unlabeled-rate rule fires.
    sub = make_submission(offer_ids=["PL-36-A"])
    unlabeled = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        artifact_text="Rates of 9.99 % for well-qualified borrowers.",
    )
    assert "PL-APR-001" in emitted_rule_ids(unlabeled)

    labeled = run(
        rulebook,
        submission=sub,
        cells=[real_cells["PL-36-A"]],
        artifact_text="APR of 9.99% for well-qualified borrowers.",
    )
    assert "PL-APR-001" not in emitted_rule_ids(labeled)
