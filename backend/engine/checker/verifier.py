"""Pass verifier — an opt-in LLM double-check of deterministic PASSES.

The deterministic engine (engine.py) stays pure and offline; this module is a
separate, additive pass a caller runs AFTER `run_checks` when it wants a model
to double-check the rules that produced no finding.

## Pass verifier

Design rule: the verifier FLAGS, it never overrides. It emits new
needs-verification findings (sub-medium severity, matching the engine's
needs-verification convention) and never removes or modifies anything the
deterministic engine produced. Its target failure mode is the
mangled-extraction miss: the upstream vision model transcribed the ad text
imperfectly (a dropped word, a wrong digit, a payload number that contradicts
the claim's own text), so a deterministic pattern that should have fired
silently did not. It is NOT a recall net for wholly-unextracted content — text
that never reached the engine also never reaches the verifier, so absence of a
dispute is not evidence of compliance.

Selection: only deterministic rules for the submission's product that produced
NO finding in the check run (a pass), restricted to text-dependent rules —
rules whose evaluation reads claim/disclosure/artifact text (any
pattern-bearing parameter key, including inside `composite_all` sub-checks).
Pure metadata-plane rules (e.g. PL-STATE-EXCL-001: states_targeted vs
states_excluded) are never sent — the model has nothing textual to
double-check. Pure ground-truth arithmetic rules (truthfulness suites, state
caps) are skipped too, UNLESS a rate-bearing claim exists, in which case they
are included as a payload-vs-text consistency ask (does value_pct match the
figure written in the claim's own text?).

Model tiering rationale: verifying a pass against an explicit rule description
and its operative patterns is a far easier task than the open-ended
classification the extractor performs (verification ≪ classification
difficulty), so the default model is deliberately cheap
("claude-haiku-4-5"; override via the `model` parameter or
ANTHROPIC_VERIFIER_MODEL). One structured-output call covers every selected
rule; unknown rule_ids in the output fail validation and trigger one
corrective retry carrying the error text (the extractor's retry shape).
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ValidationError

from backend.contracts import (
    CheckClass,
    CheckRun,
    Claim,
    ClaimType,
    Disclosure,
    Finding,
    RulebookEntry,
    Severity,
    Submission,
)
from backend.engine.checker.engine import ELEMENT_FALLBACK_PATTERNS
from backend.engine.checker.normalize import normalize
from backend.engine.checker.rulebook import Rulebook

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_VERIFIER_MODEL = "claude-haiku-4-5"
MAX_TOKENS = 4000
MAX_PATTERNS_PER_RULE = 8  # cap on operative phrases/patterns quoted per rule

SYSTEM_PROMPT = (
    "A deterministic compliance checker ran on text extracted from an ad image "
    "by an upstream model. Extraction may be imperfect. For each rule below, "
    "the checker found NO violation. Double-check: (a) does the provided text "
    "actually contain anything violating the rule? (b) do any claim payload "
    "numbers contradict the claim's own text (e.g. value_pct vs the figure "
    "written in it)?"
)

# Parameter keys whose (resolved) values are text detectors — a rule carrying
# any of these reads claim/disclosure/artifact text to decide.
_PATTERN_KEYS = (
    "phrases",
    "safety_net_patterns",
    "trigger_patterns",
    "anchor_patterns",
    "companion_patterns",
    "detection_ref",
)

# Engine-provided virtual claim_fields (metadata plane, not claim payload).
_VIRTUAL_CLAIM_FIELDS = {"review_date", "states_targeted"}


# --------------------------------------------------------------------------- #
# Model-facing output schema
# --------------------------------------------------------------------------- #


class _Verdict(BaseModel):
    rule_id: str
    disputed: bool
    reason: str
    evidence_text: str


class _VerdictList(BaseModel):
    verdicts: list[_Verdict]


# --------------------------------------------------------------------------- #
# Rule selection
# --------------------------------------------------------------------------- #


def _param_blocks(rule: RulebookEntry) -> list[dict]:
    """The rule's parameter dict plus every composite_all sub-check block."""
    blocks = [rule.parameters]
    if rule.parameters.get("check_type") == "composite_all":
        blocks += list(rule.parameters.get("checks", []))
    return blocks


