"""run_judge verdict->Finding mapping with a mocked client.

Pinned semantics under test: one structured-output call; violated=true maps to
a contract Finding with check_class=judgment, severity FROM THE RULE,
citation_url from the rule's PRIMARY authority, explanation carrying the model
reasoning (+ citation_quote when present); violated=false emits nothing;
low-confidence violations still emit; suggested_redline passes through;
claim_id links by normalized containment of evidence_text in a claim's text.
"""

from __future__ import annotations

from backend.contracts import CheckClass, Finding, FindingStatus, Product

from backend.engine.judge import run_judge

try:  # package/namespace mode (also before the implementation adds __init__.py)
    import backend.engine.judge.conftest as C
except ImportError:  # flat fallback: this directory is pytest's rootdir insert
    import conftest as C


def _run(submission, claims, disclosures, evidence, rulebook, client):
    return run_judge(
        submission=submission,
        claims=claims,
        disclosures=disclosures,
        evidence_path=evidence,
        rulebook=rulebook,
        client=client,
    )


def test_violated_true_maps_to_judgment_finding(
    rulebook, preapproved_submission, preapproved_claims
):
    reasoning = "The approval-not-guaranteed qualifier present in the approved template is absent, so the net impression overstates certainty."
    verdicts = C.full_verdicts(
        rulebook,
        Product.PERSONAL_LOAN,
        overrides={
            "PL-JUDGE-001": dict(
                violated=True,
                confidence=0.92,
                reasoning=reasoning,
                evidence_text="the fine print omits any approval-not-guaranteed qualifier",
            )
        },
    )
    client = C.FakeJudgeClient(verdicts)
    findings = _run(preapproved_submission, preapproved_claims, [], C.EVIDENCE["preapproved"], rulebook, client)

    assert len(client.calls) == 1  # ONE structured-output model call
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, Finding)
    rule = C.rule_by_id(rulebook, "PL-JUDGE-001")
    assert f.rule_id == "PL-JUDGE-001"
    assert f.check_class == CheckClass.JUDGMENT
    assert f.severity == rule.severity  # severity comes FROM THE RULE
    assert f.citation_url == rule.authorities[0].url  # PRIMARY authority's url
    assert f.status == FindingStatus.OPEN
    assert f.summary.strip() and f.explanation.strip()
    C.assert_in_norm(reasoning, f.explanation, "model reasoning in explanation")
    # citation_quote is non-null for PL-JUDGE-001 and must ride along
    C.assert_in_norm(rule.parameters["citation_quote"], f.explanation, "citation_quote in explanation")
    # evidence_text overlaps no crafted claim text -> no claim link
    assert f.claim_id is None


def test_severity_and_citation_url_come_from_each_rule(
    rulebook, preapproved_submission, preapproved_claims
):
    """Two violated rules with DIFFERENT severities and different primary
    authorities — catches hardcoded severity/citation."""
    verdicts = C.full_verdicts(
        rulebook,
        Product.PERSONAL_LOAN,
        overrides={
            "PL-JUDGE-001": dict(
                violated=True, confidence=0.9,
                reasoning="Headline promise contradicts buried limits.",
                evidence_text="the fine print contradicts the headline",
            ),
            "XP-ODDS-005-personal_loan": dict(
                violated=True, confidence=0.88,
                reasoning="Odds framing lacks any documented substantiation basis.",
                evidence_text="approval odds language without basis",
            ),
        },
    )
    client = C.FakeJudgeClient(verdicts)
    findings = _run(preapproved_submission, preapproved_claims, [], C.EVIDENCE["preapproved"], rulebook, client)

    assert len(findings) == 2
    by_rule = {f.rule_id: f for f in findings}
    assert set(by_rule) == {"PL-JUDGE-001", "XP-ODDS-005-personal_loan"}
    for rid, f in by_rule.items():
        rule = C.rule_by_id(rulebook, rid)
        assert f.severity == rule.severity, rid
        assert f.citation_url == rule.authorities[0].url, rid
        assert f.check_class == CheckClass.JUDGMENT, rid
    # the two rules genuinely differ (guarded in test_judge_rulebook_data)
    assert by_rule["PL-JUDGE-001"].severity != by_rule["XP-ODDS-005-personal_loan"].severity


def test_violated_false_produces_no_finding(
    rulebook, preapproved_submission, preapproved_claims
):
    verdicts = C.full_verdicts(rulebook, Product.PERSONAL_LOAN)  # all-clear
    client = C.FakeJudgeClient(verdicts)
    findings = _run(preapproved_submission, preapproved_claims, [], C.EVIDENCE["preapproved"], rulebook, client)
    assert findings == []


