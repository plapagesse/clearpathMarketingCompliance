# CONSUMED_FIELDS — the deterministic checker's demand ledger

Exact inventory of every input field the engine **reads** while evaluating the
38 deterministic rules (rulebook v2026.08.3). If a field is not listed for a
rule, the engine never touches it. Composite claim_field names now use the payload vocabulary directly
(ruling: no internal mapping layer). This ledger is the demand-side input to the
claim-type payload trim: a `normalized_fields` key read by no rule below (and
reserved by no judge rule) has no deterministic consumer.

## Summary — Claim.normalized_fields

**Read by the engine (11):**

| Field | Read by | Purpose |
|---|---|---|
| `value_pct` | PL-TRUTH-001, MTG-TRUTH-001, PL-STATE-CAP-001 | rate/floor reconciliation vs matrix; advertised-max vs state caps |
| `range_min_pct` | CC-TRUTH-001 | post-promo APR range vs matrix |
| `range_max_pct` | CC-TRUTH-001, PL-STATE-CAP-001 | range top vs matrix / state caps |
| `is_floor_claim` | PL-TRUTH-001 (`claim_filter`), fidelity diff | selects floor claims; floor == apr_min check |
| `labeled_as_apr` | MTG-TRUTH-001 (not_conflated) | unlabeled-rate conflation check |
| `rate_kind` | MTG-TRUTH-001 (not_conflated) | unlabeled-rate conflation check |
| `term_months` | PL-TRUTH-001 | exists_in vs cell term_months |
| `amount_value` | PL-TRUTH-001, CC-TRUTH-001 (annual_fee_value) | amount within cell bounds; annual-fee equality |
| `fee_type` | CC-TRUTH-001 (`claim_filter`) | selects annual-fee claims |
| `promo_rate_pct` | CC-TRUTH-001 | intro APR equality vs matrix |
| `promo_period_months` | CC-TRUTH-001 | intro period equality vs matrix |

**Never read by the deterministic engine (25):**
`payment_amount`, `num_payments`, `downpayment`, `finance_charge`,
`downpayment_is_pct`, `has_intro_word`, `is_deferred_interest`,
`post_promo_rate_stated`, `applies_to_rate`, `fixed_period_stated`,
`badge_word`, `strength`, `odds_value_pct`, `fee_claim_kind`, `amount_is_pct`,
`endorser_named`, `material_connection_disclosed`, `atypical_result_claimed`,
`result_claim_text`, `agency_or_program`, `is_program_reference`,
`affiliation_implied`, `representation_kind`, `claimed_deadline`,
`comparative_is_measurable`.

Judge-reserved caveat for the trim: `odds_value_pct` (XP-ODDS-005 substantiation)
and the endorsement/testimonial fields sit in **llm_judged** territory — the
judge reads claim *text* primarily, so whether these stay payload fields is a
judge-design decision, not a checker demand.

Why the never-read list is so large: every token-bound rule ("intro" adjacency,
"if paid in full", the word "fixed", APR labeling, NMLS/opt-out presence) runs
on the **text plane** — the payload fields shadowing those checks
(`has_intro_word`, `is_deferred_interest`, `post_promo_rate_stated`,
`applies_to_rate`, `fixed_period_stated`) duplicate what the text already
decides. Phrase-gated concept rules (badges, UDAAP lexicon, prequalified
qualifier, soft-pull, urgency, government, debt, fee claims) match on **claim
text** via their pattern sets, not on payload fields (`badge_word`, `strength`,
`fee_claim_kind`, `representation_kind` are therefore unread).

## Other inputs read (non-payload)

- **Claim**: `claim_types` (routing: rule subscription intersection), `text`
  (phrase/pattern matching), `id` (finding anchoring). `location` is NOT read
  (all proximity rules are text_plane today).
- **Disclosure**: `disclosure_type` (required-disclosure and element presence),
  `text` (qualifier cures). `prominence`/`location` NOT read (no claim_plane
  proximity rule exists).
- **Submission**: `product` (scope filter), `surface` (CC-SCHUMER applies_when),
  `offer_ids` (ground-truth cell selection), `states_targeted` (state cap +
  exclusion checks; metadata plane), `date_submitted` (review_date: staleness +
  urgency derivation), `mode` + `baseline_submission_id` (fidelity),
  `submission_id` (ids).
