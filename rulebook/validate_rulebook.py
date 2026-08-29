#!/usr/bin/env python3
"""Validate the rulebook: pydantic conformance, per-primitive parameter schemas,
binding/decision-plane audit fields, data_ref resolution, llm_judged enrichment,
citation indexes, manifest counts, and claim_types_legal_map/enum alignment.

Run from the repo root (or anywhere):  python rulebook/validate_rulebook.py
Non-zero exit on any failure. Rules reference shared data via '@<file>.<key>'
strings resolved against rulebook/data/{lexicons,patterns,state_apr_caps}.json;
a dangling reference is a validation failure.

Determinism audit (v2026.08.2): every deterministic rule declares
  binding: token | concept        (does the LAW bind on literal words/layout, or meaning?)
  decision_inputs: text_plane | claim_plane
Constraints: concept+text_plane is invalid; claim_plane phrase/trigger rules carry
safety_net_patterns (secondary detectors) instead of primary pattern keys;
token-bound rules must have at least one resolvable pattern-bearing key.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

RULEBOOK_DIR = Path(__file__).resolve().parent
REPO_ROOT = RULEBOOK_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.contracts import CheckKind, ClaimType, DisclosureType, RulebookEntry  # noqa: E402

DISCLOSURE_VALUES = {d.value for d in DisclosureType}
DATA_FILES = ("lexicons", "patterns", "state_apr_caps")
BINDINGS = {"token", "concept"}
PLANES = {"text_plane", "claim_plane"}
PATTERN_KEYS = ("phrases", "trigger_patterns", "anchor_patterns", "companion_patterns", "detection_ref", "safety_net_patterns")


def load_data() -> dict[str, dict]:
    data = {}
    for stem in DATA_FILES:
        path = RULEBOOK_DIR / "data" / f"{stem}.json"
        data[stem] = json.loads(path.read_text()) if path.exists() else None
    return data


DATA = load_data()


def resolve_ref(value, rid: str, errors: list[str]):
    """Resolve '@file.key' references against the shared data files."""
    if not (isinstance(value, str) and value.startswith("@")):
        return value
    parts = value[1:].split(".", 1)
    if len(parts) != 2 or parts[0] not in DATA_FILES:
        errors.append(f"{rid}: malformed data_ref {value!r}")
        return None
    stem, key = parts
    if DATA[stem] is None:
        errors.append(f"{rid}: data_ref {value!r} — data file rulebook/data/{stem}.json missing")
        return None
    if key not in DATA[stem]:
        errors.append(f"{rid}: dangling data_ref {value!r} (no key {key!r} in {stem}.json)")
        return None
    return DATA[stem][key]


def _is_str_list(v):  # noqa: ANN001
    return isinstance(v, list) and v and all(isinstance(x, str) for x in v)


MATCH_MODES = {"case_insensitive_substring", "case_insensitive_regex"}
COMPARATORS = {"within_range", "equals", "exists_in", "disjoint_from", "not_conflated"}

# check_type -> {required, optional, pattern_key}
# pattern_key: the primary detector field; on claim_plane rules it is REPLACED by
# safety_net_patterns (secondary detectors — the decision runs on claim objects).
PRIMITIVES: dict[str, dict] = {
    "phrase_prohibited": {
        "required": {"match": lambda v: v in MATCH_MODES},
        "optional": {},
        "pattern_key": "phrases",
    },
    "phrase_conditional": {
        "required": {
            "condition_field": lambda v: isinstance(v, str),
            "violates_when": lambda v: isinstance(v, (bool, str)),
        },
        "optional": {"required_qualifier": lambda v: isinstance(v, str)},
        "pattern_key": "phrases",
    },
    "trigger_requires_disclosures": {
        "required": {
            "required_disclosure_types": lambda v: _is_str_list(v) and set(v) <= DISCLOSURE_VALUES,
        },
        "optional": {},
        "pattern_key": "trigger_patterns",
    },
    "element_required": {
        "required": {"element": lambda v: isinstance(v, str)},
        "optional": {
            "applies_when": lambda v: isinstance(v, dict),
            "detection_ref": _is_str_list,  # validated post-resolution
        },
        "pattern_key": None,
    },
    "proximity_required": {
        "required": {
            "anchor_patterns": _is_str_list,
            "companion_patterns": _is_str_list,
            "requirement": lambda v: isinstance(v, str),
        },
        "optional": {},
        "pattern_key": None,
    },
    "ground_truth_consistency": {
        "required": {
            "claim_field": lambda v: isinstance(v, str),
            "matrix_field": lambda v: isinstance(v, str),
            "comparator": lambda v: v in COMPARATORS,
        },
        "optional": {},
        "pattern_key": None,
    },
    "numeric_cap_by_state": {
        "required": {
            "caps_table": lambda v: isinstance(v, dict) and v,
            "compare": lambda v: isinstance(v, str),
        },
        "optional": {},
        "pattern_key": None,
    },
    # Structural primitive (the one documented addition): ALL sub-checks must pass.
    "composite_all": {
        "required": {"checks": lambda v: isinstance(v, list) and v},
        "optional": {},
        "pattern_key": None,
    },
}

LLM_REQUIRED = {
    "judge_focus": lambda v: isinstance(v, str) and v,
    "violation_examples": lambda v: isinstance(v, list) and len(v) >= 2 and all(isinstance(x, str) for x in v),
    "compliant_contrast": lambda v: isinstance(v, str) and v,
    "citation_quote": lambda v: v is None or (isinstance(v, str) and v),
}
LLM_OPTIONAL = {"detect_patterns", "required_disclosure_types", "note"}


def has_resolvable_patterns(params: dict, rid: str, errors: list[str]) -> bool:
    """True if this primitive block (or any composite sub-check) carries at least
    one pattern-bearing key whose value resolves to a non-empty string list."""
    found = False
    for key in PATTERN_KEYS:
        if key in params:
            resolved = resolve_ref(params[key], rid, errors)
            if resolved is not None and _is_str_list(resolved):
                found = True
    for sub in params.get("checks", []) if params.get("check_type") == "composite_all" else []:
        if isinstance(sub, dict) and has_resolvable_patterns(sub, rid, errors):
            found = True
    return found


def validate_primitive(params: dict, rid: str, plane: str | None, errors: list[str], depth: int = 0) -> str | None:
    """Validate one primitive block (top-level rule or composite sub-check)."""
    ct = params.get("check_type")
    if ct not in PRIMITIVES:
        errors.append(f"{rid}: unknown or missing check_type {ct!r}")
        return None
    spec = PRIMITIVES[ct]

    binding = params.get("binding")
    if depth == 0:
        desc = params.get("check_description")
        if not (isinstance(desc, str) and desc.strip()):
            errors.append(f"{rid}: missing check_description")
        if binding not in BINDINGS:
            errors.append(f"{rid}: missing or invalid binding {binding!r} (token|concept)")
        if plane not in PLANES:
            errors.append(f"{rid}: missing or invalid decision_inputs {plane!r} (text_plane|claim_plane)")
        if binding == "concept" and plane == "text_plane":
            errors.append(f"{rid}: invalid combination binding=concept with decision_inputs=text_plane")
        if binding == "token" and not has_resolvable_patterns(params, rid, errors):
            errors.append(f"{rid}: token-bound rule has no resolvable pattern-bearing key")

    allowed = {"check_type", "check_description", "note", *spec["required"], *spec["optional"]}
    if depth == 0:
        allowed |= {"binding", "decision_inputs"}
    pk = spec["pattern_key"]
    if pk:
        allowed |= {pk, "safety_net_patterns"}
        primary = pk in params
        safety = "safety_net_patterns" in params
        if plane == "claim_plane":
            if primary:
                errors.append(f"{rid}: [{ct}] claim_plane rule must use safety_net_patterns, not {pk!r}")
            if not safety:
                errors.append(f"{rid}: [{ct}] claim_plane rule missing safety_net_patterns")
        else:  # text_plane (or sub-check inheriting it)
            if safety:
                errors.append(f"{rid}: [{ct}] text_plane rule must use {pk!r}, not safety_net_patterns")
            if not primary:
                errors.append(f"{rid}: [{ct}] missing required key {pk!r}")
        for key in (pk, "safety_net_patterns"):
            if key in params:
                resolved = resolve_ref(params[key], rid, errors)
                if resolved is not None and not _is_str_list(resolved):
                    errors.append(f"{rid}: [{ct}] malformed value for {key!r}")
    if ct == "element_required" and depth == 0:
        if plane == "text_plane" and "detection_ref" not in params:
            errors.append(f"{rid}: [element_required] text_plane rule requires detection_ref")

    for key in params:
        if key not in allowed:
            errors.append(f"{rid}: [{ct}] unexpected parameter key {key!r}")
    for key, pred in spec["required"].items():
        if key not in params:
            errors.append(f"{rid}: [{ct}] missing required key {key!r}")
            continue
        resolved = resolve_ref(params[key], rid, errors)
        if resolved is not None and not pred(resolved):
            errors.append(f"{rid}: [{ct}] malformed value for {key!r}")
    for key, pred in spec["optional"].items():
        if key in params:
            resolved = resolve_ref(params[key], rid, errors)
            if resolved is not None and not pred(resolved):
                errors.append(f"{rid}: [{ct}] malformed value for optional {key!r}")
    if "note" in params and not isinstance(params["note"], str):
        errors.append(f"{rid}: [{ct}] note must be a string")
    if ct == "composite_all":
        if depth > 0:
            errors.append(f"{rid}: composite_all may not nest")
        for i, sub in enumerate(params.get("checks", [])):
            if not isinstance(sub, dict):
                errors.append(f"{rid}: composite_all checks[{i}] is not an object")
                continue
            validate_primitive(sub, f"{rid}.checks[{i}]", plane, errors, depth + 1)
    return ct


def main() -> int:
    manifest = json.loads((RULEBOOK_DIR / "manifest.json").read_text())
    errors: list[str] = []

    for stem in DATA_FILES:
        if DATA[stem] is None:
            errors.append(f"missing data file: rulebook/data/{stem}.json")

    map_path = RULEBOOK_DIR / "claim_types_legal_map.json"
    if map_path.exists():
        mapped = set(json.loads(map_path.read_text()).get("claim_types", {}))
        enum_values = {c.value for c in ClaimType}
        if mapped != enum_values:
            errors.append(
                f"claim_types_legal_map.json misaligned with ClaimType enum "
                f"(missing={sorted(enum_values - mapped)}, extra={sorted(mapped - enum_values)})"
            )
    else:
        errors.append("missing claim_types_legal_map.json")

    by_product: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    by_check_type: Counter[str] = Counter()
    by_binding: Counter[str] = Counter()
    seen_ids: set[str] = set()
    total = 0

    for filename in manifest["rule_files"]:
        path = RULEBOOK_DIR / filename
        if not path.exists():
            errors.append(f"missing rule file: {filename}")
            continue
        data = json.loads(path.read_text())
        used_citations: set[str] = set()
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
            used_citations.add(entry.citation_url)

            if entry.check_kind == CheckKind.DETERMINISTIC:
                plane = entry.parameters.get("decision_inputs")
                ct = validate_primitive(entry.parameters, rid, plane, errors)
                if ct:
                    by_check_type[ct] += 1
                b = entry.parameters.get("binding")
                if b in BINDINGS:
                    by_binding[b] += 1
            else:
                allowed = set(LLM_REQUIRED) | LLM_OPTIONAL
                for key in entry.parameters:
                    if key not in allowed:
                        errors.append(f"{rid}: [llm_judged] unexpected parameter key {key!r}")
                for key, pred in LLM_REQUIRED.items():
                    if key not in entry.parameters:
                        errors.append(f"{rid}: [llm_judged] missing required key {key!r}")
                    elif not pred(entry.parameters[key]):
                        errors.append(f"{rid}: [llm_judged] malformed value for {key!r}")
                if "required_disclosure_types" in entry.parameters:
                    v = entry.parameters["required_disclosure_types"]
                    if not (_is_str_list(v) and set(v) <= DISCLOSURE_VALUES):
                        errors.append(f"{rid}: [llm_judged] malformed required_disclosure_types")

        declared_index = data.get("citation_index")
        if declared_index is None:
            errors.append(f"{filename}: missing citation_index")
        elif set(declared_index) != used_citations:
            missing = used_citations - set(declared_index)
            extra = set(declared_index) - used_citations
            errors.append(f"{filename}: citation_index mismatch (missing={sorted(missing)}, extra={sorted(extra)})")

    print(f"rulebook_version: {manifest['rulebook_version']}")
    print(f"total rules: {total}")
    print(f"by product: {dict(by_product)}")
    print(f"by check_kind: {dict(by_kind)}")
    print(f"by check_type (deterministic primitives): {dict(by_check_type)}")
    print(f"by binding (deterministic): {dict(by_binding)}")

    declared = manifest.get("counts", {})
    if declared.get("total") != total:
        errors.append(f"manifest total {declared.get('total')} != actual {total}")
    if declared.get("by_product") != dict(by_product):
        errors.append(f"manifest by_product {declared.get('by_product')} != actual {dict(by_product)}")
    if declared.get("by_check_kind") != dict(by_kind):
        errors.append(f"manifest by_check_kind {declared.get('by_check_kind')} != actual {dict(by_kind)}")
    if declared.get("by_check_type") != dict(by_check_type):
        errors.append(f"manifest by_check_type {declared.get('by_check_type')} != actual {dict(by_check_type)}")
    if declared.get("by_binding") != dict(by_binding):
        errors.append(f"manifest by_binding {declared.get('by_binding')} != actual {dict(by_binding)}")

    if errors:
        print("\nVALIDATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\nVALIDATION OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
