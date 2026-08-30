# Data Contracts

Source of truth: `backend/contracts.py` (pydantic v2). Frontend mirror: `frontend/src/contracts.ts` (kept in sync by hand).

**FREEZE RULE:** these models are frozen after Stage 1. Any PR that changes a contract must call the change out explicitly in its description, and update both files together. All other PRs build against these shapes as-is.

## Models

### `Claim`
One marketing claim extracted from an evidence artifact (mock, screenshot, HTML), located (`location`: where in the artifact it appears). Produced by the extractor; consumed by the checker, judge, and UI annotations.

**`claim_types` is a legal-entity taxonomy** (amended 2026-08-29, user-directed — explicit exception to the freeze): each value names the body of law that governs the claim, so rules subscribe by legal category rather than by surface form.

**Amendment #4** (2026-08-29, user-directed, explicit): `Claim.claim_type` (singular) → `Claim.claim_types: ClaimType[]` (min length 1). One statement = one claim object; a span embodying multiple legal categories lists them all.

**Amendment #5** (2026-08-29, user-directed, explicit): `Claim` gains `normalized_fields: dict` — the union of the listed claim types' payload contracts — and the payload contracts become importable typed models registered in `CLAIM_TYPE_PAYLOADS`, validated with `validate_claim_payload()` (unknown keys rejected; each listed type's required fields present and type-valid).

**Amendment #5b — payload trim (2026-08-29):** executed per the consumption ledgers (checker reads **11** fields; judge reads **0**): `rate_or_apr` keeps 6 (`value_pct` required, `range_min_pct`, `range_max_pct`, `is_floor_claim`, `labeled_as_apr`, `rate_kind`), `triggering_term` keeps `term_months`, `promotional_or_introductory` keeps `promo_rate_pct` + `promo_period_months` (both optional — deferred-interest promos have no rate), `fee_or_cost` keeps `fee_type` + `amount_value`; the other five types have **empty** payloads — they classify and route, their rules decide on text/cells/metadata. Absent optional booleans are **false-equivalent** (a filter on `is_floor_claim: true` must not match an absent field). **Add-back governance: a field returns only with a named consumer** (a rule predicate or judge input that reads it), never speculatively.

**Amendment #5a — derivation (2026-08-29):** the payload models are **generated from `rulebook/claim_types_legal_map.json` at import time** (`_build_payload_models` in `backend/contracts.py`): each structured `normalized_fields` entry (`{type, values?, optional, description}`) becomes a pydantic field (`number`→float, `boolean`→bool, `string`→str, `values`→`Literal[...]`, `optional`→`Optional[...] = None`). **Edit the map, never the models** — there are no hand-written payload models to drift, and a construction test guards the generation.

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

**Extractor convention (multi-label, replaces the former two-objects convention):** emit **one Claim per distinct statement**, listing every legal category it embodies (e.g. "0% intro APR for 15 months" → one claim with `claim_types: [promotional_or_introductory, triggering_term]`). Its `normalized_fields` payload is the **union** of the listed types' payload contracts. `rulebook/claim_types_legal_map.json` is the definition document for this enum, kept 1:1 with it.

### `Disclosure`
One disclosure found in an evidence artifact, typed (`disclosure_type`: apr_qualifier, trigger_disclosure, soft_pull, not_guaranteed, opt_out_notice, schumer_box_link, nmls_id, taxes_insurance, state_license, intro_adjacency, other) with `location` and `prominence`. Claims *trigger* required disclosures; the checker verifies presence and placement — which is why extraction must capture disclosures, not just claims.

### `RulebookEntry`
One machine-actionable compliance rule: which `product` and `claim_types` it subscribes to, whether it is `deterministic` or `llm_judged`, its `severity`, machine-usable `parameters` (trigger-term lists, required-disclosure lists, phrase lexicons, caps), structured `authorities`, and a plain-language `explanation`. Rules are data, not code.

**`authorities` is structured legal metadata** (amended 2026-08-29, user-directed — second explicit freeze exception, replacing the former `citation_url: str`): a non-empty list of `LegalAuthority` objects, primary authority first. Each carries `body` (human name of the body of law), `citation` (formal pinpoint cite to the subsection the rule operationalizes, e.g. `12 CFR § 1026.16(g)(4)`), `regime` (`statute | regulation | official_interpretation | agency_guide | enforcement_doctrine | state_statute | state_regulation` — honest about what kind of law it is: the Endorsement Guides are an `agency_guide`, not a regulation; substantiation/odds rules rest on `enforcement_doctrine`), `regulator`, and `url`. Rules resting on two bodies of law (e.g. ARM-as-"fixed": Reg Z 1026.16(f) and Reg N 1014.3) list both. Findings surface the PRIMARY authority's url.

### `OfferCell`
One row of the offer matrix — the versioned ground truth of what ClearPath is allowed to claim through a partner (APR range, terms, amounts, fees, permitted badge designation, `is_firm_offer`, excluded states, effective window). Truthfulness checks compare claims against this.

### `Submission`
One review request: an evidence artifact plus its context bundle (partner, product, surface, states, template id/version, offer cells referenced, dynamic slots, disclosures the partner says are included, asset files, SLA). `mode` distinguishes `pre_publication` (partner mock awaiting approval) from `verification` (live-placement evidence, e.g. a seed-account screenshot). For `verification` submissions, `baseline_submission_id` names the APPROVED submission the evidence is diffed against — the fidelity check's join key. *(Amendment #3, PR #1: added `baseline_submission_id: str | None`.)*

### `Finding`
One issue raised against a submission. `check_class` says which engine raised it: `legality` (vs. rulebook), `truthfulness` (vs. offer matrix / served response), `fidelity` (vs. approved baseline), `judgment` (LLM gray-area). Carries severity, the rule and claim it references, a citation URL, an optional `suggested_redline`, and a reviewer `status` (open / accepted / overridden).

### `CheckRun`
One execution of the engine against one submission. Records **which `rulebook_version` and `offer_matrix_version` it ran against** — this is what enables the re-validation sweep (matrix changes → re-run stale check runs) and the audit trail (what was approved, against which rules, when).
