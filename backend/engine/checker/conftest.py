"""Shared builders for the Stage-4 checker test suite.

Derived ONLY from the repo's specification artifacts:
  - CONTRACTS.md / backend/contracts.py       (frozen data contracts)
  - rulebook/README.md + rulebook/*.json      (deterministic-primitive spec, 51 rules)
  - fixtures/expected_findings.json + README  (ground-truth answer key)
  - fixtures/offer_matrix.csv, submissions.csv, mock HTML sources
  - backend/ingest/parsers.py                 (CSV loaders)

The interface under test (implemented in parallel on another branch):

    from backend.engine.checker import load_rulebook, run_checks

These tests are expected to be RED/uncollectable until that module lands.
"""

from __future__ import annotations

import html as htmllib
import itertools
import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:  # allow running pytest from any cwd
    sys.path.insert(0, str(REPO))

from backend.contracts import (  # noqa: E402
    CheckClass,
    Claim,
    ClaimType,
    Disclosure,
    DisclosureType,
    OfferCell,
    Severity,
    Submission,
    SubmissionMode,
    validate_claim_payload,
)
from backend.engine.checker import load_rulebook, run_checks  # noqa: E402
from backend.ingest.parsers import load_offer_matrix, load_submissions  # noqa: E402

FIXTURES = REPO / "fixtures"
RULEBOOK_DIR = REPO / "rulebook"

# Pinned versions (rulebook/manifest.json; fixtures/expected_findings.json
# `_offer_matrix_version`).
RULEBOOK_VERSION = "2026.08.4"  # authorized factual refresh: triage bump postdated the suite
OFFER_MATRIX_VERSION = "2026-08"

SEV_AT_LEAST_MEDIUM = {Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL}

# --------------------------------------------------------------------------- #
# Answer key
# --------------------------------------------------------------------------- #

with open(FIXTURES / "expected_findings.json", encoding="utf-8") as _fh:
    _KEY = json.load(_fh)
EXPECTED: dict[str, dict] = {k: v for k, v in _KEY.items() if not k.startswith("_")}

COMPLIANT_MOCKS = sorted(k for k, v in EXPECTED.items() if not v["expected_findings"])
VIOLATION_MOCKS = sorted(k for k, v in EXPECTED.items() if v["expected_findings"])


