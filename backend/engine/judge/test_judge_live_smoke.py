"""Live smoke test: ONE real-model call through run_judge.

This is the one test in the judge suite that exercises the real model, on the
one fixture whose gray-area violation is unambiguous: the pre-approved /
"Guaranteed approval" personal-loan mock (SUB-2026-0146), whose approved-v7
approval-not-guaranteed qualifier is missing from the fine print (the answer
key's judgment-class row -> PL-JUDGE-001). Positive direction only — a live
negative assertion ("the model finds nothing") would be flaky by nature.

Cost: ~1-2 cents per run at the default model (claude-haiku-4-5). Offline
suites skip it: the module is skipped unless a real ANTHROPIC_API_KEY is
available, so the no-key CI/dev loop stays hermetic and free.

NOTE: conftest's autouse _dummy_api_key fixture masks the environment with a
fake key for every test in this package; this test re-injects the real
credentials (key + optional workspace id) via monkeypatch for its one call.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from backend.contracts import CheckClass

from backend.engine.judge import run_judge

try:  # package/namespace mode
    import backend.engine.judge.conftest as C
except ImportError:  # flat fallback: this directory is pytest's rootdir insert
    import conftest as C

# Same repo-root .env that judge.py's client factory loads
# (extract._make_client: load_dotenv(REPO_ROOT / ".env")).
REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = dotenv_values(REPO_ROOT / ".env")

# The user runs evals on Haiku — cheap and sufficient for an unambiguous
# violation. ANTHROPIC_MODEL still wins when set (mirrors run_judge's default).
DEFAULT_LIVE_MODEL = "claude-haiku-4-5"


def _live_value(name: str) -> str | None:
    """Credential lookup mirroring _make_client: process env first, then the
    repo-root .env (read here without mutating os.environ)."""
    return os.environ.get(name) or _ENV_FILE.get(name) or None


_API_KEY = _live_value("ANTHROPIC_API_KEY")

pytestmark = pytest.mark.skipif(
    _API_KEY is None,
    reason=(
        "live smoke test skipped: no real Anthropic API key. Enable it by "
        "setting ANTHROPIC_API_KEY in the environment or in "
        f"{REPO_ROOT / '.env'} (the same file judge.py's client factory loads)."
    ),
)


def test_live_judge_flags_preapproved_guaranteed_mock(
    monkeypatch, rulebook, expected_findings, preapproved_submission, preapproved_claims
):
    """The real model, judging the real PNG against the real rulebook, must
    flag the missing approval-not-guaranteed qualifier (PL-JUDGE-001 — the
    judgment-class row in the answer key), and everything it returns must be a
    well-formed judgment finding on a real llm_judged rule."""
    # Restore real credentials over conftest's autouse dummy key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", _API_KEY)
    workspace_id = _live_value("ANTHROPIC_WORKSPACE_ID")
    if workspace_id:
        monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", workspace_id)

    findings = run_judge(
        submission=preapproved_submission,
        claims=preapproved_claims,
        disclosures=[],  # the qualifier fine print is missing — that IS the violation
        evidence_path=C.EVIDENCE["preapproved"],
        rulebook=rulebook,
        model=os.environ.get("ANTHROPIC_MODEL", DEFAULT_LIVE_MODEL),
    )

    # 1) The unambiguous violation must produce at least one finding.
    assert findings, "live judge returned no findings for the guaranteed-approval mock"

    # 2) At least one finding lands on the answer key's judgment-class rule(s)
    #    for this mock (read from the key, not hardcoded — today that set is
    #    exactly {PL-JUDGE-001}).
    judgment_rows = [
        row
        for row in expected_findings["mock_pl_card_preapproved_guaranteed.html"]["expected_findings"]
        if row["check_class"] == "judgment"
    ]
    expected_ids = {rid for row in judgment_rows for rid in row["expected_rule_ids"]}
    assert expected_ids, "answer key lost its judgment-class row for the preapproved mock"
    returned_ids = {f.rule_id for f in findings}
    assert returned_ids & expected_ids, (
        f"live judge found {sorted(returned_ids)} but none of the answer key's "
        f"judgment-class rule ids {sorted(expected_ids)}"
    )

    # 3) Every returned finding is a judgment finding on a real llm_judged rule.
    llm_rule_ids = {r.rule_id for r in C.llm_rules(rulebook)}
    for f in findings:
        assert f.check_class == CheckClass.JUDGMENT, f"{f.rule_id}: check_class={f.check_class}"
        assert f.rule_id in llm_rule_ids, f"unknown llm_judged rule id: {f.rule_id}"
