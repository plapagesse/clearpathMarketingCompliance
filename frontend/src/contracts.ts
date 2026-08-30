/**
 * Hand-mirrored from backend/contracts.py (the source of truth).
 * FREEZE RULE: keep in sync; changes must be called out explicitly in PRs.
 */

export type Product = 'personal_loan' | 'credit_card' | 'mortgage_prequal'

export type ClaimType =
  | 'rate'
  | 'payment'
  | 'amount'
  | 'approval'
  | 'fee'
  | 'urgency'
  | 'comparison'
  | 'testimonial'
  | 'other'

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
  claim_type: ClaimType
  text: string
  location: string
  source_evidence_id: string
}

export interface Disclosure {
  id: string
  disclosure_type: DisclosureType
  text: string
  location: string
  prominence: string
}

export interface RulebookEntry {
  rule_id: string
  product: Product
  claim_types: ClaimType[]
  check_kind: CheckKind
  severity: Severity
  parameters: Record<string, unknown>
  citation_url: string
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