- **OfferCell**: `offer_id`, `product`, `apr_min`, `apr_max`, `apr_type`,
  `term_months`, `amount_min`, `amount_max`, `annual_fee`, `intro_apr_pct`,
  `intro_period_months`, `fee_deducted_from_proceeds`, `is_firm_offer`,
  `states_excluded`, `effective_start`, `effective_end`.
  NOT read: `badge_designation_allowed` (the PL/CC-BADGE notes suggest
  reconciling `submission.badge_text` against it, but rule `note`s are
  human-context-only per the README — reported to the coordinator, not
  implemented), `min_credit_score`, `origination_fee_pct`, `offer_name`, `notes`.

## Per-rule ledger (deterministic rules)

| Rule | normalized_fields read | Other decision inputs |
|---|---|---|
| PL-TRIG-001 | — | claim.claim_types/text vs trigger patterns; disclosure_type |
| PL-APR-001 | — | text plane (anchors/companions) |
| PL-APR-002 | — | claim text vs floor patterns; disclosure_type |
| PL-TRUTH-001 | value_pct, is_floor_claim, amount_value, term_months | cells apr/amount/term bounds, effective window; date_submitted |
| PL-STATE-CAP-001 | value_pct, range_max_pct | states_targeted; caps table; cells.apr_max (fallback) |
| PL-STATE-EXCL-001 | — | states_targeted vs cells.states_excluded (metadata plane; fires with zero claims) |
| PL-FEE-001 | — | claim text vs no-fee phrases; cells.fee_deducted_from_proceeds; cure patterns over disclosures/claims/text |
| PL-BADGE-001 | — | claim text vs preapproved terms; cells.is_firm_offer |
| CC-TRIG-001 | — | claim text vs open-end trigger patterns; disclosure_type |
| CC-INTRO-001 | — | text plane; DisclosureType.intro_adjacency assist |
| CC-INTRO-002 | — | text plane (first-listing proximity) |
| CC-DEFER-001 | — | text plane composite (proximity + retroactive element) |
| CC-FIXED-001 | — | text plane 'fixed'; cells.apr_type; fixed-period cure patterns |
| CC-SCHUMER-001 | — | submission.surface; disclosure_type schumer_box_link |
| CC-PRESCREEN-001 | — | cells.is_firm_offer (applies_when); disclosure_type + detection patterns |
| CC-BADGE-001 | — | claim text; cells.is_firm_offer |
| CC-TRUTH-001 | promo_rate_pct, promo_period_months, range_min_pct, range_max_pct, amount_value, fee_type | cells intro/apr/annual_fee fields, effective window; date_submitted |
| MTG-REGN-001 | — | claim text vs mortgage-approval lexicon |
| MTG-FIXED-001 | — | text plane 'fixed'; cells.apr_type |
| MTG-GOV-001 | — | claim text vs gov terms; government_program_verified UNRESOLVABLE → needs-verification |
| MTG-TI-001 | — | claim text vs payment patterns; disclosure_type taxes_insurance |
| MTG-NMLS-001 | — | text plane NMLS detection; disclosure_type assist |
| MTG-RATE-001 | — | text plane (rate anchors vs APR label) |
| MTG-DEBT-001 | — | claim text vs debt-elimination lexicon |
| MTG-TRUTH-001 | value_pct, labeled_as_apr, rate_kind | cells apr bounds, effective window; date_submitted |
| MTG-COUNSEL-001 | — | text plane 'counselor' |
| XP-UDAAP-001-(pl/cc/mtg) | — | claim text vs UDAAP lexicon (+text safety net) |
| XP-PREQ-002-(pl/cc/mtg) | — | claim text vs prequalified terms; disclosure_type not_guaranteed |
| XP-URG-004-(pl/cc/mtg) | — | claim text vs urgency lexicon; derived effective_end_supports_urgency (cells.effective_end, date_submitted) |
| XP-SOFT-007-(pl/cc/mtg) | — | claim text vs soft-pull lexicon; soft_pull_verified UNRESOLVABLE → needs-verification |

Fidelity (engine-level, no rule): reads `value_pct` + `is_floor_claim` on rate
claims and `disclosure_type` sets when `baseline_claims`/`baseline_disclosures`
are provided; findings-delta keys otherwise.
