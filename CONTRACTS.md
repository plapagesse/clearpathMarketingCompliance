# Data Contracts

Source of truth: `backend/contracts.py` (pydantic v2). Frontend mirror: `frontend/src/contracts.ts` (kept in sync by hand).

**FREEZE RULE:** these models are frozen after Stage 1. Any PR that changes a contract must call the change out explicitly in its description, and update both files together. All other PRs build against these shapes as-is.

## Models

### `Claim`
One marketing claim extracted from an evidence artifact (mock, screenshot, HTML), located (`location`: where in the artifact it appears). Produced by the extractor; consumed by the checker, judge, and UI annotations.

**`claim_type` is a legal-entity taxonomy** (amended 2026-08-29, user-directed — explicit exception to the freeze): each value names the body of law that governs the claim, so rules subscribe by legal category rather than by surface form.

| Value | Legal anchor |
|---|---|
| `triggering_term` | Reg Z triggering terms — 12 CFR 1026.24(d) (closed-end) / 1026.16(b) (open-end): payment amounts, repayment periods, downpayments, finance charges |
| `rate_or_apr` | Reg Z rate presentation — 12 CFR 1026.24(b)-(c): rates stated and labeled as APR |
| `promotional_or_introductory` | Reg Z promotional regime — 12 CFR 1026.16(g)-(h): intro rates, deferred interest |
| `fixed_rate_representation` | "Fixed" claims — 12 CFR 1026.16(f); Reg N 12 CFR 1014.3 (fixed vs. adjustable) |
| `approval_or_prequalification` | FCRA 603(l) firm offer of credit; Reg N 1014.3(q) approval-likelihood; pre-approved/prequalified/odds claims |
| `fee_or_cost` | TILA finance-charge concept — 12 CFR 1026.4; Reg N 1014.3(c): fee and cost claims |
| `endorsement_or_testimonial` | FTC Endorsement Guides — 16 CFR 255.0 |
| `government_affiliation` | Reg N 1014.3(n): government affiliation/endorsement implications |
| `general_udaap_representation` | Residual — FTC Act §5; CFPA §1031: urgency devices, comparative/superlative claims, debt-free/savings claims, and any other representation |

**Extractor convention:** a text span that embodies two legal categories yields **two Claim objects** (e.g. "0% intro APR" → one `promotional_or_introductory` + one `rate_or_apr`). No multi-label claims. `rulebook/claim_types_legal_map.json` is the definition document for this enum, kept 1:1 with it.

### `Disclosure`
One disclosure found in an evidence artifact, typed (`disclosure_type`: apr_qualifier, trigger_disclosure, soft_pull, not_guaranteed, opt_out_notice, schumer_box_link, nmls_id, taxes_insurance, state_license, intro_adjacency, other) with `location` and `prominence`. Claims *trigger* required disclosures; the checker verifies presence and placement — which is why extraction must capture disclosures, not just claims.

### `RulebookEntry`
One machine-actionable compliance rule: which `product` and `claim_types` it subscribes to, whether it is `deterministic` or `llm_judged`, its `severity`, machine-usable `parameters` (trigger-term lists, required-disclosure lists, phrase lexicons, caps), a `citation_url` to the actual law/policy, and a plain-language `explanation`. Rules are data, not code.

### `OfferCell`
One row of the offer matrix — the versioned ground truth of what ClearPath is allowed to claim through a partner (APR range, terms, amounts, fees, permitted badge designation, `is_firm_offer`, excluded states, effective window). Truthfulness checks compare claims against this.

### `Submission`
One review request: an evidence artifact plus its context bundle (partner, product, surface, states, template id/version, offer cells referenced, dynamic slots, disclosures the partner says are included, asset files, SLA). `mode` distinguishes `pre_publication` (partner mock awaiting approval) from `verification` (live-placement evidence, e.g. a seed-account screenshot).

### `Finding`
One issue raised against a submission. `check_class` says which engine raised it: `legality` (vs. rulebook), `truthfulness` (vs. offer matrix / served response), `fidelity` (vs. approved baseline), `judgment` (LLM gray-area). Carries severity, the rule and claim it references, a citation URL, an optional `suggested_redline`, and a reviewer `status` (open / accepted / overridden).

### `CheckRun`
One execution of the engine against one submission. Records **which `rulebook_version` and `offer_matrix_version` it ran against** — this is what enables the re-validation sweep (matrix changes → re-run stale check runs) and the audit trail (what was approved, against which rules, when).
