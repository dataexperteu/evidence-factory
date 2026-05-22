# Evidence Factory

A synthetic digital-evidence corpus generator for teams showcasing AI forensic-analysis systems. Point it at a source story — a court case, a public-domain novel, a news article — and it produces a downloadable forensic disclosure corpus that looks and behaves like a real evidence package, together with a sealed answer key.

## What it does

Evidence Factory takes a source story (submitted by URL, file upload, or paste) and emits:

- **`/corpus`** — hundreds to 1000+ artifacts organised into per-custodian / per-device folders with original-style filenames and a chain-of-custody manifest CSV (acquisition time, owner, device, SHA-256). Every artifact is a real, valid file: RFC822 `.eml` emails, SMS/chat CSV and SQLite exports, PDFs with document metadata, `.xlsx` ledgers, JPEG photos with EXIF (including GPS where appropriate), and log/CSV files. The corpus contains load-bearing signal artifacts, refuted red herrings with breaker bundles, and a large haystack of noise — all written through the same six format profiles so an AI cannot distinguish signal from noise by file type alone.
- **`/SOLUTION`** — a sealed pack (truth outline, proposition graph, signal ledger, red-herring → breaker map, per-artifact provenance truth) partitioned from `/corpus` for the operator's eyes only.

Both are delivered as a single zip download.

The story's era and setting are preserved — a Victorian telegram becomes an SMS, an alibi letter becomes an email, but Baker Street stays Baker Street. Real names and places are kept per operator attestation; a synthetic-evidence disclaimer is stamped into every artifact's metadata and the manifest.

## How it works

The pipeline runs as a long-lived async job and streams progress to the browser over SSE:

1. **Source intake** — URL fetch + readability extraction, or file/paste normalisation
2. **Operator attestation gate** — acknowledges authorised use before generation begins
3. **Truth extraction** — Claude Opus/Sonnet derives a canonical truth and proposition graph from the source
4. **Event graph** — expands truth into a granular, timeline-validated event graph
5. **Artifact emission** — load-bearing artifacts are generated per event and recorded in the signal ledger
6. **Smoking-gun critique** — every load-bearing artifact is judged; anything strong enough to solve the case alone is split, diluted, redacted-and-relocated, or demoted to a red herring (strategy chosen per instance)
7. **Red-herring + breaker design** — plausible false propositions with tempting supporting artifacts and conclusive breaker bundles
8. **Noise generation** — Claude Haiku generates bulk haystack artifacts in batches, persona- and timeline-consistent, through the same six format profiles; a leak/contradict guard prevents noise from accidentally corroborating the truth
9. **Closure check** — verifies every truth proposition is corroborated above threshold and the canonical account is the uniquely best-supported explanation
10. **Packaging** — assembles and zips `/corpus` + sealed `/SOLUTION`

**Models:** Claude Opus 4.7 / Sonnet 4.6 for reasoning roles; Claude Haiku 4.5 for bulk noise. Prompt caching is used throughout to keep per-case API costs bounded.

## Running locally

**Prerequisites:** Python 3.11+, Node 20+, an Anthropic API key.

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install and build the SPA
cd ui-app && npm ci && npm run build && cd ..

# Start the API server
uvicorn api.main:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080`.

### Docker

```bash
docker build -t evidence-factory .
docker run -e ANTHROPIC_API_KEY=sk-... -p 8080:8080 evidence-factory
```

The two-stage Dockerfile builds the React SPA in a Node 20 Alpine image, then copies the compiled bundle into a Python 3.11 slim runtime alongside the FastAPI app.

## Development

```bash
# Backend tests
pytest tests/

# Frontend dev server (hot reload)
cd ui-app && npm run dev

# Frontend tests (Playwright)
cd ui-app && npx playwright test
```

The AFK autonomous implementation harness lives in `scripts/sandcastle/`. See `PRD.md` §Implementation Decisions for the sandcastle setup checklist before running it.

## Repository layout

```
api/                  FastAPI app + pipeline modules + per-profile provenance writers
  pipeline/           Stage orchestration
  provenance/         One module per format profile (email, SMS, PDF, XLSX, JPEG, log/CSV)
ui-app/               React 18 + TypeScript SPA (Vite)
tests/                Pytest suite (pure-logic modules + one end-to-end smoke test)
docs/                 ADRs and agent policy docs
scripts/sandcastle/   AFK autonomous implementation harness
Dockerfile            Two-stage build (Node SPA → Python runtime)
PRD.md                Full product requirements and design decisions
AGENTS.md             Agent policies and context discovery rules
```

## Scope

v1 covers the six core provenance profiles (email, SMS/chat, PDF, XLSX, JPEG+EXIF, log/CSV), local or single-tenant internal deployment, and no built-in scoring of the AI under test — scoring is the consumer's concern. See `PRD.md` § Out of Scope for the full exclusion list.
