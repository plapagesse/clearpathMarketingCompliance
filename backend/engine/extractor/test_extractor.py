"""Offline tests for the image-only extractor — no API key required."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

import backend.engine.extractor.eval as ev
import backend.engine.extractor.extract as ex
from backend.contracts import Claim, ClaimType, Disclosure, DisclosureType, Product

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"

# 1x1 red pixel PNG
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e44ae426082"
)


@pytest.fixture()
def png(tmp_path):
    p = tmp_path / "shot.png"
    p.write_bytes(_PNG_BYTES)
    return p


# --------------------------------------------------------------------------- #
# Artifact handling: image-only
# --------------------------------------------------------------------------- #


def test_image_block_assembly(png, tmp_path):
    block = ex.build_image_block(png)
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/png"
    import base64 as b64
    assert b64.standard_b64decode(block["source"]["data"]) == _PNG_BYTES
    jpg = tmp_path / "shot.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe0fake")
    assert ex.build_image_block(jpg)["source"]["media_type"] == "image/jpeg"
    blocks = ex._prepare_artifact(png)
    assert len(blocks) == 1 and blocks[0]["type"] == "image"


def test_non_image_input_raises(tmp_path):
    html = tmp_path / "mock.html"
    html.write_text("<div>x</div>")
    for bad in (html, "<div>raw html string</div>", tmp_path / "doc.pdf"):
        with pytest.raises(ValueError, match="image paths"):
            ex._prepare_artifact(bad)
    ctx = ex.ExtractionContext(product=Product.PERSONAL_LOAN, evidence_id="t0")
    with pytest.raises(ValueError, match="image paths"):
        ex.extract(html, ctx, client=object())


# --------------------------------------------------------------------------- #
# Spec + prompt
# --------------------------------------------------------------------------- #


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
    assert spec["triggering_term"]["definition"][:60] in prompt
    assert "NEGATIVE" in prompt
    assert "ONE claim per distinct statement" in prompt  # multi-label convention (amendment #4)
    assert "UNION" in prompt
    assert "VISUALLY" in prompt  # image-only prominence instruction
    assert "HTML" not in prompt


# --------------------------------------------------------------------------- #
# Extraction round-trips (model mocked)
# --------------------------------------------------------------------------- #


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


def test_mocked_response_roundtrips_into_contracts(monkeypatch, png):
    monkeypatch.setattr(ex, "_call_model", lambda client, system, blocks, model: _fake_raw())
    ctx = ex.ExtractionContext(product=Product.PERSONAL_LOAN, evidence_id="t1")
    result = ex.extract(png, ctx, client=object())
    assert isinstance(result.claims[0], Claim)  # subclass is a valid contract Claim
    assert result.claims[0].id == "clm-t1-000"
    assert result.claims[0].claim_types == [ClaimType.RATE_OR_APR]
    assert result.claims[0].source_evidence_id == "t1"
    assert result.claims[0].normalized_fields == {"value_pct": 8.99, "is_floor_claim": True, "rate_kind": "apr"}
    assert isinstance(result.disclosures[0], Disclosure)
    assert result.disclosures[0].prominence == "fine_print"


def test_multilabel_claim_with_union_payload(monkeypatch, png):
    """Amendment #4: '0% intro APR for 15 months' is ONE claim carrying both
    promotional_or_introductory and triggering_term, with the union payload."""
    raw = ex._ModelExtraction(
        claims=[
            ex._ModelClaim(
                claim_types=[ClaimType.PROMOTIONAL_OR_INTRODUCTORY, ClaimType.TRIGGERING_TERM],
                text="0% intro APR for 15 months",
                location="headline",
                normalized_fields=[
                    ex._NormalizedField(key="promo_rate_pct", value="0"),
                    ex._NormalizedField(key="promo_period_months", value="15"),
                    ex._NormalizedField(key="has_intro_word", value="true"),
                    ex._NormalizedField(key="is_deferred_interest", value="false"),
                    ex._NormalizedField(key="post_promo_rate_stated", value="false"),
                    ex._NormalizedField(key="term_months", value="15"),
                ],
            )
        ],
        disclosures=[],
    )
    monkeypatch.setattr(ex, "_call_model", lambda client, system, blocks, model: raw)
    ctx = ex.ExtractionContext(product=Product.CREDIT_CARD, evidence_id="ml")
    result = ex.extract(png, ctx, client=object())
    c = result.claims[0]
    assert isinstance(c, Claim)
    assert set(c.claim_types) == {ClaimType.PROMOTIONAL_OR_INTRODUCTORY, ClaimType.TRIGGERING_TERM}
    assert c.normalized_fields["promo_rate_pct"] == 0
    assert c.normalized_fields["has_intro_word"] is True
    assert c.normalized_fields["term_months"] == 15  # union payload survives


def test_malformed_response_retries_once(monkeypatch, png):
    calls = {"n": 0}

    def flaky(client, system, blocks, model):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValidationError.from_exception_data("bad", [])
        return _fake_raw()

    monkeypatch.setattr(ex, "_call_model", flaky)
    ctx = ex.ExtractionContext(product=Product.CREDIT_CARD, evidence_id="t2")
    result = ex.extract(png, ctx, client=object())
    assert calls["n"] == 2
    assert result.claims


def test_persistent_failure_propagates(monkeypatch, png):
    def always_bad(client, system, blocks, model):
        raise ValidationError.from_exception_data("bad", [])

    monkeypatch.setattr(ex, "_call_model", always_bad)
    ctx = ex.ExtractionContext(product=Product.CREDIT_CARD, evidence_id="t3")
    with pytest.raises(ValidationError):
        ex.extract(png, ctx, client=object())


# --------------------------------------------------------------------------- #
# Eval harness: image-only resolution + dry run
# --------------------------------------------------------------------------- #


def _tmp_fixtures(tmp_path, with_pngs: bool) -> Path:
    fx = tmp_path / "fixtures"
    fx.mkdir()
    shutil.copy(FIXTURES / "expected_findings.json", fx / "expected_findings.json")
    shutil.copy(FIXTURES / "submissions.csv", fx / "submissions.csv")
    if with_pngs:
        key = json.loads((fx / "expected_findings.json").read_text())
        for fname in key:
            if not fname.startswith("_"):
                (fx / (fname.replace(".html", "") + ".png")).write_bytes(_PNG_BYTES)
    return fx


def test_missing_png_is_hard_error(tmp_path, monkeypatch):
    fx = _tmp_fixtures(tmp_path, with_pngs=False)
    monkeypatch.setattr(ev, "FIXTURES", fx)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy")
    with pytest.raises(FileNotFoundError) as err:
        ev.run_eval()
    msg = str(err.value)
    assert "mock_pl_card_compliant.png" in msg  # lists the missing files
    assert "render_screenshots.py" in msg       # names the remedy
    assert "screenshot-renders" in msg


def test_normalizer_transcription_tolerance():
    assert ev._norm("Guaranteed approval — regardless") == ev._norm("guaranteed approval - regardless")
    assert ev._norm("“No hidden fees”") == ev._norm('"no hidden fees"')
    assert ev._norm("don’t pay") == ev._norm("don't pay")
    assert ev._norm("0% intro  APR") == ev._norm("0% intro apr")
    assert ev._norm("Wait…") == ev._norm("wait...")


def test_eval_dry_run_to_api_boundary(tmp_path, monkeypatch):
    """Full eval path with the model call mocked and PNGs present: resolution ->
    extract -> scoring -> report. Proves the harness runs end-to-end up to the
    API call boundary without a key or network."""
    fx = _tmp_fixtures(tmp_path, with_pngs=True)
    monkeypatch.setattr(ev, "FIXTURES", fx)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy")
    monkeypatch.setattr(ex, "_call_model", lambda client, system, blocks, model: _fake_raw())
    monkeypatch.setattr(ev, "REPORT_PATH", tmp_path / "report.json")
    report = ev.run_eval()
    assert report["evidence_format"] == "png"
    assert len(report["per_mock"]) == 10
    assert (tmp_path / "report.json").exists()
    # with a canned single-claim response, misses must carry diagnostics
    miss_rows = [r for m in report["per_mock"] for r in m["detail"] if not r["matched"]]
    assert all("extracted_texts" in r for r in miss_rows)
