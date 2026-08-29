#!/usr/bin/env python3
"""Validate every rulebook JSON entry against backend.contracts.RulebookEntry.

Run from the repo root (or anywhere):  python rulebook/validate_rulebook.py
Exits non-zero if any file is missing, any entry fails validation, or the
manifest counts disagree with the actual rule files.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

RULEBOOK_DIR = Path(__file__).resolve().parent
REPO_ROOT = RULEBOOK_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.contracts import RulebookEntry  # noqa: E402


def main() -> int:
    manifest = json.loads((RULEBOOK_DIR / "manifest.json").read_text())
    errors: list[str] = []
    by_product: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    seen_ids: set[str] = set()
    total = 0

    for filename in manifest["rule_files"]:
        path = RULEBOOK_DIR / filename
        if not path.exists():
            errors.append(f"missing rule file: {filename}")
            continue
        data = json.loads(path.read_text())
        for raw in data.get("rules", []):
            total += 1
            rid = raw.get("rule_id", f"<missing id in {filename}>")
            if rid in seen_ids:
                errors.append(f"duplicate rule_id: {rid}")
            seen_ids.add(rid)
            try:
                entry = RulebookEntry(**raw)
            except Exception as exc:  # pydantic ValidationError
                errors.append(f"{filename}:{rid}: {exc}")
                continue
            by_product[entry.product.value] += 1
            by_kind[entry.check_kind.value] += 1
            if not entry.citation_url.startswith("http"):
                errors.append(f"{rid}: citation_url is not a URL")

    print(f"rulebook_version: {manifest['rulebook_version']}")
    print(f"total rules: {total}")
    print(f"by product: {dict(by_product)}")
    print(f"by check_kind: {dict(by_kind)}")

    declared = manifest.get("counts", {})
    if declared.get("total") != total:
        errors.append(f"manifest total {declared.get('total')} != actual {total}")
    if declared.get("by_product") != dict(by_product):
        errors.append(f"manifest by_product {declared.get('by_product')} != actual {dict(by_product)}")
    if declared.get("by_check_kind") != dict(by_kind):
        errors.append(f"manifest by_check_kind {declared.get('by_check_kind')} != actual {dict(by_kind)}")

    if errors:
        print("\nVALIDATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\nVALIDATION OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
