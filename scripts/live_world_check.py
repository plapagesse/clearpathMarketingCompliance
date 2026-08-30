#!/usr/bin/env python3
"""Run the REAL pipeline over the fixture world and print the label table.

    python scripts/live_world_check.py                       # all 16 mocks
    python scripts/live_world_check.py --only SUB-2026-0143,SUB-2026-0149
    python scripts/live_world_check.py --no-judge            # extraction + rules only
    python scripts/live_world_check.py --json report.json

Why this exists
---------------
The offline suites feed the checker PERFECT extractor output: claims typed
from the answer key, disclosures typed from the partner manifest, and the
mock's own HTML text as artifact_text. Every one of those is a fact the live
pipeline has to earn from a vision model, and a whole class of defect lives in
exactly that gap — a compliant creative that the offline suite certifies clean
while the live path raises a HIGH finding on it, because the extractor typed a
Reg Z companion sentence as a neighbouring disclosure, or put a percentage fee
in a dollar field, or the caller never passed the creative's text at all.

Nothing in a green `pytest` run can see that. This script closes the loop: it
runs extraction against the fixture PNGs the way the API does, then the same
deterministic checker and the same judge, and compares the AI label each
submission lands on against the label the answer key says it deserves. Drift of
this class becomes a measurable number rather than something noticed by
accident in the UI.

It is opt-in and costs real money: one extraction call per submission, plus one
judge call unless --no-judge. Roughly $0.05-0.15 and 20-60s per submission.
It reads fixtures and writes nothing — no database, no uploads.

Labels are the UI's own three buckets (backend/api/common.py): a run whose
worst finding is high or critical is ISSUES, medium is REVIEW, anything less
(or nothing at all) is CLEAN. Expected labels come from
fixtures/expected_findings.json the same way.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.common import ATTENTION_FOR_STATUS, SEVERITY_RANK, attention  # noqa: E402
from backend.contracts import Product  # noqa: E402
from backend.engine.checker import load_rulebook, run_checks  # noqa: E402
from backend.ingest.parsers import load_offer_matrix, load_submissions  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures"
RULEBOOK_DIR = REPO_ROOT / "rulebook"
ANSWER_KEY = FIXTURES / "expected_findings.json"

# attention bucket -> the label the UI's AI-status selector shows.
LABEL_FOR_ATTENTION = {v: k for k, v in ATTENTION_FOR_STATUS.items()}


def expected_labels() -> dict[str, tuple[str, list[str]]]:
    """{submission_id: (label, [planted rule ids])} from the answer key."""
    key = json.loads(ANSWER_KEY.read_text())
    out: dict[str, tuple[str, list[str]]] = {}
    for name, entry in key.items():
        if name.startswith("_"):
            continue
        planted = entry["expected_findings"]
        severities = [f["severity"] for f in planted]
        rule_ids = sorted({r for f in planted for r in f["expected_rule_ids"]})
        out[entry["submission_id"]] = (LABEL_FOR_ATTENTION[attention(severities)], rule_ids)
    return out


def evidence_png(sub) -> Path | None:
    for name in sub.asset_files or []:
        if str(name).lower().endswith(".png"):
            p = FIXTURES / Path(str(name)).name
            return p if p.is_file() else None
    return None


def run_one(sub, cells, rulebook, *, judge: bool, judge_model: str) -> dict:
    """Extract -> run_checks -> run_judge for one submission, as the API does."""
    from backend.engine.extractor.extract import ExtractionContext, extract
    from backend.engine.judge import run_judge

    png = evidence_png(sub)
    if png is None:
        return {"error": "no PNG evidence"}

    started = time.monotonic()
    extraction = extract(
        str(png),
        ExtractionContext(
            product=Product(sub.product),
            surface=sub.surface,
            partner=sub.partner,
            evidence_id=sub.submission_id,
        ),
    )
    run = run_checks(
        submission=sub,
        claims=list(extraction.claims),
        disclosures=list(extraction.disclosures),
        offer_cells=cells,
        offer_matrix_version="live-world-check",
        rulebook=rulebook,
        artifact_text=extraction.artifact_text or None,
    )
    findings = list(run.findings)
    if judge:
        findings += list(
            run_judge(
                submission=sub,
                claims=list(extraction.claims),
                disclosures=list(extraction.disclosures),
                evidence_path=str(png),
                rulebook=rulebook,
                model=judge_model,
            )
        )

    severities = [f.severity.value for f in findings]
    return {
        "label": LABEL_FOR_ATTENTION[attention(severities)],
        "worst": max(severities, key=lambda s: SEVERITY_RANK.get(s, 0), default=None)
        if severities
        else None,
        "findings": len(findings),
        "at_or_above_medium": [
            {"rule_id": f.rule_id, "severity": f.severity.value, "summary": f.summary}
            for f in findings
            if SEVERITY_RANK.get(f.severity.value, 0) >= SEVERITY_RANK["medium"]
        ],
        "claims": len(extraction.claims),
        "disclosures": len(extraction.disclosures),
        "artifact_text_chars": len(extraction.artifact_text or ""),
        "seconds": round(time.monotonic() - started, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", default="", help="comma-separated submission ids")
    ap.add_argument("--no-judge", action="store_true", help="skip the judge call")
    ap.add_argument(
        "--judge-model",
        default=os.environ.get("CLEARPATH_JUDGE_MODEL") or "claude-sonnet-5",
        help="judge model (default: CLEARPATH_JUDGE_MODEL or claude-sonnet-5)",
    )
    ap.add_argument("--json", dest="json_path", default="", help="also write the table as JSON")
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "No ANTHROPIC_API_KEY. This script calls the real models on purpose — "
            "set the key (or put it in .env) to run it.",
            file=sys.stderr,
        )
        return 2

    expected = expected_labels()
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    submissions = [
        s for s in load_submissions(FIXTURES / "submissions.csv")
        if (not wanted or s.submission_id in wanted) and s.submission_id in expected
    ]
    if not submissions:
        print("nothing to run (check --only)", file=sys.stderr)
        return 2

    rulebook = load_rulebook(RULEBOOK_DIR)
    cells = load_offer_matrix(FIXTURES / "offer_matrix.csv")

    calls = len(submissions) * (1 if args.no_judge else 2)
    print(
        f"rulebook {rulebook.version} | {len(submissions)} submissions | ~{calls} model calls"
        + ("" if args.no_judge else f" | judge={args.judge_model}")
    )
    print()

    rows, mismatches = [], 0
    for sub in submissions:
        exp_label, planted = expected[sub.submission_id]
        try:
            got = run_one(
                sub, cells, rulebook, judge=not args.no_judge, judge_model=args.judge_model
            )
        except Exception as exc:  # noqa: BLE001 — one failure must not lose the rest
            got = {"error": f"{type(exc).__name__}: {exc}"}
        row = {
            "submission_id": sub.submission_id,
            "product": sub.product,
            "partner": sub.partner,
            "expected_label": exp_label,
            "planted_rule_ids": planted,
            **got,
        }
        rows.append(row)
        ok = row.get("label") == exp_label
        mismatches += 0 if ok else 1
        mark = "ok " if ok else "MISS"
        print(
            f"{mark} {sub.submission_id}  expected={exp_label:<6} "
            f"actual={str(row.get('label') or row.get('error')):<6} "
            f"worst={str(row.get('worst')):<8} findings={row.get('findings')}"
        )
        for f in row.get("at_or_above_medium", []):
            print(f"       {f['severity']:<8} {f['rule_id'] or '(engine)'}: {f['summary']}")
        if row.get("error"):
            print(f"       error: {row['error']}")

    print()
    print(f"{len(rows) - mismatches}/{len(rows)} submissions landed on their expected label")
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(rows, indent=2))
        print(f"wrote {args.json_path}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
