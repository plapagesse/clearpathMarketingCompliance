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
    assert "OMIT the pair entirely" in prompt  # empty-value discipline (pairs encoding)
    assert "rate_kind: one of apr | interest_rate | unlabeled" in prompt  # vocab lines survive
    assert "must NEVER be omitted" in prompt  # required-fields discipline for weaker models
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
                    ex._NormalizedField(key="labeled_as_apr", value="true"),
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
    assert result.claims[0].normalized_fields == {"value_pct": 8.99, "is_floor_claim": True, "labeled_as_apr": True, "rate_kind": "apr"}
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


def test_workspace_header_on_identity_linked_key(monkeypatch):
    """Identity-linked keys need the anthropic-workspace-id header; plain keys don't."""
    import anthropic

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test_123")
    ex._make_client()
    assert captured["default_headers"] == {"anthropic-workspace-id": "wrkspc_test_123"}
    captured.clear()
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID")
    ex._make_client()
    assert "default_headers" not in captured


# --------------------------------------------------------------------------- #
# Amendment #5: typed payloads + value grading
# --------------------------------------------------------------------------- #

from backend.contracts import CLAIM_TYPE_PAYLOADS, validate_claim_payload  # noqa: E402


def test_payload_models_constructed_from_spec():
    """Amendment #5a: models derive from the map at import. Registry complete,
    spot-checks against an independent read of the spec file, malformed spec
    entries produce clear construction errors."""
    import json as _json
    from pathlib import Path

    from backend.contracts import _build_payload_models

    assert set(CLAIM_TYPE_PAYLOADS) == set(ClaimType) and len(CLAIM_TYPE_PAYLOADS) == 9
    spec = _json.loads((Path(__file__).resolve().parents[3] /
                        "rulebook" / "claim_types_legal_map.json").read_text())["claim_types"]
    # spot-check 1: rate_or_apr — names, requiredness, Literal vocabulary
    m = CLAIM_TYPE_PAYLOADS[ClaimType.RATE_OR_APR]
    assert m.__name__ == "RateOrAprPayload"
    assert set(m.model_fields) == set(spec["rate_or_apr"]["normalized_fields"])
    for name, fs in spec["rate_or_apr"]["normalized_fields"].items():
        assert m.model_fields[name].is_required() == (not fs["optional"]), name
    import typing
    assert set(ex._literal_values(m.model_fields["rate_kind"].annotation)) == \
        set(spec["rate_or_apr"]["normalized_fields"]["rate_kind"]["values"])
    # spot-check 2: triggering_term — all optional, numbers are floats
    tt = CLAIM_TYPE_PAYLOADS[ClaimType.TRIGGERING_TERM]
    assert all(not fi.is_required() for fi in tt.model_fields.values())
    # spot-check 3: promotional — promo_rate_pct optional post-trim (deferred-interest promos)
    assert not CLAIM_TYPE_PAYLOADS[ClaimType.PROMOTIONAL_OR_INTRODUCTORY].model_fields["promo_rate_pct"].is_required()
    # spot-check 4: trimmed types have empty payload models
    assert CLAIM_TYPE_PAYLOADS[ClaimType.APPROVAL_OR_PREQUALIFICATION].model_fields == {}
    # malformed spec -> clear error
    bad = {"claim_types": {ct.value: {"normalized_fields": {}} for ct in ClaimType}}
    bad["claim_types"]["rate_or_apr"]["normalized_fields"] = {"x": {"type": "wat", "optional": True, "description": "d"}}
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        _json.dump(bad, f)
    with pytest.raises(ValueError, match="unknown type 'wat'"):
        _build_payload_models(Path(f.name))
    with pytest.raises(FileNotFoundError, match="claim_types_legal_map.json"):
        _build_payload_models(Path("/nonexistent/claim_types_legal_map.json"))


def _claim(types, nf):
    return Claim(id="c1", claim_types=types, text="x", location="body",
                 source_evidence_id="e1", normalized_fields=nf)


def test_payload_validation_accepts_valid_union():
    validate_claim_payload(_claim(
        [ClaimType.PROMOTIONAL_OR_INTRODUCTORY, ClaimType.TRIGGERING_TERM],
        {"promo_rate_pct": 0.0, "term_months": 15},
    ))
    # empty-payload types validate with no normalized_fields at all
    validate_claim_payload(_claim([ClaimType.APPROVAL_OR_PREQUALIFICATION], {}))
    validate_claim_payload(_claim([ClaimType.ENDORSEMENT_OR_TESTIMONIAL], {}))


def test_payload_validation_rejects_unknown_key():
    with pytest.raises(ValueError, match="belong to none"):
        validate_claim_payload(_claim([ClaimType.RATE_OR_APR],
            {"value_pct": 8.99, "rate_kind": "apr", "bogus_key": 1}))
    # a TRIMMED (deleted) field is now an unknown key — the trim is enforced
    with pytest.raises(ValueError, match="belong to none"):
        validate_claim_payload(_claim([ClaimType.APPROVAL_OR_PREQUALIFICATION],
            {"badge_word": "pre-approved"}))