def _classify(rule: RulebookEntry) -> str:
    """'text' | 'payload' | 'metadata' — where the rule's evaluation reads.

    text: any pattern-bearing key (or an element with engine fallback
    patterns) — the decision reads claim/disclosure/artifact text.
    payload: ground-truth arithmetic over claim payload numbers (truthfulness
    reconciliations, state caps) — no text read, but a payload-vs-text
    consistency ask is meaningful when a rate-bearing claim exists.
    metadata: neither (e.g. states_targeted vs states_excluded) — skip always.
    """
    blocks = _param_blocks(rule)
    for p in blocks:
        if any(p.get(k) for k in _PATTERN_KEYS):
            return "text"
        if p.get("check_type") == "element_required" and p.get("element") in ELEMENT_FALLBACK_PATTERNS:
            return "text"
    for p in blocks:
        ct = p.get("check_type")
        if ct == "numeric_cap_by_state":
            return "payload"
        if ct == "ground_truth_consistency" and p.get("claim_field") not in _VIRTUAL_CLAIM_FIELDS:
            return "payload"
    return "metadata"


def _select_rules(
    check_run: CheckRun,
    submission: Submission,
    claims: list[Claim],
    rulebook: Rulebook,
) -> list[tuple[RulebookEntry, str]]:
    """(rule, kind) pairs to verify: the submission's product's deterministic
    rules that produced NO finding, text-dependent always, payload-arithmetic
    only when a rate-bearing claim exists, metadata-plane never."""
    fired = {f.rule_id for f in check_run.findings if f.rule_id is not None}
    has_rate_claim = any(ClaimType.RATE_OR_APR in c.claim_types for c in claims)
    selected: list[tuple[RulebookEntry, str]] = []
    for rule in rulebook.deterministic_rules:
        if rule.product != submission.product or rule.rule_id in fired:
            continue
        kind = _classify(rule)
        if kind == "text" or (kind == "payload" and has_rate_claim):
            selected.append((rule, kind))
    return selected


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #


def _operative_summary(rule: RulebookEntry, kind: str) -> list[str]:
    """The operative detectors/comparisons, capped at MAX_PATTERNS_PER_RULE."""
    entries: list[str] = []
    for p in _param_blocks(rule):
        if kind == "text":
            for key in _PATTERN_KEYS:
                for pat in p.get(key) or []:
                    if pat not in entries:
                        entries.append(pat)
            element = p.get("element")
            if p.get("check_type") == "element_required" and element in ELEMENT_FALLBACK_PATTERNS:
                for pat in ELEMENT_FALLBACK_PATTERNS[element]:
                    if pat not in entries:
                        entries.append(pat)
        else:  # payload
            ct = p.get("check_type")
            if ct == "ground_truth_consistency":
                entries.append(
                    f"{p.get('claim_field')} {p.get('comparator')} matrix {p.get('matrix_field')}"
                )
            elif ct == "numeric_cap_by_state":
                entries.append("advertised value_pct / range_max_pct vs per-state apr_cap table")
    return entries[:MAX_PATTERNS_PER_RULE]


def _build_user_text(
    selected: list[tuple[RulebookEntry, str]],
    claims: list[Claim],
    disclosures: list[Disclosure],
    artifact_text: str | None,
) -> str:
    lines: list[str] = ["## Rules that passed (double-check each)", ""]
    for rule, kind in selected:
        lines.append(f"### {rule.rule_id}")
        lines.append(f"check: {rule.parameters.get('check_description', rule.explanation)}")
        focus = (
            "text re-check (question a)"
            if kind == "text"
            else "payload-vs-text numeric consistency (question b)"
        )
        lines.append(f"focus: {focus}")
        operative = _operative_summary(rule, kind)
        if operative:
            label = "operative patterns" if kind == "text" else "comparisons"
            lines.append(f"{label} (first {MAX_PATTERNS_PER_RULE}): " + " | ".join(operative))
        lines.append("")

    lines += ["## Evidence", "", "### Claims"]
    if not claims:
        lines.append("(none extracted)")
    for c in claims:
        numeric = {
            k: v
            for k, v in c.normalized_fields.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        payload = (
            " — numeric payload: " + ", ".join(f"{k}={v}" for k, v in sorted(numeric.items()))
            if numeric
            else ""
        )
        lines.append(f'- [{c.id}] "{c.text}"{payload}')
    lines += ["", "### Disclosures"]
    if not disclosures:
        lines.append("(none extracted)")
    for d in disclosures:
        lines.append(f'- [{d.disclosure_type.value}] "{d.text}"')
    lines += ["", "### Artifact text (normalized)"]
    lines.append(normalize(artifact_text) if artifact_text else "(no artifact text supplied)")
    lines += [
        "",
        "Return one verdict per rule listed above ({rule_id, disputed, reason, "
        "evidence_text}). Use ONLY the rule_ids listed above. Set disputed=true "
        "only when the evidence itself shows the checker's pass is wrong.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Client + call (extractor pattern: workspace-header aware, corrective retry)
# --------------------------------------------------------------------------- #


def _make_client():
    """Anthropic client, workspace-header aware (same pattern as the
    extractor): identity-linked API keys require the anthropic-workspace-id
    header, which the SDK neither sends automatically nor reads from an env
    var. Loads the repo .env first so standalone calls work."""
    import anthropic
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    if workspace_id:
        return anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace_id})
    return anthropic.Anthropic()


