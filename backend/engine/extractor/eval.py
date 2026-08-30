"""Extractor eval harness: runs extract() over every fixture mock screenshot
and scores against the ground-truth answer key (fixtures/expected_findings.json).

Run: python -m backend.engine.extractor.eval
The platform is image-only: evidence is fixtures/<base>.png, resolved locally.
A missing PNG is a HARD error listing every missing file — remedy: run
`python fixtures/render_screenshots.py`, or merge the screenshot-renders PR.

Writes backend/engine/extractor/eval_report.json and prints a table.

Scoring (per fixtures/README.md semantics):
- Universe: expected findings with non-null claim_text AND non-null
  expected_claim_type (claim-anchored findings; absence-type findings have no
  claim to extract and are excluded from extractor scoring).
- Span match: transcription-tolerant normalized substring containment in
  either direction between expected claim_text and an extracted claim's text.
  With image evidence, literal text fidelity is bounded by vision
  transcription, so the normalizer is deliberately wide: NFKC + casefold +
  whitespace collapse + dash/quote unification (em/en dash -> hyphen, curly ->
  straight quotes) + ellipsis/nbsp cleanup.
- claim recall  = span-matched expected findings / universe
- type accuracy = span matches whose best-matching claim LISTS the expected
  claim_type among its claim_types / span-matched expected findings
  (claims are multi-label per contracts amendment #4 — membership, not equality)
- On MISSES the report and stdout include every extracted claim text for that
  mock next to the expected text, so mismatch causes (transcription drift vs.
  true extraction miss) are diagnosable at a glance.
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
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

from backend.contracts import Product
from backend.engine.extractor.extract import ExtractionContext, extract

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "fixtures"
REPORT_PATH = Path(__file__).parent / "eval_report.json"
MISSING_PNG_REMEDY = (
    "run `python fixtures/render_screenshots.py`, or merge the screenshot-renders PR"
)

_PUNCT_MAP = str.maketrans({
    "—": "-", "–": "-", "−": "-",              # em/en dash, minus -> hyphen
    "‘": "'", "’": "'",                               # curly single quotes
    "“": '"', "”": '"',                               # curly double quotes
    " ": " ",                                              # nbsp
    "…": "...",                                            # ellipsis
})


def _norm(s: str) -> str:
    """Transcription-tolerant normalizer (see module docstring)."""
    s = unicodedata.normalize("NFKC", htmllib.unescape(s)).translate(_PUNCT_MAP)
    return re.sub(r"\s+", " ", s).strip().casefold()


def _resolve_screenshots(fnames: list[str]) -> dict[str, Path]:
    """Map each mock key (…html basename, per the answer key) to its local PNG.

    Image-only platform: a missing render is a HARD error, raised up front for
    ALL missing files so one run reports the full remedy list."""
    resolved: dict[str, Path] = {}
    missing: list[str] = []
    for fname in fnames:
        png = FIXTURES / (fname.replace(".html", "") + ".png")
        if png.exists():
            resolved[fname] = png
        else:
            missing.append(png.name)
    if missing:
        raise FileNotFoundError(
            "missing screenshot renders in fixtures/: "
            + ", ".join(sorted(missing))
            + f" — {MISSING_PNG_REMEDY}"
        )
    return resolved


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
    screenshots = _resolve_screenshots(sorted(entries))  # hard error if any PNG missing

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
        result = extract(screenshots[fname], ctx)
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
                got = [t.value for t in best.claim_types]
                ok = f["expected_claim_type"] in got  # membership: claims are multi-label
                type_hits += ok
                rows.append({"claim_text": f["claim_text"], "matched": True,
                             "expected_type": f["expected_claim_type"],
                             "got_types": got, "type_ok": ok})
            else:
                rows.append({"claim_text": f["claim_text"], "matched": False,
                             "expected_type": f["expected_claim_type"],
                             "extracted_texts": [c.text for c in claims]})
                print(f"  MISS in {fname}: expected {f['claim_text']!r}", file=sys.stderr)
                for c in claims:
                    print(f"    extracted: {c.text!r}", file=sys.stderr)
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
        "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        "claims_are_multi_label": True,  # amendment #4: expected type scored by MEMBERSHIP in claim_types
        "evidence_format": "png",  # image-only platform
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
