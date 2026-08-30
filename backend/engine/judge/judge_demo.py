"""Judge smoke demo (not a test).

Builds a perfect-extractor reading of one violation fixture from the answer
key, loads the llm_judged rules straight from the rulebook JSON files, and:
- with ANTHROPIC_API_KEY available: runs the judge live and prints findings;
- without: prints the assembled prompt (head) so the material is inspectable.

Run: python -m backend.engine.judge.judge_demo
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from backend.contracts import Claim, ClaimType, Disclosure, DisclosureType, RulebookEntry
from backend.engine.judge import build_judge_prompt, run_judge
from backend.ingest.parsers import load_submissions

REPO = Path(__file__).resolve().parents[3]
MOCK = "mock_pl_card_preapproved_guaranteed.html"  # carries a PL-JUDGE-001 expected judgment finding


def _rules() -> list[RulebookEntry]:
    out = []
    for f in ("personal_loan", "credit_card", "mortgage_prequal", "cross_product"):
        for r in json.loads((REPO / "rulebook" / f"{f}.json").read_text())["rules"]:
            out.append(RulebookEntry.model_validate(r))
    return [r for r in out if r.check_kind.value == "llm_judged"]


def _perfect_extraction() -> tuple[list[Claim], list[Disclosure]]:
    key = json.loads((REPO / "fixtures" / "expected_findings.json").read_text())
    entry = key[MOCK]
    claims = []
    for i, f in enumerate(e for e in entry["expected_findings"] if e.get("claim_text")):
        claims.append(
            Claim(
                id=f"demo-{i:03d}",
                claim_types=[ClaimType(f["expected_claim_type"])],
                text=f["claim_text"],
                location=f.get("location_note", "unknown"),
                source_evidence_id=MOCK.replace(".html", ""),
                normalized_fields=f.get("expected_normalized_fields", {}),
            )
        )
    # this mock's planted defect includes a MISSING qualifier — one soft-pull
    # disclosure is present in the creative; reflect that single one
    disclosures = [
        Disclosure(
            id="demo-d00",
            disclosure_type=DisclosureType.SOFT_PULL,
            text="Checking your rate won't affect your credit score",
            location="fine print",
            prominence="fine_print",
        )
    ]
    return claims, disclosures


def main() -> None:
    load_dotenv(REPO / ".env")
    subs = load_submissions(str(REPO / "fixtures" / "submissions.csv"))
    submission = next(s for s in subs if MOCK in ";".join(s.asset_files))
    claims, disclosures = _perfect_extraction()
    rules = _rules()
    png = REPO / "fixtures" / MOCK.replace(".html", ".png")

    if os.environ.get("ANTHROPIC_API_KEY"):
        findings = run_judge(
            submission=submission,
            claims=claims,
            disclosures=disclosures,
            evidence_path=png,
            rulebook=rules,
        )
        print(f"{len(findings)} judgment finding(s):")
        for f in findings:
            print(f"- {f.rule_id} [{f.severity.value}] {f.summary}")
            print(f"  {f.explanation[:220]}")
            if f.suggested_redline:
                print(f"  redline: {f.suggested_redline[:160]}")
    else:
        applicable = [r for r in rules if r.product == submission.product]
        prompt = build_judge_prompt(applicable, submission, claims, disclosures)
        print(f"(no ANTHROPIC_API_KEY — prompt preview, {len(prompt)} chars, "
              f"{len(applicable)} applicable rules)\n")
        print(prompt[:2600])


if __name__ == "__main__":
    main()
