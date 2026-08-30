# Rulebook

Machine-actionable compliance rules for the ClearPath marketing-compliance engine.
Every entry conforms to `RulebookEntry` in `backend/contracts.py` and carries structured
`authorities` (a non-empty list of `LegalAuthority` objects — body, pinpoint citation,
regime, regulator, url; primary first) plus a plain-language `explanation`. The `regime`
field is honest about what kind of law each authority is: the Endorsement Guides are an
`agency_guide`, substantiation/odds rules rest on `enforcement_doctrine`, state rate caps
are `state_statute`.

## What is a rule

A rule is a **(scope, single decidable predicate, authority, consequence)** quadruple:
*scope* (product + claim_types + any surface/offer gating), one *predicate* that decides
pass/fail, the *authorities* it operationalizes, and a *consequence* (one finding kind at
one severity with one explanation). The boundary is the **one-finding test**: every
violation of the rule produces one kind of finding, one explanation, one severity. If
articulating a violation needs two different explanations, that is two rules. If two
rules always fire together and share one explanation, that is one rule. This is
normative for all future rule additions.

Rule `claim_types` use the **legal-entity taxonomy** (ClaimType enum, amended
2026-08-29): triggering_term, rate_or_apr, promotional_or_introductory,
fixed_rate_representation, approval_or_prequalification, fee_or_cost,
endorsement_or_testimonial, government_affiliation, general_udaap_representation.
`claim_types_legal_map.json` is the definition document for the enum, kept 1:1 with it.

## Structure

| File | Scope |
|---|---|
| `manifest.json` | `rulebook_version`, file list, declared counts (validated against actuals) |
| `personal_loan.json` | Reg Z closed-end (12 CFR 1026.24): trigger terms, APR labeling, "as low as", truthfulness vs. offer matrix, state APR caps, fee claims, badges |
| `credit_card.json` | Reg Z open-end (12 CFR 1026.16, 1026.60): negative-claim triggers, intro-rate adjacency/proximity, deferred interest, "fixed", Schumer box, FCRA prescreen format |
| `mortgage_prequal.json` | Reg N (12 CFR 1014.3) prohibitions, Reg Z mortgage provisions, NMLS display, taxes-&-insurance, staleness |
| `cross_product.json` | UDAAP lexicon, "prequalified" qualifiers, substantiation, urgency, endorsements, soft-pull claims — expanded per product (see below) |
| `claim_types_legal_map.json` | ClaimType classification spec: definitions, positive/near-miss examples, structured authorities, and `normalized_fields` — the extractor↔checker payload CONTRACT per type (injected into the Stage-3 extractor prompt) |
| [`PROVENANCE.md`](PROVENANCE.md) | Generated provenance map: every rule traced to its top-level body of law (regenerate: `python rulebook/generate_provenance.py`; staleness guard: `--check`) |
| `data/lexicons.json` | Shared phrase lists (referenced via `@lexicons.<key>`) |
| `data/patterns.json` | Shared regex sets (referenced via `@patterns.<key>`) |
| `data/state_apr_caps.json` | State APR cap table (referenced via `@state_apr_caps.<key>`) |

**Cross-product expansion:** `RulebookEntry.product` is single-valued, so each
cross-product concept is expanded into three concrete entries with rule_ids of the
form `XP-<FAMILY>-<NNN>-<product>`. Treat entries sharing a family prefix as one
conceptual rule for display/dedup purposes.

Each product file ends with a `citation_index`: the unique citation URLs used in that
file (validated against actual usage).

## What "deterministic" means here (normative for the Stage-4 checker)

"Deterministic" is a claim about the **decision**, not about detection completeness.
Every deterministic rule is a **pure function**: given its detected inputs (matched
text, extracted Claim/Disclosure objects, offer-cell fields, submission metadata), the
verdict is fully determined — no judgment anywhere in the decision. Detection
completeness for concept-bound rules is a separate, *measured* property (the fixtures
eval), never an assumed one.

Each deterministic rule therefore declares two audit fields:

