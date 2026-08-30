#!/usr/bin/env python3
"""Generate rulebook/PROVENANCE.md — a visual provenance map tracing every rule
back to the top-level body of law it operationalizes.

Usage:
    python rulebook/generate_provenance.py           # (re)write PROVENANCE.md
    python rulebook/generate_provenance.py --check   # exit 1 if committed file is stale

The generator reads manifest.json, the rule files it lists, and
claim_types_legal_map.json — it never hand-lists rules. Families are derived
from each authority's body/regime via CANONICAL_FAMILIES below; an authority
that fits no family is a loud generator error, which is what keeps the mapping
complete as new bodies of law enter the rulebook.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

RULEBOOK_DIR = Path(__file__).resolve().parent

PRODUCTS = ("personal_loan", "credit_card", "mortgage_prequal")

# Canonical top-level bodies-of-law families, in display order.
FAMILIES = [
    ("regz", "TILA / Regulation Z"),
    ("regn", "Regulation N (MAP Rule)"),
    ("udaap", "FTC Act §5 / CFPA UDAAP deception floor"),
    ("fcra", "FCRA (Fair Credit Reporting Act)"),
    ("endorse", "FTC Endorsement Guides"),
    ("state", "State & licensing law (incl. SAFE Act)"),
]
FAMILY_NAME = dict(FAMILIES)


def family_of(authority: dict) -> str:
    """Map one LegalAuthority to a canonical family key. Order matters."""
    body = authority.get("body", "")
    regime = authority.get("regime", "")
    if "Endorsement Guides" in body:
        return "endorse"
    if "Regulation Z" in body:
        return "regz"
    if "Regulation N" in body:
        return "regn"
    if "Fair Credit Reporting" in body or "Regulation V" in body:
        return "fcra"
    if "UDAAP" in body or "Consumer Financial Protection Act" in body or "FTC Act" in body or "substantiation" in body:
        return "udaap"
    if regime in ("state_statute", "state_regulation") or "SAFE Mortgage Licensing" in body:
        return "state"
    raise SystemExit(
        f"generator error: authority fits no canonical family: {body!r} (regime={regime!r}). "
        f"Add a mapping in CANONICAL family_of() before regenerating."
    )


def first_sentence(text: str) -> str:
    for stop in (". ", "? ", "! "):
        if stop in text:
            return text.split(stop, 1)[0] + stop.strip()
    return text


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def load() -> tuple[dict, list[dict], dict]:
    manifest = json.loads((RULEBOOK_DIR / "manifest.json").read_text())
    rules: list[dict] = []
    for filename in manifest["rule_files"]:
        rules.extend(json.loads((RULEBOOK_DIR / filename).read_text())["rules"])
    claim_map = json.loads((RULEBOOK_DIR / "claim_types_legal_map.json").read_text())
    return manifest, rules, claim_map


def generate() -> str:
    manifest, rules, claim_map = load()
    version = manifest["rulebook_version"]

    primary_family: dict[str, str] = {}
    secondary_families: dict[str, list[tuple[str, str]]] = defaultdict(list)  # rule_id -> [(family, citation)]
    per_family_rules: dict[str, list[dict]] = defaultdict(list)
    per_family_product: dict[str, Counter] = defaultdict(Counter)
    secondary_refs: dict[str, list[str]] = defaultdict(list)  # family -> rule_ids anchored there secondarily
    by_binding: Counter = Counter()
    multi_authority = 0

    for rule in rules:
        rid = rule["rule_id"]
        auths = rule["authorities"]
        fam = family_of(auths[0])
        primary_family[rid] = fam
        per_family_rules[fam].append(rule)
        per_family_product[fam][rule["product"]] += 1
        if len(auths) > 1:
            multi_authority += 1
            for a in auths[1:]:
                f2 = family_of(a)
                if f2 != fam:
                    secondary_families[rid].append((f2, a["citation"]))
                    secondary_refs[f2].append(rid)
        b = rule["parameters"].get("binding")
        if b:
            by_binding[b] += 1

    total = len(rules)
    per_product_total = Counter(r["product"] for r in rules)

    out: list[str] = []
    out.append("# Rulebook Provenance Map")
    out.append("")
    out.append(f"**Where does each rule come from?** Every rule in rulebook v{version} traces to one")
    out.append("of six top-level bodies of law (its *primary* family — the first entry in its")
    out.append("`authorities` list); rules resting on multiple bodies are cross-referenced. This file")
    out.append("is **generated** by `rulebook/generate_provenance.py` — do not hand-edit (see Maintenance).")
    out.append("")
    out.append("## Map")
    out.append("")
    out.append("```mermaid")
    out.append("flowchart LR")
    out.append(f'    LAW["US consumer-credit advertising law"]')
    for key, name in FAMILIES:
        count = len(per_family_rules[key])
        out.append(f'    LAW --> {key}["{name} — {count} rules"]')
    for key, _name in FAMILIES:
        for product in PRODUCTS:
            n = per_family_product[key].get(product, 0)
            if n:
                out.append(f'    {key} --> {key}_{product}["{product} ({n})"]')
    out.append("```")
    out.append("")

    for key, name in FAMILIES:
        fam_rules = per_family_rules[key]
        out.append(f"## {name}")
        out.append("")
        out.append(f"{len(fam_rules)} rules anchored here as primary authority.")
        out.append("")
        out.append("| rule_id | product | pinpoint citation | severity | check_kind | what it checks | also anchored in |")
        out.append("|---|---|---|---|---|---|---|")
        for rule in fam_rules:
            rid = rule["rule_id"]
            params = rule["parameters"]
            if rule["check_kind"] == "deterministic":
                desc = params.get("check_description", "")
            else:
                desc = first_sentence(params.get("judge_focus", ""))
            also = "; ".join(
                f"{FAMILY_NAME[f]} ({cite})" for f, cite in secondary_families.get(rid, [])
            ) or "—"
            out.append(
                f"| {rid} | {rule['product']} | {cell(rule['authorities'][0]['citation'])} "
                f"| {rule['severity']} | {rule['check_kind']} | {cell(desc)} | {cell(also)} |"
            )
        out.append("")
        refs = secondary_refs.get(key, [])
        if refs:
            out.append(f"*Also a secondary anchor for:* {', '.join(refs)}")
            out.append("")

    out.append("## Claim-type taxonomy anchors")
    out.append("")
    out.append("The ClaimType enum (see `claim_types_legal_map.json`) anchors to the same families:")
    out.append("")
    out.append("| claim_type | primary family | pinpoint citation |")
    out.append("|---|---|---|")
    for ct_name, spec in claim_map["claim_types"].items():
        a0 = spec["authorities"][0]
        out.append(f"| {ct_name} | {FAMILY_NAME[family_of(a0)]} | {cell(a0['citation'])} |")
    out.append("")

    out.append("## Stats")
    out.append("")
    out.append(f"- Generated from rulebook_version: **{version}**")
    out.append(f"- Total rules: **{total}**")
    fam_counts = ", ".join(f"{name} {len(per_family_rules[key])}" for key, name in FAMILIES)
    out.append(f"- Rules per family (primary): {fam_counts}")
    prod_counts = ", ".join(f"{p} {per_product_total[p]}" for p in PRODUCTS)
    out.append(f"- Rules per product: {prod_counts}")
    out.append(f"- Multi-authority rules: **{multi_authority}** of {total}")
    out.append(
        f"- Deterministic binding split: token {by_binding.get('token', 0)} / concept {by_binding.get('concept', 0)}"
        f" (llm_judged: {sum(1 for r in rules if r['check_kind'] == 'llm_judged')})"
    )
    out.append("")

    out.append("## Maintenance")
    out.append("")
    out.append("- **This file is generated. Do not hand-edit.** Regenerate with:")
    out.append("  `python rulebook/generate_provenance.py`")
    out.append("- CI / reviewer guard: `python rulebook/generate_provenance.py --check` exits")
    out.append("  non-zero when this file is stale relative to the rule files.")
    out.append("- **When a NEW body of law enters the rulebook:** the generator fails loudly")
    out.append("  (\"authority fits no canonical family\") until the new authority is placed in")
    out.append("  `family_of()` / `FAMILIES` in `generate_provenance.py`. That failure is the")
    out.append("  mechanism that keeps this map complete — do not suppress it.")
    out.append("")
    return "\n".join(out)


def main() -> int:
    content = generate()
    target = RULEBOOK_DIR / "PROVENANCE.md"
    if "--check" in sys.argv:
        if not target.exists() or target.read_text() != content:
            print("PROVENANCE.md is STALE — regenerate with: python rulebook/generate_provenance.py")
            return 1
        print("PROVENANCE.md is up to date")
        return 0
    target.write_text(content)
    print(f"wrote {target} ({len(content)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
