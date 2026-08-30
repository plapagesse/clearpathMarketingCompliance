"""Frozen data contracts for the ClearPath marketing-compliance platform.

Single source of truth for every shape that crosses a component boundary.
Frontend mirror: frontend/src/contracts.ts (kept in sync by hand).

FREEZE RULE: after Stage 1 these models change only via a PR that calls the
change out explicitly. See CONTRACTS.md.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class Product(str, Enum):
    PERSONAL_LOAN = "personal_loan"
    CREDIT_CARD = "credit_card"
    MORTGAGE_PREQUAL = "mortgage_prequal"


class ClaimType(str, Enum):
    """Legal-entity taxonomy: each value names the body of law that governs the claim.

    A text span embodying two legal categories yields two Claim objects (extractor
    convention — no multi-label claims). Anchors documented in CONTRACTS.md and
    rulebook/claim_types_legal_map.json.
    """

    TRIGGERING_TERM = "triggering_term"                          # Reg Z 1026.24(d) / 1026.16(b)
    RATE_OR_APR = "rate_or_apr"                                  # Reg Z 1026.24(b)-(c)
    PROMOTIONAL_OR_INTRODUCTORY = "promotional_or_introductory"  # Reg Z 1026.16(g)-(h)
    FIXED_RATE_REPRESENTATION = "fixed_rate_representation"      # Reg Z 1026.16(f); Reg N 1014.3
    APPROVAL_OR_PREQUALIFICATION = "approval_or_prequalification"  # FCRA 603(l); Reg N 1014.3(q)
    FEE_OR_COST = "fee_or_cost"                                  # TILA 1026.4; Reg N 1014.3(c)
    ENDORSEMENT_OR_TESTIMONIAL = "endorsement_or_testimonial"    # 16 CFR 255.0
    GOVERNMENT_AFFILIATION = "government_affiliation"            # Reg N 1014.3(n)
    GENERAL_UDAAP_REPRESENTATION = "general_udaap_representation"  # FTC Act §5; CFPA §1031 (residual)


class DisclosureType(str, Enum):
    APR_QUALIFIER = "apr_qualifier"          # creditworthiness / floor qualifiers
    TRIGGER_DISCLOSURE = "trigger_disclosure"  # Reg Z companion terms after a trigger
    SOFT_PULL = "soft_pull"                  # "won't affect your credit score"
    NOT_GUARANTEED = "not_guaranteed"        # approval-not-guaranteed qualifier
    OPT_OUT_NOTICE = "opt_out_notice"        # FCRA prescreen short/long notice
    SCHUMER_BOX_LINK = "schumer_box_link"
    NMLS_ID = "nmls_id"
    TAXES_INSURANCE = "taxes_insurance"      # payment excludes taxes & insurance
    STATE_LICENSE = "state_license"
    INTRO_ADJACENCY = "intro_adjacency"      # "intro" adjacent to promo rate
    OTHER = "other"


class CheckClass(str, Enum):
    LEGALITY = "legality"        # claims vs. rulebook
    TRUTHFULNESS = "truthfulness"  # claims vs. offer matrix / served response
    FIDELITY = "fidelity"        # rendered artifact vs. approved baseline
    JUDGMENT = "judgment"        # LLM gray-area assessment


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStatus(str, Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    OVERRIDDEN = "overridden"


class CheckKind(str, Enum):
    DETERMINISTIC = "deterministic"
    LLM_JUDGED = "llm_judged"


class SubmissionMode(str, Enum):
    PRE_PUBLICATION = "pre_publication"  # partner mock awaiting approval
    VERIFICATION = "verification"        # live-placement evidence (seed account etc.)


class BadgeDesignation(str, Enum):
    PREQUALIFIED = "prequalified"
    PRE_APPROVED = "pre-approved"


# --------------------------------------------------------------------------- #
# Extraction output
# --------------------------------------------------------------------------- #


class Claim(BaseModel):
    """One marketing claim extracted from an evidence artifact."""

    id: str
    claim_type: ClaimType
    text: str = Field(description="Verbatim claim text as rendered")
    location: str = Field(description="Where in the artifact (e.g. 'headline', 'fine print', 'badge')")
    source_evidence_id: str


class Disclosure(BaseModel):
    """One disclosure found in an evidence artifact."""

    id: str
    disclosure_type: DisclosureType
    text: str
    location: str
    prominence: str = Field(description="e.g. 'headline', 'body', 'fine_print', 'below_fold'")


# --------------------------------------------------------------------------- #
# Rulebook
# --------------------------------------------------------------------------- #


class AuthorityRegime(str, Enum):
    STATUTE = "statute"
    REGULATION = "regulation"
    OFFICIAL_INTERPRETATION = "official_interpretation"
    AGENCY_GUIDE = "agency_guide"
    ENFORCEMENT_DOCTRINE = "enforcement_doctrine"
    STATE_STATUTE = "state_statute"
    STATE_REGULATION = "state_regulation"


class LegalAuthority(BaseModel):
    """One body of law a rule rests on, with a pinpoint citation."""

    body: str = Field(description="Human name, e.g. 'Regulation Z (Truth in Lending Act)'")
    citation: str = Field(description="Formal pinpoint cite, e.g. '12 CFR § 1026.24(d)(2)'")
    regime: AuthorityRegime
    regulator: str = Field(description="CFPB | FTC | state name")
    url: str


class RulebookEntry(BaseModel):
    """One machine-actionable compliance rule."""

    rule_id: str
    product: Product
    claim_types: list[ClaimType] = Field(description="Claim types this rule subscribes to")
    check_kind: CheckKind
    severity: Severity
    parameters: dict = Field(
        default_factory=dict,
        description="Machine-actionable data: trigger-term lists, required disclosures, phrase lexicon, caps",
    )
    authorities: list[LegalAuthority] = Field(
        min_length=1, description="Authorities this rule operationalizes; primary authority first"
    )
    explanation: str = Field(description="Plain-language rationale, shown to reviewers and fed to the LLM judge")


# --------------------------------------------------------------------------- #
# Ground truth: offer matrix (mirrors fixtures/offer matrix CSV columns)
# --------------------------------------------------------------------------- #


class OfferCell(BaseModel):
    """One row of the offer matrix — the source of truth of what may be claimed."""

    offer_id: str
    product: Product
    offer_name: str
    apr_min: float | None = None
    apr_max: float | None = None
    apr_type: str | None = None  # fixed | variable
    term_months: int | None = None
    amount_min: float | None = None
    amount_max: float | None = None
    origination_fee_pct: str | None = None  # range string, e.g. "1.0-6.0"
    fee_deducted_from_proceeds: bool | None = None
    intro_apr_pct: float | None = None
    intro_period_months: int | None = None
    annual_fee: float | None = None
    badge_designation_allowed: BadgeDesignation
    is_firm_offer: bool
    min_credit_score: int | None = None
    states_excluded: list[str] = Field(default_factory=list)
    effective_start: date
    effective_end: date
    notes: str = ""


# --------------------------------------------------------------------------- #
# Intake: submission manifest (mirrors fixtures/submission CSV columns)
# --------------------------------------------------------------------------- #


class Submission(BaseModel):
    """One review request: an evidence artifact plus its context bundle."""

    id: str
    submission_id: str  # partner-facing id, e.g. SUB-2026-0142
    partner: str
    date_submitted: date
    surface: str  # e.g. marketplace_offer_card, prescreen_email, mortgage_rate_table
    product: Product
    template_id: str
    template_version: str
    offer_ids: list[str] = Field(default_factory=list)
    proposed_headline: str = ""
    badge_text: str = ""
    dynamic_slots: list[str] = Field(default_factory=list)
    disclosures_included: list[str] = Field(default_factory=list)
    asset_files: list[str] = Field(default_factory=list)
    states_targeted: str = ""
    requested_launch: date | None = None
    change_summary: str = ""
    status: str = "pending_review"
    sla_due: date | None = None
    mode: SubmissionMode = SubmissionMode.PRE_PUBLICATION
    baseline_submission_id: str | None = Field(
        default=None,
        description="verification mode: the APPROVED submission this evidence is diffed against (fidelity join key)",
    )


# --------------------------------------------------------------------------- #
# Engine output
# --------------------------------------------------------------------------- #


class Finding(BaseModel):
    """One issue (or gray-area flag) raised against a submission."""

    id: str
    check_class: CheckClass
    severity: Severity
    rule_id: str | None = None
    claim_id: str | None = None
    summary: str
    explanation: str
    citation_url: str | None = None
    suggested_redline: str | None = None
    status: FindingStatus = FindingStatus.OPEN


class CheckRun(BaseModel):
    """One execution of the engine against one submission.

    Records WHICH rulebook version and WHICH offer-matrix version it ran
    against — this is what makes re-validation sweeps and the audit trail work.
    """

    id: str
    submission_id: str
    rulebook_version: str
    offer_matrix_version: str
    mode: SubmissionMode
    created_at: datetime
    findings: list[Finding] = Field(default_factory=list)
