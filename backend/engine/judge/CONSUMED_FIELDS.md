# Judge demand ledger — what the prompt serialization actually injects

This file is the judge's half of the payload-trim evidence: exactly which
fields of each object reach the model. If a field is not listed here, the
judge never reads it.

## Claim

| Field | Injected | Note |
|---|---|---|
| `id` | yes | anchor for claim linking in verdicts |
| `claim_types` | yes | category context per claim line |
| `text` | yes | the verbatim span — what judgment actually reasons over |
| `location` | yes | prominence/placement context |
| `normalized_fields` | **NO — none, for any claim type** | judgment reads the verbatim text; e.g. the odds number in "90%+ approval odds" is judged from the text itself |
| `source_evidence_id` | no | |

## Disclosure

| Field | Injected |
|---|---|
| `disclosure_type` | yes |
| `text` | yes |
| `location` | yes |
| `prominence` | yes |

## Submission

`submission_id`, `product` (also scope-filters the rules), `surface`,
`partner`, `mode`, `states_targeted`. Nothing else.

## RulebookEntry (llm_judged rules)

`rule_id`, `severity`, `parameters.judge_focus`,
`parameters.violation_examples`, `parameters.compliant_contrast`,
`parameters.citation_quote`, `authorities[0]` (body, citation → prompt;
url → Finding.citation_url), `explanation` (fallback when judge_focus absent),
`check_kind` + `product` (filtering only).

## Trim implication

The judge consumes **zero** `normalized_fields` keys. Any payload field kept
after the trim must therefore be justified by the deterministic checker's
ledger (`backend/engine/checker/CONSUMED_FIELDS.md`) or by eval grading — the
judge adds no demand.
