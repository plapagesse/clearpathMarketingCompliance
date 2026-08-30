# Fixtures — evidence set for the ClearPath compliance engine

Demo/eval inputs: Credit Karma-style placement mocks (self-contained HTML, inline CSS), the offer matrix they must be checked against, the submission manifest, and the ground-truth answer key.

- `offer_matrix.csv` — ClearPath's current offer cells (version `2026-08`): the truthfulness baseline for every check.
- `submissions.csv` — one manifest row per mock (context bundle: product, template, offer cells, states, mode, SLA). Columns match `backend/contracts.py::Submission`, including `baseline_submission_id`: the machine-readable fidelity join key, populated only on verification-mode rows and pointing at the pre_publication submission the capture drifted from (empty everywhere else); `change_summary` keeps the human-readable context.
- `expected_findings.json` — planted violations per mock, keyed by asset file. **Eval key for the extractor/checker/judge — do not feed to the engine as input.** Per finding: `claim_text` is strictly literal-or-null — non-null values appear verbatim in the entity-decoded, tag-stripped, comment-stripped, whitespace-collapsed text of the mock; absence/layout findings use `null` and carry the context in `location_note` (present on every finding); `expected_claim_type` is the legal-entity ClaimType the extractor should assign to the claim span (per `rulebook/claim_types_legal_map.json`), or `null` for absence-type findings; `expected_rule_ids` lists the canonical rulebook rule_ids expected to fire (**soft data coupling to rulebook v2026.08.3** — regenerate this key if rule IDs ever change).
- `validate_fixtures.py` — enforces the invariants above (coverage, manifest/mode agreement, literal claim_text, location_note presence, compliant-fixture emptiness). Run `python3 fixtures/validate_fixtures.py`; exit 0 = pass.
- `*.png` — **the canonical evidence artifacts: the platform ingests screenshots (images), and these committed renders are the deterministic eval inputs.** Each is rendered from its same-basename HTML at 600x1200 via headless Chrome.
- `*.html` — the SOURCE files that generate the PNGs (edit these, then re-render). HTML comments (the planted-violation annotations) never render, so the PNGs are clean-room eval inputs by construction — no comment-stripping needed.
- `render_screenshots.py` — regenerates all PNGs: `python3 fixtures/render_screenshots.py` (requires Google Chrome at the standard macOS path). Pass explicit paths to render ad-hoc fixtures instead of the default glob: `python3 fixtures/render_screenshots.py canary.html ...` — the PNG lands next to each source.

## Inventory

| File | Product | Mode | Compliant? | Planted violations |
|---|---|---|---|---|
| `mock_pl_card_compliant.html` | personal_loan | pre_publication | ✅ | — |
| `mock_pl_card_preapproved_guaranteed.html` | personal_loan | pre_publication | ❌ | "Pre-approved" badge on non-firm offer; "guaranteed approval regardless of credit history"; missing not-a-guarantee qualifier |
| `mock_pl_card_trigger_stale.html` | personal_loan | pre_publication | ❌ | Payment trigger term ($299/mo) w/o Reg Z companion disclosures; APR floor 7.49% not in offer matrix; thin "as low as" qualifier |
| `mock_pl_card_il_leak.html` | personal_loan | pre_publication | ❌ | 60-mo cell advertised as available in IL despite matrix exclusion (PLPA 36% cap) |
| `mock_cc_card_compliant.html` | credit_card | pre_publication | ✅ | — |
| `mock_cc_card_intro_violations.html` | credit_card | pre_publication | ❌ | "0% APR" without "intro" adjacency; post-promo APR buried in 8px footer; "No annual fee / no interest" net-impression fee claim |
| `mock_cc_prescreen_email_no_optout.html` | credit_card | pre_publication | ❌ | Legit firm offer but FCRA prescreen opt-out notice (short+long) entirely missing |
| `mock_mtg_table_compliant.html` | mortgage_prequal | pre_publication | ✅ | — |
| `mock_mtg_arm_as_fixed.html` | mortgage_prequal | pre_publication | ❌ | 5/6 ARM sold as "fixed"; NMLS ID missing; payment shown w/o taxes-&-insurance qualifier |
| `seed_screenshot_pl_card_drift.html` | personal_loan | **verification** | ❌ | Drift vs approved `mock_pl_card_compliant.html`: APR floor 8.99→7.99 (also matrix-invalid); entire qualifier fine-print paragraph dropped |

Notes: every planted violation is literally present (or verifiably absent) in the HTML text and its rendered PNG — `expected_findings.json` has no phantom entries. `submissions.csv` `asset_files` lists `<base>.png;<base>.html` (PNG first = what the platform ingests); `expected_findings.json` stays keyed by the HTML basename — the PNG↔HTML↔key mapping is by basename convention.
