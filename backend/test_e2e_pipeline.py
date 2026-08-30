"""End-to-end LIVE tests for the marketing-compliance pipeline.

Black-box: these tests drive the pipeline only through its public interface —
``extract`` -> ``run_checks`` -> ``verify_passed_rules`` -> ``run_judge`` — and
grade the result against the committed answer key in
``fixtures/expected_findings.json``. Nothing here reaches into engine internals.

WHAT MAKES THIS SUITE DIFFERENT from the per-stage suites: every other test in
the repo stubs the model. These run a real screenshot through the real
Anthropic API, so they are the only check that the four stages actually
*compose* — that the claims the extractor emits are shaped the way the checker
expects, and that the rule ids the checker and judge produce line up with the
answer key.

LIVE — this suite calls the Anthropic API and is SKIPPED by default.
To enable it, make an API key resolvable in either of two ways:

  * export ANTHROPIC_API_KEY=sk-ant-...        (process environment), or
  * put ANTHROPIC_API_KEY=sk-ant-... in the repo-root .env file

The key is only *detected* here, via ``dotenv_values``, which reads the file
without mutating the environment; each engine stage then resolves its own
credentials as documented.

MODELS AND COST: extraction runs on the ANTHROPIC_MODEL default (sonnet —
claim classification is the hard step, and the cheap models are not reliable at
it); the judge and the pass-verifier are pinned to Haiku, which is all they
need. The suite makes 6 live calls in total (2 mocks x extract + verify +
judge), costing roughly $0.10-0.20 per full run. Each mock's pipeline is a
module-scoped fixture, so extra assertions cost no extra calls.

ROBUSTNESS: the model stages are non-deterministic and may legitimately raise
findings beyond the planted ones. Every assertion here is therefore SUBSET or
membership — never exact equality against the answer key.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from backend.contracts import (
    CheckClass,
    CheckRun,
    Claim,
    Disclosure,
    Finding,
    Submission,
)
from backend.engine.checker import load_rulebook, run_checks, verify_passed_rules
from backend.engine.extractor.extract import ExtractionContext, extract
from backend.engine.judge import run_judge
from backend.ingest.parsers import load_offer_matrix, load_submissions

# --------------------------------------------------------------------------- #
# Paths and constants
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures"
RULEBOOK_DIR = REPO_ROOT / "rulebook"
SUBMISSIONS_CSV = FIXTURES / "submissions.csv"
OFFER_MATRIX_CSV = FIXTURES / "offer_matrix.csv"
EXPECTED_FINDINGS_JSON = FIXTURES / "expected_findings.json"

#: Recorded into CheckRun.offer_matrix_version by this suite, so a run produced
#: by these tests is identifiable in any downstream audit trail.
OFFER_MATRIX_VERSION = "e2e"

#: The judge and verifier are graded on rule *selection*, not prose quality, so
#: the cheap model is the right tool. Extraction keeps the ANTHROPIC_MODEL
#: default on purpose.
CHEAP_MODEL = "claude-haiku-4-5"

VIOLATION_MOCK = "mock_pl_card_preapproved_guaranteed.html"
COMPLIANT_MOCK = "mock_pl_card_compliant.html"

#: Higher rank = more severe, so "medium or above" is one comparison.
_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_MEDIUM_RANK = _SEVERITY_RANK["medium"]


def _severity_rank(finding: Finding) -> int:
    """Rank of a finding's severity; unknown values sort as most severe."""
    value = getattr(finding.severity, "value", finding.severity)
    return _SEVERITY_RANK.get(str(value), max(_SEVERITY_RANK.values()))


# --------------------------------------------------------------------------- #
# Live-suite gate
# --------------------------------------------------------------------------- #


def _resolve_api_key() -> str | None:
    """Return an Anthropic key from the environment, else the repo-root .env.

    Detection only: ``dotenv_values`` parses the file into a dict and never
    touches ``os.environ``, so importing this module cannot change how any
    other test resolves credentials.
    """
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if key:
        return key

    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return None
    try:
        from dotenv import dotenv_values
    except ImportError:  # python-dotenv absent: env var is then the only path
        return None
    return ((dotenv_values(env_path) or {}).get("ANTHROPIC_API_KEY") or "").strip() or None


_API_KEY = _resolve_api_key()

SKIP_REASON = (
    "live Anthropic API suite: no ANTHROPIC_API_KEY resolved. To enable, either "
    "`export ANTHROPIC_API_KEY=sk-ant-...` or add a line "
    "`ANTHROPIC_API_KEY=sk-ant-...` to the repo-root .env file "
    f"({REPO_ROOT / '.env'}), then re-run. Costs ~$0.10-0.20 per full run "
    "(6 model calls: 2 mocks x extract + verify + judge)."
)

