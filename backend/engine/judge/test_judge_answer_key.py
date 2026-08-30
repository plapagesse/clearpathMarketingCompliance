"""Answer-key conformance (mocked model): judge findings for the two fixture
mocks that plant judge-visible violations line up with
fixtures/expected_findings.json.

- mock_cc_card_intro_violations.html: the fee net-impression row is the one
  check_class="judgment" row in the whole key; expected_rule_ids include
  CC-JUDGE-001.
- mock_pl_card_preapproved_guaranteed.html: the missing-qualifier row maps to
  PL-JUDGE-001. NOTE: that row is recorded with check_class "legality" and
  severity "high" while PL-JUDGE-001 is an llm_judged rule of severity
  "medium" — per the pinned interface (llm_judged -> check_class judgment,
  severity from the rule) conformance against this row is asserted as rule_id
  MEMBERSHIP in expected_rule_ids only; class/severity are asserted against
  the interface contract, not the row.
"""

from __future__ import annotations

from backend.contracts import CheckClass, Product, Severity

from backend.engine.judge import run_judge

try:  # package/namespace mode (also before the implementation adds __init__.py)
    import backend.engine.judge.conftest as C
except ImportError:  # flat fallback: this directory is pytest's rootdir insert
    import conftest as C


def test_cc_intro_judgment_finding_matches_answer_key(
    rulebook, expected_findings, cc_intro_submission, cc_intro_claims, cc_intro_disclosures
):
    key_rows = [
        f
        for f in expected_findings["mock_cc_card_intro_violations.html"]["expected_findings"]
        if f["check_class"] == "judgment"
    ]
    assert len(key_rows) == 1
    row = key_rows[0]

    verdicts = C.full_verdicts(
        rulebook,
        Product.CREDIT_CARD,
        overrides={
            "CC-JUDGE-001": dict(
                violated=True,
                confidence=0.9,
                reasoning=(
                    "'No annual fee. No interest. No brainer.' omits the 3% balance-transfer "
                    "fee and the permanent post-promo interest — a misleading net impression."
                ),
                evidence_text=row["claim_text"],  # the answer key's literal claim span
            )
        },
    )
    client = C.FakeJudgeClient(verdicts)
    findings = run_judge(
        submission=cc_intro_submission,
        claims=cc_intro_claims,
        disclosures=cc_intro_disclosures,
        evidence_path=C.EVIDENCE["cc_intro"],
        rulebook=rulebook,
        client=client,
    )

    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id in row["expected_rule_ids"]  # answer-key alignment
    assert f.rule_id == "CC-JUDGE-001"  # ...via the llm_judged member of that list
    assert f.check_class == CheckClass.JUDGMENT
    assert f.check_class.value == row["check_class"]
    # here the key row and the rule agree on severity, so both hold
    assert f.severity == C.rule_by_id(rulebook, "CC-JUDGE-001").severity
    assert f.severity == Severity(row["severity"])
    # the verdict quoted the key row's literal claim span -> the finding links
    # the crafted claim carrying exactly that text
    fee_claim = next(c for c in cc_intro_claims if c.text == row["claim_text"])
    assert f.claim_id == fee_claim.id


def test_preapproved_judge_finding_matches_answer_key(
    rulebook, expected_findings, preapproved_submission, preapproved_claims
):
    rows = [
        f
        for f in expected_findings["mock_pl_card_preapproved_guaranteed.html"]["expected_findings"]
        if "PL-JUDGE-001" in f["expected_rule_ids"]
    ]
    assert len(rows) == 1
    row = rows[0]

    verdicts = C.full_verdicts(
        rulebook,
        Product.PERSONAL_LOAN,
        overrides={
            "PL-JUDGE-001": dict(
                violated=True,
                confidence=0.9,
                reasoning=(
                    "The approval-not-guaranteed qualifier present in the approved v7 fine "
                    "print is absent, compounding the approval misrepresentation."
                ),
                evidence_text="the approval-not-guaranteed qualifier is absent from the fine print",
            )
        },
    )
    client = C.FakeJudgeClient(verdicts)
    findings = run_judge(
        submission=preapproved_submission,
        claims=preapproved_claims,
        disclosures=[],  # the mock's qualifier fine print is missing — that IS the violation
        evidence_path=C.EVIDENCE["preapproved"],
        rulebook=rulebook,
        client=client,
    )

    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id in row["expected_rule_ids"]  # answer-key alignment (membership)
    assert f.rule_id == "PL-JUDGE-001"
    # interface contract (NOT the key row — see module docstring):
    assert f.check_class == CheckClass.JUDGMENT
    assert f.severity == C.rule_by_id(rulebook, "PL-JUDGE-001").severity
    # the key row is an absence finding (claim_text null): nothing to link
    assert row["claim_text"] is None
    assert f.claim_id is None
