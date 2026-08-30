"""Deterministic check engine.

Executes the rulebook's deterministic rules against one submission's evidence:
typed claims/disclosures (claim_plane), normalized artifact text (text_plane),
the referenced offer-matrix cells (truthfulness ground truth), submission
metadata, and — in verification mode — an approved baseline (fidelity).

The 8 primitives are generic engines consuming rule parameters; there is no
per-rule code. See CONSUMED_FIELDS.md for the exact demand ledger of every
input field each rule's evaluation reads.
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

_SEVERITY_ORDER = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


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
                f"Prohibited phrase '{hit}' present",
                f"{rule.explanation} Detected in artifact text.",
                dedupe_key=("phrase", None, normalize(hit)),
            )
        return
    covered: set[str] = set()
    for c in ctx.routed_claims(rule):
        for hit in phrase_hits(phrases, normalize(c.text), match):
            covered.add(normalize(hit))
            ctx.emit(
                rule, CheckClass.LEGALITY,
                f"Prohibited phrase '{hit}' in claim",
                f"{rule.explanation} Claim: \"{c.text}\".",
                claim_id=c.id,
                suggested_redline=f"Remove or rewrite the phrase '{hit}'.",
                dedupe_key=("phrase", c.id, normalize(hit)),
            )
    # Safety net: hits in raw text no routed claim covered (extraction misses).
    for hit in phrase_hits(phrases, ctx.text, match):
        if normalize(hit) not in covered:
            ctx.emit(
                rule, CheckClass.LEGALITY,
                f"Prohibited phrase '{hit}' present (safety-net detection)",
                f"{rule.explanation} Detected in artifact text; no extracted claim covered it.",
                dedupe_key=("phrase", None, normalize(hit)),
            )


# ---------------------------------------------------------------------------
# Primitive: phrase_conditional
# ---------------------------------------------------------------------------


def _resolve_condition(field_name: str, ctx: _Ctx):
    """Resolve a phrase_conditional condition_field.

    Returns a list of per-cell values for OfferCell columns, a single-element
    list for derived conditions, or None when unresolvable (verification
    inputs like soft_pull_verified / government_program_verified)."""
    if field_name in _OFFER_CONDITION_FIELDS:
        return [getattr(c, field_name) for c in ctx.cells]
    if field_name == "effective_end_supports_urgency":
        supported = any(
            (c.effective_end - ctx.review_date).days <= URGENCY_WINDOW_DAYS
            for c in ctx.cells
        )
        return [supported]
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

    detections: list[tuple[str, str | None]] = []  # (phrase, claim_id)
    if text_plane:
        detections = [(h, None) for h in phrase_hits(phrases, ctx.text, match)]
    else:
        covered: set[str] = set()
        for c in ctx.routed_claims(rule):
            for hit in phrase_hits(phrases, normalize(c.text), match):
                covered.add(normalize(hit))
                detections.append((hit, c.id))
        for hit in phrase_hits(phrases, ctx.text, match):  # safety net
            if normalize(hit) not in covered:
                detections.append((hit, None))
    if not detections:
        return

    values = _resolve_condition(p["condition_field"], ctx)
    if values is None:
        for hit, claim_id in detections:
            ctx.emit(
                rule, CheckClass.LEGALITY,
                f"Needs verification: '{hit}' depends on unverified {p['condition_field']}",
                f"{rule.explanation} The condition '{p['condition_field']}' is a verification "
                "input the engine cannot resolve from the offer matrix; verify before approval.",
                severity=Severity.MEDIUM,
                claim_id=claim_id,
                dedupe_key=("phrase", claim_id, normalize(hit)),
            )
        return

    violates_when = p.get("violates_when")
    violating_cells = [
        c.offer_id for c, v in zip(ctx.cells, values)
        if v == violates_when
    ] if len(values) == len(ctx.cells) else (["(derived)"] if values and values[0] == violates_when else [])
    if not violating_cells:
        return

    qualifier = p.get("required_qualifier")
    if qualifier and _qualifier_cured(qualifier, ctx):
        return  # the cure is proximately present; finding cleared

    for hit, claim_id in detections:
        ctx.emit(
            rule, CheckClass.TRUTHFULNESS if p["condition_field"] in _OFFER_CONDITION_FIELDS else CheckClass.LEGALITY,
            f"'{hit}' conflicts with referenced offer ({p['condition_field']})",
            f"{rule.explanation} Offer cells implicated: {', '.join(violating_cells)}."
            + (f" Required cure absent: {qualifier}." if qualifier else ""),
            claim_id=claim_id,
            suggested_redline=(f"Add: {qualifier}" if qualifier else f"Remove '{hit}' or change the referenced offer."),
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
            trigger_evidence = f"{hit} (safety-net detection; no extracted claim covered it)"
    if trigger_evidence is None:
        return

    present = {d.disclosure_type for d in ctx.disclosures}
    for dt in required:
        if dt not in present:
            ctx.emit(
                rule, CheckClass.LEGALITY,
                f"Missing required disclosure '{dt.value}'",
                f"{rule.explanation} Triggered by: \"{trigger_evidence}\"; no extracted "
                f"disclosure of type '{dt.value}' is present.",
                claim_id=trigger_claim.id if trigger_claim else None,
                suggested_redline=f"Add a '{dt.value}' disclosure adjacent to the triggering statement.",
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
    ctx.emit(
        rule, CheckClass.LEGALITY,
        f"Required element '{element}' is missing",
        f"{rule.explanation} No extracted disclosure of that type"
        + (" and no text-plane detection" if detection else "")
        + " found in the artifact.",
        suggested_redline=f"Add the mandated '{element}' element to the creative.",
    )


# ---------------------------------------------------------------------------
# Primitive: proximity_required
# ---------------------------------------------------------------------------


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
    for a_start, a_end, a_text in check_anchors:
        ok = any(
            (c_start - a_end) <= window and (a_start - c_end) <= window
            for c_start, c_end, _ in companions
        )
        if not ok:
            ctx.emit(
                rule, CheckClass.LEGALITY,
                f"'{a_text}' lacks its required companion in proximity",
                f"{rule.explanation} Requirement: {requirement} "
                f"No companion within ~{window} characters of '{a_text}'.",
                suggested_redline="Move the required companion text into immediate proximity of the anchor.",
            )
    return True


# ---------------------------------------------------------------------------
# Primitive: ground_truth_consistency
# ---------------------------------------------------------------------------


def _rate_claims(ctx: _Ctx) -> list[Claim]:
    return [c for c in ctx.claims if ClaimType.RATE_OR_APR in c.claim_types]


def _resolve_claim_field(field_name: str, ctx: _Ctx) -> list[tuple[object, Claim | None]]:
    nf = lambda c: c.normalized_fields  # noqa: E731
    if field_name in ("rate_value", "rate_or_apr_value"):
        return [(nf(c)["value_pct"], c) for c in _rate_claims(ctx) if nf(c).get("value_pct") is not None]
    if field_name == "rate_floor":
        return [
            (nf(c)["value_pct"], c)
            for c in _rate_claims(ctx)
            if nf(c).get("is_floor_claim") and nf(c).get("value_pct") is not None
        ]
    if field_name == "amount_value":
        return [(nf(c)["amount_value"], c) for c in ctx.claims if nf(c).get("amount_value") is not None]
    if field_name == "term_months":
        return [
            (nf(c)["term_months"], c)
            for c in ctx.claims
            if ClaimType.TRIGGERING_TERM in c.claim_types and nf(c).get("term_months") is not None
        ]
    if field_name == "intro_apr_value":
        return [
            (nf(c)["promo_rate_pct"], c)
            for c in ctx.claims
            if ClaimType.PROMOTIONAL_OR_INTRODUCTORY in c.claim_types
            and nf(c).get("promo_rate_pct") is not None
        ]
    if field_name == "intro_period_value":
        return [
            (nf(c)["promo_period_months"], c)
            for c in ctx.claims
            if ClaimType.PROMOTIONAL_OR_INTRODUCTORY in c.claim_types
            and nf(c).get("promo_period_months") is not None
        ]
    if field_name == "post_promo_apr_range":
        return [
            ((nf(c)["range_min_pct"], nf(c)["range_max_pct"]), c)
            for c in _rate_claims(ctx)
            if nf(c).get("range_min_pct") is not None and nf(c).get("range_max_pct") is not None
        ]
    if field_name == "annual_fee_value":
        return [
            (nf(c)["amount_value"], c)
            for c in ctx.claims
            if ClaimType.FEE_OR_COST in c.claim_types
            and nf(c).get("fee_type") == "annual_fee"
            and nf(c).get("amount_value") is not None
        ]
    if field_name == "review_date":
        return [(ctx.review_date, None)]
    if field_name == "states_targeted":
        return [(ctx.states_targeted, None)]
    if field_name == "rate_label":
        return [(None, c) for c in _rate_claims(ctx)]  # not_conflated inspects the claims
    return []


def _matrix_bounds(cell: OfferCell, spec: str):
    if ".." in spec:
        lo_f, hi_f = spec.split("..")
        return getattr(cell, lo_f, None), getattr(cell, hi_f, None)
    return getattr(cell, spec, None)


def _ground_truth_consistency(rule: RulebookEntry, ctx: _Ctx, params: dict | None = None) -> None:
    p = params or rule.parameters
    claim_field, matrix_field, comparator = p["claim_field"], p["matrix_field"], p["comparator"]
    values = _resolve_claim_field(claim_field, ctx)
    if not values:
        return  # nothing claimed -> nothing to reconcile
    if not ctx.cells and comparator != "not_conflated":
        return

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
                    f"{c.offer_id}: {_matrix_bounds(c, matrix_field)[0]}–{_matrix_bounds(c, matrix_field)[1]}"
                    for c in ctx.cells
                ]
                ctx.emit(
                    rule, CheckClass.TRUTHFULNESS,
                    f"Claimed {claim_field} {value} is outside every referenced offer cell",
                    f"{rule.explanation} Claimed value {value} vs {matrix_field} of referenced "
                    f"cells ({'; '.join(ranges)}).",
                    claim_id=cid,
                    suggested_redline=f"Correct the advertised {claim_field} to the current offer-matrix value.",
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
                ctx.emit(
                    rule, CheckClass.TRUTHFULNESS,
                    f"Claimed {claim_field} {value} matches no referenced offer cell",
                    f"{rule.explanation} Claimed {value}; referenced cells carry "
                    f"{[_matrix_bounds(c, matrix_field) for c in ctx.cells]} for {matrix_field}.",
                    claim_id=cid,
                    suggested_redline=f"Align the advertised {claim_field} with the offer matrix.",
                    dedupe_key=("gt", rule.rule_id, claim_field, str(value)),
                )
        elif comparator == "exists_in":
            if not any(_matrix_bounds(c, matrix_field) == value for c in ctx.cells):
                ctx.emit(
                    rule, CheckClass.TRUTHFULNESS,
                    f"Claimed {claim_field} {value} exists in no referenced offer cell",
                    f"{rule.explanation} No referenced cell carries {matrix_field} == {value}.",
                    claim_id=cid,
                    dedupe_key=("gt", rule.rule_id, claim_field, str(value)),
                )
        elif comparator == "disjoint_from":
            overlaps = []
            for c in ctx.cells:
                excluded = set(getattr(c, matrix_field, []) or [])
                hit = sorted(excluded & set(value))
                if hit:
                    overlaps.append(f"{c.offer_id}: {', '.join(hit)}")
            if overlaps:
                ctx.emit(
                    rule, CheckClass.TRUTHFULNESS,
                    "Placement targets states the referenced offers exclude",
                    f"{rule.explanation} Overlaps — {'; '.join(overlaps)}.",
                    claim_id=cid,
                    suggested_redline="Exclude the listed states from the placement's targeting, or reference offers available there.",
                )
        elif comparator == "not_conflated":
            bare = [
                c for _, c in values
                if c is not None
                and not c.normalized_fields.get("labeled_as_apr")
                and c.normalized_fields.get("rate_kind") == "unlabeled"
            ]
            for c in bare:
                ctx.emit(
                    rule, CheckClass.LEGALITY,
                    "Rate presented without an APR label (conflation risk)",
                    f"{rule.explanation} Claim \"{c.text}\" carries an unlabeled rate.",
                    claim_id=c.id,
                    suggested_redline="Label the rate as APR (or present it only alongside the labeled APR).",
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
        f"{state} (cap {entry['apr_cap']}%{', all-in' if entry.get('all_in') else ''})"
        for state, entry in sorted(caps.items())
        if state in ctx.states_targeted and adv_max > float(entry["apr_cap"])
    ]
    if over:
        ctx.emit(
            rule, CheckClass.LEGALITY,
            f"Advertised APR max {adv_max}% exceeds rate caps of targeted states",
            f"{rule.explanation} Advertised maximum {adv_max}% vs: {'; '.join(over)}.",
            suggested_redline="Geo-exclude the capped states or cap the advertised range per state.",
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
            ctx.emit(
                None, CheckClass.FIDELITY,
                f"Approved disclosure '{dt.value}' dropped from live placement",
                f"The approved baseline ({base_ref}) carried a '{dt.value}' disclosure that the "
                "captured live placement no longer shows.",
                severity=Severity.CRITICAL,
                suggested_redline="Restore the approved disclosure text.",
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
                    f"Advertised rate changed vs approved baseline ({base_v}% → {v}%)",
                    f"The approved baseline ({base_ref}) advertised {base_v}%; the captured live "
                    f"placement shows {v}%. Claim: \"{c.text}\".",
                    severity=Severity.CRITICAL,
                    claim_id=c.id,
                )
    if baseline is not None:
        base_keys = {_finding_key(f) for f in baseline.findings}
        new = [
            f for f in ctx.findings
            if f.check_class in (CheckClass.LEGALITY, CheckClass.TRUTHFULNESS)
            and _finding_key(f) not in base_keys
        ]
        if new:
            ctx.emit(
                None, CheckClass.FIDELITY,
                f"{len(new)} violation(s) present that the approved baseline did not have",
                f"Relative to the approved baseline ({base_ref}), this capture introduces: "
                + "; ".join(f"{f.rule_id or 'engine'}: {f.summary}" for f in new)
                + ". The partner materially changed the placement after approval.",
                severity=Severity.CRITICAL,
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
    None they degrade to the concatenated claim+disclosure texts and one
    info-severity finding records the degraded coverage. claim_plane rules run
    on typed claims/disclosures plus ground truth. In verification mode with a
    baseline, engine-level fidelity diffs run last (rule_id=None by design).
    """
    referenced = [c for c in offer_cells if c.offer_id in set(submission.offer_ids)] or [
        c for c in offer_cells if c.product == submission.product
    ]
    degraded = artifact_text is None
    raw_text = artifact_text if artifact_text is not None else " ".join(
        [c.text for c in claims] + [d.text for d in disclosures]
    )
    ctx = _Ctx(
        submission=submission,
        claims=claims,
        disclosures=disclosures,
        cells=referenced,
        text=normalize(raw_text),
        degraded_text=degraded,
        review_date=submission.date_submitted,
        states_targeted=normalize_states_targeted(submission.states_targeted),
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
            "Degraded text-plane coverage: no artifact text supplied",
            "Token-bound (text_plane) rules ran against the concatenated extracted claim and "
            "disclosure texts instead of the full artifact text; layout/proximity findings may "
            "be incomplete for this run.",
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