pytestmark = pytest.mark.skipif(_API_KEY is None, reason=SKIP_REASON)


# --------------------------------------------------------------------------- #
# Answer-key helpers (fixtures/expected_findings.json)
# --------------------------------------------------------------------------- #


def _answer_key() -> dict[str, Any]:
    return json.loads(EXPECTED_FINDINGS_JSON.read_text())


def expected_rule_ids(mock_filename: str, *, judgment: bool) -> set[str]:
    """Planted rule ids for one mock, split by plane.

    Only rows that actually carry ``expected_rule_ids`` count. A row belongs to
    the judgment plane iff its ``check_class`` is ``"judgment"``; everything
    else (legality / truthfulness / fidelity) is deterministic.
    """
    entry = _answer_key()[mock_filename]
    ids: set[str] = set()
    for row in entry.get("expected_findings", []):
        rule_ids = row.get("expected_rule_ids") or []
        if not rule_ids:
            continue
        if (row.get("check_class") == "judgment") is judgment:
            ids.update(rule_ids)
    return ids


def rulebook_manifest_version() -> str:
    """The version string the rulebook manifest declares."""
    return json.loads((RULEBOOK_DIR / "manifest.json").read_text())["rulebook_version"]


# --------------------------------------------------------------------------- #
# The full-pipeline helper
# --------------------------------------------------------------------------- #


@dataclass
class PipelineRun:
    """Everything one end-to-end run produced, for tests to assert against."""

    mock_filename: str
    submission: Submission
    evidence_path: Path
    extraction: Any
    claims: list[Claim]
    disclosures: list[Disclosure]
    rulebook: Any
    check_run: CheckRun
    verifier_findings: list[Finding]
    judge_findings: list[Finding]
    #: Captured either side of verify_passed_rules to prove it is additive.
    findings_len_before_verify: int
    findings_len_after_verify: int
    finding_ids_before_verify: tuple[str, ...]

    @property
    def deterministic_findings(self) -> list[Finding]:
        return [f for f in self.check_run.findings if f.check_class != CheckClass.JUDGMENT]

    @property
    def check_rule_ids(self) -> set[str]:
        return {f.rule_id for f in self.check_run.findings if f.rule_id}

    @property
    def judge_rule_ids(self) -> set[str]:
        return {f.rule_id for f in self.judge_findings if f.rule_id}

    @property
    def all_findings(self) -> list[Finding]:
        return [*self.check_run.findings, *self.verifier_findings, *self.judge_findings]


def submission_for(mock_filename: str) -> Submission:
    """The manifest row that ships ``mock_filename`` as one of its assets."""
    for submission in load_submissions(SUBMISSIONS_CSV):
        if mock_filename in submission.asset_files:
            return submission
    raise AssertionError(
        f"no submissions.csv row lists {mock_filename!r} in asset_files "
        "(fixtures and manifest are out of sync)"
    )


def run_full_pipeline(mock_filename: str) -> PipelineRun:
    """Run all four stages for one mock and return every intermediate result.

    Evidence is the rendered PNG beside the mock (screenshots are the canonical
    artifact); ``artifact_text`` is left None so the deterministic plane is
    graded on what the extractor actually recovered from the image.
    """
    submission = submission_for(mock_filename)
    evidence_path = FIXTURES / f"{Path(mock_filename).stem}.png"
    assert evidence_path.exists(), f"missing rendered evidence {evidence_path}"

    # Stage 1 — extraction (live; ANTHROPIC_MODEL default, i.e. sonnet).
    context = ExtractionContext(
        product=submission.product,
        surface=submission.surface,
        partner=submission.partner,
        evidence_id=evidence_path.name,
    )
    extraction = extract(evidence_path, context)
    claims = list(extraction.claims)
    disclosures = list(extraction.disclosures)

    # Stage 2 — deterministic checks (offline).
    rulebook = load_rulebook(RULEBOOK_DIR)
    offer_cells = load_offer_matrix(OFFER_MATRIX_CSV)
    check_run = run_checks(
        submission=submission,
        claims=claims,
        disclosures=disclosures,
        offer_cells=offer_cells,
        offer_matrix_version=OFFER_MATRIX_VERSION,
        rulebook=rulebook,
        artifact_text=None,
    )

    # Stage 3 — LLM double-check of the PASSED rules (live; cheap model).
    # Snapshot the CheckRun either side: the contract says this is additive.
    findings_len_before = len(check_run.findings)
    finding_ids_before = tuple(f.id for f in check_run.findings)
    verifier_findings = list(
        verify_passed_rules(
            check_run=check_run,
            submission=submission,
            claims=claims,
            disclosures=disclosures,
            artifact_text=None,
            rulebook=rulebook,
            model=CHEAP_MODEL,
        )
    )
    findings_len_after = len(check_run.findings)

    # Stage 4 — LLM judge over the gray-area rules, with the screenshot (live).
    judge_findings = list(
        run_judge(
            submission=submission,
            claims=claims,
            disclosures=disclosures,
            evidence_path=evidence_path,
            rulebook=rulebook,
            model=CHEAP_MODEL,
        )
    )

    return PipelineRun(
        mock_filename=mock_filename,
        submission=submission,
        evidence_path=evidence_path,
        extraction=extraction,
        claims=claims,
        disclosures=disclosures,
        rulebook=rulebook,
        check_run=check_run,
        verifier_findings=verifier_findings,
        judge_findings=judge_findings,
        findings_len_before_verify=findings_len_before,
        findings_len_after_verify=findings_len_after,
        finding_ids_before_verify=finding_ids_before,
    )


