"""Extractor eval harness: runs extract() over every fixture mock and scores
against the ground-truth answer key (fixtures/expected_findings.json).

Run: python -m backend.engine.extractor.eval
Writes backend/engine/extractor/eval_report.json and prints a table.

Scoring (per fixtures/README.md semantics):
- Universe: expected findings with non-null claim_text AND non-null
  expected_claim_type (claim-anchored findings; absence-type findings have no
  claim to extract and are excluded from extractor scoring).
- Span match: normalized (entity-decoded, whitespace-collapsed, lowercased)
  substring containment in either direction between expected claim_text and an
  extracted claim's text.
- claim recall  = span-matched expected findings / universe
- type accuracy = span matches whose best-matching claim carries the expected
  claim_type / span-matched expected findings
- Unmatched extracted claims are reported as informational volume, NOT errors:
  the answer key enumerates planted VIOLATIONS, while the extractor correctly
  emits every claim including compliant ones.
"""

from __future__ import annotations

import csv
import html as htmllib
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from backend.contracts import Product
from backend.engine.extractor.extract import ExtractionContext, extract

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "fixtures"
REPORT_PATH = Path(__file__).parent / "eval_report.json"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(s)).strip().lower()


def _load_manifest() -> dict[str, dict]:
    """asset file -> {product, surface, partner, mode} from the submissions manifest."""
    out: dict[str, dict] = {}
    with open(FIXTURES / "submissions.csv") as f:
        for row in csv.DictReader(f):
            for asset in row["asset_files"].split(";"):
                out[asset.strip()] = {
                    "product": row["product"],
                    "surface": row["surface"],
                    "partner": row["partner"],
                    "mode": row["mode"],
                }
    return out


def run_eval() -> dict:
    load_dotenv(REPO_ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set (env or .env) — live eval cannot run.", file=sys.stderr)
        sys.exit(2)

    key = json.loads((FIXTURES / "expected_findings.json").read_text())
    entries = {k: v for k, v in key.items() if not k.startswith("_")}
    manifest = _load_manifest()

    per_mock: list[dict] = []
    tot_universe = tot_span = tot_type = tot_unmatched = 0

    for fname, entry in sorted(entries.items()):
        meta = manifest.get(fname, {"product": entry.get("product", "personal_loan"), "surface": "", "partner": "", "mode": entry.get("mode", "")})
        ctx = ExtractionContext(
            product=Product(meta["product"]),
            surface=meta["surface"],
            partner=meta["partner"],
            evidence_id=fname.replace(".html", ""),
        )
        result = extract(FIXTURES / fname, ctx)
        claims = result.claims

        expected = [
            f for f in entry.get("expected_findings", [])
            if f.get("claim_text") and f.get("expected_claim_type")
        ]
        matched_claim_ids: set[str] = set()
        rows = []
        span_hits = type_hits = 0
        for f in expected:
            want = _norm(f["claim_text"])
            best = None
            for c in claims:
                got = _norm(c.text)
                if want in got or got in want:
                    best = c
                    break
            if best is not None:
                span_hits += 1
                matched_claim_ids.add(best.id)
                ok = best.claim_type.value == f["expected_claim_type"]
                type_hits += ok
                rows.append({"claim_text": f["claim_text"], "matched": True,
                             "expected_type": f["expected_claim_type"],
                             "got_type": best.claim_type.value, "type_ok": ok})
            else:
                rows.append({"claim_text": f["claim_text"], "matched": False,
                             "expected_type": f["expected_claim_type"]})
        unmatched = len(claims) - len(matched_claim_ids)
        per_mock.append({
            "mock": fname, "mode": meta["mode"],
            "expected_claim_anchored": len(expected),
            "span_matched": span_hits, "type_correct": type_hits,
            "claims_extracted": len(claims),
            "unmatched_extracted_claims": unmatched,
            "disclosures_found": len(result.disclosures),
            "disclosure_types": sorted({d.disclosure_type.value for d in result.disclosures}),
            "usage": result.usage, "detail": rows,
        })
        tot_universe += len(expected)
        tot_span += span_hits
        tot_type += type_hits
        tot_unmatched += unmatched
        print(f"{fname}: {span_hits}/{len(expected)} spans, {type_hits} types ok, "
              f"{len(claims)} claims, {len(result.disclosures)} disclosures")

    report = {
        "model": per_mock and extract.__module__ and os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        "universe_claim_anchored_findings": tot_universe,
        "claim_recall": round(tot_span / tot_universe, 3) if tot_universe else None,
        "type_accuracy_on_matched": round(tot_type / tot_span, 3) if tot_span else None,
        "unmatched_extracted_claims_total": tot_unmatched,
        "per_mock": per_mock,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=1))

    print("\n=== extractor eval ===")
    print(f"{'mock':44s} {'recall':>8s} {'types':>6s} {'claims':>7s} {'discl':>6s}")
    for m in per_mock:
        r = f"{m['span_matched']}/{m['expected_claim_anchored']}"
        print(f"{m['mock']:44s} {r:>8s} {m['type_correct']:>6d} {m['claims_extracted']:>7d} {m['disclosures_found']:>6d}")
    print(f"\nclaim recall: {report['claim_recall']}  |  type accuracy on matched: {report['type_accuracy_on_matched']}"
          f"  |  extra claims (informational): {tot_unmatched}")
    print(f"report: {REPORT_PATH}")
    return report


if __name__ == "__main__":
    run_eval()
