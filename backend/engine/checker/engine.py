"""Deterministic check engine.

Executes the rulebook's deterministic rules against one submission's evidence:
typed claims/disclosures (claim_plane), normalized artifact text (text_plane),
the referenced offer-matrix cells (truthfulness ground truth), submission
metadata, and — in verification mode — an approved baseline (fidelity).

The 8 primitives are generic engines consuming rule parameters; there is no
per-rule code. See CONSUMED_FIELDS.md for the exact demand ledger of every
input field each rule's evaluation reads.

## Finding prose

Every `summary`, `explanation` and `suggested_redline` this module emits is
written for a compliance officer, not for an engineer: no enum tokens, no
"claim_plane"/"text_plane"/"artifact text", no rule ids in the summary (the UI
renders rule id and citation as chips beside it). A finding names what is
wrong with the creative and the concrete edit that fixes it. The vocabulary
tables below (DISCLOSURE_LABELS, MISSING_DISCLOSURE_PROSE, ELEMENT_PROSE,
CONDITION_PROSE, CLAIM_FIELD_PROSE, MATRIX_FIELD_PROSE) are how that stays
data-driven — they key off the same enum/parameter vocabulary the rules use,
so the primitives remain generic and no rule gets bespoke code. Internal
identifiers appear only in `explanation`, and only where they aid audit (the
fidelity drift diff names the rule that newly fired).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from backend.contracts import (
    CheckClass,
    CheckRun,
    Claim,
    ClaimType,
    Disclosure,
    DisclosureType,
    Finding,
    OfferCell,
    RulebookEntry,
    Severity,
    Submission,
    SubmissionMode,
)
from backend.engine.checker.normalize import (
    any_pattern_match,
    normalize,
    pattern_spans,
    phrase_hits,
)
from backend.engine.checker.rulebook import Rulebook
from backend.ingest.parsers import normalize_states_targeted

# ---------------------------------------------------------------------------
# Engine data (keyed by element/qualifier vocabulary — data, not per-rule code)
# ---------------------------------------------------------------------------

# Proximity windows on normalized text (characters between nearest span edges).
IMMEDIATE_WINDOW = 60    # requirements containing "immediate"
PROXIMATE_WINDOW = 150   # "closely proximate" / default

# XP-URG-004 note: effective_end_supports_urgency is derived from the offer
# cell's effective_end — urgency is "real" iff some referenced offer actually
# expires within this many days of the review date.
URGENCY_WINDOW_DAYS = 7

# element_required fallback detection for elements that are not DisclosureType
# values and carry no detection_ref (currently only the deferred-interest
# retroactive-accrual disclosure).
ELEMENT_FALLBACK_PATTERNS: dict[str, list[str]] = {
    "retroactive_accrual_disclosure": [
        r"retroactiv",
        r"charged .{0,40}from the (purchase|original|transaction) date",
        r"interest .{0,50}from the (date of )?purchase",
    ],
}

# phrase_conditional required_qualifier cures: matched against the qualifier
# prose to select detection patterns for the cure.
QUALIFIER_CURE_PATTERNS: dict[str, list[str]] = {
    "origination fee": [r"origination fee"],
    "period during which the rate is fixed": [
        r"fixed (for|until|through)\s?\d",
        r"fixed .{0,25}\b\d{1,2}\s?(months|years|billing cycles)",
    ],
}

# condition_field values resolvable from OfferCell columns.
_OFFER_CONDITION_FIELDS = {"fee_deducted_from_proceeds", "is_firm_offer", "apr_type"}

# condition_field values resolvable from the partner integration registry
# (rulebook/data/integration_config.json), keyed by Submission.partner. A
# partner absent from the registry — or a field absent for a listed partner —
# stays unresolved and emits a needs-verification finding, which is the point:
# a new partner whose flow nobody has walked is exactly what the rule catches.
_INTEGRATION_CONDITION_FIELDS = {"soft_pull_verified"}

_SEVERITY_ORDER = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


# ---------------------------------------------------------------------------
# Plain-English vocabulary (see the module docstring's "Finding prose" note)
# ---------------------------------------------------------------------------

# What a reviewer calls each disclosure type out loud.
DISCLOSURE_LABELS: dict[DisclosureType, str] = {
    DisclosureType.APR_QUALIFIER: "creditworthiness qualifier on the advertised rate",
    DisclosureType.TRIGGER_DISCLOSURE: "companion terms required beside a rate or fee claim",
    DisclosureType.SOFT_PULL: "soft-credit-check statement",
    DisclosureType.NOT_GUARANTEED: "approval-is-not-guaranteed qualifier",
    DisclosureType.OPT_OUT_NOTICE: "prescreen and opt-out notice",
    DisclosureType.SCHUMER_BOX_LINK: "link to the card's rates, fees and terms",
    DisclosureType.NMLS_ID: "lender's NMLS ID",
    DisclosureType.TAXES_INSURANCE: "note that the payment excludes taxes and insurance",
    DisclosureType.STATE_LICENSE: "state licensing statement",
    DisclosureType.INTRO_ADJACENCY: "the word 'intro' beside the promotional rate",
    DisclosureType.OTHER: "other disclosure",
}

# trigger_requires_disclosures: how a missing required disclosure reads.
# (summary, suggested_redline) — the trigger claim is quoted in the explanation.
MISSING_DISCLOSURE_PROSE: dict[DisclosureType, tuple[str, str]] = {
    DisclosureType.TRIGGER_DISCLOSURE: (
        "Rate or fee claim shown without the required companion terms "
        "(APR, variable-rate statement, fees)",
        "Add the companion terms near the rate or fee claim.",
    ),
    DisclosureType.APR_QUALIFIER: (
        "Lowest-rate claim shown without the qualifier saying who actually gets that rate",
        "Add a line stating the lowest rate depends on creditworthiness and that most "
        "applicants are priced higher.",
    ),
    DisclosureType.NOT_GUARANTEED: (
        "'Prequalified' used without saying approval is not guaranteed",
        "Add 'Prequalification is not a guarantee of approval' beside the prequalified wording.",
    ),
    DisclosureType.TAXES_INSURANCE: (
        "Monthly payment shown without saying it leaves out taxes and insurance",
        "Add that the payment estimate excludes taxes and insurance, so the real payment "
        "will be higher.",
    ),
}

# element_required: (summary, suggested_redline) per mandated element.
ELEMENT_PROSE: dict[str, tuple[str, str]] = {
    "schumer_box_link": (
        "Card offer has no link to the rates, fees and terms (Schumer box)",
        "Add a visible link to the card's rates, fees and terms next to the apply button.",
    ),
    "opt_out_notice": (
        "Prescreened offer is missing the required opt-out notice",
        "Add the prescreen and opt-out notice, including the 1-888-5-OPTOUT number.",
    ),
    "nmls_id": (
        "Mortgage creative does not show the lender's NMLS ID",
        "Add the lender name and NMLS number to the creative.",
    ),
    "retroactive_accrual_disclosure": (
        "Deferred-interest offer does not say interest is charged back to the purchase date",
        "State that if the balance is not paid in full by the end of the promotional period, "
        "interest is charged from the purchase date.",
    ),
}

# phrase_conditional: how each condition_field reads when it cannot be
# resolved ("unresolved") and when it resolves against the claim ("conflict"),
# plus the edit that fixes it.
CONDITION_PROSE: dict[str, dict[str, str]] = {
    "fee_deducted_from_proceeds": {
        "unresolved": "whether the referenced offer deducts a fee from the loan proceeds "
                      "could not be established",
        "conflict": "the referenced offer deducts an origination fee from the loan proceeds",
        "fix": "Drop the no-fee wording, or state the origination fee immediately beside it.",
    },
    "is_firm_offer": {
        "unresolved": "whether a firm offer of credit backs this claim could not be established",
        "conflict": "no firm offer of credit backs it — the referenced offer is a "
                    "prequalification, not an approval",
        "fix": "Say 'prequalified' rather than 'pre-approved', or back the claim with a real "
               "firm offer of credit.",
    },
    "apr_type": {
        "unresolved": "whether the referenced offer's rate can change could not be established",
        "conflict": "the referenced offer's rate is adjustable, not fixed",
        "fix": "Remove the 'fixed' wording, or state exactly how long the rate is fixed for.",
    },
    "government_program_verified": {
        "unresolved": "no one has confirmed this is a genuine government loan program",
        "conflict": "the referenced offer is not a verified government loan program",
        "fix": "Remove the government-program wording unless the offer really is one, and never "
               "imply a government agency sent this.",
    },
    "effective_end_supports_urgency": {
        "unresolved": "whether any referenced offer really expires that soon could not "
                      "be established",
        "conflict": "no referenced offer actually expires that soon",
        "fix": "Remove the deadline wording, or tie it to the date the offer really ends.",
    },
    "soft_pull_verified": {
        "unresolved": "the partner flow hasn't been confirmed as soft-pull only",
        "conflict": "the partner's flow runs a hard credit check before the consumer applies",
        "fix": "Have the partner integration confirmed as soft-pull end to end, or remove the "
               "credit-score promise.",
    },
}

# ground_truth_consistency: (what the creative claims, unit suffix).
CLAIM_FIELD_PROSE: dict[str, tuple[str, str]] = {
    "value_pct": ("advertised rate", "%"),
    "range_min_pct": ("bottom of the advertised rate range", "%"),
    "range_max_pct": ("top of the advertised rate range", "%"),
    "promo_rate_pct": ("advertised promotional rate", "%"),
    "promo_period_months": ("advertised promotional period", " months"),
    "term_months": ("advertised repayment term", " months"),
    "amount_value": ("advertised amount", ""),
}

# ground_truth_consistency: what the offer matrix says, in reviewer's words.
MATRIX_FIELD_PROSE: dict[str, str] = {
    "apr_min..apr_max": "the rate range the referenced offers actually carry",
    "apr_min": "the lowest rate the referenced offers actually reach",
    "amount_min..amount_max": "the loan amounts the referenced offers actually allow",
    "term_months": "the repayment terms the referenced offers actually allow",
    "effective_start..effective_end": "the dates the referenced offers are in force",
    "intro_apr_pct": "the referenced offer's real promotional rate",
    "intro_period_months": "the referenced offer's real promotional period",
    "annual_fee": "the referenced offer's real annual fee",
    "states_excluded": "the states the referenced offers exclude",
}


def _fmt(value) -> str:
    """A number the way a reviewer would write it (8.99, 36, 2000–50000)."""
    if isinstance(value, tuple):
        return "–".join(_fmt(v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _claimed(claim_field: str, value) -> str:
    """'advertised rate of 7.49%' — the creative's side of a reconciliation."""
    label, unit = CLAIM_FIELD_PROSE.get(claim_field, (claim_field.replace("_", " "), ""))
    return f"{label} of {_fmt(value)}{unit}"


