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

Exit code 0 = all pass; 1 = any mismatch.
"""

import csv
import html as htmllib
import json
import pathlib
import re
import sys

FX = pathlib.Path(__file__).parent


def normalized_text(raw_html: str) -> str:
    body = re.sub(r"<!--.*?-->", "", raw_html, flags=re.S)   # comments
    body = re.sub(r"<[^>]+>", " ", body)                      # tags -> space
    body = htmllib.unescape(body)                             # entities
    return re.sub(r"\s+", " ", body).strip()                  # collapse ws


def main() -> int:
    key = json.load(open(FX / "expected_findings.json"))
    mocks = {k: v for k, v in key.items() if not k.startswith("_")}
    subs = {r["asset_files"]: r for r in csv.DictReader(open(FX / "submissions.csv"))}
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
            ct = exp["claim_text"]
            if ct is None:
                continue
            probe = re.sub(r"\s+", " ", htmllib.unescape(ct)).strip()
            if probe not in text:
                errors.append(f"{label}: claim_text not literal in mock: '{probe}'")

    for f, meta in mocks.items():
        if "compliant" in f and meta["expected_findings"]:
            errors.append(f"compliant fixture has findings: {f}")

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