# Module-scoped: each mock costs 3 live calls, and every test below reuses
# these two runs rather than paying again.
@pytest.fixture(scope="module")
def violation_run() -> PipelineRun:
    return run_full_pipeline(VIOLATION_MOCK)


@pytest.fixture(scope="module")
def compliant_run() -> PipelineRun:
    return run_full_pipeline(COMPLIANT_MOCK)


# --------------------------------------------------------------------------- #
# TEST A — the violation path
# --------------------------------------------------------------------------- #


def test_violation_mock_fires_the_planted_deterministic_rules(violation_run: PipelineRun) -> None:
    """Every deterministic rule planted in the pre-approved/guaranteed mock fires.

    Subset, not equality: the checker is free to raise more than the answer key
    plants (e.g. needs-verification findings), and that is not a regression.
    """
    planted = expected_rule_ids(VIOLATION_MOCK, judgment=False)
    assert planted, "answer key lists no deterministic rule ids for this mock"

    fired = violation_run.check_rule_ids
    missing = planted - fired
    assert not missing, (
        f"deterministic rules planted in {VIOLATION_MOCK} did not fire: {sorted(missing)}. "
        f"CheckRun raised: {sorted(fired)}"
    )


def test_violation_mock_judge_flags_a_planted_judgment_rule(violation_run: PipelineRun) -> None:
    """The judge raises at least one of the mock's planted judgment-plane rules."""
    planted = expected_rule_ids(VIOLATION_MOCK, judgment=True)
    assert planted, "answer key lists no judgment rule ids for this mock"

    raised = violation_run.judge_rule_ids
    assert planted & raised, (
        f"judge raised none of the planted judgment rules {sorted(planted)} for "
        f"{VIOLATION_MOCK}; it raised: {sorted(raised)}"
    )

    for finding in violation_run.judge_findings:
        assert finding.check_class == CheckClass.JUDGMENT, (
            f"judge finding {finding.id} has check_class {finding.check_class}, expected judgment"
        )


def test_violation_mock_findings_are_well_formed(violation_run: PipelineRun) -> None:
    """Findings from all three stages satisfy the Finding contract in substance."""
    for finding in violation_run.all_findings:
        assert finding.id, "finding is missing an id"
        assert str(getattr(finding.severity, "value", finding.severity)) in _SEVERITY_RANK, (
            f"finding {finding.id} has unrecognised severity {finding.severity!r}"
        )
        assert finding.summary.strip(), f"finding {finding.id} has an empty summary"

    # A deterministic finding that names a rule is asserting a legal violation,
    # so it must carry the authority it rests on.
    for finding in violation_run.deterministic_findings:
        if finding.rule_id is None:
            # Rule-less deterministic findings are run annotations, not
            # violations (e.g. the degraded-text-plane notice raised when no
            # artifact_text is supplied). They have no rule, hence no citation —
            # but they must stay INFO so they can never be mistaken for one.
            assert _severity_rank(finding) == _SEVERITY_RANK["info"], (
                f"finding {finding.id} names no rule yet is severity "
                f"{finding.severity!r}; rule-less findings must be INFO annotations. "
                f"summary={finding.summary!r}"
            )
            continue
        assert finding.citation_url and finding.citation_url.strip(), (
            f"deterministic finding {finding.id} (rule {finding.rule_id}) has no citation_url"
        )


