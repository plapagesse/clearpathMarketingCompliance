"""Product scoping of run_judge: a mortgage_prequal submission is judged
against exactly the mortgage-scoped llm_judged rules — MTG-JUDGE-001 plus the
three cross-product expansions carrying the mortgage product (rulebook README:
cross-product rules are pre-expanded per product, XP-<FAMILY>-<NNN>-<product>)
— and never against deterministic rules or other products' llm_judged rules.

The scenario is SUB-2026-0156 / mock_lt_mtg_preapproved_gov, the fixture set's
violating mortgage creative (approval-certainty, government-program framing,
ARM described as fixed). What the judge is SHOWN comes from that mock; what it
RETURNS is scripted by the fake client, so these tests are about scoping and
severity resolution, not about which rule the model happens to pick.
"""

from __future__ import annotations

from backend.contracts import Product

from backend.engine.judge import run_judge

try:  # package/namespace mode (also before the implementation adds __init__.py)
    import backend.engine.judge.conftest as C
except ImportError:  # flat fallback: this directory is pytest's rootdir insert
    import conftest as C

MTG_LLM_IDS = {
    "MTG-JUDGE-001",
    "XP-COMP-003-mortgage_prequal",
    "XP-ODDS-005-mortgage_prequal",
    "XP-TEST-006-mortgage_prequal",
}


def test_mortgage_submission_judges_only_mortgage_llm_rules(
    rulebook, mortgage_submission, mortgage_claims
):
    verdicts = C.full_verdicts(rulebook, Product.MORTGAGE_PREQUAL)  # all-clear
    client = C.FakeJudgeClient(verdicts)
    findings = run_judge(
        submission=mortgage_submission,
        claims=mortgage_claims,
        disclosures=[],
        evidence_path=C.EVIDENCE["mortgage"],
        rulebook=rulebook,
        client=client,
    )
    assert findings == []
    assert len(client.calls) == 1  # one structured-output call

    sent = C.request_text(client.calls[0])
    assert {r.rule_id for r in C.llm_rules(rulebook, Product.MORTGAGE_PREQUAL)} == MTG_LLM_IDS
    for rid in MTG_LLM_IDS:
        C.assert_in_norm(rid, sent, "mortgage-scoped llm_judged rule id in request")
    # no other product's llm_judged rules
    for rule in C.llm_rules(rulebook):
        if rule.rule_id not in MTG_LLM_IDS:
            C.assert_not_in_norm(rule.rule_id, sent, "foreign-product llm_judged rule id in request")
    # no deterministic rules at all
    for rule in C.det_rules(rulebook):
        C.assert_not_in_norm(rule.rule_id, sent, "deterministic rule id in request")


def test_mortgage_scoped_violation_uses_mortgage_rule_severity(
    rulebook, mortgage_submission, mortgage_claims
):
    """XP-ODDS-005 is the one family whose severity DIFFERS by product
    (critical on mortgage vs high elsewhere): the finding must carry the
    mortgage entry's severity, proving scoping selected the right expansion."""
    verdicts = C.full_verdicts(
        rulebook,
        Product.MORTGAGE_PREQUAL,
        overrides={
            "XP-ODDS-005-mortgage_prequal": dict(
                violated=True, confidence=0.95,
                reasoning="Numeric approval-odds framing on mortgage creative; Reg N 1014.3(q) prohibition.",
                evidence_text="92% approval odds",
            )
        },
    )
    client = C.FakeJudgeClient(verdicts)
    findings = run_judge(
        submission=mortgage_submission,
        claims=mortgage_claims,
        disclosures=[],
        evidence_path=C.EVIDENCE["mortgage"],
        rulebook=rulebook,
        client=client,
    )
    assert len(findings) == 1
    f = findings[0]
    mtg_rule = C.rule_by_id(rulebook, "XP-ODDS-005-mortgage_prequal")
    pl_rule = C.rule_by_id(rulebook, "XP-ODDS-005-personal_loan")
    assert f.rule_id == "XP-ODDS-005-mortgage_prequal"
    assert f.severity == mtg_rule.severity
    assert f.severity != pl_rule.severity  # critical vs high — guarded in data tests
    assert f.citation_url == mtg_rule.authorities[0].url