def _matrix_prose(matrix_field: str) -> str:
    return MATRIX_FIELD_PROSE.get(matrix_field, matrix_field.replace("_", " "))


@dataclass
class _Ctx:
    submission: Submission
    claims: list[Claim]
    disclosures: list[Disclosure]
    cells: list[OfferCell]           # referenced offer cells (ground truth)
    text: str                        # normalized text_plane input
    degraded_text: bool              # True when artifact_text was absent
    review_date: date
    states_targeted: list[str]
    # Disclosure types actually present, by legal FUNCTION: the extractor's own
    # label unioned with every type derived from the disclosure's text (see
    # _effective_disclosure_types). Membership tests use this, never the raw
    # labels — a companion sentence that is physically on the creative counts
    # even when the vision model filed it under a neighbouring type.
    effective_disclosure_types: set[DisclosureType] = field(default_factory=set)
    integration: dict = field(default_factory=dict)   # this partner's verified inputs
    findings: list[Finding] = field(default_factory=list)
    _seq: int = 0

    def routed_claims(self, rule: RulebookEntry) -> list[Claim]:
        wanted = set(rule.claim_types)
        return [c for c in self.claims if wanted & set(c.claim_types)]

    def emit(
        self,
        rule: RulebookEntry | None,
        check_class: CheckClass,
        summary: str,
        explanation: str,
        *,
        severity: Severity | None = None,
        claim_id: str | None = None,
        suggested_redline: str | None = None,
        dedupe_key: tuple | None = None,
    ) -> Finding:
        self._seq += 1
        f = Finding(
            id=f"fnd-{self.submission.submission_id}-{self._seq:03d}",
            check_class=check_class,
            severity=severity or (rule.severity if rule else Severity.INFO),
            rule_id=rule.rule_id if rule else None,
            claim_id=claim_id,
            summary=summary,
            explanation=explanation,
            citation_url=rule.authorities[0].url if rule else None,
            suggested_redline=suggested_redline,
        )
        f.__dict__["_dedupe_key"] = dedupe_key  # transient, stripped before return
        self.findings.append(f)
        return f