def test_rate_claim_requires_value_or_range():
    """Cross-field contract: a rate_or_apr claim carries value_pct OR a full
    range (range_min_pct + range_max_pct). Range-only claims ('Rates from
    11.49% to 35.99%') are legitimate; neither is a hard failure."""
    # value-only passes
    validate_claim_payload(_claim([ClaimType.RATE_OR_APR], {"value_pct": 8.99}))
    # range-only passes
    validate_claim_payload(_claim([ClaimType.RATE_OR_APR],
        {"range_min_pct": 11.49, "range_max_pct": 35.99}))
    # neither fails with the cross-field message
    with pytest.raises(ValueError, match="rate claim requires value_pct or a range"):
        validate_claim_payload(_claim([ClaimType.RATE_OR_APR], {"is_floor_claim": True}))
    # a half range does not satisfy the contract either
    with pytest.raises(ValueError, match="rate claim requires value_pct or a range"):
        validate_claim_payload(_claim([ClaimType.RATE_OR_APR], {"range_min_pct": 11.49}))


def test_payload_validation_rejects_wrong_type():
    with pytest.raises(ValidationError):
        validate_claim_payload(_claim([ClaimType.RATE_OR_APR],
            {"value_pct": 8.99, "rate_kind": "banana"}))


def test_invalid_payload_joins_retry_path(monkeypatch, png):
    calls = {"n": 0}

    def flaky(client, system, blocks, model):
        calls["n"] += 1
        if calls["n"] == 1:
            bad = _fake_raw()
            bad.claims[0].normalized_fields.append(ex._NormalizedField(key="bogus_key", value="1"))
            return bad
        return _fake_raw()

    monkeypatch.setattr(ex, "_call_model", flaky)
    ctx = ex.ExtractionContext(product=Product.PERSONAL_LOAN, evidence_id="t5")
    result = ex.extract(png, ctx, client=object())
    assert calls["n"] == 2 and result.claims


def test_grade_values_semantics():
    got = {"value_pct": 8.99, "rate_kind": "apr", "is_floor_claim": True, "term_months": 15}
    assert ev._grade_values({"value_pct": 8.99}, got) == []
    assert ev._grade_values({"value_pct": 8.9901}, got) == []          # float tolerance
    assert ev._grade_values({"rate_kind": "APR"}, got) == []            # casefold
    assert ev._grade_values({"is_floor_claim": True}, got) == []
    mm = ev._grade_values({"value_pct": 9.99, "absent": 1}, got)
    assert {m["reason"] for m in mm} == {"value", "missing"}
    assert ev._grade_values({"is_floor_claim": 1}, got)                 # bool vs int is NOT equal


def test_eval_value_grading_end_to_end(tmp_path, monkeypatch):
    """Span+type-matched findings with expected_normalized_fields get value-graded;
    metric and mismatch diagnostics land in the report."""
    fx = _tmp_fixtures(tmp_path, with_pngs=True)
    key = json.loads((fx / "expected_findings.json").read_text())
    key["mock_pl_card_compliant.html"]["expected_findings"] = [
        {"rule_area": "t", "check_class": "truthfulness", "severity": "high",
         "claim_text": "as low as 8.99%", "location_note": "n",
         "expected_claim_type": "rate_or_apr",
         "expected_normalized_fields": {"value_pct": 8.99, "rate_kind": "APR", "is_floor_claim": True}},
        {"rule_area": "t2", "check_class": "truthfulness", "severity": "high",
         "claim_text": "APR as low as 8.99%", "location_note": "n",
         "expected_claim_type": "rate_or_apr",
         "expected_normalized_fields": {"value_pct": 9.99}},
    ]
    (fx / "expected_findings.json").write_text(json.dumps(key))
    monkeypatch.setattr(ev, "FIXTURES", fx)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy")
    monkeypatch.setattr(ex, "_call_model", lambda client, system, blocks, model: _fake_raw())
    monkeypatch.setattr(ev, "REPORT_PATH", tmp_path / "report.json")
    report = ev.run_eval()
    assert report["value_graded_findings"] == 2
    assert report["value_accuracy_on_matched"] == 0.5
    mock = next(m for m in report["per_mock"] if m["mock"] == "mock_pl_card_compliant.html")
    graded = [r for r in mock["detail"] if r.get("value_graded")]
    assert len(graded) == 2
    bad = next(r for r in graded if not r["value_ok"])
    assert bad["value_mismatches"][0]["field"] == "value_pct"
    assert bad["value_mismatches"][0]["expected"] == 9.99