- **`binding`** — what the law binds on:
  - `token`: the law mandates/prohibits **literal words or layout** — APR labeling
    (1026.24(c)), "intro" adjacency (1026.16(g)), "if paid in full" (1026.16(h)), the
    prescreen notice heading, NMLS presence, the literal word "fixed", "counselor".
    For these, a normalized string/layout check **is** the legal test.
  - `concept`: the law binds on **meaning however phrased** — trigger terms as
    concepts, guaranteed-approval paraphrases, fee misrepresentation, urgency,
    soft-pull claims. A phrase list can never be the legal test for these.
- **`decision_inputs`** — where the decision runs:
  - `text_plane` (token-bound only): the check runs on normalized raw text; its
    pattern fields are the test itself.
  - `claim_plane` (concept-bound): the decision runs on typed Claim/Disclosure
    objects from the extractor (plus offer-cell/submission fields). Any raw-text
    patterns on such rules are named **`safety_net_patterns`** — secondary detectors
    that catch what extraction missed; they are NOT the test.

`concept` + `text_plane` is an invalid combination (validation fails): a concept rule
whose only decision input is raw text would be smuggling a completeness claim.

### Normalization spec (all text_plane checks)

One pipeline, applied identically everywhere, in order:

1. Unicode **NFKC** normalization;
2. HTML-entity decode (`&amp;` → `&`, numeric entities included);
3. curly/straight **quote and apostrophe normalization** (`’` → `'`, `“”` → `"`);
4. **lowercase** (checks are case-insensitive; patterns written in caps are style, not case-sensitivity);
5. **whitespace collapse** (all runs of whitespace, incl. NBSP, → one space);
6. matching is **word-boundary aware**; where a pattern intends plural tolerance it
   says so explicitly with `(s)?` — pluralization is never implied.

Principle: *deterministic = specified reproducible normalization + pure decision
function*. It is **not** byte-equality, and it is **not** a completeness claim for
concept detection.

## Deterministic primitives — the Stage-4 checker implementation spec

Every deterministic rule's `parameters` carries exactly:

1. **`check_type`** — one of the CLOSED vocabulary below (undeclared/malformed fails validation);
2. **`check_description`** — one plain-English sentence saying what the check verifies (required, for non-engineers);
3. **`binding`** and **`decision_inputs`** — the audit fields defined above;
4. **minimal readable parameters** — small inline values, or **named data references**: any list/dict-valued field may be the string `"@<file>.<key>"`, resolved against `data/lexicons.json`, `data/patterns.json`, or `data/state_apr_caps.json`. A dangling reference fails validation. Bulky data (phrase lists, regex sets, the cap table) lives ONLY in `data/`. On claim_plane rules, phrase/trigger pattern fields are renamed `safety_net_patterns`.

`note` is the only other key permitted — human context, never executed.

| check_type | Schema (required → optional) | Executor semantics |
|---|---|---|
| `phrase_prohibited` | `phrases`, `match` | If any phrase matches extracted claim/disclosure text (per `match`: `case_insensitive_substring` or `case_insensitive_regex`), emit a finding. Flat ban. |
| `phrase_conditional` | `phrases`, `condition_field`, `violates_when` → `required_qualifier` | If a phrase matches (case-insensitive substring), resolve `condition_field` against the referenced offer cell or verification input; a violation exists when its value equals `violates_when`. `required_qualifier` names the cure whose proximate presence downgrades/clears the finding. |
| `trigger_requires_disclosures` | `trigger_patterns`, `required_disclosure_types` | If any pattern (case-insensitive regex; plain words act as substrings) matches an extracted claim, every listed `DisclosureType` must be present among extracted disclosures. Missing ones are findings. |
| `element_required` | `element` → `applies_when`, `detection_ref` | The named element/DisclosureType must be present in the artifact (`detection_ref` supplies detection regexes). `applies_when` gates the rule (variants below); absent = always applies. |
| `proximity_required` | `anchor_patterns`, `companion_patterns`, `requirement` | For every anchor match, at least one companion must satisfy the stated proximity/prominence `requirement` (evaluated against `Claim.location` / `Disclosure.location`+`prominence`). Anchor with no compliant companion = finding. |
| `ground_truth_consistency` | `claim_field`, `matrix_field`, `comparator` | Reconcile one extracted-claim field against the referenced offer-matrix field. `matrix_field` may be a `lo..hi` span. Comparators: `within_range`, `equals`, `exists_in`, `disjoint_from`, `not_conflated`. |
| `numeric_cap_by_state` | `caps_table`, `compare` | For every targeted state present in the caps table, the advertised APR max must not exceed `apr_cap` (entries may carry `all_in`/`scope` qualifiers). |
| `composite_all` | `checks` | **Structural addition — the one primitive added beyond the mandated seven (documented here prominently).** Every item in `checks` is itself a primitive block (no nested `composite_all`); ALL must pass. Used where one rule atomically bundles reconciliations (truthfulness suites) or paired requirements (deferred interest: proximity + retroactive disclosure). `check_description` is required at the rule level, optional on sub-checks. |