# ---------------------------------------------------------------------------
# Effective disclosure types (deterministic typing augmentation)
# ---------------------------------------------------------------------------


def _effective_disclosure_types(
    disclosures: list[Disclosure], type_patterns: dict[str, list[str]]
) -> set[DisclosureType]:
    """Disclosure types present by LEGAL FUNCTION, not merely by label.

    The extractor assigns one type per disclosure from a vision read, and
    neighbouring types are genuinely easy to confuse: the Reg Z companion
    sentence that follows a promotional rate ("after the intro period a
    variable APR of 19.24%–29.24% applies") is, depending on which half you
    look at, an apr_qualifier, an intro_adjacency or the trigger companion.
    Presence tests that key on the exact label therefore report a mandated
    disclosure "missing" while it sits on the creative in plain sight.

    So the checker derives types deterministically too: each disclosure's TEXT
    is matched against the per-type pattern sets in
    rulebook/data/disclosure_type_patterns.json, and the result is UNIONED with
    the extractor's own label. Derivation only ever adds — the extractor's type
    is never removed — and the patterns are deliberately high-precision, since
    a wrong derivation suppresses a real finding rather than raising a false
    one.
    """
    present = {d.disclosure_type for d in disclosures}
    if not type_patterns:
        return present
    for d in disclosures:
        text = normalize(d.text)
        if not text:
            continue
        for type_name, patterns in type_patterns.items():
            try:
                dt = DisclosureType(type_name)
            except ValueError:
                continue  # unknown type name in the data file: ignore, never crash
            if dt in present:
                continue
            if any_pattern_match(patterns, text) is not None:
                present.add(dt)
    return present


# ---------------------------------------------------------------------------
# Primitive: phrase_prohibited
# ---------------------------------------------------------------------------


def _phrase_prohibited(rule: RulebookEntry, ctx: _Ctx) -> None:
    p = rule.parameters
    match = p.get("match", "case_insensitive_substring")
    phrases = p.get("phrases") or p.get("safety_net_patterns") or []
    if p.get("decision_inputs") == "text_plane":
        for hit in phrase_hits(phrases, ctx.text, match):
            ctx.emit(
                rule, CheckClass.LEGALITY,
                f"The creative uses banned wording: \"{hit}\"",
                f"{rule.explanation} Found in the creative's text.",
                suggested_redline=f"Remove \"{hit}\" from the creative.",
                dedupe_key=("phrase", None, normalize(hit)),
            )
        return
    covered: set[str] = set()
    for c in ctx.routed_claims(rule):
        for hit in phrase_hits(phrases, normalize(c.text), match):
            covered.add(normalize(hit))
            ctx.emit(
                rule, CheckClass.LEGALITY,
                f"The creative uses banned wording: \"{hit}\"",
                f"{rule.explanation} It appears in the claim: \"{c.text}\".",
                claim_id=c.id,
                suggested_redline=f"Remove or rewrite \"{hit}\" in that line.",
                dedupe_key=("phrase", c.id, normalize(hit)),
            )
    # Safety net: hits in raw text no routed claim covered (extraction misses).
    # Ratified: safety-net-only detections emit sub-medium needs-verification
    # findings — the pattern matched but no claim corroborates it.
    for hit in phrase_hits(phrases, ctx.text, match):
        if normalize(hit) not in covered:
            ctx.emit(
                rule, CheckClass.LEGALITY,
                f"Needs verification: banned wording \"{hit}\" appears on the creative",
                f"{rule.explanation} The wording was found in the creative's text, but the "
                "reading of the creative did not capture it as a claim — confirm what the "
                "creative actually says before treating this as a violation.",
                severity=Severity.LOW,
                suggested_redline=f"If the creative really says \"{hit}\", remove it.",
                dedupe_key=("phrase", None, normalize(hit)),
            )


# ---------------------------------------------------------------------------
# Primitive: phrase_conditional
# ---------------------------------------------------------------------------


def _resolve_condition(field_name: str, ctx: _Ctx):
    """Resolve a phrase_conditional condition_field.

    Returns a list of per-cell values for OfferCell columns, a single-element
    list for derived and registry-backed conditions, or None when unresolvable.

    Three sources, per rulebook/README.md "Verification-input condition
    fields": OfferCell columns; conditions derived from the matrix
    (effective_end_supports_urgency); and the partner integration registry
    (soft_pull_verified), which is the source the rules' own `note` names.
    Anything the registry does not answer stays None, so the rule emits a
    needs-verification finding rather than passing silently."""
    if field_name in _OFFER_CONDITION_FIELDS:
        return [getattr(c, field_name) for c in ctx.cells]
    if field_name == "effective_end_supports_urgency":
        supported = any(
            (c.effective_end - ctx.review_date).days <= URGENCY_WINDOW_DAYS
            for c in ctx.cells
        )
        return [supported]
    if field_name in _INTEGRATION_CONDITION_FIELDS:
        value = ctx.integration.get(field_name)
        return None if value is None else [value]
    return None


def _qualifier_cured(qualifier: str, ctx: _Ctx) -> bool:
    """Does the creative carry the named cure? Deterministic: keyword-selected
    patterns run over disclosures, claims, and the artifact text."""
    for key, patterns in QUALIFIER_CURE_PATTERNS.items():
        if key in qualifier:
            haystacks = [ctx.text] + [normalize(d.text) for d in ctx.disclosures] + [
                normalize(c.text) for c in ctx.claims
            ]
            return any(any_pattern_match(patterns, h) for h in haystacks)
    return False  # unrecognized qualifier prose: no cure detected


