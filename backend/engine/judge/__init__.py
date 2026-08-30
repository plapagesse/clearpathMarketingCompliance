"""LLM judge — gray-area assessment of llm_judged rulebook rules.

Public interface (pinned; the isolated test suite imports exactly this):
- run_judge(*, submission, claims, disclosures, evidence_path, rulebook,
            model=None, client=None) -> list[Finding]
- build_judge_prompt(rules, submission, claims, disclosures) -> str
"""

from backend.engine.judge.judge import build_judge_prompt, run_judge

__all__ = ["run_judge", "build_judge_prompt"]
