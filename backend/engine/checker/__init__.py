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

`artifact_text` is the creative's own text in reading order and callers should
always supply it — the extractor now returns it on the same call that types the
claims. Without it the token-bound rules fall back to a concatenation of claim
and disclosure fragments, where distance is an artefact of concatenation order:
the run carries one info-severity coverage marker and every proximity finding
is demoted to a sub-medium needs-verification finding, never the rule's full
severity.

Findings are written for compliance officers — see engine.py's "Finding prose"
note for the convention and the vocabulary tables that keep it data-driven.

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
