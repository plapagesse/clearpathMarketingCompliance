"""Shared fixtures for the LLM-judge test suite (backend.engine.judge).

These tests are written against the PINNED judge interface:

    run_judge(*, submission, claims, disclosures, evidence_path, rulebook,
              model=None, client=None) -> list[Finding]
    build_judge_prompt(rules, submission, claims, disclosures) -> str

derived exclusively from the repo's specification artifacts: CONTRACTS.md /
backend/contracts.py (frozen contracts), rulebook/README.md (llm_judged
enrichment contract, severity rubric), the 13 check_kind="llm_judged" entries
in rulebook/*.json, and fixtures/expected_findings.json (answer key). The
implementation is developed in parallel on another branch; until
backend.engine.judge exists, every test file that imports it fails collection
BY DESIGN. test_judge_rulebook_data.py imports only spec data and runs today.

No API key, no network anywhere: the model client is faked. The fake mirrors
the established extractor convention (extract.py: client.messages.parse(...,
output_format=Model) -> response.parsed_output; corrective retry carries the
validation error text) and adapts the pinned per-rule verdict shape

    {rule_id, violated, confidence, reasoning, evidence_text, suggested_redline?}

onto whatever structured-output wrapper model the implementation passes as
`output_format` (single list field, root list, or a "verdicts" key), so the
tests do not depend on the implementation's private model class names.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from pydantic import BaseModel, TypeAdapter

from backend.contracts import (
    Claim,
    ClaimType,
    Disclosure,
    DisclosureType,
    Product,
    RulebookEntry,
    Submission,
    validate_claim_payload,
)

RULEBOOK_DIR = ROOT / "rulebook"
FIXTURES_DIR = ROOT / "fixtures"
RULE_FILES = [
    "personal_loan.json",
    "credit_card.json",
    "mortgage_prequal.json",
    "cross_product.json",
]


# --------------------------------------------------------------------------- #
# Environment: tests must never require a real key
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _dummy_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy")


# --------------------------------------------------------------------------- #
# Spec data loading (rulebook + answer key) — the sources of truth
# --------------------------------------------------------------------------- #


def load_rulebook() -> list[RulebookEntry]:
    entries: list[RulebookEntry] = []
    for fname in RULE_FILES:
        data = json.loads((RULEBOOK_DIR / fname).read_text())
        for raw in data["rules"]:
            entries.append(RulebookEntry.model_validate(raw))
    return entries


@pytest.fixture(scope="session")
def rulebook() -> list[RulebookEntry]:
    return load_rulebook()


@pytest.fixture(scope="session")
def expected_findings() -> dict:
    return json.loads((FIXTURES_DIR / "expected_findings.json").read_text())


def llm_rules(rulebook: list[RulebookEntry], product: Product | None = None) -> list[RulebookEntry]:
    out = [r for r in rulebook if r.check_kind.value == "llm_judged"]
    if product is not None:
        out = [r for r in out if r.product == product]
    return out


def det_rules(rulebook: list[RulebookEntry]) -> list[RulebookEntry]:
    return [r for r in rulebook if r.check_kind.value == "deterministic"]


def rule_by_id(rulebook: list[RulebookEntry], rule_id: str) -> RulebookEntry:
    return next(r for r in rulebook if r.rule_id == rule_id)


# --------------------------------------------------------------------------- #
# Normalization for containment assertions
#
# Tolerant of prompt-side line-wrapping / casing but nothing semantic: NFKC,
# curly->straight quotes, dash unification, casefold, whitespace collapse
# (mirrors the transcription-tolerant normalizer convention in extractor/eval).
# --------------------------------------------------------------------------- #

_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-",
})


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).translate(_QUOTE_MAP)
    return " ".join(s.casefold().split())


def assert_in_norm(needle: str, haystack: str, context: str = "") -> None:
    assert norm(needle) in norm(haystack), f"{context}: expected fragment not found: {needle!r}"


def assert_not_in_norm(needle: str, haystack: str, context: str = "") -> None:
    assert norm(needle) not in norm(haystack), f"{context}: forbidden fragment present: {needle!r}"


# --------------------------------------------------------------------------- #
# Request flattening: all TEXT the model was shown (system + text blocks),
# skipping image blocks.
# --------------------------------------------------------------------------- #


def request_text(call_kwargs: dict) -> str:
    parts: list[str] = []
    system = call_kwargs.get("system")
    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        for b in system:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
    for msg in call_kwargs.get("messages") or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b.get("text", ""))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Fake client
# --------------------------------------------------------------------------- #


def _confidence_label(v: dict) -> dict:
    """Variant of a verdict with confidence as a label, in case the
    implementation's verdict model types confidence as a vocabulary rather
    than a number."""
    out = dict(v)
    c = out.get("confidence")
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        out["confidence"] = "low" if c < 0.4 else ("medium" if c < 0.75 else "high")
    return out


def _shape_verdicts(output_format, verdicts: list[dict]):
    """Fit the pinned verdict dicts into the implementation's structured-output
    model, whatever wrapper it chose."""
    errors: list[Exception] = []
    variants = [verdicts, [_confidence_label(v) for v in verdicts]]
    if isinstance(output_format, type) and issubclass(output_format, BaseModel):
        for variant in variants:
            for field_name in output_format.model_fields:
                try:
                    return output_format.model_validate({field_name: variant})
                except Exception as e:  # noqa: BLE001 — best-effort adapter
                    errors.append(e)
            try:
                return output_format.model_validate(variant)  # RootModel-style
            except Exception as e:  # noqa: BLE001
                errors.append(e)
    else:
        for variant in variants:
            try:
                return TypeAdapter(output_format).validate_python(variant)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
    raise AssertionError(
        "FakeJudgeClient could not fit the pinned verdict shape "
        "{rule_id, violated, confidence, reasoning, evidence_text, suggested_redline?} "
        f"into output_format={output_format!r}; last error: {errors[-1] if errors else None}"
    )


class _FakeMessages:
    def __init__(self, owner: "FakeJudgeClient"):
        self._owner = owner

    def parse(self, **kwargs):
        return self._owner._handle(kwargs, structured=True)

    def create(self, **kwargs):
        return self._owner._handle(kwargs, structured=False)


class FakeJudgeClient:
    """Scripted stand-in for the Anthropic client.

    Each script entry serves one model call: a list of verdict dicts (returned
    as a structured response) or an Exception instance (raised, simulating a
    malformed/unparseable model turn). The last entry repeats for any extra
    calls. Every call's kwargs are recorded in .calls.
    """

    def __init__(self, *script):
        assert script, "FakeJudgeClient needs at least one scripted behavior"
        self.script = list(script)
        self.calls: list[dict] = []
        self.messages = _FakeMessages(self)
        self.beta = SimpleNamespace(messages=self.messages)

    def _handle(self, kwargs: dict, structured: bool):
        self.calls.append(kwargs)
        behavior = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(behavior, Exception):
            raise behavior
        text = json.dumps({"verdicts": behavior})
        parsed = None
        output_format = kwargs.get("output_format")
        if structured and output_format is not None:
            parsed = _shape_verdicts(output_format, behavior)
        return SimpleNamespace(
            parsed_output=parsed,
            content=[SimpleNamespace(type="text", text=text)],
            usage=None,
        )


# --------------------------------------------------------------------------- #
# Verdict builders
# --------------------------------------------------------------------------- #


def make_verdict(
    rule_id: str,
    *,
    violated: bool = False,
    confidence: float = 0.9,
    reasoning: str | None = None,
    evidence_text: str = "",
    suggested_redline: str | None = None,
) -> dict:
    v = {
        "rule_id": rule_id,
        "violated": violated,
        "confidence": confidence,
        "reasoning": reasoning if reasoning is not None else f"Assessed {rule_id}: no violation found.",
        "evidence_text": evidence_text,
    }
    if suggested_redline is not None:
        v["suggested_redline"] = suggested_redline
    return v


def full_verdicts(rulebook: list[RulebookEntry], product: Product, overrides: dict[str, dict] | None = None) -> list[dict]:
    """One verdict per product-scoped llm_judged rule (an implementation may
    validate coverage), all-clear except the given overrides {rule_id: kwargs}."""
    overrides = overrides or {}
    out = []
    for r in llm_rules(rulebook, product):
        out.append(make_verdict(r.rule_id, **overrides.get(r.rule_id, {})))
    return out


# --------------------------------------------------------------------------- #
# Contract-object builders (claims are payload-validated on construction)
# --------------------------------------------------------------------------- #


def make_claim(cid: str, types: list[ClaimType], text: str, location: str, evidence_id: str, nf: dict) -> Claim:
    claim = Claim(
        id=cid,
        claim_types=types,
        text=text,
        location=location,
        source_evidence_id=evidence_id,
        normalized_fields=nf,
    )
    validate_claim_payload(claim)  # keep test inputs contract-valid (amendment #5)
    return claim


def make_disclosure(did: str, dtype: DisclosureType, text: str, location: str, prominence: str) -> Disclosure:
    return Disclosure(id=did, disclosure_type=dtype, text=text, location=location, prominence=prominence)


def make_submission(product: Product, **overrides) -> Submission:
    base = dict(
        id="SUB-TEST-0001",
        submission_id="SUB-TEST-0001",
        partner="credit_karma",
        date_submitted=date(2026, 8, 24),
        surface="marketplace_offer_card",
        product=product,
        template_id="CK-TEST",
        template_version="v1",
        offer_ids=[],
    )
    base.update(overrides)
    return Submission(**base)


# --- Scenario bundles mirroring fixtures/submissions.csv + the mock content --- #


@pytest.fixture()
def compliant_pl_submission() -> Submission:
    # SUB-2026-0142 (mock_pl_card_compliant)
    return make_submission(
        Product.PERSONAL_LOAN,
        id="SUB-2026-0142",
        submission_id="SUB-2026-0142",
        surface="marketplace_offer_card",
        template_id="CK-PL-CARD",
        template_version="v7",
        offer_ids=["PL-36-A", "PL-60-A"],
        proposed_headline="You're prequalified for up to {{max_amount}}",
        badge_text="Prequalified",
        states_targeted="ALL except IA;WV",
    )


@pytest.fixture()
def compliant_pl_claims() -> list[Claim]:
    ev = "mock_pl_card_compliant"
    return [
        make_claim(
            "clm-pl0142-000",
            [ClaimType.APPROVAL_OR_PREQUALIFICATION],
            "You're prequalified for up to $50,000",
            "headline",
            ev,
            {},
        ),
        make_claim(
            "clm-pl0142-001",
            [ClaimType.RATE_OR_APR],
            "APR as low as 8.99%",
            "subheadline",
            ev,
            {"value_pct": 8.99, "is_floor_claim": True, "labeled_as_apr": True, "rate_kind": "apr"},
        ),
    ]


@pytest.fixture()
def compliant_pl_disclosures() -> list[Disclosure]:
    ev = "mock_pl_card_compliant"
    return [
        make_disclosure(f"dsc-{ev}-000", DisclosureType.SOFT_PULL,
                        "Checking your rate won't affect your credit score.", "fine print", "fine_print"),
        make_disclosure(f"dsc-{ev}-001", DisclosureType.NOT_GUARANTEED,
                        "Prequalification is not a guarantee of approval.", "fine print", "fine_print"),
        make_disclosure(f"dsc-{ev}-002", DisclosureType.APR_QUALIFIER,
                        "Lowest APR available to applicants with excellent credit and autopay.", "fine print", "fine_print"),
    ]


@pytest.fixture()
def cc_intro_submission() -> Submission:
    # SUB-2026-0149 (mock_cc_card_intro_violations)
    return make_submission(
        Product.CREDIT_CARD,
        id="SUB-2026-0149",
        submission_id="SUB-2026-0149",
        surface="marketplace_offer_card",
        template_id="CK-CC-CARD",
        template_version="v5-draft",
        offer_ids=["CC-PLAT"],
        proposed_headline="0% APR for 15 months",
        badge_text="Prequalified",
        states_targeted="ALL",
    )


CC_FEE_CLAIM_TEXT = "No annual fee. No interest. No brainer."


@pytest.fixture()
def cc_intro_claims() -> list[Claim]:
    ev = "mock_cc_card_intro_violations"
    return [
        make_claim(
            "clm-cc0149-000",
            [ClaimType.PROMOTIONAL_OR_INTRODUCTORY],
            "0% APR for 15 months",
            "headline",
            ev,
            {
                "promo_rate_pct": 0.0,
                "promo_period_months": 15,
            },
        ),
        make_claim(
            "clm-cc0149-001",
            [ClaimType.FEE_OR_COST],
            CC_FEE_CLAIM_TEXT,
            "bold subheadline",
            ev,
            {"fee_type": "annual_fee"},
        ),
    ]


@pytest.fixture()
def cc_intro_disclosures() -> list[Disclosure]:
    ev = "mock_cc_card_intro_violations"
    return [
        make_disclosure(f"dsc-{ev}-000", DisclosureType.OTHER,
                        "After the promotional period, a variable APR of 19.24%-29.24% applies. Balance transfer fee: 3%.",
                        "footer", "fine_print"),
    ]


@pytest.fixture()
def preapproved_submission() -> Submission:
    # SUB-2026-0146 (mock_pl_card_preapproved_guaranteed)
    return make_submission(
        Product.PERSONAL_LOAN,
        id="SUB-2026-0146",
        submission_id="SUB-2026-0146",
        surface="marketplace_offer_card",
        template_id="CK-PL-CARD",
        template_version="v8-draft",
        offer_ids=["PL-36-A", "PL-60-A"],
        proposed_headline="You're pre-approved for up to {{max_amount}}",
        badge_text="Pre-approved",
        states_targeted="ALL except IA;WV",
    )


@pytest.fixture()
def preapproved_claims() -> list[Claim]:
    ev = "mock_pl_card_preapproved_guaranteed"
    return [
        make_claim(
            "clm-pl0146-000",
            [ClaimType.APPROVAL_OR_PREQUALIFICATION],
            "You're pre-approved for up to $50,000",
            "headline",
            ev,
            {},
        ),
        make_claim(
            "clm-pl0146-001",
            [ClaimType.APPROVAL_OR_PREQUALIFICATION],
            "Guaranteed approval — regardless of credit history",
            "emphasis line below the APR line",
            ev,
            {},
        ),
    ]


@pytest.fixture()
def mortgage_submission() -> Submission:
    # SUB-2026-0150 (mock_mtg_arm_as_fixed)
    return make_submission(
        Product.MORTGAGE_PREQUAL,
        id="SUB-2026-0150",
        submission_id="SUB-2026-0150",
        surface="mortgage_rate_module",
        template_id="CK-MTG-TABLE",
        template_version="v10-draft",
        offer_ids=["MTG-ARM"],
        proposed_headline="Lock in a fixed low rate of {{rate}}",
        badge_text="Prequalified",
        states_targeted="ALL except AK;HI;NY;VT;WV",
    )


@pytest.fixture()
def mortgage_claims() -> list[Claim]:
    ev = "mock_mtg_arm_as_fixed"
    return [
        make_claim(
            "clm-mtg0150-000",
            [ClaimType.FIXED_RATE_REPRESENTATION],
            "Lock in a fixed low rate of 6.250%",
            "headline",
            ev,
            {},
        ),
        make_claim(
            "clm-mtg0150-001",
            [ClaimType.TRIGGERING_TERM],
            "Pay just $2,463/month on a $400,000 loan",
            "subheadline",
            ev,
            {},
        ),
    ]


# Real committed evidence PNGs (agent4/screenshot-renders) — deterministic inputs.
EVIDENCE = {
    "compliant_pl": FIXTURES_DIR / "mock_pl_card_compliant.png",
    "cc_intro": FIXTURES_DIR / "mock_cc_card_intro_violations.png",
    "preapproved": FIXTURES_DIR / "mock_pl_card_preapproved_guaranteed.png",
    "mortgage": FIXTURES_DIR / "mock_mtg_arm_as_fixed.png",
}
