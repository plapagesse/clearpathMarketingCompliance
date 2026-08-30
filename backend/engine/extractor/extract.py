"""Image-only claim/disclosure extractor.

Evidence is screenshots — PNG/JPEG paths, nothing else. Partner-submitted
mocks and seed-account captures arrive as pixels; the artifact goes to Claude
as a vision block, and prominence assessment (an 8px footer vs. a headline) is
a genuine visual-perception judgment: relative text size, position in the
layout, contrast. Screenshots cannot contain HTML comments by construction;
the HTML sources live in /fixtures only as render inputs.

The classification spec is injected from rulebook/claim_types_legal_map.json
at runtime; output is typed, contract-validated Claim/Disclosure objects.

Cost/latency (rough, per extraction call at the default model claude-sonnet-5,
$2/$10 per MTok): system prompt with the injected spec ~4K tokens; an image
costs ~(width*height)/750 input tokens — a typical 800x1200 offer-card
screenshot ~1.3-1.6K tokens; ~1-2K structured tokens out. About $0.02-0.05
and 10-40s per artifact.

Text fidelity note: the literal `text` of claims and disclosures is bounded by
the vision model's transcription — dashes, quote glyphs, and spacing may
differ from source text. Downstream matching must use a transcription-tolerant
normalizer (see eval.py's _norm).

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

from backend.contracts import (
    Claim,
    ClaimType,
    Disclosure,
    DisclosureType,
    Product,
    validate_claim_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LEGAL_MAP_PATH = REPO_ROOT / "rulebook" / "claim_types_legal_map.json"

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000

# --------------------------------------------------------------------------- #
# Public result types
# --------------------------------------------------------------------------- #


# Amendment #5 moved normalized_fields into the Claim contract itself;
# the ExtractedClaim subclass is gone. Deprecated alias for stray importers:
ExtractedClaim = Claim


class ExtractionResult(BaseModel):
    evidence_id: str
    claims: list[Claim]
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
    claim_types: list[ClaimType] = Field(
        min_length=1,
        description="EVERY legal category this statement embodies (multi-label; one claim per distinct statement)",
    )
    text: str = Field(description="VERBATIM span as rendered in the artifact")
    location: str = Field(description="Where in the artifact, e.g. 'headline', 'badge', 'fine print'")
    normalized_fields: list[_NormalizedField] = Field(
        default_factory=list,
        description="UNION of the payload contracts of every listed claim type",
    )


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


SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg"}


def build_image_block(path: Path) -> dict:
    """Vision content block for the one evidence type there is: a screenshot."""
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def _prepare_artifact(evidence: str | Path) -> list[dict]:
    """A PNG/JPEG screenshot path -> one vision block. Anything else is an error:
    the platform is image-only."""
    p = Path(evidence)
    if p.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"unsupported evidence {evidence!r}: the extractor accepts only image paths "
            f"({', '.join(sorted(SUPPORTED_SUFFIXES))})"
        )
    return [build_image_block(p)]


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
        "## Claim types (legal-entity taxonomy — multi-label: list every category a statement embodies)",
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
        "- Emit ONE claim per distinct statement; list EVERY legal category it embodies in",
        "  claim_types (e.g. '0% intro APR for 15 months' -> [promotional_or_introductory,",
        "  triggering_term]). Never split one statement into multiple claim objects.",
        "- `text` must be the VERBATIM rendered span (entity-decoded), not a paraphrase.",
        "- Extract every claim, compliant or not; the downstream checker decides compliance.",
        "- normalized_fields is the UNION of the payload contracts of every listed claim type",
        "  (each listed type's applicable fields present); keys WITHOUT the '?' suffix;",
        "  values stringified ('true'/'false' for booleans, plain digits for numbers).",
        "",
        "## Disclosure types (assign exactly one per disclosure)",
        "- " + ", ".join(d.value for d in DisclosureType),
        "  (apr_qualifier: creditworthiness/floor qualifiers; trigger_disclosure: Reg Z companion terms;",
        "   soft_pull: 'won't affect your credit score'; not_guaranteed: approval-not-guaranteed qualifier;",
        "   opt_out_notice: FCRA prescreen notice; nmls_id: NMLS number; taxes_insurance: payment excludes T&I;",
        "   intro_adjacency: the word 'intro' adjacent to a promo rate; use 'other' only as a last resort.)",
        "- Report EVERY disclosure present with its location and an honest prominence assessment.",
        "  Judge prominence VISUALLY: relative text size versus the dominant text, position in the",
        "  layout (top/bottom, above/below the fold line), and contrast against the background —",
        "  tiny low-contrast footer text is 'fine_print' or 'footer' no matter what it says.",
        "- Transcribe text spans as accurately as the rendering allows; do not normalize away",
        "  punctuation you can see (dashes, quotes), but never invent characters you cannot read.",
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
        Claim(
            id=f"clm-{ctx.evidence_id}-{i:03d}",
            claim_types=c.claim_types,
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


def _make_client():
    """Anthropic client, workspace-header aware.

    Identity-linked API keys are rejected with 400 ("anthropic-workspace-id is
    required when authenticating with an identity-linked API key") unless the
    anthropic-workspace-id header is sent; the SDK neither sends it
    automatically nor reads an env var for it. Plain keys need no header.
    Loads the repo .env first so standalone extract() calls work without the
    caller pre-loading the environment."""
    import anthropic
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    if workspace_id:
        return anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace_id})
    return anthropic.Anthropic()


def extract(evidence: str | Path, context: ExtractionContext, client=None) -> ExtractionResult:
    """Extract typed claims and disclosures from one evidence artifact.

    Retries the model call ONCE on schema/validation failure; API transport
    errors propagate (the SDK already retries 429/5xx internally)."""
    import anthropic

    if client is None:
        client = _make_client()
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    spec = load_classification_spec()
    system = build_system_prompt(spec)
    blocks = _prepare_artifact(evidence) + [
        {
            "type": "text",
            "text": (
                f"Context: product={context.product.value}, surface={context.surface or 'unknown'}, "
                f"partner={context.partner or 'unknown'}. "
                "Extract all claims and disclosures from the screenshot now."
            ),
        }
    ]
    def _attempt() -> ExtractionResult:
        raw = _call_model(client, system, blocks, model)
        result = _finalize(raw, context, model)
        for c in result.claims:
            validate_claim_payload(c)  # amendment #5: typed union-payload validation
        return result

    try:
        return _attempt()
    except anthropic.APIStatusError:
        raise
    except (ValidationError, json.JSONDecodeError, ValueError):
        return _attempt()  # one retry on malformed output OR invalid payload