def _phrase_conditional(rule: RulebookEntry, ctx: _Ctx) -> None:
    p = rule.parameters
    phrases = p.get("phrases") or p.get("safety_net_patterns") or []
    match = p.get("match", "case_insensitive_substring")
    text_plane = p.get("decision_inputs") == "text_plane"

    # (hit, claim_id, via_safety_net): text_plane detections are PRIMARY (the
    # text IS the decision plane); claim_plane detections are primary when a
    # routed claim carries the phrase and safety-net otherwise. Ratified:
    # safety-net-only detections emit at sub-medium severity.
    detections: list[tuple[str, str | None, bool]] = []
    if text_plane:
        detections = [(h, None, False) for h in phrase_hits(phrases, ctx.text, match)]
    else:
        covered: set[str] = set()
        for c in ctx.routed_claims(rule):
            for hit in phrase_hits(phrases, normalize(c.text), match):
                covered.add(normalize(hit))
                detections.append((hit, c.id, False))
        for hit in phrase_hits(phrases, ctx.text, match):  # safety net
            if normalize(hit) not in covered:
                detections.append((hit, None, True))
    if not detections:
        return

    condition_field = p["condition_field"]
    prose = CONDITION_PROSE.get(condition_field, {})
    fix = prose.get("fix", f"Remove \"{detections[0][0]}\" or change the referenced offer.")
    per_cell = condition_field in _OFFER_CONDITION_FIELDS  # otherwise derived/registry-backed
    values = _resolve_condition(condition_field, ctx)

    if values is None:
        unresolved = prose.get(
            "unresolved", "this depends on a check nobody has recorded an answer for"
        )
        for hit, claim_id, via_net in detections:
            ctx.emit(
                rule, CheckClass.LEGALITY,
                f"\"{hit}\" is unverified: {unresolved}",
                f"{rule.explanation} This claim can only be cleared by confirming the underlying "
                "facts with the partner — the offer matrix does not answer it."
                + (" The wording was found in the creative's text but not captured as a claim, "
                   "so confirm what the creative says as well." if via_net else ""),
                severity=Severity.LOW if via_net else Severity.MEDIUM,
                claim_id=claim_id,
                suggested_redline=fix,
                dedupe_key=("phrase", claim_id, normalize(hit)),
            )
        return

    violates_when = p.get("violates_when")
    if per_cell and len(ctx.cells) > 1:
        # Multi-cell semantics: ALL referenced cells violating -> full-severity
        # violation; NONE -> pass; MIXED -> needs_verification BELOW medium
        # (arbitration: the phrase may lawfully describe the non-violating
        # cell(s) — attribution cannot be decided deterministically).
        matches = [v == violates_when for v in values]
        if not any(matches):
            return
        if not all(matches):
            mixed_cells = [c.offer_id for c, m in zip(ctx.cells, matches) if m]
            clean_cells = [c.offer_id for c, m in zip(ctx.cells, matches) if not m]
            for hit, claim_id, via_net in detections:
                ctx.emit(
                    rule, CheckClass.LEGALITY,
                    f"Needs verification: \"{hit}\" is true of some referenced offers but not others",
                    f"{rule.explanation} The wording holds for {', '.join(clean_cells)} but not for "
                    f"{', '.join(mixed_cells)}, where {prose.get('conflict', 'it does not hold')}. "
                    "Confirm which offer the line is describing.",
                    severity=Severity.LOW,
                    claim_id=claim_id,
                    suggested_redline="Make the line say which offer it describes, or drop it.",
                    dedupe_key=("phrase", claim_id, normalize(hit)),
                )
            return
        violating = [c.offer_id for c in ctx.cells]
    elif per_cell:
        violating = [c.offer_id for c, v in zip(ctx.cells, values) if v == violates_when]
    else:
        # Derived from the matrix or read from the partner integration
        # registry: one value for the whole submission, never per cell.
        violating = [c.offer_id for c in ctx.cells] if values[0] == violates_when else []
        if values[0] == violates_when and not ctx.cells:
            violating = ["none could be matched to this submission"]
    if not violating:
        return

    qualifier = p.get("required_qualifier")
    if qualifier and _qualifier_cured(qualifier, ctx):
        return  # the cure is proximately present; finding cleared

    conflict = prose.get("conflict", "the referenced offer does not support it")
    for hit, claim_id, via_net in detections:
        ctx.emit(
            rule,
            CheckClass.TRUTHFULNESS if per_cell else CheckClass.LEGALITY,
            (f"Needs verification: \"{hit}\" appears on the creative and {conflict}"
             if via_net else f"The creative says \"{hit}\", but {conflict}"),
            f"{rule.explanation} Offers referenced by this submission: {', '.join(violating)}."
            + (f" The wording that would cure this is absent: {qualifier}." if qualifier else "")
            + (" The phrase was found in the creative's text but not captured as a claim, so "
               "confirm what the creative says before acting." if via_net else ""),
            severity=Severity.LOW if via_net else None,
            claim_id=claim_id,
            suggested_redline=fix,
            dedupe_key=("phrase", claim_id, normalize(hit)),
        )


# ---------------------------------------------------------------------------
# Primitive: trigger_requires_disclosures
# ---------------------------------------------------------------------------


def _trigger_requires_disclosures(rule: RulebookEntry, ctx: _Ctx) -> None:
    p = rule.parameters
    patterns = p.get("trigger_patterns") or p.get("safety_net_patterns") or []
    required = [DisclosureType(t) for t in p.get("required_disclosure_types", [])]

    trigger_claim: Claim | None = None
    trigger_evidence: str | None = None
    for c in ctx.routed_claims(rule):
        hit = any_pattern_match(patterns, normalize(c.text)) if patterns else c.text
        if hit:
            trigger_claim, trigger_evidence = c, c.text
            break
    if trigger_claim is None and patterns:
        hit = any_pattern_match(patterns, ctx.text)  # safety net on raw text
        if hit:
            trigger_evidence = hit
    if trigger_evidence is None:
        return

    via_net = trigger_claim is None  # ratified: safety-net-only triggers emit sub-medium
    # Membership uses effective types (extractor label ∪ text-derived function),
    # so a companion disclosure the creative really carries is never reported
    # missing because the vision model filed it under a neighbouring label.
    present = ctx.effective_disclosure_types
    for dt in required:
        if dt in present:
            continue
        label = DISCLOSURE_LABELS.get(dt, dt.value.replace("_", " "))
        summary, redline = MISSING_DISCLOSURE_PROSE.get(
            dt,
            (f"Required wording is missing: {label}", f"Add the {label} to the creative."),
        )
        ctx.emit(
            rule, CheckClass.LEGALITY,
            f"Needs verification: {summary[0].lower()}{summary[1:]}" if via_net else summary,
            f"{rule.explanation} What triggers the requirement here: \"{trigger_evidence}\". "
            f"Nothing on the creative was read as the {label}."
            + (" The trigger was found in the creative's text but not captured as a claim — "
               "confirm what the creative says before treating this as a violation."
               if via_net else ""),
            severity=Severity.LOW if via_net else None,
            claim_id=trigger_claim.id if trigger_claim else None,
            suggested_redline=redline,
        )


