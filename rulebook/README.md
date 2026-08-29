# Rulebook

Machine-actionable compliance rules for the ClearPath marketing-compliance engine.
Every entry conforms to `RulebookEntry` in `backend/contracts.py` and carries a real
`citation_url` (eCFR/CFPB/FTC or state source) plus a plain-language `explanation`.

## Structure

| File | Scope |
|---|---|
| `manifest.json` | `rulebook_version`, file list, declared counts (validated against actuals) |
| `personal_loan.json` | Reg Z closed-end (12 CFR 1026.24): trigger terms, APR labeling, "as low as", truthfulness vs. offer matrix, state APR caps, fee claims, badges |
| `credit_card.json` | Reg Z open-end (12 CFR 1026.16, 1026.60): negative-claim triggers, intro-rate adjacency/proximity, deferred interest, "fixed", Schumer box, FCRA prescreen format |
| `mortgage_prequal.json` | Reg N (12 CFR 1014.3) prohibitions, Reg Z mortgage provisions, NMLS display, taxes-&-insurance, staleness |
| `cross_product.json` | UDAAP lexicon, "prequalified" qualifiers, substantiation, urgency, endorsements, soft-pull claims — expanded per product (see below) |

**Cross-product expansion:** `RulebookEntry.product` is single-valued, so each
cross-product concept is expanded into three concrete entries with rule_ids of the
form `XP-<FAMILY>-<NNN>-<product>`. Treat entries sharing a family prefix as one
conceptual rule for display/dedup purposes.

## How the engine consumes this

**Deterministic checker (check_kind = `deterministic`)** executes `parameters` directly:

- `prohibited_phrases` / `flag_phrases` + `match` — lexicon scans over extracted claim/disclosure text (`case_insensitive_substring` or `case_insensitive_regex` per the `match` field).
- `conditional_phrases` — each `{phrase, condition_field, violates_when}` is evaluated against the referenced offer cell(s): the phrase violates only when the offer field equals `violates_when` (e.g. "no fees" violates when `fee_deducted_from_proceeds=true`; "pre-approved" violates when `is_firm_offer=false`).
- `trigger_patterns` + `required_disclosure_types` / `required_elements` — if any pattern matches an extracted claim, the listed `DisclosureType`s must be present among extracted disclosures (adjacency/prominence expectations are named in the parameters and checked against `Disclosure.location`/`prominence`).
- `matrix_checks` — named reconciliations against the referenced offer-matrix cells at their current version (rate/amount/term within range, intro terms equal, effective window contains today).
- `state_apr_caps` — advertised APR max vs. cap for every targeted state.
- `applies_to_surfaces` / `applies_when` — rule gating by submission surface or offer flags.

**LLM judge (check_kind = `llm_judged`)** receives `parameters.judge_focus` and the
`explanation` as prompt context for the submission's product, and emits `Finding`s with
`check_class="judgment"`; `detect_patterns`, where present, help the judge locate the
relevant claim but are not enforced deterministically.

Findings produced from a rule inherit its `severity` and `citation_url`. Overlapping
hits (e.g. `XP-UDAAP-001-mortgage_prequal` vs `MTG-REGN-001`) should be deduped by
(claim, phrase), keeping the highest-severity rule.

## Versioning

`rulebook_version` = `YYYY.MM.patch` — bump patch for edits to existing rules, month for
additions. Every `CheckRun` records the version it executed against, so re-validation
sweeps and the audit trail can attribute findings to the exact rule set in force.

## Validation

```
python rulebook/validate_rulebook.py
```

Validates every entry against the pydantic model, checks for duplicate rule_ids and
non-URL citations, and reconciles manifest counts against actuals. Non-zero exit on any
failure. Note: state APR cap values are simplified for demo purposes (loan-size and
licensee carve-outs not modeled); see `PL-STATE-CAP-001.parameters.values_note`.