def test_negative_control_compliant_mock_all_false(
    rulebook, compliant_pl_submission, compliant_pl_claims, compliant_pl_disclosures
):
    """fixtures/expected_findings.json: mock_pl_card_compliant has an empty
    expected_findings array — all-clear verdicts must yield an empty list."""
    verdicts = C.full_verdicts(rulebook, Product.PERSONAL_LOAN)
    client = C.FakeJudgeClient(verdicts)
    findings = _run(
        compliant_pl_submission,
        compliant_pl_claims,
        compliant_pl_disclosures,
        C.EVIDENCE["compliant_pl"],
        rulebook,
        client,
    )
    assert findings == []
    assert len(client.calls) == 1


def test_low_confidence_violation_still_produces_finding(
    rulebook, preapproved_submission, preapproved_claims
):
    verdicts = C.full_verdicts(
        rulebook,
        Product.PERSONAL_LOAN,
        overrides={
            "PL-JUDGE-001": dict(
                violated=True,
                confidence=0.3,
                reasoning="Possibly misleading net impression; low certainty.",
                evidence_text="the fine print may contradict the headline",
            )
        },
    )
    client = C.FakeJudgeClient(verdicts)
    findings = _run(preapproved_submission, preapproved_claims, [], C.EVIDENCE["preapproved"], rulebook, client)
    assert len(findings) == 1, "low-confidence violations must STILL produce findings"
    text = f"{findings[0].summary} {findings[0].explanation}"
    assert ("confiden" in text.lower()) or ("0.3" in text) or ("30%" in text), (
        "the finding should note the low confidence somewhere in summary/explanation"
    )


def test_suggested_redline_passthrough(
    rulebook, preapproved_submission, preapproved_claims
):
    redline = "Add 'Approval is not guaranteed; final terms depend on a full application.' adjacent to the badge."
    verdicts = C.full_verdicts(
        rulebook,
        Product.PERSONAL_LOAN,
        overrides={
            "PL-JUDGE-001": dict(
                violated=True, confidence=0.9,
                reasoning="Missing qualifier.", evidence_text="fine print",
                suggested_redline=redline,
            ),
            "XP-ODDS-005-personal_loan": dict(
                violated=True, confidence=0.9,
                reasoning="Unsubstantiated odds.", evidence_text="approval odds",
                # no suggested_redline on this verdict
            ),
        },
    )
    client = C.FakeJudgeClient(verdicts)
    findings = _run(preapproved_submission, preapproved_claims, [], C.EVIDENCE["preapproved"], rulebook, client)
    by_rule = {f.rule_id: f for f in findings}
    assert by_rule["PL-JUDGE-001"].suggested_redline == redline
    assert by_rule["XP-ODDS-005-personal_loan"].suggested_redline is None


def test_evidence_text_containment_links_claim_id(
    rulebook, cc_intro_submission, cc_intro_claims, cc_intro_disclosures
):
    """Normalized containment: evidence_text is the model's quote of the
    offending span; when it is contained in a claim's text (case/whitespace
    tolerant), the finding links that claim."""
    fee_claim = next(c for c in cc_intro_claims if c.text == C.CC_FEE_CLAIM_TEXT)

    # (a) whole claim text, different case
    verdicts = C.full_verdicts(
        rulebook,
        Product.CREDIT_CARD,
        overrides={
            "CC-JUDGE-001": dict(
                violated=True, confidence=0.9,
                reasoning="Absolute fee/interest denial contradicted by footer terms.",
                evidence_text=C.CC_FEE_CLAIM_TEXT.lower(),
            )
        },
    )
    findings = _run(
        cc_intro_submission, cc_intro_claims, cc_intro_disclosures,
        C.EVIDENCE["cc_intro"], rulebook, C.FakeJudgeClient(verdicts),
    )
    assert len(findings) == 1
    assert findings[0].claim_id == fee_claim.id

    # (b) verbatim fragment of the claim text
    verdicts = C.full_verdicts(
        rulebook,
        Product.CREDIT_CARD,
        overrides={
            "CC-JUDGE-001": dict(
                violated=True, confidence=0.9,
                reasoning="Absolute fee/interest denial contradicted by footer terms.",
                evidence_text="No annual fee. No interest.",
            )
        },
    )
    findings = _run(
        cc_intro_submission, cc_intro_claims, cc_intro_disclosures,
        C.EVIDENCE["cc_intro"], rulebook, C.FakeJudgeClient(verdicts),
    )
    assert len(findings) == 1
    assert findings[0].claim_id == fee_claim.id


def test_non_overlapping_evidence_text_leaves_claim_id_unset(
    rulebook, cc_intro_submission, cc_intro_claims, cc_intro_disclosures
):
    verdicts = C.full_verdicts(
        rulebook,
        Product.CREDIT_CARD,
        overrides={
            "CC-JUDGE-001": dict(
                violated=True, confidence=0.9,
                reasoning="Overall creative framing concern.",
                evidence_text="the creative's overall visual hierarchy buries the balance-transfer terms",
            )
        },
    )
    findings = _run(
        cc_intro_submission, cc_intro_claims, cc_intro_disclosures,
        C.EVIDENCE["cc_intro"], rulebook, C.FakeJudgeClient(verdicts),
    )
    assert len(findings) == 1
    assert findings[0].claim_id is None