# ---------------------------------------------------------------------------
# Primitive: element_required
# ---------------------------------------------------------------------------


def _applies(applies_when: dict | None, ctx: _Ctx, any_anchor_matched: bool = False) -> bool:
    if not applies_when:
        return True
    if "offer_field" in applies_when:
        return any(
            getattr(c, applies_when["offer_field"], None) == applies_when.get("equals")
            for c in ctx.cells
        )
    if "surface_in" in applies_when:
        return ctx.submission.surface in applies_when["surface_in"]
    if applies_when.get("any_anchor_matched"):
        return any_anchor_matched
    return True


def _element_required(rule: RulebookEntry, ctx: _Ctx, params: dict | None = None,
                      any_anchor_matched: bool = False) -> None:
    p = params or rule.parameters
    if not _applies(p.get("applies_when"), ctx, any_anchor_matched):
        return
    element = p["element"]
    detection = p.get("detection_ref") or ELEMENT_FALLBACK_PATTERNS.get(element) or []

    present = False
    try:
        dt = DisclosureType(element)
        present = any(d.disclosure_type == dt for d in ctx.disclosures)
    except ValueError:
        dt = None
    if not present and detection:
        present = any_pattern_match(detection, ctx.text) is not None
    if present:
        return
    label = DISCLOSURE_LABELS.get(dt) if dt else None
    label = label or element.replace("_", " ")
    summary, redline = ELEMENT_PROSE.get(
        element,
        (f"The creative is missing the required {label}", f"Add the {label} to the creative."),
    )
    ctx.emit(
        rule, CheckClass.LEGALITY,
        summary,
        f"{rule.explanation} Nothing on the creative was read as the {label}"
        + (", and searching the creative's own text for it found nothing either." if detection
           else "."),
        suggested_redline=redline,
    )


# ---------------------------------------------------------------------------
# Primitive: proximity_required
# ---------------------------------------------------------------------------


def _requirement_prose(requirement: str) -> str:
    """The rule's requirement sentence with its engine directives stripped.

    `requirement` is prose for humans that also carries one machine clause —
    "(maps to DisclosureType.X)", the instruction that an extracted disclosure
    of that type satisfies the anchor. The clause belongs in the rulebook, not
    in a finding a compliance officer reads."""
    import re as _re
    cleaned = _re.sub(r"\s*\(maps to DisclosureType\.\w+\)", "", requirement)
    return " ".join(cleaned.split())


def _proximity_required(rule: RulebookEntry, ctx: _Ctx, params: dict | None = None) -> bool:
    """Returns True when at least one anchor matched (for composite gating)."""
    p = params or rule.parameters
    requirement: str = p.get("requirement", "")
    window = IMMEDIATE_WINDOW if "immediate" in requirement.lower() else PROXIMATE_WINDOW
    anchors = pattern_spans(p["anchor_patterns"], ctx.text)
    if not anchors:
        return False
    companions = pattern_spans(p["companion_patterns"], ctx.text)

    # "maps to DisclosureType.X" in the requirement: an extracted disclosure of
    # that type satisfies every anchor (the extractor vouched for adjacency).
    import re as _re
    m = _re.search(r"maps to DisclosureType\.(\w+)", requirement)
    if m:
        try:
            dt = DisclosureType(m.group(1))
            if any(d.disclosure_type == dt for d in ctx.disclosures):
                return True
        except ValueError:
            pass

    check_anchors = anchors[:1] if "first" in requirement.lower() else anchors

    def _near(spans, a_start, a_end):
        return any(
            (c_start - a_end) <= window and (a_start - c_end) <= window
            for c_start, c_end, _ in spans
        )

    mode = p.get("companions_require", "any")
    for a_start, a_end, a_text in check_anchors:
        if mode == "all":
            # Every companion PATTERN that matches anywhere in the text must
            # also match within the window — a proximate alternative phrasing
            # cannot satisfy on behalf of a distant one (v2026.08.4).
            missing = []
            for pat in p["companion_patterns"]:
                spans = pattern_spans([pat], ctx.text)
                if spans and not _near(spans, a_start, a_end):
                    missing.append(spans[0][2])
            ok = not missing
            summary = f"Required wording sits too far from \"{a_text}\""
            detail = (
                "It is on the creative but not beside it: "
                f"{', '.join(chr(34) + m + chr(34) for m in missing)}." if missing else ""
            )
            redline = f"Move that wording up next to \"{a_text}\"."
        else:
            ok = _near(companions, a_start, a_end)
            if companions:
                summary = f"Required wording sits too far from \"{a_text}\""
                detail = (
                    "It appears elsewhere on the creative — nearest match: "
                    f"\"{companions[0][2]}\" — but not beside the figure."
                )
                redline = f"Move that wording up next to \"{a_text}\"."
            else:
                summary = f"\"{a_text}\" is shown without the wording the law requires beside it"
                detail = "Nothing matching the required wording appears on the creative at all."
                redline = f"Add the required wording immediately beside \"{a_text}\"."
        if not ok:
            if ctx.degraded_text:
                # Safety belt: without the creative's own text in reading order
                # the "distance" between two fragments of a concatenation is an
                # artefact of concatenation order, not of layout. Such a finding
                # is never allowed to carry the rule's full severity.
                ctx.emit(
                    rule, CheckClass.LEGALITY,
                    f"Needs verification: could not check what is printed next to \"{a_text}\"",
                    f"{rule.explanation} Layout could not be assessed for this run — the check "
                    "ran on the captured claim and disclosure text only, not the creative's full "
                    "text in reading order, so spacing and adjacency cannot be judged from it. "
                    "Re-run this submission, or check the placement by eye.",
                    severity=Severity.LOW,
                    suggested_redline="Re-run this submission so the layout check can read the "
                                      "creative's full text.",
                )
                continue
            ctx.emit(
                rule, CheckClass.LEGALITY,
                summary,
                f"{rule.explanation} {detail} What the rule requires: "
                f"{_requirement_prose(requirement)}",
                suggested_redline=redline,
            )
    return True


