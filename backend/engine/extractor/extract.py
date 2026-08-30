"""Multimodal claim/disclosure extractor.

Reads an evidence artifact (HTML file, raw HTML string, or PNG/JPEG image),
sends it to Claude with the classification spec injected from
rulebook/claim_types_legal_map.json, and returns typed, contract-validated
Claim and Disclosure objects.

Cost/latency (rough, per extraction call at the default model claude-sonnet-5,
$2/$10 per MTok): system prompt with the injected spec ~4K tokens + a fixture-
sized HTML artifact ~1-2K tokens in, ~1-2K structured tokens out — about
$0.02-0.04 and 10-30s per artifact. Images cost more input tokens
(~1.5K per 1000x1000px image).

Model note: temperature is deliberately NOT set — sampling parameters
(temperature/top_p/top_k) are removed on Claude Sonnet 5 / Opus 5 / Fable 5
and return HTTP 400. Determinism is steered by the structured-output schema
and the spec-driven prompt instead.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from backend.contracts import Claim, ClaimType, Disclosure, DisclosureType, Product

REPO_ROOT = Path(__file__).resolve().parents[3]
LEGAL_MAP_PATH = REPO_ROOT / "rulebook" / "claim_types_legal_map.json"

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# --------------------------------------------------------------------------- #
# Public result types
# --------------------------------------------------------------------------- #


class ExtractedClaim(Claim):
    """A contract Claim plus the classification spec's normalized payload.

    The frozen Claim contract carries no normalized_fields; the classification
    spec (rulebook/claim_types_legal_map.json) defines a per-type payload
    contract the checker consumes. Until/unless a contracts amendment adds the
    field, the extractor ships this subclass — it IS a valid Claim (all
    contract fields present and validated) with the payload alongside.
    """

    normalized_fields: dict = Field(default_factory=dict)


class ExtractionResult(BaseModel):
    evidence_id: str
    claims: list[ExtractedClaim]
    disclosures: list[Disclosure]
    model: str
    usage: dict = Field(default_factory=dict)  # input/output token counts


class ExtractionContext(BaseModel):
    """Context bundle for one extraction: selects prompt framing only —
    the extractor never judges compliance, it only classifies."""

    product: Product
    surface: str = ""
    partner: str = ""
    evidence_id: str = "evidence"


# --------------------------------------------------------------------------- #
# Model-facing output schema (no ids — ids are assigned deterministically here;
# normalized fields travel as key/value string pairs because strict structured
# outputs reject open-ended dicts)
# --------------------------------------------------------------------------- #


class _NormalizedField(BaseModel):
    key: str
    value: str = Field(description="Stringified value: 'true'/'false' for booleans, plain digits for numbers")


class _ModelClaim(BaseModel):
    claim_type: ClaimType
    text: str = Field(description="VERBATIM span as rendered in the artifact")
    location: str = Field(description="Where in the artifact, e.g. 'headline', 'badge', 'fine print'")
    normalized_fields: list[_NormalizedField] = Field(default_factory=list)


class _ModelDisclosure(BaseModel):
    disclosure_type: DisclosureType
    text: str
    location: str
    prominence: str = Field(description="'headline' | 'body' | 'fine_print' | 'footer' | 'below_fold', with any size signal noted")


class _ModelExtraction(BaseModel):
    claims: list[_ModelClaim]
    disclosures: list[_ModelDisclosure]


# --------------------------------------------------------------------------- #
# Artifact preparation
# --------------------------------------------------------------------------- #


def strip_html_comments(html: str) -> str:
    """Remove ALL HTML comments before the model ever sees the artifact.

    Fixture mocks carry answer-key comments describing their planted
    violations (see fixtures/README.md) — leaving them in would contaminate
    any eval. Applies to every HTML artifact, not just fixtures: comments are
    never rendered to consumers, so they are never part of the ad."""
    return _COMMENT_RE.sub("", html)


def _prepare_artifact(evidence: str | Path) -> tuple[list[dict], str]:
    """Return (message content blocks, kind). Accepts an HTML/image path or a raw HTML string.

    HTML is sent as cleaned SOURCE, not tag-stripped text: inline styles
    (font-size, position) and element structure are exactly the prominence
    signals the Disclosure.prominence assessment needs, and fixture-sized
    artifacts make the token overhead negligible."""
    p = Path(evidence) if not (isinstance(evidence, str) and "<" in evidence[:200]) else None
    if p is not None and p.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        media = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        data = base64.standard_b64encode(p.read_bytes()).decode("utf-8")
        return [{"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}], "image"
    html = p.read_text() if p is not None else str(evidence)
    cleaned = strip_html_comments(html)
    return [{"type": "text", "text": f"<artifact>\n{cleaned}\n</artifact>"}], "html"


# --------------------------------------------------------------------------- #
# Prompt assembly — the classification spec is loaded AT RUNTIME from the
# rulebook; type definitions are never hardcoded here.
# --------------------------------------------------------------------------- #


def load_classification_spec(path: Path = LEGAL_MAP_PATH) -> dict:
    spec = json.loads(path.read_text())
    types = spec["claim_types"]
    missing = {ct.value for ct in ClaimType} - set(types)
    if missing:
        raise ValueError(f"classification spec out of sync with ClaimType enum; missing: {missing}")
    return types


def build_system_prompt(spec: dict) -> str:
    lines = [
        "You are a claim-and-disclosure extractor for consumer-finance marketing compliance.",
        "You read one advertisement artifact and emit EVERY marketing claim and EVERY disclosure in it.",
        "You do NOT judge compliance — you classify and transcribe. Neutral, exhaustive, verbatim.",
        "",
        "## Claim types (legal-entity taxonomy — assign exactly one per claim)",
        "",
    ]
    for name, t in spec.items():
        lines.append(f"### {name}")
        lines.append(f"Definition: {t['definition']}")
        ex = t.get("examples", {})
        for pos in ex.get("positive", []):
            lines.append(f"- POSITIVE: {pos!r}")
        for neg in ex.get("negative", []):
            if isinstance(neg, dict):
                lines.append(f"- NEGATIVE (do not classify as {name}): {neg['span']!r} — {neg['reason']}")
            else:
                lines.append(f"- NEGATIVE: {neg!r}")
        nf = t.get("normalized_fields", {})
        if nf:
            lines.append("Normalized fields to populate when applicable ('?' suffix = optional):")
            for k, v in nf.items():
                lines.append(f"  - {k}: {v}")
        lines.append("")
    lines += [
        "## Conventions",
        "- A span embodying TWO legal categories yields TWO claim objects (no multi-label).",
        "- `text` must be the VERBATIM rendered span (entity-decoded), not a paraphrase.",
        "- Extract every claim, compliant or not; the downstream checker decides compliance.",
        "- Populate normalized_fields per the type's payload contract; keys WITHOUT the '?' suffix;",
        "  values stringified ('true'/'false' for booleans, plain digits for numbers).",
        "",
        "## Disclosure types (assign exactly one per disclosure)",
        "- " + ", ".join(d.value for d in DisclosureType),
        "  (apr_qualifier: creditworthiness/floor qualifiers; trigger_disclosure: Reg Z companion terms;",
        "   soft_pull: 'won't affect your credit score'; not_guaranteed: approval-not-guaranteed qualifier;",
        "   opt_out_notice: FCRA prescreen notice; nmls_id: NMLS number; taxes_insurance: payment excludes T&I;",
        "   intro_adjacency: the word 'intro' adjacent to a promo rate; use 'other' only as a last resort.)",
        "- Report EVERY disclosure present with its location and an honest prominence assessment",
        "  (font-size styles and position in the source are your prominence signals).",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


def _call_model(client, system: str, content_blocks: list[dict], model: str) -> _ModelExtraction:
    """Single API call. Isolated so tests can monkeypatch it."""
    response = client.messages.parse(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": content_blocks}],
        output_format=_ModelExtraction,
    )
    out = response.parsed_output
    usage = getattr(response, "usage", None)
    if usage is not None:
        out.__dict__["_usage"] = {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }
    return out


def _coerce(value: str):
    v = value.strip()
    if v.lower() in {"true", "false"}:
        return v.lower() == "true"
    try:
        return int(v) if re.fullmatch(r"-?\d+", v) else float(v)
    except ValueError:
        return v


def _finalize(raw: _ModelExtraction, ctx: ExtractionContext, model: str) -> ExtractionResult:
    claims = [
        ExtractedClaim(
            id=f"clm-{ctx.evidence_id}-{i:03d}",
            claim_type=c.claim_type,
            text=c.text,
            location=c.location,
            source_evidence_id=ctx.evidence_id,
            normalized_fields={nf.key: _coerce(nf.value) for nf in c.normalized_fields},
        )
        for i, c in enumerate(raw.claims)
    ]
    disclosures = [
        Disclosure(
            id=f"dsc-{ctx.evidence_id}-{i:03d}",
            disclosure_type=d.disclosure_type,
            text=d.text,
            location=d.location,
            prominence=d.prominence,
        )
        for i, d in enumerate(raw.disclosures)
    ]
    return ExtractionResult(
        evidence_id=ctx.evidence_id,
        claims=claims,
        disclosures=disclosures,
        model=model,
        usage=raw.__dict__.get("_usage", {}),
    )


def extract(evidence: str | Path, context: ExtractionContext, client=None) -> ExtractionResult:
    """Extract typed claims and disclosures from one evidence artifact.

    Retries the model call ONCE on schema/validation failure; API transport
    errors propagate (the SDK already retries 429/5xx internally)."""
    import anthropic

    if client is None:
        client = anthropic.Anthropic()
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    spec = load_classification_spec()
    system = build_system_prompt(spec)
    blocks, kind = _prepare_artifact(evidence)
    blocks = blocks + [
        {
            "type": "text",
            "text": (
                f"Context: product={context.product.value}, surface={context.surface or 'unknown'}, "
                f"partner={context.partner or 'unknown'}, artifact kind={kind}. "
                "Extract all claims and disclosures now."
            ),
        }
    ]
    try:
        raw = _call_model(client, system, blocks, model)
    except anthropic.APIStatusError:
        raise
    except (ValidationError, json.JSONDecodeError, ValueError):
        raw = _call_model(client, system, blocks, model)  # one retry on malformed output
    return _finalize(raw, context, model)
