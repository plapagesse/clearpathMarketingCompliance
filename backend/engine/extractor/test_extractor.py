"""Offline tests for the extractor — no API key required."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import backend.engine.extractor.extract as ex
from backend.contracts import Claim, ClaimType, Disclosure, DisclosureType, Product

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def test_strip_comments_removes_answer_key_comments():
    raw = (FIXTURES / "mock_pl_card_trigger_stale.html").read_text()
    assert "<!--" in raw  # fixtures carry answer-key comments by design
    cleaned = ex.strip_html_comments(raw)
    assert "<!--" not in cleaned and "-->" not in cleaned
    assert cleaned != raw
    # visible ad content survives
    assert "Check my rate" in cleaned or "APR" in cleaned


def test_classification_spec_loads_and_covers_enum():
    spec = ex.load_classification_spec()
    assert set(spec) == {ct.value for ct in ClaimType}
    for name, t in spec.items():
        assert t.get("definition"), name
        assert t.get("examples"), name
        assert "normalized_fields" in t, name


def test_spec_sync_failure_raises(tmp_path):
    bad = {"claim_types": {"rate_or_apr": {"definition": "x", "examples": {}, "normalized_fields": {}}}}
    p = tmp_path / "map.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="out of sync"):
        ex.load_classification_spec(p)


def test_prompt_includes_spec_and_disclosure_enum():
    spec = ex.load_classification_spec()
    prompt = ex.build_system_prompt(spec)
    for ct in ClaimType:
        assert ct.value in prompt
    for dt in DisclosureType:
        assert dt.value in prompt
    # a known definition fragment and a near-miss negative made it in verbatim
    assert spec["triggering_term"]["definition"][:60] in prompt
    assert "NEGATIVE" in prompt
    assert "TWO claim objects" in prompt  # two-categories convention


def _fake_raw():
    return ex._ModelExtraction(
        claims=[
            ex._ModelClaim(
                claim_type=ClaimType.RATE_OR_APR,
                text="APR as low as 8.99%",
                location="body",
                normalized_fields=[
                    ex._NormalizedField(key="value_pct", value="8.99"),
                    ex._NormalizedField(key="is_floor_claim", value="true"),
                    ex._NormalizedField(key="rate_kind", value="apr"),
                ],
            )
        ],
        disclosures=[
            ex._ModelDisclosure(
                disclosure_type=DisclosureType.APR_QUALIFIER,
                text="Lowest APR requires excellent credit",
                location="fine print",
                prominence="fine_print",
            )
        ],
    )


def test_mocked_response_roundtrips_into_contracts(monkeypatch):
    monkeypatch.setattr(ex, "_call_model", lambda client, system, blocks, model: _fake_raw())
    ctx = ex.ExtractionContext(product=Product.PERSONAL_LOAN, evidence_id="t1")
    result = ex.extract(FIXTURES / "mock_pl_card_compliant.html", ctx, client=object())
    assert isinstance(result.claims[0], Claim)  # subclass is a valid contract Claim
    assert result.claims[0].id == "clm-t1-000"
    assert result.claims[0].source_evidence_id == "t1"
    assert result.claims[0].normalized_fields == {"value_pct": 8.99, "is_floor_claim": True, "rate_kind": "apr"}
    assert isinstance(result.disclosures[0], Disclosure)
    assert result.disclosures[0].prominence == "fine_print"


def test_malformed_response_retries_once(monkeypatch):
    calls = {"n": 0}

    def flaky(client, system, blocks, model):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValidationError.from_exception_data("bad", [])
        return _fake_raw()

    monkeypatch.setattr(ex, "_call_model", flaky)
    ctx = ex.ExtractionContext(product=Product.CREDIT_CARD, evidence_id="t2")
    result = ex.extract("<div>0% intro APR</div>", ctx, client=object())
    assert calls["n"] == 2
    assert result.claims


def test_persistent_failure_propagates(monkeypatch):
    def always_bad(client, system, blocks, model):
        raise ValidationError.from_exception_data("bad", [])

    monkeypatch.setattr(ex, "_call_model", always_bad)
    ctx = ex.ExtractionContext(product=Product.CREDIT_CARD, evidence_id="t3")
    with pytest.raises(ValidationError):
        ex.extract("<div>x</div>", ctx, client=object())