def test_violation_check_run_records_the_manifest_rulebook_version(
    violation_run: PipelineRun,
) -> None:
    """The run is stamped with the version the rulebook manifest declares."""
    assert violation_run.check_run.rulebook_version == rulebook_manifest_version()


# --------------------------------------------------------------------------- #
# TEST B — the compliant path
# --------------------------------------------------------------------------- #


def test_compliant_mock_raises_no_material_deterministic_finding(
    compliant_run: PipelineRun,
) -> None:
    """The compliant mock produces nothing at medium severity or above.

    LOW/INFO findings are expected and allowed: those are the needs-verification
    flags and run annotations the deterministic plane raises when it cannot
    fully settle a rule from the artifact alone.

    KNOWN FLAKE (real defect, not a test bug): this has been observed to fail
    with PL-TRIG-001 "Missing required disclosure 'trigger_disclosure'" when
    extraction does not label the fine-print companion terms as a
    TRIGGER_DISCLOSURE. The artifact is compliant either way, so a medium+
    finding here means the pipeline would wrongly block a compliant creative —
    worth failing over. The message below prints the extracted disclosure types
    so the reader can tell a stage-1 miss from a stage-2 false positive.
    """
    material = [
        f for f in compliant_run.deterministic_findings if _severity_rank(f) >= _MEDIUM_RANK
    ]
    assert not material, (
        "compliant mock raised medium-or-above deterministic findings: "
        + "; ".join(f"{f.rule_id}[{f.severity}] {f.summary}" for f in material)
        + f" | extracted disclosure types: "
        f"{[d.disclosure_type.value for d in compliant_run.disclosures]}"
        + f" | claims: {len(compliant_run.claims)}"
    )


def test_compliant_mock_answer_key_plants_nothing(compliant_run: PipelineRun) -> None:
    """Guard the premise of the test above: this mock really is the clean one."""
    assert not expected_rule_ids(COMPLIANT_MOCK, judgment=False)
    assert not expected_rule_ids(COMPLIANT_MOCK, judgment=True)


def test_compliant_mock_extraction_actually_ran(compliant_run: PipelineRun) -> None:
    """Sanity: a clean result must come from a populated extraction, not an empty one.

    Without this, TEST B would pass just as happily on a screenshot the
    extractor failed to read at all.
    """
    assert len(compliant_run.claims) >= 3, (
        f"expected >=3 claims from {COMPLIANT_MOCK}, got {len(compliant_run.claims)}: "
        f"{[c.text for c in compliant_run.claims]}"
    )
    assert len(compliant_run.disclosures) >= 2, (
        f"expected >=2 disclosures from {COMPLIANT_MOCK}, got "
        f"{len(compliant_run.disclosures)}: {[d.text for d in compliant_run.disclosures]}"
    )
    assert compliant_run.extraction.model, "ExtractionResult recorded no model"


# --------------------------------------------------------------------------- #
# TEST C — version stamping and verifier additivity
# --------------------------------------------------------------------------- #


def test_check_run_stamps_both_versions_and_mode(violation_run: PipelineRun) -> None:
    """The audit trail records what the run executed against."""
    check_run = violation_run.check_run
    assert check_run.rulebook_version == rulebook_manifest_version()
    assert check_run.offer_matrix_version == OFFER_MATRIX_VERSION
    assert check_run.mode == violation_run.submission.mode
    assert check_run.submission_id == violation_run.submission.submission_id


def test_verifier_returns_only_sub_medium_severities(violation_run: PipelineRun) -> None:
    """verify_passed_rules is a soft signal: never medium or above."""
    too_severe = [f for f in violation_run.verifier_findings if _severity_rank(f) >= _MEDIUM_RANK]
    assert not too_severe, (
        "verify_passed_rules returned medium-or-above findings, breaking its "
        "sub-medium contract: "
        + "; ".join(f"{f.rule_id}[{f.severity}]" for f in too_severe)
    )


def test_verifier_does_not_mutate_the_check_run(violation_run: PipelineRun) -> None:
    """The verifier is additive: it hands back extra findings, it does not edit."""
    assert violation_run.findings_len_after_verify == violation_run.findings_len_before_verify, (
        "verify_passed_rules changed the number of findings on the CheckRun "
        f"({violation_run.findings_len_before_verify} -> "
        f"{violation_run.findings_len_after_verify})"
    )
    assert (
        tuple(f.id for f in violation_run.check_run.findings)
        == violation_run.finding_ids_before_verify
    ), "verify_passed_rules altered the identity or order of CheckRun.findings"
