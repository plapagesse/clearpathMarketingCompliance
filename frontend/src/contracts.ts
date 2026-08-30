/**
 * Hand-mirrored from backend/contracts.py (the source of truth).
 * FREEZE RULE: keep in sync; changes must be called out explicitly in PRs.
 */

export type Product = 'personal_loan' | 'credit_card' | 'mortgage_prequal'

/**
 * Legal-entity taxonomy: each value names the body of law governing the claim.
 * Claims are MULTI-LABEL (amendment #4): one statement = one Claim object,
 * listing every legal category it embodies. See CONTRACTS.md.
 */
export type ClaimType =
  | 'triggering_term' // Reg Z 1026.24(d) / 1026.16(b)
  | 'rate_or_apr' // Reg Z 1026.24(b)-(c)
  | 'promotional_or_introductory' // Reg Z 1026.16(g)-(h)
  | 'fixed_rate_representation' // Reg Z 1026.16(f); Reg N 1014.3
  | 'approval_or_prequalification' // FCRA 603(l); Reg N 1014.3(q)
  | 'fee_or_cost' // TILA 1026.4; Reg N 1014.3(c)
  | 'endorsement_or_testimonial' // 16 CFR 255.0
  | 'government_affiliation' // Reg N 1014.3(n)
  | 'general_udaap_representation' // FTC Act §5; CFPA §1031 (residual)

export type DisclosureType =
  | 'apr_qualifier'
  | 'trigger_disclosure'
  | 'soft_pull'
  | 'not_guaranteed'
  | 'opt_out_notice'
  | 'schumer_box_link'
  | 'nmls_id'
  | 'taxes_insurance'
  | 'state_license'
  | 'intro_adjacency'
  | 'other'

export type CheckClass = 'legality' | 'truthfulness' | 'fidelity' | 'judgment'
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'
export type FindingStatus = 'open' | 'accepted' | 'overridden'
export type CheckKind = 'deterministic' | 'llm_judged'
export type SubmissionMode = 'pre_publication' | 'verification'
export type BadgeDesignation = 'prequalified' | 'pre-approved'

export interface Claim {
  id: string
  /** Every legal category this statement embodies (amendment #4; min length 1) */
  claim_types: ClaimType[]
  text: string
  location: string
  source_evidence_id: string
  /** Amendment #5: union of the listed claim types' payload contracts (see payload interfaces below) */
  normalized_fields: Record<string, unknown>
}

/* Claim-type payload contracts (amendment #5) — mirror of CLAIM_TYPE_PAYLOADS
   in backend/contracts.py; field names come from rulebook/claim_types_legal_map.json. */
export interface TriggeringTermPayload {
  payment_amount?: number | null
  num_payments?: number | null
  term_months?: number | null
  downpayment?: number | null
  finance_charge?: number | null
}
export interface RateOrAprPayload {
  value_pct?: number | null
  range_min_pct?: number | null
  range_max_pct?: number | null
  is_floor_claim: boolean
  labeled_as_apr: boolean
  rate_kind: 'apr' | 'interest_rate' | 'unlabeled'
}
export interface PromotionalOrIntroductoryPayload {
  promo_rate_pct: number
  promo_period_months?: number | null
  has_intro_word: boolean
  is_deferred_interest: boolean
  post_promo_rate_stated: boolean
}
export interface FixedRateRepresentationPayload {
  applies_to_rate: boolean
  fixed_period_stated?: string | null
}
export interface ApprovalOrPrequalificationPayload {
  badge_word: string
  strength: 'guaranteed' | 'pre_approved' | 'prequalified' | 'odds_numeric' | 'odds_qualitative' | 'invitation'
  odds_value_pct?: number | null
}
export interface FeeOrCostPayload {
  fee_claim_kind: 'absence_of_fee' | 'specific_fee_amount' | 'fee_disclosure'
  fee_type?: 'annual_fee' | 'origination_fee' | 'closing_costs' | 'balance_transfer_fee' | 'other' | null
  amount_value?: number | null
}
export interface EndorsementOrTestimonialPayload {
  endorser_named: boolean
  material_connection_disclosed: boolean
  atypical_result_claimed: boolean
  result_claim_text?: string | null
}
export interface GovernmentAffiliationPayload {
  agency_or_program?: string | null
  is_program_reference: boolean
  affiliation_implied: boolean
}
export interface GeneralUdaapRepresentationPayload {
  representation_kind:
    | 'urgency_device'
    | 'comparative_superlative'
    | 'amount_offered'
    | 'savings_claim'
    | 'soft_pull_claim'
    | 'debt_elimination'
    | 'geographic_availability'
    | 'other'
  amount_value?: number | null
  claimed_deadline?: string | null
  comparative_is_measurable?: boolean | null
}

export interface Disclosure {
  id: string
  disclosure_type: DisclosureType
  text: string
  location: string
  prominence: string
}

export type AuthorityRegime =
  | 'statute'
  | 'regulation'
  | 'official_interpretation'
  | 'agency_guide'
  | 'enforcement_doctrine'
  | 'state_statute'
  | 'state_regulation'

export interface LegalAuthority {
  body: string // e.g. 'Regulation Z (Truth in Lending Act)'
  citation: string // pinpoint cite, e.g. '12 CFR § 1026.24(d)(2)'
  regime: AuthorityRegime
  regulator: string // CFPB | FTC | state name
  url: string
}

export interface RulebookEntry {
  rule_id: string
  product: Product
  claim_types: ClaimType[]
  check_kind: CheckKind
  severity: Severity
  parameters: Record<string, unknown>
  authorities: LegalAuthority[] // min length 1; primary first
  explanation: string
}

export interface OfferCell {
  offer_id: string
  product: Product
  offer_name: string
  apr_min: number | null
  apr_max: number | null
  apr_type: string | null
  term_months: number | null
  amount_min: number | null
  amount_max: number | null
  origination_fee_pct: string | null
  fee_deducted_from_proceeds: boolean | null
  intro_apr_pct: number | null
  intro_period_months: number | null
  annual_fee: number | null
  badge_designation_allowed: BadgeDesignation
  is_firm_offer: boolean
  min_credit_score: number | null
  states_excluded: string[]
  effective_start: string // ISO date
  effective_end: string // ISO date
  notes: string
}

export interface Submission {
  id: string
  submission_id: string
  partner: string
  date_submitted: string // ISO date
  surface: string
  product: Product
  template_id: string
  template_version: string
  offer_ids: string[]
  proposed_headline: string
  badge_text: string
  dynamic_slots: string[]
  disclosures_included: string[]
  asset_files: string[]
  states_targeted: string
  requested_launch: string | null // ISO date
  change_summary: string
  status: string
  sla_due: string | null // ISO date
  mode: SubmissionMode
  // verification mode: the APPROVED submission this evidence is diffed against
  baseline_submission_id: string | null
}

export interface Finding {
  id: string
  check_class: CheckClass
  severity: Severity
  rule_id: string | null
  claim_id: string | null
  summary: string
  explanation: string
  citation_url: string | null
  suggested_redline: string | null
  status: FindingStatus
}

export interface CheckRun {
  id: string
  submission_id: string
  rulebook_version: string
  offer_matrix_version: string
  mode: SubmissionMode
  created_at: string // ISO datetime
  findings: Finding[]
}
