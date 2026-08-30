"""Checker smoke demo (not a test).

Runs the deterministic checker against the trigger-stale fixture using
key-derived "perfect extraction" claims: the answer key's claim_text /
expected_claim_type / expected_normalized_fields become Claim objects, the
mock's fine print is scanned for the disclosures a perfect extractor would
have found, and the artifact text is the tag-stripped mock HTML.

Run: python -m backend.engine.checker.checker_demo
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from backend.contracts import Claim, ClaimType, Disclosure, DisclosureType
from backend.engine.checker import load_rulebook, run_checks
from backend.ingest.parsers import load_offer_matrix, load_submissions

REPO = Path(__file__).resolve().parents[3]
MOCK = "mock_pl_card_trigger_stale.html"

# fine-print scans a perfect extractor would have produced (demo-only)
_DISCLOSURE_SCANS: list[tuple[DisclosureType, str]] = [
    (DisclosureType.NOT_GUARANTEED, r"not a guarantee|not guaranteed"),
    (DisclosureType.SOFT_PULL, r"won'?t affect your credit|soft credit pull"),
    (DisclosureType.APR_QUALIFIER, r"autopay|excellent credit|creditworthiness"),
    (DisclosureType.TRIGGER_DISCLOSURE, r"repayment terms|number of payments"),
]


def main() -> None:
    key = json.loads((REPO / "fixtures" / "expected_findings.json").read_text())[MOCK]
    html = (REPO / "fixtures" / MOCK).read_text()
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<!--.*?-->", "", html, flags=re.S))

    claims, seen = [], set()
    for i, f in enumerate(key["expected_findings"]):
        if not f.get("claim_text") or f["claim_text"] in seen:
            continue
        seen.add(f["claim_text"])
        claims.append(
            Claim(
                id=f"clm-demo-{i:03d}",
                claim_types=[ClaimType(f["expected_claim_type"])],
                text=f["claim_text"],
                location=f.get("location_note", "unknown"),
                source_evidence_id=MOCK,
                normalized_fields=f.get("expected_normalized_fields", {}),
            )
        )
    disclosures = [
        Disclosure(id=f"dsc-demo-{i:03d}", disclosure_type=dt, text=m.group(0),
                   location="fine print", prominence="fine_print")
        for i, (dt, pattern) in enumerate(_DISCLOSURE_SCANS)
        if (m := re.search(pattern, text, re.IGNORECASE))
    ]

    submission = next(
        s for s in load_submissions(REPO / "fixtures" / "submissions.csv")
        if MOCK in s.asset_files or f"{MOCK.removesuffix('.html')}.png" in s.asset_files
    )
    cells = load_offer_matrix(REPO / "fixtures" / "offer_matrix.csv")
    rulebook = load_rulebook(REPO / "rulebook")

    run = run_checks(
        submission=submission,
        claims=claims,
        disclosures=disclosures,
        offer_cells=cells,
        offer_matrix_version="omx-demo",
        rulebook=rulebook,
        artifact_text=text,
    )

    print(f"submission {run.submission_id} | rulebook {run.rulebook_version} | "
          f"matrix {run.offer_matrix_version} | {len(run.findings)} finding(s)\n")
    for f in run.findings:
        print(f"[{f.severity.value:8s}] {f.rule_id or 'engine':28s} {f.check_class.value:12s} {f.summary}")
        print(f"           {f.explanation[:150]}")
        if f.suggested_redline:
            print(f"           redline: {f.suggested_redline}")
        print()


if __name__ == "__main__":
    main()