def _call_model(client, user_text: str, model: str) -> _VerdictList:
    """Single structured-output call. Isolated so tests can monkeypatch it."""
    response = client.messages.parse(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"type": "text", "text": user_text}]}],
        output_format=_VerdictList,
    )
    return response.parsed_output


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def verify_passed_rules(
    *,
    check_run: CheckRun,
    submission: Submission,
    claims: list[Claim],
    disclosures: list[Disclosure],
    artifact_text: str | None,
    rulebook: Rulebook,
    model: str | None = None,
    client=None,
) -> list[Finding]:
    """LLM double-check of the deterministic passes in `check_run`.

    Returns NEW needs-verification findings only (sub-medium severity,
    explanation prefixed "Pass verifier (model double-check): "); never
    removes or modifies anything in `check_run.findings`. Returns [] without
    any API call when no rule qualifies for verification.

    Model resolution: `model` param > ANTHROPIC_VERIFIER_MODEL env >
    "claude-haiku-4-5". Retries the model call ONCE on schema/validation
    failure (unknown rule_ids), sending the error text back; API transport
    errors propagate (the SDK already retries 429/5xx internally).
    """
    selected = _select_rules(check_run, submission, claims, rulebook)
    if not selected:
        return []  # nothing to verify — no client, no API call

    import anthropic

    if client is None:
        client = _make_client()
    model = model or os.environ.get("ANTHROPIC_VERIFIER_MODEL") or DEFAULT_VERIFIER_MODEL

    rules_by_id = {rule.rule_id: (rule, kind) for rule, kind in selected}
    user_text = _build_user_text(selected, claims, disclosures, artifact_text)

    def _attempt(correction: str | None = None) -> _VerdictList:
        attempt_text = user_text
        if correction is not None:
            # corrective retry: tell the model exactly what failed instead of
            # blindly re-sending the identical request
            attempt_text = (
                f"{user_text}\n\nYour previous verification failed validation: "
                f"{correction}. Re-emit the complete corrected verdict list."
            )
        out = _call_model(client, attempt_text, model)
        unknown = sorted({v.rule_id for v in out.verdicts} - set(rules_by_id))
        if unknown:
            raise ValueError(
                f"verdicts name rule_ids not in the verified set: {unknown}; "
                f"valid rule_ids: {sorted(rules_by_id)}"
            )
        return out

    try:
        result = _attempt()
    except anthropic.APIStatusError:
        raise
    except (ValidationError, ValueError) as e:
        result = _attempt(correction=str(e))  # one corrective retry, max 2 attempts

    findings: list[Finding] = []
    seen: set[str] = set()
    for v in result.verdicts:
        if not v.disputed or v.rule_id in seen:
            continue
        seen.add(v.rule_id)
        rule, kind = rules_by_id[v.rule_id]
        explanation = f"Pass verifier (model double-check): {v.reason}"
        if v.evidence_text.strip():
            explanation += f' Evidence: "{v.evidence_text.strip()}"'
        findings.append(
            Finding(
                id=f"fnd-{submission.submission_id}-pv-{len(findings) + 1:03d}",
                check_class=CheckClass.TRUTHFULNESS if kind == "payload" else CheckClass.LEGALITY,
                severity=Severity.LOW,  # needs-verification convention: below medium
                rule_id=rule.rule_id,
                claim_id=None,
                summary=f"Needs verification: pass verifier disputes the '{rule.rule_id}' pass",
                explanation=explanation,
                citation_url=rule.authorities[0].url,
                suggested_redline=None,
            )
        )
    return findings