Additional conventions:

- **Pattern-key placement by plane:** `phrase_prohibited`/`phrase_conditional` carry `phrases` and `trigger_requires_disclosures` carries `trigger_patterns` ONLY on text_plane rules; on claim_plane rules the same data appears as `safety_net_patterns` (secondary detectors). Token-bound rules must carry at least one resolvable pattern-bearing key; text_plane `element_required` rules must carry `detection_ref`.
- `applies_when` variants: `{"offer_field": F, "equals": V}` (gate on the referenced offer cell), `{"surface_in": [...]}` (gate on `Submission.surface`), `{"any_anchor_matched": true}` (inside `composite_all`: gate on a sibling proximity check's anchors having matched).
- **Verification-input condition fields:** some `condition_field` values are not `OfferCell` columns (`soft_pull_verified`, `government_program_verified`, `effective_end_supports_urgency`). The checker resolves them from integration config / derives them from the matrix as described in each rule's `note`; an unresolvable condition emits a needs-verification finding rather than passing silently.
- Findings inherit the rule's `severity` and surface its PRIMARY authority's url. Overlapping hits (e.g. `XP-UDAAP-001-mortgage_prequal` vs `MTG-REGN-001`) dedupe by (claim, phrase), keeping the highest-severity rule.

**LLM judge (check_kind = `llm_judged`)** rules carry: `judge_focus` (the question),
`violation_examples` (2-3 creative sentences that fail), `compliant_contrast` (one that
passes), and `citation_quote` — a verbatim quote fetched from the cited authority page
(`null` where the page was unfetchable at authoring time, never invented — see the
rule's `note`). The judge receives all four plus `explanation` as prompt context and
emits `Finding`s with `check_class="judgment"`; `detect_patterns`, where present, help
locate the relevant claim but are not enforced deterministically.

## Severity rubric

Severity is **editorial** — the law assigns no severities itself. Assignments are
anchored to this rubric:

- **critical** — flat prohibition or truth defect with direct enforcement precedent:
  false "pre-approved" (FTC v. Credit Karma, $3M, 2023), stale/unavailable advertised
  rates on partner surfaces (CFPB v. Amerisave, $19.3M, 2014), "fixed" on variable
  mortgage rates and government-affiliation implications (CFPB's 2020 VA-advertising
  sweep, 8-9 consent orders), deferred-interest and prescreen-notice defects, state
  rate-cap violations (loans void in IL).
- **high** — an explicitly mandated element is missing or misplaced: trigger-term
  companion disclosures, "intro" adjacency, Schumer box, NMLS ID, taxes-&-insurance,
  prequalified-without-qualifier.
- **medium** — qualifier/prominence/verification defects: urgency devices, soft-pull
  claims pending verification, net-impression concerns, testimonial disclosure quality.
- **low / info** — advisory; style and best-practice flags (none currently emitted by
  this rulebook version).

## Versioning

`rulebook_version` = `YYYY.MM.patch` — bump patch for edits to existing rules, month for
additions. Every `CheckRun` records the version it executed against, so re-validation
sweeps and the audit trail can attribute findings to the exact rule set in force.

## Validation

```
python rulebook/validate_rulebook.py
```

Validates every entry against the pydantic model; enforces the closed `check_type`
vocabulary and per-primitive schemas (with `@` data-ref resolution — dangling refs
fail); requires `check_description` on every deterministic rule; enforces the
llm_judged enrichment contract; checks duplicate rule_ids, non-URL citations, per-file
`citation_index` accuracy, manifest-vs-actual counts, and 1:1 alignment of
`claim_types_legal_map.json` with the ClaimType enum. Non-zero exit on any failure.
Note: state APR cap values are simplified for demo purposes (loan-size and licensee
carve-outs not modeled); see `data/state_apr_caps.json`.
