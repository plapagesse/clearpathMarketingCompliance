"""build_judge_prompt: product-scoped llm_judged rule selection and verbatim
rulebook-data enrichment (judge_focus / violation_examples / compliant_contrast
/ citation_quote / explanation), with claims present and zero deterministic
leakage.

The full rulebook (all 51 entries) is passed in; the prompt for a
personal_loan submission must carry ONLY that product's 5 llm_judged rules.
"""

from __future__ import annotations

from backend.contracts import Product

from backend.engine.judge import build_judge_prompt

try:  # package/namespace mode (also before the implementation adds __init__.py)
    import backend.engine.judge.conftest as C
except ImportError:  # flat fallback: this directory is pytest's rootdir insert
    import conftest as C


def _pl_prompt(rulebook, submission, claims, disclosures) -> str:
    prompt = build_judge_prompt(rulebook, submission, claims, disclosures)
    assert isinstance(prompt, str) and prompt.strip()
    return prompt


def test_prompt_includes_only_product_scoped_llm_judged_rules(
    rulebook, compliant_pl_submission, compliant_pl_claims, compliant_pl_disclosures
):
    prompt = _pl_prompt(rulebook, compliant_pl_submission, compliant_pl_claims, compliant_pl_disclosures)
    pl_ids = {r.rule_id for r in C.llm_rules(rulebook, Product.PERSONAL_LOAN)}
    assert pl_ids == {
        "PL-JUDGE-001", "PL-JUDGE-002",
        "XP-COMP-003-personal_loan", "XP-ODDS-005-personal_loan", "XP-TEST-006-personal_loan",
    }
    for rid in pl_ids:
        C.assert_in_norm(rid, prompt, "personal_loan llm_judged rule id")
    # other products' llm_judged rules must NOT be judged for this submission
    for rule in C.llm_rules(rulebook):
        if rule.product != Product.PERSONAL_LOAN:
            C.assert_not_in_norm(rule.rule_id, prompt, "foreign-product llm_judged rule id")


def test_no_deterministic_rule_content_leaks_into_prompt(
    rulebook, compliant_pl_submission, compliant_pl_claims, compliant_pl_disclosures
):
    prompt = _pl_prompt(rulebook, compliant_pl_submission, compliant_pl_claims, compliant_pl_disclosures)
    for rule in C.det_rules(rulebook):
        C.assert_not_in_norm(rule.rule_id, prompt, "deterministic rule id")
        desc = rule.parameters.get("check_description")
        if isinstance(desc, str) and desc.strip():
            C.assert_not_in_norm(desc, prompt, f"check_description of {rule.rule_id}")


def test_prompt_carries_rule_enrichment_verbatim_from_rulebook_data(
    rulebook, compliant_pl_submission, compliant_pl_claims, compliant_pl_disclosures
):
    """rulebook/README.md: the judge receives judge_focus, violation_examples,
    compliant_contrast, citation_quote plus explanation as prompt context —
    loaded from the rulebook JSON (asserted against the parsed entries, never
    against strings copied into this test)."""
    prompt = _pl_prompt(rulebook, compliant_pl_submission, compliant_pl_claims, compliant_pl_disclosures)
    for rule in C.llm_rules(rulebook, Product.PERSONAL_LOAN):
        p = rule.parameters
        C.assert_in_norm(p["judge_focus"], prompt, f"{rule.rule_id} judge_focus")
        C.assert_in_norm(p["compliant_contrast"], prompt, f"{rule.rule_id} compliant_contrast")
        assert any(
            C.norm(example) in C.norm(prompt) for example in p["violation_examples"]
        ), f"{rule.rule_id}: no violation_example made it into the prompt"
        if p["citation_quote"] is not None:
            C.assert_in_norm(p["citation_quote"], prompt, f"{rule.rule_id} citation_quote")
        C.assert_in_norm(rule.explanation, prompt, f"{rule.rule_id} explanation")


def test_prompt_contains_claim_texts(
    rulebook, compliant_pl_submission, compliant_pl_claims, compliant_pl_disclosures
):
    prompt = _pl_prompt(rulebook, compliant_pl_submission, compliant_pl_claims, compliant_pl_disclosures)
    for claim in compliant_pl_claims:
        C.assert_in_norm(claim.text, prompt, f"claim {claim.id} text")


def test_prompt_rule_content_is_data_driven_not_hardcoded(
    rulebook, compliant_pl_submission, compliant_pl_claims, compliant_pl_disclosures
):
    """Mutating a rule's judge_focus in the passed-in rulebook must change the
    prompt accordingly — proving the enrichment is read from rulebook data at
    call time, not baked into the module."""
    sentinel = "SENTINEL-JUDGE-FOCUS-83b1f (not a real compliance question)"
    original = C.rule_by_id(rulebook, "PL-JUDGE-001")
    mutated = original.model_copy(deep=True)
    mutated.parameters["judge_focus"] = sentinel
    mutated_rulebook = [mutated if r.rule_id == "PL-JUDGE-001" else r for r in rulebook]

    prompt = _pl_prompt(mutated_rulebook, compliant_pl_submission, compliant_pl_claims, compliant_pl_disclosures)
    C.assert_in_norm(sentinel, prompt, "mutated judge_focus")
    C.assert_not_in_norm(original.parameters["judge_focus"], prompt, "original judge_focus after mutation")
