# Evidence Factory — AGENTS.md

Synthetic digital-evidence generator for showcasing AI digital-evidence analysis systems. Source story (URL / upload / paste) → canonical truth + proposition graph → validated event graph → load-bearing artifacts (gated by a smoking-gun critic, refuted-by-construction red herrings) → haystack noise → closure check → forensic-style `/corpus` + sealed `/SOLUTION` pack.

See `PRD.md` for the full design.

## Canonical project structure — extend, never re-scaffold

The product lives in **`api/`** (FastAPI app + `api/pipeline/` + per-profile
modules under `api/provenance/`) and **`ui-app/`** (the SPA). All slices extend
this tree. Do **not** create a parallel top-level package (e.g. `backend/`,
`src/`) or re-scaffold the project — if something seems missing, add it inside
`api/`/`ui-app/`. A provenance profile is a **per-profile module** under
`api/provenance/` (see `api/provenance/email_profile.py`), not a branch in a
monolithic catalog. The only expected top-level dirs are `api/`, `ui-app/`,
`tests/`, `docs/`, and `scripts/`.

## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues; skills use the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical vocabulary, no overrides: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
