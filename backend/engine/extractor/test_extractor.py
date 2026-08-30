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
    assert "ONE claim per distinct statement" in prompt  # multi-label convention (amendment #4)
    assert "UNION" in prompt


def _fake_raw():
    return ex._ModelExtraction(
        claims=[
            ex._ModelClaim(
                claim_types=[ClaimType.RATE_OR_APR],
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
    assert result.claims[0].claim_types == [ClaimType.RATE_OR_APR]
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


# --------------------------------------------------------------------------- #
# Image-first additions
# --------------------------------------------------------------------------- #

# 1x1 red pixel PNG
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e44ae426082"
)


def test_image_block_assembly(tmp_path):
    png = tmp_path / "shot.png"
    png.write_bytes(_PNG_BYTES)
    block = ex.build_image_block(png)
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/png"
    import base64 as b64
    assert b64.standard_b64decode(block["source"]["data"]) == _PNG_BYTES
    jpg = tmp_path / "shot.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe0fake")
    assert ex.build_image_block(jpg)["source"]["media_type"] == "image/jpeg"
    # _prepare_artifact routes a png path to a single vision block
    blocks, kind = ex._prepare_artifact(png)
    assert kind == "image" and blocks[0]["type"] == "image"


def test_evidence_resolution_png_first_then_fallback(tmp_path, monkeypatch):
    import backend.engine.extractor.eval as ev
    monkeypatch.setattr(ev, "FIXTURES", tmp_path)
    monkeypatch.setattr(ev, "RENDER_CACHE", tmp_path / "cache")
    (tmp_path / "mock_a.html").write_text("<div>x</div>")
    (tmp_path / "mock_a.png").write_bytes(_PNG_BYTES)
    p, fmt = ev._resolve_evidence("mock_a.html", "png")
    assert fmt == "png" and p.suffix == ".png"          # png wins when present
    (tmp_path / "mock_b.html").write_text("<div>y</div>")
    p, fmt = ev._resolve_evidence("mock_b.html", "png")
    assert fmt == "html" and p.suffix == ".html"        # loud fallback path
    p, fmt = ev._resolve_evidence("mock_a.html", "html")
    assert fmt == "html"                                 # explicit html mode


def test_normalizer_transcription_tolerance():
    import backend.engine.extractor.eval as ev
    assert ev._norm("Guaranteed approval — regardless") == ev._norm("guaranteed approval - regardless")
    assert ev._norm("“No hidden fees”") == ev._norm('"no hidden fees"')
    assert ev._norm("don’t pay") == ev._norm("don't pay")
    assert ev._norm("0% intro  APR") == ev._norm("0% intro apr")
    assert ev._norm("Wait…") == ev._norm("wait...")


def test_eval_png_dry_run_to_api_boundary(tmp_path, monkeypatch):
    """Full eval path in png mode with the model call mocked: resolution ->
    extract -> scoring -> report. No key, no network, no PNGs on disk (falls
    back loudly per mock) — proves the harness runs end-to-end."""
    import backend.engine.extractor.eval as ev
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy")
    monkeypatch.setattr(ex, "_call_model", lambda client, system, blocks, model: _fake_raw())
    monkeypatch.setattr(ev, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(ev, "RENDER_CACHE", tmp_path / "cache")
    report = ev.run_eval(evidence_format="png")
    assert report["evidence_format_requested"] == "png"
    assert len(report["per_mock"]) == 10
    assert (tmp_path / "report.json").exists()
    # with a canned single-claim response, misses must carry diagnostics
    miss_rows = [r for m in report["per_mock"] for r in m["detail"] if not r["matched"]]
    assert all("extracted_texts" in r for r in miss_rows)


def test_multilabel_claim_with_union_payload(monkeypatch):
    """Amendment #4: '0% intro APR for 15 months' is ONE claim carrying both
    promotional_or_introductory and triggering_term, with the union of both
    payload contracts."""
    raw = ex._ModelExtraction(
        claims=[
            ex._ModelClaim(
                claim_types=[ClaimType.PROMOTIONAL_OR_INTRODUCTORY, ClaimType.TRIGGERING_TERM],
                text="0% intro APR for 15 months",
                location="headline",
                normalized_fields=[
                    # promotional_or_introductory payload
                    ex._NormalizedField(key="promo_rate_pct", value="0"),
                    ex._NormalizedField(key="promo_period_months", value="15"),
                    ex._NormalizedField(key="has_intro_word", value="true"),
                    ex._NormalizedField(key="is_deferred_interest", value="false"),
                    ex._NormalizedField(key="post_promo_rate_stated", value="false"),
                    # triggering_term payload (union)
                    ex._NormalizedField(key="term_months", value="15"),
                ],
            )
        ],
        disclosures=[],
    )
    monkeypatch.setattr(ex, "_call_model", lambda client, system, blocks, model: raw)
    ctx = ex.ExtractionContext(product=Product.CREDIT_CARD, evidence_id="ml")
    result = ex.extract("<div>0% intro APR for 15 months</div>", ctx, client=object())
    c = result.claims[0]
    assert isinstance(c, Claim)  # validates against the amended contract
    assert set(c.claim_types) == {ClaimType.PROMOTIONAL_OR_INTRODUCTORY, ClaimType.TRIGGERING_TERM}
    assert c.normalized_fields["promo_rate_pct"] == 0
    assert c.normalized_fields["has_intro_word"] is True
    assert c.normalized_fields["term_months"] == 15  # union payload survives
