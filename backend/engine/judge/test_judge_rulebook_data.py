"""Spec-data guards for the judge: the llm_judged rulebook contract and the
answer-key premises the judge tests rely on.

This file deliberately does NOT import backend.engine.judge, so it runs (and
must pass) even before the implementation lands — it pins the data the judge
is contractually required to consume.
"""

from __future__ import annotations

import json

from backend.contracts import Product, Severity

try:  # package/namespace mode (also before the implementation adds __init__.py)
    import backend.engine.judge.conftest as C
except ImportError:  # flat fallback: this directory is pytest's rootdir insert
    import conftest as C

RULEBOOK_DIR = C.RULEBOOK_DIR
llm_rules = C.llm_rules
rule_by_id = C.rule_by_id


def test_exactly_thirteen_llm_judged_rules(rulebook):
    ids = sorted(r.rule_id for r in llm_rules(rulebook))
    assert len(ids) == 13, ids
    assert ids == [
        "CC-JUDGE-001",
        "MTG-JUDGE-001",
        "PL-JUDGE-001",
        "PL-JUDGE-002",
        "XP-COMP-003-credit_card",
        "XP-COMP-003-mortgage_prequal",
        "XP-COMP-003-personal_loan",
        "XP-ODDS-005-credit_card",
        "XP-ODDS-005-mortgage_prequal",
        "XP-ODDS-005-personal_loan",
        "XP-TEST-006-credit_card",
        "XP-TEST-006-mortgage_prequal",
        "XP-TEST-006-personal_loan",
    ]


def test_llm_judged_product_scope_counts(rulebook):
    """Product scoping the judge must honor: cross-product llm_judged rules are
    pre-expanded per product (rulebook README), so scope == rule.product."""
    assert len(llm_rules(rulebook, Product.PERSONAL_LOAN)) == 5
    assert len(llm_rules(rulebook, Product.CREDIT_CARD)) == 4
    assert len(llm_rules(rulebook, Product.MORTGAGE_PREQUAL)) == 4


def test_manifest_llm_judged_count_agrees(rulebook):
    manifest = json.loads((RULEBOOK_DIR / "manifest.json").read_text())
    assert manifest["counts"]["by_check_kind"]["llm_judged"] == len(llm_rules(rulebook)) == 13


def test_llm_judged_enrichment_contract(rulebook):
    """rulebook/README.md: llm_judged rules carry judge_focus,
    violation_examples (2-3), compliant_contrast, and citation_quote (verbatim
    or null-with-note, never invented) — the four prompt ingredients."""
    for r in llm_rules(rulebook):
        p = r.parameters
        assert isinstance(p.get("judge_focus"), str) and p["judge_focus"].strip(), r.rule_id
        examples = p.get("violation_examples")
        assert isinstance(examples, list) and 2 <= len(examples) <= 3, r.rule_id
        assert all(isinstance(e, str) and e.strip() for e in examples), r.rule_id
        assert isinstance(p.get("compliant_contrast"), str) and p["compliant_contrast"].strip(), r.rule_id
        assert "citation_quote" in p, r.rule_id
        quote = p["citation_quote"]
        assert quote is None or (isinstance(quote, str) and quote.strip()), r.rule_id
        if quote is None:
            # null is only legal with an explanatory note (rulebook README)
            assert isinstance(p.get("note"), str) and p["note"].strip(), r.rule_id
        # Findings surface the PRIMARY authority's url (CONTRACTS.md), so it must exist
        assert r.authorities and r.authorities[0].url.startswith("http"), r.rule_id
        assert isinstance(r.severity, Severity)
        assert r.explanation.strip(), r.rule_id


def test_severities_the_mapping_tests_depend_on(rulebook):
    """The severity-from-rule tests need rules whose severities actually differ;
    pin the values so a rulebook edit that invalidates the tests is loud."""
    assert rule_by_id(rulebook, "PL-JUDGE-001").severity == Severity.MEDIUM
    assert rule_by_id(rulebook, "XP-ODDS-005-personal_loan").severity == Severity.HIGH
    assert rule_by_id(rulebook, "XP-ODDS-005-mortgage_prequal").severity == Severity.CRITICAL
    assert rule_by_id(rulebook, "CC-JUDGE-001").severity == Severity.MEDIUM


def test_answer_key_premises_for_judge_conformance(expected_findings, rulebook):
    """The answer-key conformance tests target two rows; guard their shape.

    Note (spec observation, encoded deliberately loosely): the cc_intro fee
    net-impression row is check_class 'judgment', but the preapproved
    missing-qualifier row that maps to llm_judged rule PL-JUDGE-001 is recorded
    as check_class 'legality' with severity 'high', while PL-JUDGE-001 itself
    is severity 'medium' and llm_judged rules emit check_class 'judgment'
    (rulebook README). The judge tests therefore assert only
    rule_id-membership against that row, per the pinned interface.
    """
    cc = expected_findings["mock_cc_card_intro_violations.html"]
    assert cc["product"] == "credit_card"
    judgment_rows = [f for f in cc["expected_findings"] if f["check_class"] == "judgment"]
    assert len(judgment_rows) == 1
    row = judgment_rows[0]
    assert "CC-JUDGE-001" in row["expected_rule_ids"]
    assert row["claim_text"] == "No annual fee. No interest. No brainer."
    assert row["severity"] == "medium" == rule_by_id(rulebook, "CC-JUDGE-001").severity.value

    pl = expected_findings["mock_pl_card_preapproved_guaranteed.html"]
    assert pl["product"] == "personal_loan"
    pl_rows = [f for f in pl["expected_findings"] if "PL-JUDGE-001" in f["expected_rule_ids"]]
    assert len(pl_rows) == 1
    assert pl_rows[0]["claim_text"] is None  # absence finding: no claim anchor
