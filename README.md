# ClearPath Marketing Compliance

AI-assisted marketing-compliance review platform for a consumer lender (ClearPath Financial: personal loans, credit cards, mortgage prequalification). Partner marketing placements — offer cards, prescreen emails, rate tables — are ingested, their claims and disclosures extracted by a multimodal LLM, checked deterministically against a **cited regulatory rulebook** (legality), a **versioned offer matrix** (truthfulness), and the **approved baseline** (fidelity), then passed through an LLM judgment layer for gray areas. Findings land in a human review queue with accept/override decisions and an immutable, exam-exportable audit trail — replacing the Excel-and-email process that bottlenecks the compliance team.

## Architecture

```
evidence artifact (image/HTML)
  + context bundle (partner, product, surface, states)   → selects rulebook
  + ground truth (offer-matrix version | served API resp) → truthfulness baseline
  + approved baseline (verification mode only)            → fidelity baseline
        │
        ▼
  extract claims & disclosures (LLM, multimodal; typed + located)
        │
        ▼
  deterministic checks: legality · truthfulness · fidelity
        │
        ▼
  LLM judgment layer: net impression, badge defensibility → suggested redlines
        │
        ▼
  severity-tiered findings → human review queue → decision → immutable record
```

## Local setup

```bash
# backend
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cp .env.example .env          # add your ANTHROPIC_API_KEY
PORT=5001 .venv/bin/python -m backend.app   # 5000 is taken by macOS AirPlay

# frontend (dev, separate terminal — proxies /api to :5001)
cd frontend && npm install && npm run dev

# or serve the built frontend from Flask directly
cd frontend && npm run build && cd .. && PORT=5001 .venv/bin/python -m backend.app
```

Smoke test: `./smoke.sh`

## Assumptions

_TODO_

## Demo script

_TODO_

## Deployment

The `Procfile` runs gunicorn with `--threads 8`: the batch AI review fires one
`/process` request per selected submission simultaneously, and a single sync
worker would serialize them. Requests beyond the thread count queue rather than
fail, so raise `--threads` if reviewers routinely batch more than a handful.

`CLEARPATH_DEMO` gates the "Reset demo data" button's endpoint
(`POST /api/review/reset`), which wipes every check run, decision and upload
without asking for credentials. It is **off** unless the variable is set or the
process is in Flask debug — so the dev server has it and a gunicorn deployment
does not. Set it only on a throwaway demo instance, never on one holding work
anyone would miss.

_TODO_
