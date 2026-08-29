# Data Contracts

Source of truth: `backend/contracts.py` (pydantic v2). Frontend mirror: `frontend/src/contracts.ts` (kept in sync by hand).

**FREEZE RULE:** these models are frozen after Stage 1. Any PR that changes a contract must call the change out explicitly in its description, and update both files together. All other PRs build against these shapes as-is.

## Models

### `Claim`
One marketing claim extracted from an evidence artifact (mock, screenshot, HTML). Typed (`claim_type`: rate, payment, amount, approval, fee, urgency, comparison, testimonial, other) and located (`location`: where in the artifact it appears). Produced by the extractor; consumed by the checker, judge, and UI annotations.

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
