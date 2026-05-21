# Evidence Factory sandcastle harness — runbook

Operator runbook for the AFK sandcastle harness (`scripts/sandcastle/`,
authored by [#2](https://github.com/dataexperteu/evidence-factory/issues/2)).
The harness uses [`@ai-hero/sandcastle`](https://github.com/mattpocock/sandcastle)
to drive autonomous Claude Code agents through the slice DAG defined in
`orchestrate.ts`. Runs are launched on the shared avian VM, not the laptop.

This file is **EF-specific**. The general operational command shapes
(start / peek / stop, OAuth refresh, failure modes, adding a project) live in
the canonical runbook in the agentic-ai repo:

> `C:\Users\Jacob\Dev projects\agentic-ai\context\development\operator\sandcastle-on-vm-runbook.md`

Read its §0 (locked decisions) and §3 (SSH command shapes) once; the rest of
this file just plugs Evidence Factory values into that framework.

## Evidence Factory parameters

| Parameter | Value |
|--|--|
| Repo | `dataexperteu/evidence-factory` |
| VM checkout | `/home/agentic/projects/evidence-factory/` |
| Tmux session | `sandcastle-evidence-factory` |
| Sandbox image tag | `evidence-factory-sandbox` |
| Per-project secrets | **none** — no `security/secrets.env`, no lab forensic creds |
| Auth keyset | `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `GH_TOKEN` |

## One-time operator onboarding (VM-side)

The user-wide creds files (`~/.sandcastle-creds-max`,
`~/.sandcastle-creds-api`) are **already populated** on the VM from
agentic-ai onboarding — they are shared across every sandcastle project. Do
**not** re-create them. The Claude OAuth token may need a refresh; that's
covered in the canonical runbook §4.

The Evidence-Factory-specific steps (run from the laptop with VPN active and
`$GH_TOKEN` exported via `gh auth token`):

```bash
# 1. Clone EF into the per-project layout
ssh -i security/agentic_autoinstall_key agentic@172.16.85.95 \
  "git clone https://x-access-token:${GH_TOKEN}@github.com/dataexperteu/evidence-factory.git ~/projects/evidence-factory"

# 2. Install harness deps + build the sandbox image (~5–10 min first time)
ssh -i security/agentic_autoinstall_key agentic@172.16.85.95 \
  "cd ~/projects/evidence-factory/scripts/sandcastle && npm i && \
   cd ~/projects/evidence-factory && \
   docker build -t evidence-factory-sandbox -f scripts/sandcastle/sandbox.Dockerfile ."

# 3. Seed the passthrough-only .sandcastle/.env (all keys blank)
ssh -i security/agentic_autoinstall_key agentic@172.16.85.95 \
  "cp ~/projects/evidence-factory/.sandcastle/.env.example ~/projects/evidence-factory/.sandcastle/.env && \
   chmod 600 ~/projects/evidence-factory/.sandcastle/.env"

# 4. Smoke-test the preflight (no sandbox spin, no cost). Expect: empty output, exit 0.
ssh -i security/agentic_autoinstall_key agentic@172.16.85.95 bash <<'SH'
cd ~/projects/evidence-factory
. ~/.sandcastle-creds-max
cd scripts/sandcastle
./node_modules/.bin/tsx orchestrate.ts
echo "exit: $?"
SH
```

A non-zero exit or a `Preflight: …` error message indicates a misconfigured
credential or missing `.sandcastle/.env` — see the canonical runbook §7.

There is no `security/secrets.env` to SCP across (Evidence Factory has no lab
forensic credentials). Skip §2.3 of the canonical runbook entirely.

## Ongoing operations

Use the canonical runbook §3 verbatim, substituting `<project>=evidence-factory`.
Quick reference:

| Action | Command shape (substitute `<project>=evidence-factory`) |
|--|--|
| Start a run (Max OAuth) | Canonical runbook §3 "Start a run (default: Max OAuth)" — **omit** the `set -a / secrets.env / set +a` block (EF has no `security/secrets.env`) |
| Peek at live run | `tmux capture-pane -p -S -200 -t sandcastle-evidence-factory` |
| Stop a run | Canonical runbook §3 "Stop a run" |
| List active runs | `tmux ls 2>/dev/null \| grep ^sandcastle-evidence-factory \|\| echo 'none'` |
| Find logs after a run | `ls -lt ~/projects/evidence-factory/logging/sandcastle-*.log` |
| Refresh main view | `cd ~/projects/evidence-factory && git fetch origin && git checkout main && git pull --ff-only` |

The simplified EF start-run command (no secrets.env loop):

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
ssh -i <KEY> agentic@172.16.85.95 bash <<SH
cd ~/projects/evidence-factory
mkdir -p logging
. ~/.sandcastle-creds-max
LOG=\$HOME/projects/evidence-factory/logging/sandcastle-${TS}.log
cd scripts/sandcastle
tmux new -d -s sandcastle-evidence-factory "npm run go 2>&1 | tee -a \$LOG"
SH
```

The harness is resumable: once a slice's GitHub issue is closed (its PR
merged with `Closes #N`), restarting `npm run go` skips that slice and lets
its dependents proceed. A spine-slice failure halts the whole run; an apron
failure only excludes its own slice.

## Locked decisions inherited from the canonical runbook

Read these in `sandcastle-on-vm-runbook.md` §0 — do not relitigate here:

1. Target VM: `agenticdocker` (`172.16.85.95`), `agentic` user.
2. Persistence: `tmux`, one session per project (`sandcastle-<project>`).
3. Trigger: raw SSH from Claude's Bash tool. No MCP wrappers.
4. Layout: `/home/agentic/projects/<project>/`. One concurrent flow per project.
5. Two user-wide creds files (Max OAuth default, API key fallback). On Max
   OAuth preflight failure: **stop and ask Jacob** — never silent-fall-back.
6. Per-project secrets in `security/secrets.env`. Evidence Factory has none.
7. VPN only in Phase 1. Tailscale (Phase 2) deferred.
8. Laptop sandcastle kept as fallback.

## Related

- [`AGENTS.md`](../../AGENTS.md) — agent policies for this repo.
- [`PRD.md`](../../PRD.md) — Evidence Factory PRD (also published as
  [issue #1](https://github.com/dataexperteu/evidence-factory/issues/1)).
- [`scripts/sandcastle/orchestrate.ts`](../../scripts/sandcastle/orchestrate.ts) —
  the slice DAG and per-slice prompts. To change agent behaviour AFK, edit
  this file.
- Canonical runbook: `agentic-ai/context/development/operator/sandcastle-on-vm-runbook.md`.
- Canonical gotcha (Windows-only worktree mount): `agentic-ai/context/development/gotchas/sandcastle_cwd_must_be_repo_root.md`.