def test_grade_values_expected_null_semantics():
    """Expected-null passes when the field is absent OR present-as-null; only a
    concrete value fails (reason 'value', never 'missing')."""
    assert ev._grade_values({"fixed_period_stated": None}, {}) == []
    assert ev._grade_values({"fixed_period_stated": None}, {"fixed_period_stated": None}) == []
    mm = ev._grade_values({"fixed_period_stated": None}, {"fixed_period_stated": "15 months"})
    assert len(mm) == 1 and mm[0]["reason"] == "value" and mm[0]["got"] == "15 months"


def test_corrective_retry_includes_failure_text(monkeypatch, png):
    """The second attempt must carry the exact failure text, not re-send the
    identical request."""
    seen_blocks = []

    def flaky(client, system, blocks, model):
        seen_blocks.append(blocks)
        if len(seen_blocks) == 1:
            bad = _fake_raw()
            bad.claims[0].normalized_fields.append(ex._NormalizedField(key="bogus_key", value="1"))
            return bad
        return _fake_raw()

    monkeypatch.setattr(ex, "_call_model", flaky)
    ctx = ex.ExtractionContext(product=Product.PERSONAL_LOAN, evidence_id="t6")
    result = ex.extract(png, ctx, client=object())
    assert len(seen_blocks) == 2 and result.claims
    texts1 = [b.get("text", "") for b in seen_blocks[0] if b.get("type") == "text"]
    texts2 = [b.get("text", "") for b in seen_blocks[1] if b.get("type") == "text"]
    assert not any("failed validation" in t for t in texts1)
    corrective = [t for t in texts2 if "failed validation" in t]
    assert corrective and "bogus_key" in corrective[0]
    assert "Re-emit the complete corrected extraction" in corrective[0]


# --------------------------------------------------------------------------- #
# Decode-enforced payload schema (typed union object)
# --------------------------------------------------------------------------- #


def test_empty_value_pairs_are_dropped(monkeypatch, png):
    """The observed Haiku failure: odds_value_pct emitted as '' — an empty or
    whitespace-only value string means "no value": the pair is dropped in
    finalize, never reaching a payload, and the claim validates."""
    raw = ex._ModelExtraction(
        claims=[
            ex._ModelClaim(
                claim_types=[ClaimType.RATE_OR_APR],
                text="APR as low as 8.99%",
                location="body",
                normalized_fields=[
                    ex._NormalizedField(key="value_pct", value="8.99"),
                    ex._NormalizedField(key="range_min_pct", value=""),
                    ex._NormalizedField(key="rate_kind", value="   "),
                ],
            )
        ],
        disclosures=[],
    )
    monkeypatch.setattr(ex, "_call_model", lambda client, system, blocks, model: raw)
    ctx = ex.ExtractionContext(product=Product.PERSONAL_LOAN, evidence_id="t7")
    result = ex.extract(png, ctx, client=object())
    nf = result.claims[0].normalized_fields
    assert "range_min_pct" not in nf and "rate_kind" not in nf
    assert nf == {"value_pct": 8.99}


def test_spec_prose_never_references_undeclared_fields():
    """Prose lint: any snake_case field-shaped token in a type's definition or
    normalized_fields descriptions must be a declared field of that type, a
    Literal vocabulary value of its payload model, or on the explicit allowlist.
    Makes the phantom-flag class of drift structurally impossible to reintroduce."""
    import re as _re
    import typing

    ALLOWLIST = {
        "claim_types", "normalized_fields", "claim_type",
        "not_conflated",  # rulebook comparator name, not a payload field
    }
    token_re = _re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
    spec = ex.load_classification_spec()
    offenders = []
    for ct in ClaimType:
        t = spec[ct.value]
        model = CLAIM_TYPE_PAYLOADS[ct]
        allowed = set(t["normalized_fields"]) | ALLOWLIST
        allowed |= {c.value for c in ClaimType}  # cross-references to sibling types are legitimate
        for info in model.model_fields.values():
            for arg in typing.get_args(info.annotation):
                for lit in typing.get_args(arg) or ([arg] if isinstance(arg, str) else []):
                    if isinstance(lit, str):
                        allowed.add(lit)
        prose = [("definition", t["definition"])]
        prose += [(f"normalized_fields.{k}", v["description"]) for k, v in t["normalized_fields"].items()]
        for where, text in prose:
            for tok in token_re.findall(text):
                if tok not in allowed:
                    offenders.append(f"{ct.value}.{where}: {tok!r}")
    assert not offenders, "prose references undeclared fields:\n" + "\n".join(offenders)


def test_grade_values_absent_bool_is_false_equivalent():
    """Trim semantics: absent optional booleans are false-equivalent — expected
    false matches an absent field; expected true does not."""
    assert ev._grade_values({"is_floor_claim": False}, {"value_pct": 8.99}) == []
    mm = ev._grade_values({"is_floor_claim": True}, {"value_pct": 8.99})
    assert len(mm) == 1 and mm[0]["reason"] == "value" and mm[0]["got"] is False
    assert ev._grade_values({"is_floor_claim": True}, {"is_floor_claim": True}) == []
