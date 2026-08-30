#!/usr/bin/env python3
"""Validate the fixture set: file/manifest coverage, mode agreement, and the
strict literal-claim property of expected_findings.json.

Invariants enforced:
  1. Every mock in expected_findings.json exists on disk; every *.html in this
     directory is covered by the key.
  2. Every mock has a submissions.csv row; submission_id and mode agree.
  3. claim_text is STRICTLY literal-or-null: when non-null it must appear
     verbatim in the entity-decoded, tag-stripped, comment-stripped,
     whitespace-collapsed text of the mock. Absence/layout findings use
     claim_text=null and carry a location_note instead.
  4. Every finding has a non-empty location_note.
  5. Fixtures named *compliant* have zero expected findings.
  6. expected_claim_type is one of the 9 legal-entity ClaimType values or null
     (absence-type findings anchor to no claim).
  7. expected_rule_ids is a non-empty list of canonical rulebook v2026.08.3
     rule_ids matching the rule-id pattern.
  8. Every mock HTML has a sibling rendered PNG (same basename) of non-trivial
     size (>5KB) — PNGs are the canonical evidence artifacts the platform
     ingests; regenerate with render_screenshots.py.

Exit code 0 = all pass; 1 = any mismatch.
"""

import csv
import html as htmllib
import json
import pathlib
import re
import sys

FX = pathlib.Path(__file__).parent

# Mirrors backend/contracts.py ClaimType (legal-entity taxonomy, amended
# 2026-08-29 on agent2/rulebook). Kept inline so this validator stays
# stdlib-only and runnable without the backend venv.
CLAIM_TYPES = {
    "triggering_term",
    "rate_or_apr",
    "promotional_or_introductory",
    "fixed_rate_representation",
    "approval_or_prequalification",
    "fee_or_cost",
    "endorsement_or_testimonial",
    "government_affiliation",
    "general_udaap_representation",
}

# Observed canonical forms: PL-TRIG-001, PL-STATE-CAP-001, CC-PRESCREEN-001,
# MTG-TI-001, XP-UDAAP-001-personal_loan (cross-product expansion suffix).
RULE_ID_RE = re.compile(r"^(PL|CC|MTG|XP)-[A-Z]+(-[A-Z]+)*-\d{3}(-[a-z_]+)?$")


def normalized_text(raw_html: str) -> str:
    body = re.sub(r"<!--.*?-->", "", raw_html, flags=re.S)   # comments
    body = re.sub(r"<[^>]+>", " ", body)                      # tags -> space
    body = htmllib.unescape(body)                             # entities
    return re.sub(r"\s+", " ", body).strip()                  # collapse ws


def main() -> int:
    key = json.load(open(FX / "expected_findings.json"))
    mocks = {k: v for k, v in key.items() if not k.startswith("_")}
    subs = {next(a for a in r["asset_files"].split(";") if a.endswith(".html")): r
            for r in csv.DictReader(open(FX / "submissions.csv"))}
    errors: list[str] = []

    for f in mocks:
        if not (FX / f).exists():
            errors.append(f"missing file: {f}")
    for f in FX.glob("*.html"):
        if f.name not in mocks:
            errors.append(f"html not in answer key: {f.name}")

    for f, meta in mocks.items():
        row = subs.get(f)
        if not row:
            errors.append(f"no manifest row for {f}")
            continue
        if row["mode"] != meta["mode"]:
            errors.append(f"mode mismatch {f}: manifest={row['mode']} key={meta['mode']}")
        if row["submission_id"] != meta["submission_id"]:
            errors.append(f"submission_id mismatch {f}")

    for f, meta in mocks.items():
        if not (FX / f).exists():
            continue
        text = normalized_text((FX / f).read_text())
        for i, exp in enumerate(meta["expected_findings"]):
            label = f"{f}[{i}] {exp['rule_area']}"
            if not exp.get("location_note", "").strip():
                errors.append(f"{label}: missing location_note")
            if "expected_claim_type" not in exp:
                errors.append(f"{label}: missing expected_claim_type")
            elif exp["expected_claim_type"] is not None and exp["expected_claim_type"] not in CLAIM_TYPES:
                errors.append(f"{label}: invalid expected_claim_type '{exp['expected_claim_type']}'")
            rids = exp.get("expected_rule_ids")
            if not isinstance(rids, list) or not rids:
                errors.append(f"{label}: expected_rule_ids missing or empty")
            else:
                for rid in rids:
                    if not isinstance(rid, str) or not RULE_ID_RE.match(rid):
                        errors.append(f"{label}: malformed rule_id '{rid}'")
            ct = exp["claim_text"]
            if ct is None:
                continue
            probe = re.sub(r"\s+", " ", htmllib.unescape(ct)).strip()
            if probe not in text:
                errors.append(f"{label}: claim_text not literal in mock: '{probe}'")

    for f in mocks:
        png = (FX / f).with_suffix(".png")
        if not png.exists():
            errors.append(f"{f}: missing sibling PNG render ({png.name})")
        elif png.stat().st_size <= 5000:
            errors.append(f"{f}: PNG render suspiciously small ({png.stat().st_size}B)")

    for f, meta in mocks.items():
        if "compliant" in f and meta["expected_findings"]:
            errors.append(f"compliant fixture has findings: {f}")

    # baseline_submission_id: required join key on verification rows only,
    # must reference an existing pre_publication submission_id in this file.
    by_id = {r["submission_id"]: r for r in subs.values()}
    for r in subs.values():
        sid, mode = r["submission_id"], r["mode"]
        baseline = r.get("baseline_submission_id", "").strip()
        if mode == "verification":
            if not baseline:
                errors.append(f"{sid}: verification row missing baseline_submission_id")
            elif baseline not in by_id:
                errors.append(f"{sid}: baseline_submission_id '{baseline}' not in manifest")
            elif by_id[baseline]["mode"] != "pre_publication":
                errors.append(f"{sid}: baseline '{baseline}' is not a pre_publication row")
        elif baseline:
            errors.append(f"{sid}: pre_publication row must leave baseline_submission_id empty")

    n_findings = sum(len(m["expected_findings"]) for m in mocks.values())
    if errors:
        print("FAIL")
        print(*errors, sep="\n")
        return 1
    print(f"ALL CHECKS PASS — {len(mocks)} mocks, {n_findings} planted findings, "
          f"{len(subs)} manifest rows, claim_text strictly literal-or-null")
    return 0


if __name__ == "__main__":
    sys.exit(main())
