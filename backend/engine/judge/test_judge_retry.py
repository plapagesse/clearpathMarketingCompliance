"""Corrective-retry behavior of run_judge, mirroring the established extractor
convention (backend/engine/extractor/extract.py): on a schema/validation
failure the judge retries ONCE, and the second attempt's request content
carries the validation error text (never a blind identical re-send); a second
failure propagates.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.contracts import Product

from backend.engine.judge import run_judge

try:  # package/namespace mode (also before the implementation adds __init__.py)
    import backend.engine.judge.conftest as C
except ImportError:  # flat fallback: this directory is pytest's rootdir insert
    import conftest as C

MARKER = "JUDGE_MALFORMED_MARKER_83b1f"


def _malformed_error() -> ValidationError:
    err = ValidationError.from_exception_data(
        MARKER, [{"type": "missing", "loc": ("verdicts",), "input": {}}]
    )
    assert MARKER in str(err)  # the marker must be visible in the error text
    return err


def _run(rulebook, submission, claims, client):
    return run_judge(
        submission=submission,
        claims=claims,
        disclosures=[],
        evidence_path=C.EVIDENCE["preapproved"],
        rulebook=rulebook,
        client=client,
    )


def test_corrective_retry_carries_error_text(
    rulebook, preapproved_submission, preapproved_claims
):
    good = C.full_verdicts(
        rulebook,
        Product.PERSONAL_LOAN,
        overrides={
            "PL-JUDGE-001": dict(
                violated=True, confidence=0.9,
                reasoning="Missing qualifier.", evidence_text="fine print",
            )
        },
    )
    client = C.FakeJudgeClient(_malformed_error(), good)
    findings = _run(rulebook, preapproved_submission, preapproved_claims, client)

    assert len(client.calls) == 2, "exactly one corrective retry"
    assert len(findings) == 1 and findings[0].rule_id == "PL-JUDGE-001"

    first = C.request_text(client.calls[0])
    second = C.request_text(client.calls[1])
    assert MARKER not in first, "the first attempt cannot know the error yet"
    assert MARKER in second, (
        "the corrective retry must include the validation error text in the request"
    )


def test_two_failures_raise(rulebook, preapproved_submission, preapproved_claims):
    client = C.FakeJudgeClient(_malformed_error())  # last behavior repeats: always fails
    with pytest.raises(Exception, match=MARKER):
        _run(rulebook, preapproved_submission, preapproved_claims, client)
    assert len(client.calls) == 2, "one corrective retry, then give up — max 2 attempts"
