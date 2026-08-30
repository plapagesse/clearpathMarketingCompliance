"""load_rulebook: version, deterministic/llm partition, @data-ref resolution.

Spec sources: rulebook/manifest.json (declared counts, version),
rulebook/README.md (closed check_type vocabulary, data-ref semantics:
"A dangling reference fails validation"), rulebook/data/*.json.
"""

from __future__ import annotations

import json
import shutil

import pytest

from conftest import (
    RULEBOOK_DIR,
    RULEBOOK_VERSION,
    deterministic_ids,
    llm_judged_ids,
    rule_by_id,
)
from backend.engine.checker import load_rulebook

CLOSED_CHECK_TYPES = {
    "phrase_prohibited",
    "phrase_conditional",
    "trigger_requires_disclosures",
    "element_required",
    "proximity_required",
    "ground_truth_consistency",
    "numeric_cap_by_state",
    "composite_all",
}


def test_version_and_partition_counts(rulebook):
    # manifest.json: 2026.08.3; 51 rules = 38 deterministic + 13 llm_judged
    assert rulebook.version == RULEBOOK_VERSION
    det = deterministic_ids(rulebook)
    llm = llm_judged_ids(rulebook)
    assert len(rulebook.deterministic_rules) == 38
    assert len(rulebook.llm_judged_rules) == 13
    assert len(det) == 38 and len(llm) == 13  # rule_ids unique within kind
    assert det.isdisjoint(llm)
    assert len(det | llm) == 51


def test_known_rules_land_in_the_right_partition(rulebook):
    det = deterministic_ids(rulebook)
    llm = llm_judged_ids(rulebook)
    for rid in ("PL-TRIG-001", "PL-TRUTH-001", "CC-PRESCREEN-001",
                "MTG-FIXED-001", "XP-UDAAP-001-personal_loan",
                "XP-PREQ-002-mortgage_prequal", "PL-STATE-CAP-001"):
        assert rid in det, rid
    for rid in ("PL-JUDGE-001", "CC-JUDGE-001", "MTG-JUDGE-001",
                "XP-ODDS-005-credit_card", "XP-COMP-003-personal_loan"):
        assert rid in llm, rid


def test_deterministic_rules_carry_primitive_spec(rulebook):
    # README: every deterministic rule declares a closed-vocabulary check_type
    # and a plain-English check_description.
    for rule in rulebook.deterministic_rules:
        params = rule.parameters
        assert params.get("check_type") in CLOSED_CHECK_TYPES, rule.rule_id
        assert str(params.get("check_description", "")).strip(), rule.rule_id


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _walk_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk_strings(v)


def test_data_refs_are_resolved(rulebook):
    """"@<file>.<key>" strings must be resolved against rulebook/data/*.json:
    no unresolved reference survives loading, and resolved values equal the
    data-file contents."""
    for rule in rulebook.deterministic_rules:
        for s in _walk_strings(rule.parameters):
            assert not s.startswith(("@lexicons.", "@patterns.", "@state_apr_caps.")), (
                rule.rule_id,
                s,
            )

    lexicons = json.loads((RULEBOOK_DIR / "data" / "lexicons.json").read_text())
    patterns = json.loads((RULEBOOK_DIR / "data" / "patterns.json").read_text())
    caps = json.loads((RULEBOOK_DIR / "data" / "state_apr_caps.json").read_text())

    badge = rule_by_id(rulebook, "PL-BADGE-001")
    assert badge.parameters["safety_net_patterns"] == lexicons["preapproved_terms"]

    nmls = rule_by_id(rulebook, "MTG-NMLS-001")
    assert nmls.parameters["detection_ref"] == patterns["nmls_id"]

    cap_rule = rule_by_id(rulebook, "PL-STATE-CAP-001")
    table = cap_rule.parameters["caps_table"]
    assert table == caps["us_consumer_loan_caps"]
    assert table["IL"]["apr_cap"] == 36.0 and table["IL"]["all_in"] is True


def test_dangling_data_ref_raises(tmp_path):
    """A rulebook whose rule references a nonexistent data key must fail to
    load (README: "A dangling reference fails validation")."""
    broken = tmp_path / "rulebook"
    shutil.copytree(RULEBOOK_DIR, broken)
    pl_path = broken / "personal_loan.json"
    doc = json.loads(pl_path.read_text())
    target = next(r for r in doc["rules"] if r["rule_id"] == "PL-BADGE-001")
    target["parameters"]["safety_net_patterns"] = "@lexicons.__does_not_exist__"
    pl_path.write_text(json.dumps(doc))
    with pytest.raises(Exception):
        load_rulebook(broken)
