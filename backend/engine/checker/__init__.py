"""Deterministic checker (Stage 4).

Public interface (pinned):
- load_rulebook(rulebook_dir) -> Rulebook
- run_checks(*, submission, claims, disclosures, offer_cells,
             offer_matrix_version, rulebook, artifact_text=None,
             baseline=None) -> CheckRun

Additive optional kwargs on run_checks (interface friction, reported): the
contract CheckRun stores findings but not the extracted claims/disclosures, so
the full fidelity diff accepts `baseline_claims` / `baseline_disclosures`
directly; with only `baseline` the fidelity pass degrades to a findings-delta.

Pass verifier (opt-in, separate from the pure/offline engine):
`verify_passed_rules(...)` (verifier.py) sends the text-dependent rules that
produced NO finding to a cheap model ("claude-haiku-4-5" by default —
verification against explicit rule patterns is far easier than open-ended
classification, so it doesn't need the extractor's tier) for one
structured-output double-check. It FLAGS, it never overrides: disputed passes
come back as additive sub-medium needs-verification findings and nothing in
the CheckRun is removed or modified. It catches mangled-extraction misses
(wrong digit, dropped word, payload numbers contradicting the claim's own
text), NOT wholly-unextracted content.
"""

from backend.engine.checker.engine import run_checks
from backend.engine.checker.rulebook import Rulebook, RulebookLoadError, load_rulebook
from backend.engine.checker.verifier import verify_passed_rules

__all__ = ["Rulebook", "RulebookLoadError", "load_rulebook", "run_checks", "verify_passed_rules"]