# ---------------------------------------------------------------------------
# Primitive: ground_truth_consistency
# ---------------------------------------------------------------------------


def _rate_claims(ctx: _Ctx) -> list[Claim]:
    return [c for c in ctx.claims if ClaimType.RATE_OR_APR in c.claim_types]


def _resolve_claim_field(
    field_name: str,
    ctx: _Ctx,
    claim_filter: dict | None = None,
    claim_types_any: list[str] | None = None,
) -> list[tuple[object, Claim | None]]:
    """Resolve a composite claim_field to (value, claim) pairs.

    claim_field names come from the payload contract vocabulary (value_pct,
    promo_rate_pct, term_months, ...) and are read directly off
    Claim.normalized_fields; `claim_filter` (optional rule parameter) narrows
    to claims whose payload matches every key (e.g. {"is_floor_claim": true}).
    Two engine-provided virtual fields exist: review_date (:=
    submission.date_submitted — the effectivity reference point) and
    states_targeted (normalized from submission metadata).

    `claim_types_any` (optional rule parameter) narrows to claims carrying at
    least one of the named ClaimTypes. Payload keys are shared across the
    taxonomy — `amount_value` means "the fee amount or percentage" on a
    fee_or_cost claim and "the sum being advertised" on a triggering term —
    so a reconciliation that compares a claim number against a matrix column
    must say which KIND of claim legitimately states that number. Without it a
    "4% origination fee" is read as a $4 loan and reported as a critical
    truthfulness defect against a $2,000–$50,000 range."""
    if field_name == "review_date":
        return [(ctx.review_date, None)]
    if field_name == "states_targeted":
        return [(ctx.states_targeted, None)]
    wanted = {ClaimType(t) for t in claim_types_any} if claim_types_any else None
    out: list[tuple[object, Claim | None]] = []
    for c in ctx.claims:
        nf = c.normalized_fields
        if field_name not in nf or nf[field_name] is None:
            continue
        if wanted is not None and not (wanted & set(c.claim_types)):
            continue
        if claim_filter and any(nf.get(k) != v for k, v in claim_filter.items()):
            continue
        out.append((nf[field_name], c))
    return out


def _matrix_bounds(cell: OfferCell, spec: str):
    if ".." in spec:
        lo_f, hi_f = spec.split("..")
        return getattr(cell, lo_f, None), getattr(cell, hi_f, None)
    return getattr(cell, spec, None)


def _ground_truth_consistency(rule: RulebookEntry, ctx: _Ctx, params: dict | None = None) -> None:
    p = params or rule.parameters
    claim_field, matrix_field, comparator = p["claim_field"], p["matrix_field"], p["comparator"]
    values = _resolve_claim_field(
        claim_field, ctx, p.get("claim_filter"), p.get("claim_types_any")
    )
    if not values:
        return  # nothing claimed -> nothing to reconcile
    if not ctx.cells and comparator != "not_conflated":
        return

    truth = _matrix_prose(matrix_field)
    for value, claim in values:
        cid = claim.id if claim else None
        if comparator == "within_range":
            def _contains(cell):
                lo, hi = _matrix_bounds(cell, matrix_field)
                if lo is None or hi is None:
                    return False
                if isinstance(value, tuple):
                    return lo <= value[0] and value[1] <= hi
                return lo <= value <= hi
            if not any(_contains(c) for c in ctx.cells):
                ranges = [
                    f"{c.offer_id} allows {_fmt(_matrix_bounds(c, matrix_field)[0])}"
                    f"–{_fmt(_matrix_bounds(c, matrix_field)[1])}"
                    for c in ctx.cells
                ]
                if claim_field == "review_date":
                    # The staleness check: the "claim" is the review date, so the
                    # defect is the offer's effective window, not a number the
                    # creative printed.
                    ctx.emit(
                        rule, CheckClass.TRUTHFULNESS,
                        "No referenced offer is in force on the day this was reviewed",
                        f"{rule.explanation} Reviewed {_fmt(value)}; {truth}: "
                        f"{'; '.join(ranges)}.",
                        claim_id=cid,
                        suggested_redline="Point the placement at a current offer, or hold it "
                                          "until the offer is in force.",
                        dedupe_key=("gt", rule.rule_id, claim_field, str(value)),
                    )
                else:
                    ctx.emit(
                        rule, CheckClass.TRUTHFULNESS,
                        f"The {_claimed(claim_field, value)} is not available on any offer "
                        "this placement references",
                        f"{rule.explanation} The creative advertises "
                        f"{_claimed(claim_field, value)}, but {truth}: {'; '.join(ranges)}.",
                        claim_id=cid,
                        suggested_redline="Advertise a figure the referenced offers actually "
                                          "carry, or reference the offer that carries this one.",
                        dedupe_key=("gt", rule.rule_id, claim_field, str(value)),
                    )
        elif comparator == "equals":
            def _eq(cell):
                mv = _matrix_bounds(cell, matrix_field)
                if mv is None:
                    return False
                try:
                    return abs(float(mv) - float(value)) < 1e-6
                except (TypeError, ValueError):
                    return mv == value
            if not any(_eq(c) for c in ctx.cells):
                actual = "; ".join(
                    f"{c.offer_id}: {_fmt(_matrix_bounds(c, matrix_field))}" for c in ctx.cells
                )
                ctx.emit(
                    rule, CheckClass.TRUTHFULNESS,
                    f"The {_claimed(claim_field, value)} does not match the referenced offer",
                    f"{rule.explanation} The creative advertises {_claimed(claim_field, value)}; "
                    f"{truth}: {actual}.",
                    claim_id=cid,
                    suggested_redline="Correct the figure on the creative to the one the offer "
                                      "actually carries.",
                    dedupe_key=("gt", rule.rule_id, claim_field, str(value)),
                )
        elif comparator == "exists_in":
            if not any(_matrix_bounds(c, matrix_field) == value for c in ctx.cells):
                actual = "; ".join(
                    f"{c.offer_id}: {_fmt(_matrix_bounds(c, matrix_field))}" for c in ctx.cells
                )
                ctx.emit(
                    rule, CheckClass.TRUTHFULNESS,
                    f"No referenced offer is sold with the {_claimed(claim_field, value)}",
                    f"{rule.explanation} The creative advertises "
                    f"{_claimed(claim_field, value)}; {truth}: {actual}.",
                    claim_id=cid,
                    suggested_redline="Advertise a term the referenced offers actually sell.",
                    dedupe_key=("gt", rule.rule_id, claim_field, str(value)),
                )
        elif comparator == "disjoint_from":
            # Arbitration semantics: a targeted state is a full-severity leak
            # only when EVERY referenced cell excludes it; excluded by some but
            # not all -> sub-medium needs-verification (one available cell may
            # keep the placement honest there, but attribution is unverified).
            full, partial = [], []
            excl_by_cell = [set(getattr(c, matrix_field, []) or []) for c in ctx.cells]
            for state in value:
                n = sum(state in e for e in excl_by_cell)
                if n == len(ctx.cells) and n > 0:
                    full.append(state)
                elif n > 0:
                    partial.append(state)
            if full:
                ctx.emit(
                    rule, CheckClass.TRUTHFULNESS,
                    "The placement runs in states where none of these offers are available",
                    f"{rule.explanation} No referenced offer is available in: "
                    f"{', '.join(sorted(full))}.",
                    claim_id=cid,
                    suggested_redline="Stop targeting those states, or reference an offer that is "
                                      "available there.",
                )
            if partial:
                per = [
                    f"{c.offer_id} is not available in {', '.join(sorted(e & set(partial)))}"
                    for c, e in zip(ctx.cells, excl_by_cell) if e & set(partial)
                ]
                ctx.emit(
                    rule, CheckClass.TRUTHFULNESS,
                    "Needs verification: some of these offers are not available in states the "
                    "placement targets",
                    f"{rule.explanation} In {', '.join(sorted(partial))}, at least one referenced "
                    f"offer is still available, but not all of them ({'; '.join(per)}). Confirm "
                    "which offer consumers in those states are actually shown.",
                    severity=Severity.LOW,
                    claim_id=cid,
                    suggested_redline="Confirm the per-state offer routing, or narrow the "
                                      "targeting to states every referenced offer serves.",
                )
        elif comparator == "not_conflated":
            bare = [
                c for _, c in values
                if c is not None
                and not c.normalized_fields.get("labeled_as_apr")
                and c.normalized_fields.get("rate_kind") == "unlabeled"
            ]  # claim_field is the payload key 'rate_kind'; labeled_as_apr read alongside
            for c in bare:
                ctx.emit(
                    rule, CheckClass.LEGALITY,
                    "A rate is shown without saying whether it is the APR",
                    f"{rule.explanation} The line \"{c.text}\" gives a rate but never labels it, "
                    "so a reader cannot tell it apart from the APR.",
                    claim_id=c.id,
                    suggested_redline="Label the figure as APR, or show it beside the labelled APR.",
                )
            break  # inspects the claim set once, not per-value
        # review_date within_range doubles as the staleness check and is
        # handled by the generic within_range branch above.


