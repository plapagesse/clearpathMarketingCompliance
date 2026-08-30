"""LLM judge — the gray-area layer of the check engine.

Judges the rulebook's ``llm_judged`` rules (net impression, substantiation,
badge defensibility, urgency, testimonial typicality …) against one evidence
artifact. The deterministic checker handles everything mechanical; this module
handles exactly what a regex cannot: standards, not rules.

Design (mirrors the extractor's conventions):
- ONE structured-output call for all applicable rules per submission (not one
  call per rule — cost and shared context both favor batching).
- The judge sees the AD ITSELF (screenshot vision block) plus the structured
  reading of it (typed claims + disclosures) plus the submission context.
- Every rule's prompt material (judge_focus, violation_examples,
  compliant_contrast, citation_quote, authorities) is loaded from the rulebook
  at runtime — never hardcoded here.
- Severity comes FROM THE RULE, never from the model. The model contributes:
  violated, confidence, reasoning, evidence_text, suggested_redline.
- Corrective retry: one repair attempt that feeds the exact validation failure
  back to the model; API transport errors propagate (SDK retries those).
- Findings with confidence=low are still emitted — flagging uncertain gray
  areas for humans IS the judge's job; confidence is noted in the explanation.

Payload note (demand ledger): the judge injects NO normalized_fields — the
verbatim claim text is what judgment reasons over. See CONSUMED_FIELDS.md.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from backend.contracts import (
    CheckClass,
    CheckKind,
    Claim,
    Disclosure,
    Finding,
    RulebookEntry,
    Submission,
)
from backend.engine.extractor.extract import (
    DEFAULT_MODEL,
    MAX_TOKENS,
    _make_client,
    build_image_block,
)

# --------------------------------------------------------------------------- #
# Model-facing output schema (deliberately small: 6 fields, one 3-value
# Literal — far under the API's compiled-grammar cap that the extractor's
# 36-field union exceeded)
# --------------------------------------------------------------------------- #


class _JudgeVerdict(BaseModel):
    rule_id: str = Field(description="EXACTLY one of the rule ids listed in the instructions")
    violated: bool
    confidence: Literal["high", "medium", "low"]
    reasoning: str = Field(description="2-3 sentences: why this does or does not violate the rule's standard")
    evidence_text: str = Field(
        description="The verbatim span from the ad most relevant to the verdict ('' if none)"
    )
    suggested_redline: str | None = Field(
        default=None,
        description="When violated: a minimal concrete rewrite or addition that would cure it",
    )


class _JudgeOutput(BaseModel):
    verdicts: list[_JudgeVerdict]


# --------------------------------------------------------------------------- #
# Prompt assembly (exported for testability)
# --------------------------------------------------------------------------- #


def _applicable_rules(rulebook, submission: Submission) -> list[RulebookEntry]:
    """llm_judged rules scoped to the submission's product.

    ``rulebook`` duck-types: the checker module's Rulebook object (has
    .llm_judged_rules) OR a plain list of RulebookEntry.
    """
    rules = getattr(rulebook, "llm_judged_rules", rulebook)
    out = []
    for r in rules:
        entry = r if isinstance(r, RulebookEntry) else RulebookEntry.model_validate(r)
        if entry.check_kind == CheckKind.LLM_JUDGED and entry.product == submission.product:
            out.append(entry)
    return out


def _serialize_claims(claims: list[Claim]) -> str:
    lines = []
    for c in claims:
        types = ", ".join(t.value for t in c.claim_types)
        lines.append(f'- [{c.id}] ({types}; {c.location}) "{c.text}"')
    return "\n".join(lines) if lines else "(no claims extracted)"


def _serialize_disclosures(disclosures: list[Disclosure]) -> str:
    lines = []
    for d in disclosures:
        lines.append(
            f'- ({d.disclosure_type.value}; {d.location}; prominence={d.prominence}) "{d.text}"'
        )
    return "\n".join(lines) if lines else "(no disclosures extracted)"


def build_judge_prompt(
    rules: list[RulebookEntry],
    submission: Submission,
    claims: list[Claim],
    disclosures: list[Disclosure],
) -> str:
    """The full judge instruction: role, the rules' standards, the structured
    reading of the ad, and the output contract. Used as the system prompt; the
    screenshot travels alongside as a vision block.

    Self-scoping: accepts the full rulebook (object or list) and filters to the
    submission's product's llm_judged rules itself — idempotent for pre-filtered
    input."""
    rules = _applicable_rules(rules, submission)
    lines = [
        "You are the gray-area compliance judge for consumer-credit advertising.",
        "A deterministic checker has already enforced every mechanical rule; your job is",
        "ONLY the standards below — net impression, substantiation, framing. Judge the ad",
        "as a reasonable consumer would perceive it (the screenshot is the ground truth;",
        "the extracted claims/disclosures are a structured reading to anchor your citations).",
        "",
        "For EVERY rule listed below return exactly one verdict (violated true or false).",
        "Severity is fixed by the rulebook — do not assess it. Be honest about confidence:",
        "'low' is a legitimate answer and routes the item to a human reviewer.",
        "",
        "## Rules to judge",
        "",
    ]
    for r in rules:
        p = r.parameters
        primary = r.authorities[0]
        lines.append(f"### {r.rule_id}  (severity: {r.severity.value})")
        lines.append(f"Authority: {primary.body}, {primary.citation}")
        lines.append(f"Standard: {p.get('judge_focus', r.explanation)}")
        lines.append(f"Rationale: {r.explanation}")
        for ex in p.get("violation_examples", []) or []:
            lines.append(f"  - WOULD violate: {ex}")
        cc = p.get("compliant_contrast")
        if cc:
            lines.append(f"  - Would NOT violate: {cc}")
        q = p.get("citation_quote")
        if q:
            lines.append(f'  - Authority text: "{q}"')
        lines.append("")
    lines += [
        "## Submission context",
        f"submission_id={submission.submission_id}  product={submission.product.value}  "
        f"surface={submission.surface}  partner={submission.partner}  "
        f"mode={submission.mode.value}  states_targeted={submission.states_targeted or 'unknown'}",
        "",
        "## Extracted claims",
        _serialize_claims(claims),
        "",
        "## Extracted disclosures",
        _serialize_disclosures(disclosures),
        "",
        "## Output contract",
        "One verdict per rule id listed above — no other rule ids. evidence_text must be a",
        "verbatim span from the ad when one exists. When violated, suggest a minimal",
        "concrete redline (a rewrite or an addition) that would cure the problem.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Judging
# --------------------------------------------------------------------------- #


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip().casefold()


def _link_claim(evidence_text: str, claims: list[Claim]) -> str | None:
    """claim_id whose text overlaps the verdict's evidence span (normalized
    containment either direction), else None."""
    ev = _norm(evidence_text)
    if not ev:
        return None
    for c in claims:
        ct = _norm(c.text)
        if ev in ct or ct in ev:
            return c.id
    return None


def _call_model(client, system: str, blocks: list[dict], model: str) -> _JudgeOutput:
    """Single API call; isolated so tests can monkeypatch it."""
    response = client.messages.parse(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": blocks}],
        output_format=_JudgeOutput,
    )
    return response.parsed_output


def run_judge(
    *,
    submission: Submission,
    claims: list[Claim],
    disclosures: list[Disclosure],
    evidence_path: str | Path,
    rulebook,
    model: str | None = None,
    client=None,
) -> list[Finding]:
    """Judge the applicable llm_judged rules against one evidence artifact.

    Returns judgment Findings for rules the model marks violated (any
    confidence). Non-violations produce no Finding. Verdicts naming unknown
    rule ids are treated as a validation failure (one corrective retry), then
    dropped."""
    import anthropic

    rules = _applicable_rules(rulebook, submission)
    if not rules:
        return []
    rules_by_id = {r.rule_id: r for r in rules}

    if client is None:
        client = _make_client()
    model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    system = build_judge_prompt(rules, submission, claims, disclosures)
    blocks = [
        build_image_block(Path(evidence_path)),
        {"type": "text", "text": "Evaluate the ad now. Return exactly one verdict per listed rule id."},
    ]

    def _attempt(correction: str | None = None) -> _JudgeOutput:
        attempt_blocks = blocks
        if correction is not None:
            attempt_blocks = blocks + [
                {
                    "type": "text",
                    "text": (
                        f"Your previous judgment failed validation: {correction}. "
                        "Re-emit the complete corrected set of verdicts."
                    ),
                }
            ]
        out = _call_model(client, system, attempt_blocks, model)
        unknown = sorted({v.rule_id for v in out.verdicts} - set(rules_by_id))
        if unknown:
            raise ValueError(f"verdicts name unknown rule ids: {unknown}")
        return out

    try:
        out = _attempt()
    except anthropic.APIStatusError:
        raise
    except (ValidationError, json.JSONDecodeError, ValueError) as exc:
        # one corrective retry with the exact failure fed back; a second
        # failure propagates (max 2 attempts, same semantics as the extractor)
        out = _attempt(correction=str(exc))

    findings: list[Finding] = []
    n = 0
    for v in out.verdicts:
        rule = rules_by_id.get(v.rule_id)
        if rule is None or not v.violated:
            continue
        n += 1
        quote = rule.parameters.get("citation_quote")
        explanation = f"{v.reasoning} (Judge confidence: {v.confidence}.)"
        if quote:
            explanation += f' Authority: "{quote}"'
        findings.append(
            Finding(
                id=f"jdg-{submission.submission_id}-{n:03d}",
                check_class=CheckClass.JUDGMENT,
                severity=rule.severity,
                rule_id=rule.rule_id,
                claim_id=_link_claim(v.evidence_text, claims),
                summary=f"{rule.rule_id}: gray-area violation ({v.confidence} confidence)",
                explanation=explanation,
                citation_url=rule.authorities[0].url,
                suggested_redline=v.suggested_redline,
            )
        )
    return findings