def normalized_text(raw_html: str) -> str:
    """Entity-decoded, tag-stripped, comment-stripped, whitespace-collapsed
    text of a mock — the exact convention fixtures/README.md defines for
    `claim_text` literalness (mirrors fixtures/validate_fixtures.py)."""
    body = re.sub(r"<!--.*?-->", "", raw_html, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    body = htmllib.unescape(body)
    return re.sub(r"\s+", " ", body).strip()


def mock_text(mock_name: str) -> str:
    """The perfect-OCR artifact text for a fixture mock (from its HTML source)."""
    return normalized_text((FIXTURES / mock_name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Perfect-extractor builders (key-derived)
# --------------------------------------------------------------------------- #


def claims_from_key(mock_name: str) -> list[Claim]:
    """Perfect-extractor claims for a mock, straight from the answer key.

    Multi-label convention (contract amendment #4): key entries sharing one
    literal claim_text are ONE statement, so they merge into one Claim whose
    claim_types / normalized_fields are the union of the entries'.
    """
    merged: dict[str, dict] = {}
    for entry in EXPECTED[mock_name]["expected_findings"]:
        text = entry.get("claim_text")
        if text is None:
            continue  # absence/layout finding — anchors no claim
        slot = merged.setdefault(
            text, {"types": [], "fields": {}, "location": entry.get("location_note", "")}
        )
        ctype = ClaimType(entry["expected_claim_type"])
        if ctype not in slot["types"]:
            slot["types"].append(ctype)
        slot["fields"].update(entry.get("expected_normalized_fields", {}))
    claims = []
    for i, (text, slot) in enumerate(merged.items()):
        claim = Claim(
            id=f"{mock_name}::c{i}",
            claim_types=slot["types"],
            text=text,
            location=slot["location"].split(";")[0].strip() or "body",
            source_evidence_id=mock_name,
            normalized_fields=slot["fields"],
        )
        validate_claim_payload(claim)  # self-check against amendment #5
        claims.append(claim)
    return claims


# Mapping from the human-readable `disclosures_included` manifest labels
# (fixtures/submissions.csv) to the DisclosureType enum.
MANIFEST_DISCLOSURE_TYPES: dict[str, DisclosureType] = {
    "soft-pull statement": DisclosureType.SOFT_PULL,
    "approval-not-guaranteed qualifier": DisclosureType.NOT_GUARANTEED,
    "not-a-loan-approval qualifier": DisclosureType.NOT_GUARANTEED,
    "APR creditworthiness qualifier": DisclosureType.APR_QUALIFIER,
    "APR qualifier": DisclosureType.APR_QUALIFIER,
    "intro-rate adjacency": DisclosureType.INTRO_ADJACENCY,
    "post-intro APR range": DisclosureType.TRIGGER_DISCLOSURE,
    "variable-rate statement": DisclosureType.TRIGGER_DISCLOSURE,
    "balance-transfer fee": DisclosureType.TRIGGER_DISCLOSURE,
    "term disclosure": DisclosureType.TRIGGER_DISCLOSURE,
    "Schumer box link": DisclosureType.SCHUMER_BOX_LINK,
    "NMLS ID": DisclosureType.NMLS_ID,
    "taxes-and-insurance qualifier": DisclosureType.TAXES_INSURANCE,
    "unsubscribe footer": DisclosureType.OTHER,
}


def disclosures_from_manifest(sub: Submission) -> list[Disclosure]:
    """Perfect-extractor disclosures: what the partner manifest says the
    creative includes (fixtures/submissions.csv `disclosures_included`)."""
    out = []
    for i, label in enumerate(sub.disclosures_included):
        out.append(
            Disclosure(
                id=f"{sub.submission_id}::d{i}",
                disclosure_type=MANIFEST_DISCLOSURE_TYPES.get(label, DisclosureType.OTHER),
                text=label,
                location="fine print",
                prominence="fine_print",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Crafted-object builders (for per-primitive unit tests)
# --------------------------------------------------------------------------- #

_counter = itertools.count()


def make_claim(
    text: str,
    claim_types: list[ClaimType],
    normalized_fields: dict | None = None,
    location: str = "headline",
) -> Claim:
    claim = Claim(
        id=f"crafted::c{next(_counter)}",
        claim_types=claim_types,
        text=text,
        location=location,
        source_evidence_id="crafted",
        normalized_fields=normalized_fields or {},
    )
    validate_claim_payload(claim)
    return claim


def make_disclosure(
    disclosure_type: DisclosureType,
    text: str = "",
    location: str = "fine print",
    prominence: str = "fine_print",
) -> Disclosure:
    return Disclosure(
        id=f"crafted::d{next(_counter)}",
        disclosure_type=disclosure_type,
        text=text or disclosure_type.value,
        location=location,
        prominence=prominence,
    )


def make_submission(**overrides) -> Submission:
    base = dict(
        id="SUB-TEST-0001",
        submission_id="SUB-TEST-0001",
        partner="credit_karma",
        date_submitted=date(2026, 8, 28),
        surface="marketplace_offer_card",
        product="personal_loan",
        template_id="CK-PL-CARD",
        template_version="v7",
        offer_ids=[],
        states_targeted="ALL except IA;WV",
        mode=SubmissionMode.PRE_PUBLICATION,
    )
    base.update(overrides)
    return Submission(**base)


def make_cell(**overrides) -> OfferCell:
    base = dict(
        offer_id="TEST-CELL",
        product="personal_loan",
        offer_name="Crafted Test Cell",
        apr_min=8.99,
        apr_max=29.99,
        apr_type="fixed",
        term_months=36,
        amount_min=2000.0,
        amount_max=50000.0,
        badge_designation_allowed="prequalified",
        is_firm_offer=False,
        states_excluded=[],
        effective_start=date(2026, 8, 1),
        effective_end=date(2026, 9, 30),
    )
    base.update(overrides)
    return OfferCell(**base)


# --------------------------------------------------------------------------- #
# Shared fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def rulebook():
    return load_rulebook(RULEBOOK_DIR)


@pytest.fixture(scope="session")
def all_cells() -> list[OfferCell]:
    return load_offer_matrix(FIXTURES / "offer_matrix.csv")


@pytest.fixture(scope="session")
def submissions_by_id() -> dict[str, Submission]:
    return {s.submission_id: s for s in load_submissions(FIXTURES / "submissions.csv")}


@pytest.fixture(scope="session")
def real_cells(all_cells) -> dict[str, OfferCell]:
    return {c.offer_id: c for c in all_cells}


# --------------------------------------------------------------------------- #
# Rulebook-derived helpers
# --------------------------------------------------------------------------- #


def deterministic_ids(rulebook) -> set[str]:
    return {r.rule_id for r in rulebook.deterministic_rules}


def llm_judged_ids(rulebook) -> set[str]:
    return {r.rule_id for r in rulebook.llm_judged_rules}


def rule_by_id(rulebook, rule_id: str):
    for r in list(rulebook.deterministic_rules) + list(rulebook.llm_judged_rules):
        if r.rule_id == rule_id:
            return r
    raise KeyError(rule_id)


def expected_deterministic_ids(mock_name: str, rulebook) -> set[str]:
    """The key's expected_rule_ids for a mock, restricted to deterministic
    rules (check_kind per the rulebook) and to non-fidelity key entries.

    Fidelity-class key entries are excluded because the pinned engine
    interface emits fidelity findings with rule_id=None (the key's ids on
    those entries name the *underlying* rules, exercised separately in
    test_checker_fidelity.py).
    """
    det = deterministic_ids(rulebook)
    out: set[str] = set()
    for entry in EXPECTED[mock_name]["expected_findings"]:
        if entry.get("check_class") == "fidelity":
            continue
        out |= {rid for rid in entry["expected_rule_ids"] if rid in det}
    return out


def emitted_rule_ids(run) -> set[str]:
    return {f.rule_id for f in run.findings if f.rule_id is not None}


def findings_for_rule(run, rule_id: str):
    return [f for f in run.findings if f.rule_id == rule_id]


# --------------------------------------------------------------------------- #
# Full-run helper (answer-key conformance)
# --------------------------------------------------------------------------- #


def run_mock(mock_name: str, rulebook, submissions_by_id, real_cells):
    """Run the checker for a fixture mock with perfect-extractor inputs:
    key-derived claims, manifest-derived disclosures, the referenced offer
    cells, and the mock's flattened artifact text. Verification-mode mocks
    get a baseline CheckRun built by running their approved baseline mock."""
    entry = EXPECTED[mock_name]
    sub = submissions_by_id[entry["submission_id"]]
    cells = [real_cells[oid] for oid in sub.offer_ids]
    baseline = None
    if entry.get("approved_baseline"):
        baseline = run_mock(entry["approved_baseline"], rulebook, submissions_by_id, real_cells)
    return run_checks(
        submission=sub,
        claims=claims_from_key(mock_name),
        disclosures=disclosures_from_manifest(sub),
        offer_cells=cells,
        offer_matrix_version=OFFER_MATRIX_VERSION,
        rulebook=rulebook,
        artifact_text=mock_text(mock_name),
        baseline=baseline,
    )


def assert_engine_invariants(run, rulebook):
    """Invariants of the deterministic engine, for any run:
    - deterministic rules only: no llm_judged rule_id ever appears, and no
      finding carries check_class=judgment;
    - every non-None rule_id resolves to a deterministic rulebook entry.

    (Severity/citation inheritance from the rule is asserted per-case in the
    primitive tests, not here: needs-verification findings may legitimately
    surface below the rule's severity.)"""
    llm = llm_judged_ids(rulebook)
    det = deterministic_ids(rulebook)
    for f in run.findings:
        assert f.check_class != CheckClass.JUDGMENT, f
        if f.rule_id is not None:
            assert f.rule_id not in llm, f
            assert f.rule_id in det, f