# ---------------------------------------------------------------------------
# Primitive: numeric_cap_by_state
# ---------------------------------------------------------------------------


def _numeric_cap_by_state(rule: RulebookEntry, ctx: _Ctx) -> None:
    p = rule.parameters
    caps: dict = p["caps_table"]
    advertised: list[float] = []
    for c in _rate_claims(ctx):
        for k in ("value_pct", "range_max_pct"):
            v = c.normalized_fields.get(k)
            if v is not None:
                advertised.append(float(v))
    if not advertised:
        advertised = [float(c.apr_max) for c in ctx.cells if c.apr_max is not None]
    if not advertised:
        return
    adv_max = max(advertised)
    over = [
        f"{state} caps rates at {_fmt(entry['apr_cap'])}%"
        + (" including fees" if entry.get("all_in") else "")
        for state, entry in sorted(caps.items())
        if state in ctx.states_targeted and adv_max > float(entry["apr_cap"])
    ]
    if over:
        ctx.emit(
            rule, CheckClass.LEGALITY,
            f"The advertised rate of {_fmt(adv_max)}% is above the legal cap in states this "
            "placement targets",
            f"{rule.explanation} The creative advertises up to {_fmt(adv_max)}%, but "
            f"{'; '.join(over)}.",
            suggested_redline="Stop targeting the capped states, or advertise a rate within "
                              "their cap for those states.",
        )


# ---------------------------------------------------------------------------
# Primitive: composite_all
# ---------------------------------------------------------------------------


def _composite_all(rule: RulebookEntry, ctx: _Ctx) -> None:
    any_anchor = False
    for sub in rule.parameters["checks"]:
        ct = sub["check_type"]
        if ct == "ground_truth_consistency":
            _ground_truth_consistency(rule, ctx, params=sub)
        elif ct == "proximity_required":
            any_anchor = _proximity_required(rule, ctx, params=sub) or any_anchor
        elif ct == "element_required":
            _element_required(rule, ctx, params=sub, any_anchor_matched=any_anchor)
        else:  # pragma: no cover — validator forbids other nestings
            raise ValueError(f"composite_all: unsupported sub-check {ct} in {rule.rule_id}")


# ---------------------------------------------------------------------------
# Fidelity (engine-level; no rulebook rule — ratified design decision)
# ---------------------------------------------------------------------------


def _finding_key(f: Finding) -> tuple:
    """Identity of a finding for the baseline diff.

    Summary text is part of the key, which is safe because both sides of the
    diff are produced by the same engine build in the same process. A baseline
    CheckRun rehydrated from a database written by an older wording would make
    every finding look new; nothing does that today (the API never passes a
    baseline), and if it ever does, key on (rule_id, check_class, claim_id)."""
    return (f.rule_id, f.check_class.value, f.summary)


