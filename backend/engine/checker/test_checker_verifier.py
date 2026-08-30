"""Tests for the LLM pass verifier (backend/engine/checker/verifier.py).

Fully offline: the Anthropic client is a mock capturing every
`messages.parse` call and answering from scripted verdict payloads. Covers
the dispute→finding mapping, the agree-all no-op, text-dependent rule
selection (metadata-plane and arithmetic-without-rate-claim exclusions), the
payload-vs-text prompt content, the corrective-retry shape, and additivity
(the CheckRun's findings are never touched).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.contracts import (
    CheckClass,
    CheckRun,
    ClaimType,
    DisclosureType,
    Finding,
    Severity,
)
from conftest import make_claim, make_disclosure, make_submission
from backend.engine.checker.verifier import verify_passed_rules

# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def make_check_run(sub, findings=()) -> CheckRun:
    return CheckRun(
        id=f"chk-{sub.submission_id}-test",
        submission_id=sub.submission_id,
        rulebook_version="2026.08.4",
        offer_matrix_version="2026-08",
        mode=sub.mode,
        created_at=datetime.now(timezone.utc),
        findings=list(findings),
    )


def make_finding(rule_id: str) -> Finding:
    return Finding(
        id=f"fnd-test-{rule_id}",
        check_class=CheckClass.LEGALITY,
        severity=Severity.HIGH,
        rule_id=rule_id,
        summary=f"crafted finding for {rule_id}",
        explanation="crafted",
    )


class MockClient:
    """Anthropic-shaped mock: `client.messages.parse(**kwargs)` records the
    call and builds the response from the next scripted verdict payload
    (validated through the real output_format model)."""

    def __init__(self, *verdict_payloads: list[dict]):
        self.calls: list[dict] = []
        self._payloads = list(verdict_payloads)
        self.messages = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        payload = self._payloads[min(len(self.calls) - 1, len(self._payloads) - 1)]
        out = kwargs["output_format"].model_validate({"verdicts": payload})
        return SimpleNamespace(parsed_output=out)

    def user_text(self, call_index: int = 0) -> str:
        blocks = self.calls[call_index]["messages"][0]["content"]
        return "\n".join(b["text"] for b in blocks if b.get("type") == "text")


def agree(rule_id: str) -> dict:
    return {"rule_id": rule_id, "disputed": False, "reason": "pass confirmed", "evidence_text": ""}


def dispute(rule_id: str, reason: str, evidence: str = "") -> dict:
    return {"rule_id": rule_id, "disputed": True, "reason": reason, "evidence_text": evidence}


RATE_CLAIM = make_claim(
    "Rates as low as 5.99% APR",
    [ClaimType.RATE_OR_APR],
    {"value_pct": 5.99, "is_floor_claim": True},
)
NON_RATE_CLAIM = make_claim("No hidden fees, ever", [ClaimType.FEE_OR_COST])


def run_verifier(client, *, claims=None, disclosures=None, prior_findings=(),
                 artifact_text="Rates as low as 5.99% APR. No hidden fees, ever.",
                 rulebook=None):
    sub = make_submission()
    check_run = make_check_run(sub, prior_findings)
    return check_run, verify_passed_rules(
        check_run=check_run,
        submission=sub,
        claims=list(claims if claims is not None else [RATE_CLAIM]),
        disclosures=list(disclosures if disclosures is not None else []),
        artifact_text=artifact_text,
        rulebook=rulebook,
        client=client,
    )


# --------------------------------------------------------------------------- #
# Dispute mapping
# --------------------------------------------------------------------------- #


def test_dispute_emits_one_sub_medium_prefixed_finding(rulebook):
    client = MockClient([
        agree("PL-TRIG-001"),
        dispute("PL-APR-001", "the text shows a rate figure with no APR label nearby",
                "as low as 5.99%"),
    ])
    _, findings = run_verifier(client, rulebook=rulebook)

    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "PL-APR-001"
    assert f.severity in {Severity.LOW, Severity.INFO}  # strictly below medium
    assert f.severity == Severity.LOW  # matches the engine's needs-verification severity
    assert f.explanation.startswith("Pass verifier (model double-check): ")
    assert "no APR label" in f.explanation
    assert f.suggested_redline is None
    assert f.summary.startswith("Needs verification")
    assert len(client.calls) == 1


def test_agree_all_returns_empty_list(rulebook):
    client = MockClient([agree("PL-APR-001"), agree("PL-FEE-001"), agree("PL-TRIG-001")])
    _, findings = run_verifier(client, rulebook=rulebook)
    assert findings == []


# --------------------------------------------------------------------------- #
# Rule selection
# --------------------------------------------------------------------------- #


def test_selection_excludes_metadata_plane_rules(rulebook):
    client = MockClient([agree("PL-APR-001")])
    run_verifier(client, rulebook=rulebook)
    prompt = client.user_text()

    # metadata-plane rule (states_targeted vs states_excluded): never sent
    assert "PL-STATE-EXCL-001" not in prompt
    # text-dependent passes are sent
    for rid in ("PL-APR-001", "PL-TRIG-001", "PL-FEE-001", "XP-UDAAP-001-personal_loan"):
        assert rid in prompt


def test_selection_includes_arithmetic_rules_only_with_rate_claim(rulebook):
    with_rate = MockClient([agree("PL-APR-001")])
    run_verifier(with_rate, claims=[RATE_CLAIM], rulebook=rulebook)
    prompt = with_rate.user_text()
    assert "PL-TRUTH-001" in prompt
    assert "PL-STATE-CAP-001" in prompt

    without_rate = MockClient([agree("PL-APR-001")])
    run_verifier(without_rate, claims=[NON_RATE_CLAIM], rulebook=rulebook)
    prompt = without_rate.user_text()
    assert "PL-TRUTH-001" not in prompt
    assert "PL-STATE-CAP-001" not in prompt
    assert "PL-STATE-EXCL-001" not in prompt


def test_selection_excludes_rules_that_already_fired(rulebook):
    client = MockClient([agree("PL-TRIG-001")])
    run_verifier(client, prior_findings=[make_finding("PL-APR-001")], rulebook=rulebook)
    prompt = client.user_text()
    assert "### PL-APR-001" not in prompt  # fired -> not a pass -> not verified
    assert "### PL-TRIG-001" in prompt


# --------------------------------------------------------------------------- #
# Prompt content
# --------------------------------------------------------------------------- #


def test_prompt_puts_numeric_payload_next_to_claim_text(rulebook):
    client = MockClient([agree("PL-APR-001")])
    run_verifier(client, rulebook=rulebook)
    prompt = client.user_text()

    claim_lines = [ln for ln in prompt.splitlines() if RATE_CLAIM.text in ln]
    assert claim_lines, "claim text missing from the prompt"
    assert "value_pct=5.99" in claim_lines[0]  # payload figure on the same line


def test_prompt_carries_system_prompt_and_evidence(rulebook):
    client = MockClient([agree("PL-APR-001")])
    run_verifier(
        client,
        disclosures=[make_disclosure(DisclosureType.APR_QUALIFIER, "Rate depends on creditworthiness")],
        rulebook=rulebook,
    )
    call = client.calls[0]
    assert "the checker found NO violation" in call["system"]
    assert "Extraction may be imperfect" in call["system"]
    prompt = client.user_text()
    assert "apr_qualifier" in prompt
    assert "Rate depends on creditworthiness" in prompt
    assert "rates as low as 5.99% apr" in prompt  # normalized artifact text


# --------------------------------------------------------------------------- #
# Corrective retry
# --------------------------------------------------------------------------- #


def test_unknown_rule_id_triggers_one_corrective_retry_with_error_text(rulebook):
    client = MockClient(
        [dispute("NOT-A-RULE-999", "bogus")],
        [dispute("PL-APR-001", "rate figure lacks APR label")],
    )
    _, findings = run_verifier(client, rulebook=rulebook)

    assert len(client.calls) == 2
    retry_text = client.user_text(1)
    assert "failed validation" in retry_text
    assert "NOT-A-RULE-999" in retry_text  # the error text names the offender
    assert len(findings) == 1 and findings[0].rule_id == "PL-APR-001"


def test_second_invalid_response_propagates(rulebook):
    client = MockClient([dispute("NOT-A-RULE-999", "bogus")])  # both attempts invalid
    with pytest.raises(ValueError, match="NOT-A-RULE-999"):
        run_verifier(client, rulebook=rulebook)
    assert len(client.calls) == 2


# --------------------------------------------------------------------------- #
# Additivity
# --------------------------------------------------------------------------- #


def test_existing_findings_are_untouched(rulebook):
    prior = [make_finding("PL-APR-001"), make_finding("PL-TRIG-001")]
    snapshot = [f.model_copy(deep=True) for f in prior]
    client = MockClient([dispute("PL-FEE-001", "text hints at a deducted fee")])
    check_run, findings = run_verifier(client, prior_findings=prior, rulebook=rulebook)

    assert check_run.findings == snapshot  # additive: nothing removed or modified
    assert len(findings) == 1
    assert {f.id for f in findings}.isdisjoint({f.id for f in check_run.findings})


def test_no_selectable_rules_makes_no_api_call(rulebook):
    # every PL deterministic rule already fired -> nothing to verify
    fired = [make_finding(r.rule_id) for r in rulebook.deterministic_rules
             if r.product.value == "personal_loan"]
    client = MockClient([])
    _, findings = run_verifier(client, prior_findings=fired, rulebook=rulebook)
    assert findings == []
    assert client.calls == []
