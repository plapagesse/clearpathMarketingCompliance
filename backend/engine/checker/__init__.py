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
"""

from backend.engine.checker.engine import run_checks
from backend.engine.checker.rulebook import Rulebook, RulebookLoadError, load_rulebook

__all__ = ["Rulebook", "RulebookLoadError", "load_rulebook", "run_checks"]