def _fidelity(
    ctx: _Ctx,
    baseline: CheckRun | None,
    baseline_claims: list[Claim] | None,
    baseline_disclosures: list[Disclosure] | None,
) -> None:
    base_ref = ctx.submission.baseline_submission_id or (
        baseline.submission_id if baseline else "unknown baseline"
    )
    if baseline_disclosures is not None:
        dropped = {d.disclosure_type for d in baseline_disclosures} - {
            d.disclosure_type for d in ctx.disclosures
        }
        for dt in sorted(dropped, key=lambda d: d.value):
            label = DISCLOSURE_LABELS.get(dt, dt.value.replace("_", " "))
            ctx.emit(
                None, CheckClass.FIDELITY,
                f"An approved disclosure is gone from the live placement: the {label}",
                f"The version approved as {base_ref} carried the {label}; the live placement "
                "captured here no longer shows it. The partner changed the creative after "
                "approval.",
                severity=Severity.CRITICAL,
                suggested_redline="Put the approved wording back on the live placement.",
            )
    if baseline_claims is not None:
        base_rates = {
            c.normalized_fields.get("is_floor_claim", False): c.normalized_fields.get("value_pct")
            for c in baseline_claims
            if ClaimType.RATE_OR_APR in c.claim_types and c.normalized_fields.get("value_pct") is not None
        }
        for c in _rate_claims(ctx):
            v = c.normalized_fields.get("value_pct")
            base_v = base_rates.get(c.normalized_fields.get("is_floor_claim", False))
            if v is not None and base_v is not None and abs(float(v) - float(base_v)) > 1e-6:
                ctx.emit(
                    None, CheckClass.FIDELITY,
                    f"The advertised rate changed after approval: {_fmt(base_v)}% → {_fmt(v)}%",
                    f"The version approved as {base_ref} advertised {_fmt(base_v)}%; the live "
                    f"placement captured here shows {_fmt(v)}%. The line reads: \"{c.text}\".",
                    severity=Severity.CRITICAL,
                    claim_id=c.id,
                )
    if baseline is not None:
        base_keys = {_finding_key(f) for f in baseline.findings}
        for f in [
            f for f in ctx.findings
            if f.check_class in (CheckClass.LEGALITY, CheckClass.TRUTHFULNESS)
            and _finding_key(f) not in base_keys
        ]:
            # One fidelity finding PER newly-introduced violation (rule_id=None
            # by design — the drift itself is engine-level, the underlying rule
            # is named in the explanation, where it aids the audit trail).
            ctx.emit(
                None, CheckClass.FIDELITY,
                f"New problem since approval — {f.summary}",
                f"The version approved as {base_ref} did not have this problem; the live "
                f"placement captured here does ({f.rule_id or 'engine check'}: {f.summary}). "
                "The partner materially changed the placement after approval.",
                severity=Severity.CRITICAL,
                claim_id=f.claim_id,
            )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_DISPATCH = {
    "phrase_prohibited": _phrase_prohibited,
    "phrase_conditional": _phrase_conditional,
    "trigger_requires_disclosures": _trigger_requires_disclosures,
    "element_required": _element_required,
    "proximity_required": _proximity_required,
    "ground_truth_consistency": _ground_truth_consistency,
    "numeric_cap_by_state": _numeric_cap_by_state,
    "composite_all": _composite_all,
}


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Overlapping phrase hits dedupe by (claim/span, phrase), keeping the
    highest-severity rule (README convention)."""
    best: dict[tuple, Finding] = {}
    out: list[Finding] = []
    for f in findings:
        key = f.__dict__.pop("_dedupe_key", None)
        if key is None:
            out.append(f)
            continue
        cur = best.get(key)
        if cur is None or _SEVERITY_ORDER[f.severity] > _SEVERITY_ORDER[cur.severity]:
            best[key] = f
    out.extend(best.values())
    out.sort(key=lambda f: (-_SEVERITY_ORDER[f.severity], f.rule_id or "~", f.id))
    return out


def run_checks(
    *,
    submission: Submission,
    claims: list[Claim],
    disclosures: list[Disclosure],
    offer_cells: list[OfferCell],
    offer_matrix_version: str,
    rulebook: Rulebook,
    artifact_text: str | None = None,
    baseline: CheckRun | None = None,
    baseline_claims: list[Claim] | None = None,
    baseline_disclosures: list[Disclosure] | None = None,
) -> CheckRun:
    """Run every deterministic rule for the submission's product.

    text_plane rules run on normalize(artifact_text); when artifact_text is
    None they degrade to the concatenated claim+disclosure texts, one
    info-severity finding records the degraded coverage, and proximity findings
    are demoted to sub-medium needs-verification (the distance between two
    fragments of a concatenation is an artefact of concatenation order, not of
    the creative's layout, so it may never carry full severity). claim_plane
    rules run on typed claims/disclosures plus ground truth. In verification
    mode with a baseline, engine-level fidelity diffs run last (rule_id=None by
    design).
    """
    referenced = [c for c in offer_cells if c.offer_id in set(submission.offer_ids)] or [
        c for c in offer_cells if c.product == submission.product
    ]
    degraded = artifact_text is None or not artifact_text.strip()
    raw_text = artifact_text if not degraded else " ".join(
        [c.text for c in claims] + [d.text for d in disclosures]
    )
    data = getattr(rulebook, "data", None) or {}
    ctx = _Ctx(
        submission=submission,
        claims=claims,
        disclosures=disclosures,
        cells=referenced,
        text=normalize(raw_text),
        degraded_text=degraded,
        review_date=submission.date_submitted,
        states_targeted=normalize_states_targeted(submission.states_targeted),
        effective_disclosure_types=_effective_disclosure_types(
            disclosures, data.get("disclosure_type_patterns", {})
        ),
        integration=data.get("integration_config", {}).get(submission.partner, {}),
    )

    rules = [r for r in rulebook.deterministic_rules if r.product == submission.product]
    ran_text_plane = False
    for rule in rules:
        if rule.parameters.get("decision_inputs") == "text_plane":
            ran_text_plane = True
        _DISPATCH[rule.parameters["check_type"]](rule, ctx)

    if degraded and ran_text_plane:
        ctx.emit(
            None, CheckClass.LEGALITY,
            "Layout and spacing checks ran on extracted text only for this run; proximity "
            "results may be incomplete",
            "The creative's full text in reading order was not available, so the wording checks "
            "ran against the captured claims and disclosures instead. Anything about what sits "
            "next to what on the page should be confirmed by eye.",
            severity=Severity.INFO,
        )

    if submission.mode == SubmissionMode.VERIFICATION and (
        baseline is not None or baseline_claims is not None or baseline_disclosures is not None
    ):
        _fidelity(ctx, baseline, baseline_claims, baseline_disclosures)

    return CheckRun(
        id=f"chk-{submission.submission_id}-{rulebook.version}-{offer_matrix_version}",
        submission_id=submission.submission_id,
        rulebook_version=rulebook.version,
        offer_matrix_version=offer_matrix_version,
        mode=submission.mode,
        created_at=datetime.now(timezone.utc),
        findings=_dedupe(ctx.findings),
    )
