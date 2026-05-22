/**
 * Sandcastle orchestration for the Evidence Factory AFK harness (PRD #1 / GitHub #2).
 *
 * Official tool: @ai-hero/sandcastle (github.com/mattpocock/sandcastle).
 * Patterns adapted from the proven dataexperteu/agentic-ai harness.
 *
 * STATUS: AUTHORED, NOT RUN. Running launches autonomous sandboxed Claude Code
 * agents (cost + irreversible). Do not invoke until the operator checklist in
 * `docs/agents/sandcastle-runbook.md` is complete (sandbox image built,
 * `.sandcastle/.env` populated, auth tokens exported in the run shell).
 *
 * The dependency graph is PRD-locked at the issue level (#3–#14), so a hardcoded
 * DAG is correct and prevents agents from re-deriving settled dependencies.
 */

import * as sandcastle from "@ai-hero/sandcastle";
import { docker } from "@ai-hero/sandcastle/sandboxes/docker";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * The harness MUST run with the git repository root as sandcastle's cwd.
 *
 * Sandcastle defaults its repo dir to `process.cwd()` (resolveCwd(undefined)).
 * If launched from `scripts/sandcastle/` (`cd scripts/sandcastle && npm run go`),
 * `process.cwd()` is NOT the git root, and on Windows the worktree git-mount
 * chain silently breaks: `resolveGitMounts(join(cwd,".git"))` looks for
 * `scripts/sandcastle/.git` (absent), its `.catchAll(()=>[])` swallows the miss,
 * and `patchGitMountsForWindows` then emits NO `/.sandcastle-parent-git` bind
 * mount. Inside the container the worktree `.git` dangles →
 * `fatal: not a git repository: /.sandcastle-parent-git/worktrees/<name>`.
 * (It also makes `resolveEnv` read the wrong `.sandcastle/.env`, so agents run
 * un-credentialed.) Resolving the real toplevel here makes the harness correct
 * regardless of where it is launched from. The bug only fires on Windows, but
 * the defence is cheap and applies on the Linux VM too.
 */
function resolveRepoRoot(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  let root: string;
  try {
    root = execFileSync("git", ["-C", here, "rev-parse", "--show-toplevel"], {
      encoding: "utf8",
    }).trim();
  } catch (e) {
    throw new Error(
      `Preflight: cannot resolve git repo root from ${here}: ${e}. ` +
        `Run the harness inside the evidence-factory git repository.`,
    );
  }
  if (!root || !existsSync(join(root, ".git"))) {
    throw new Error(`Preflight: resolved repo root has no .git: ${root}`);
  }
  // Sandcastle reads agent auth from <repoRoot>/.sandcastle/.env
  // (EnvResolver). Missing here ⇒ agents would run un-credentialed.
  const envFile = join(root, ".sandcastle", ".env");
  if (!existsSync(envFile)) {
    throw new Error(
      `Preflight: ${envFile} not found. The harness env keyset (Claude OAuth + ` +
        `GitHub PAT) must exist at <repo-root>/.sandcastle/.env before --go. ` +
        `See docs/agents/sandcastle-runbook.md.`,
    );
  }
  return root;
}

/**
 * Fail fast if the agent credentials are missing BEFORE any sandbox launches.
 *
 * Why: sandcastle forwards a declared key into the container only when its
 * effective value is truthy — `fileValue || process.env[key]`, then
 * `if (value)` (EnvResolver.js). All keys in `.sandcastle/.env` are declared
 * blank (passthrough-only), so the value comes from the shell that ran
 * `npm run go`. A missing/empty/stale `CLAUDE_CODE_OAUTH_TOKEN` ⇒ the
 * in-container `claude --print` gets no/!invalid bearer ⇒
 * `401 Invalid bearer token`, but only AFTER a full Docker spin-up + an agent
 * iteration. This check mirrors EnvResolver's resolution so the failure is
 * reported in <1s with the exact fix, not after burning a sandbox.
 */
function preflightAgentEnv(root: string): void {
  // Minimal .env parse, consistent with sandcastle's EnvResolver (trim,
  // skip #comments, strip one layer of matching quotes). Values are NOT logged.
  const fileVals: Record<string, string> = {};
  for (const line of readFileSync(join(root, ".sandcastle", ".env"), "utf8").split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const i = t.indexOf("=");
    if (i === -1) continue;
    let v = t.slice(i + 1).trim();
    if (v.length >= 2 && ((v[0] === '"' && v.at(-1) === '"') || (v[0] === "'" && v.at(-1) === "'"))) {
      v = v.slice(1, -1);
    }
    fileVals[t.slice(0, i).trim()] = v;
  }
  // Effective value sandcastle would forward (truthy check, like EnvResolver).
  const eff = (k: string) => fileVals[k] || process.env[k] || "";
  const RUNBOOK = "docs/agents/sandcastle-runbook.md";

  const claudeOk = !!eff("CLAUDE_CODE_OAUTH_TOKEN") || !!eff("ANTHROPIC_API_KEY");
  if (!claudeOk) {
    throw new Error(
      `Preflight: no Claude agent credential. Neither CLAUDE_CODE_OAUTH_TOKEN ` +
        `nor ANTHROPIC_API_KEY is set in this shell (and both are blank in ` +
        `.sandcastle/.env). The in-sandbox 'claude --print' would 401. In the ` +
        `SAME shell that runs 'npm run go':\n` +
        `  export CLAUDE_CODE_OAUTH_TOKEN="$(claude setup-token)"\n` +
        `(Claude Max OAuth; interactive — the token expires/can be revoked, ` +
        `re-run if stale.) See ${RUNBOOK}.`,
    );
  }
  if (!eff("GH_TOKEN")) {
    throw new Error(
      `Preflight: GH_TOKEN not set in this shell (blank in .sandcastle/.env). ` +
        `Agents open PRs and acceptance scripts use 'gh' — this would fail ` +
        `late, inside the agent. In the SAME shell:\n` +
        `  export GH_TOKEN="$(gh auth token)"\nSee ${RUNBOOK}.`,
    );
  }
}

const REPO_ROOT = resolveRepoRoot();
preflightAgentEnv(REPO_ROOT);

const REPO = "dataexperteu/evidence-factory";
const SPINE_MODEL = "claude-opus-4-7";
const APRON_MODEL = "claude-sonnet-4-6";

// Image MUST be built before --go (operator, see docs/agents/sandcastle-runbook.md):
// `docker build -t evidence-factory-sandbox -f scripts/sandcastle/sandbox.Dockerfile .`
// No host networking and no isolated network — sandcastle's default bridge is fine.
// Evidence Factory has no live external systems to reach from inside agents.
const SANDBOX_IMAGE = "evidence-factory-sandbox";
// uid/gid omitted: DockerOptions defaults to host UID/GID (1000 on Linux/Windows),
// matching AGENT_UID/AGENT_GID=1000 in sandbox.Dockerfile.
const sandboxProvider = () => docker({ imageName: SANDBOX_IMAGE });

// In-sandbox setup before each agent. Tolerant of missing scaffolding — early
// slices create requirements.txt / ui-app/ as part of their own work, so the
// hooks must succeed even before those exist.
const hooks = {
  sandbox: {
    onSandboxReady: [
      { command: "test -f requirements.txt && pip install -r requirements.txt || echo 'no requirements.txt yet — skipping'" },
      { command: "test -d ui-app && (cd ui-app && npm ci) || echo 'no ui-app/ yet — skipping'" },
    ],
  },
};

type Slice = {
  issue: number;
  key: string;
  dependsOn: number[];
  lane: "spine" | "apron";
};

/**
 * Evidence Factory slice DAG (issues #3–#14, parented to #1).
 * Spine = correctness backbone (tracer bullet, Smoking-Gun Critic, Red-Herring
 * Designer/Breaker, dominance+watermark). Apron = extends stable interfaces only.
 */
const SLICES: Slice[] = [
  { issue: 3,  key: "slice-1-tracer-bullet",        dependsOn: [],       lane: "spine" },
  { issue: 4,  key: "slice-2-intake",               dependsOn: [3],      lane: "apron" },
  { issue: 5,  key: "slice-3-profile-pdf",          dependsOn: [3],      lane: "apron" },
  { issue: 6,  key: "slice-4-profile-sms",          dependsOn: [3],      lane: "apron" },
  { issue: 7,  key: "slice-5-profile-xlsx",         dependsOn: [3],      lane: "apron" },
  { issue: 8,  key: "slice-6-profile-jpeg",         dependsOn: [3],      lane: "apron" },
  { issue: 9,  key: "slice-7-profile-log",          dependsOn: [3],      lane: "apron" },
  { issue: 10, key: "slice-8-smoking-gun-critic",   dependsOn: [3],      lane: "spine" },
  { issue: 11, key: "slice-9-red-herring-breaker",  dependsOn: [10],     lane: "spine" },
  { issue: 12, key: "slice-10-noise-generator",     dependsOn: [3],      lane: "apron" },
  { issue: 13, key: "slice-11-dominance-watermark", dependsOn: [11],     lane: "spine" },
  { issue: 14, key: "slice-12-difficulty-presets",  dependsOn: [11, 12], lane: "apron" },
  // Consolidation capstone (#33): port the noise generator + leak/contradict
  // guard into api/ and delete the dead backend/ fork. Depends on all five new
  // per-profile writers (#5–#9) landing in api/ first — noise writes through
  // every profile, so it cannot run until they exist. Spine: carries the
  // ≥95% leak-guard characterization test and a tree-wide deletion.
  { issue: 33, key: "slice-13-consolidate-noise",   dependsOn: [5, 6, 7, 8, 9], lane: "spine" },
  // Gap fixes filed 2026-05-22: upload dials bug (#43), closure failure UX (#46).
  { issue: 43, key: "slice-14-upload-dials-fix",    dependsOn: [14, 4],          lane: "apron" },
  { issue: 46, key: "slice-15-closure-failure-ux",  dependsOn: [3],              lane: "apron" },
  // UX + infra improvements filed 2026-05-22.
  { issue: 48, key: "slice-16-help-panel",           dependsOn: [3],              lane: "apron" },
  { issue: 50, key: "slice-17-docker-deploy",        dependsOn: [3],              lane: "apron" },
  { issue: 51, key: "slice-18-activity-log",         dependsOn: [3],              lane: "apron" },
];

const ready = (s: Slice, merged: Set<number>) =>
  s.dependsOn.every((d) => merged.has(d));

/** Spine strictly serial; apron parallel. */
function nextBatch(pending: Slice[], merged: Set<number>, running: Set<number>) {
  const runnable = pending.filter((s) => ready(s, merged) && !running.has(s.issue));
  const spineInFlight = [...running].some(
    (i) => SLICES.find((s) => s.issue === i)?.lane === "spine",
  );
  const batch: Slice[] = [];
  const spine = runnable.filter((s) => s.lane === "spine");
  if (!spineInFlight && spine.length) batch.push(spine[0]);
  batch.push(...runnable.filter((s) => s.lane === "apron"));
  return batch;
}

function prompt(s: Slice) {
  return [
    `You are an autonomous engineer. Implement GitHub issue #${s.issue} in ${REPO}.`,
    `Read the issue body in full: it contains the locked PRD #1 decisions as`,
    `acceptance criteria — DO NOT re-decide settled questions.`,
    ``,
    `Stack: Python FastAPI backend + a light SPA front-end (ui-app/). Early`,
    `slices may not yet have all of that scaffolding — follow the slice's own`,
    `acceptance criteria for which parts to build.`,
    ``,
    `CANONICAL PROJECT STRUCTURE — read before writing any file:`,
    `- The product lives in \`api/\` (FastAPI app + \`api/pipeline/\` + per-profile`,
    `  modules under \`api/provenance/\`) and \`ui-app/\` (the SPA). EXTEND THIS TREE.`,
    `- NEVER create a parallel top-level package (e.g. \`backend/\`, \`src/\`, a new`,
    `  app root) and NEVER re-scaffold the project from scratch. If something you`,
    `  need seems missing, it belongs IN \`api/\`/\`ui-app/\` — add it there.`,
    `- A provenance profile is a per-profile module under \`api/provenance/\``,
    `  (see \`api/provenance/email_profile.py\` for the established pattern), NOT a`,
    `  branch in a monolithic catalog.`,
    `- Before adding code, \`ls api/ api/pipeline/ api/provenance/\` and mirror the`,
    `  existing conventions. If a top-level dir other than \`api/\`/\`ui-app/\`/\`tests/\``,
    `  /\`docs/\` seems necessary, STOP — you have misread the structure.`,
    ``,
    `Hard constraints:`,
    `- Honour AGENTS.md and the docs under docs/agents/ (index-based discovery,`,
    `  context maintenance, no stray root-level files).`,
    `- Branch \`slice/${s.key}\` already exists; commit on it only. Do not touch`,
    `  any other branch and do not modify other slices' code.`,
    `- This sandbox has NO access to external production systems. Use fixtures`,
    `  and dummy data only. If real-system access seems required, STOP.`,
    s.lane === "spine"
      ? `- SPINE / correctness-critical: add characterization or golden tests`
        + ` that pin the intended behaviour, and assert your implementation`
        + ` matches them. Definition-of-Done for spine slices.`
      : `- Follow TDD against the issue's acceptance criteria.`,
    `- If this slice changes clickable UI, ensure a Playwright live-env test`,
    `  under ui-app/e2e/ covers it (the Phase-2 UI-tester agent will validate).`,
    ``,
    `Definition of "green" for THIS slice (the only bar you must clear):`,
    `  1. Lint / format / typecheck pass for whatever surface this slice touches`,
    `     (Python: ruff/mypy if configured; TypeScript: \`cd ui-app && npx tsc -b --noEmit\`).`,
    `  2. \`cd ui-app && npx vite build\` exits 0 (if ui-app/ exists).`,
    `  3. The slice's own new/changed unit + golden/characterization tests pass.`,
    `Once those hold: commit on \`slice/${s.key}\`, open a PR linked to`,
    `#${s.issue} (body must contain \`Closes #${s.issue}\`), and STOP immediately`,
    `— do not re-verify, do not poll CI, do not merge. A separate UI-tester agent`,
    `then validates this slice's UI; if it passes, the harness enables GitHub`,
    `auto-merge (squash) and the PR merges itself once CI goes green.`,
  ].join("\n");
}

/**
 * UI-tester gate: a fresh agent on the SAME branch reads the slice's diff and,
 * if it touched clickable UI, writes + runs a human-like Playwright test
 * against the live seeded env. It MUST end its output with exactly one line:
 *   <test-result>PASS: …</test-result>
 *   <test-result>FAIL: …</test-result>
 *   <test-result>SKIPPED-NOT-UI</test-result>
 * FAIL excludes the slice from "landed" (its PR must not merge).
 */
function uiTestPrompt(s: Slice) {
  return [
    `You are a QA engineer. The branch for GitHub issue #${s.issue} (${REPO})`,
    `has an implementation commit. Validate it END-TO-END.`,
    ``,
    `1. Inspect the diff on this branch. If it introduces/changes NO clickable`,
    `   UI (backend-only or docs), output exactly:`,
    `   <test-result>SKIPPED-NOT-UI</test-result> and stop.`,
    `2. Otherwise: write a human-like Playwright test under ui-app/e2e/ that`,
    `   exercises every clickable feature this slice adds, and run it against`,
    `   the local dev environment (Vite dev server + FastAPI). Use real seeded`,
    `   fixture data, not mocks.`,
    `3. Commit any new test files on this same branch.`,
    `4. End with exactly one line:`,
    `   <test-result>PASS: <what you verified></test-result>  OR`,
    `   <test-result>FAIL: <what broke></test-result>`,
    `Do not touch production code; only add/adjust the e2e test.`,
  ].join("\n");
}

/**
 * Enable GitHub auto-merge (squash) on the slice's PR once it has landed and
 * the UI-tester gate passed. The agent already opened the PR (with `Closes #N`),
 * so `gh` resolves it from the branch. `--auto` queues the squash-merge to fire
 * only when branch protection's required checks (CI) go green — never merging
 * red code. Defensive: a failure here (e.g. auto-merge not enabled on the repo
 * yet, or no branch protection) is logged, not fatal — the PR simply waits for a
 * human, exactly as before. Requires the repo setting "Allow auto-merge" ON and
 * branch protection with a required status check; see the runbook.
 */
function enableAutoMerge(branch: string) {
  try {
    execFileSync(
      "gh",
      ["pr", "merge", branch, "--repo", REPO, "--auto", "--squash"],
      { cwd: REPO_ROOT, stdio: "pipe", encoding: "utf8" },
    );
    console.log(`  ⏳ auto-merge (squash) enabled for ${branch} — fires when CI is green.`);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    console.error(
      `  ! auto-merge not enabled for ${branch} (${msg.split("\n")[0]}). ` +
        `PR left for manual merge — check repo "Allow auto-merge" + branch protection.`,
    );
  }
}

/** Reclaim the host worktree createSandbox() materialises. */
function reclaimWorktree(branch: string) {
  try {
    const list = execFileSync("git", ["worktree", "list", "--porcelain"], {
      cwd: REPO_ROOT,
      encoding: "utf8",
    });
    let cur: string | undefined;
    for (const line of list.split("\n")) {
      if (line.startsWith("worktree ")) cur = line.slice(9);
      else if (line === `branch refs/heads/${branch}` && cur) {
        execFileSync("git", ["worktree", "remove", "--force", cur], {
          cwd: REPO_ROOT,
          stdio: "inherit",
        });
      }
    }
    execFileSync("git", ["worktree", "prune"], { cwd: REPO_ROOT });
  } catch (e) {
    console.error(`  ! worktree reclaim failed for ${branch}: ${e}`);
  }
}

async function runSlice(s: Slice) {
  const branch = `slice/${s.key}`;
  const sandbox = await sandcastle.createSandbox({
    branch,
    cwd: REPO_ROOT, // MUST be the git root — see resolveRepoRoot() above.
    sandbox: sandboxProvider(),
    hooks,
  });
  try {
    // Phase 1 — implementer.
    const impl = await sandbox.run({
      name: `slice-${s.issue}-impl`,
      agent: sandcastle.claudeCode(s.lane === "spine" ? SPINE_MODEL : APRON_MODEL),
      // Spine gets more iterations for the characterization-test DoD.
      maxIterations: s.lane === "spine" ? 120 : 80,
      prompt: prompt(s),
    });
    if ((impl.commits?.length ?? 0) === 0) {
      return { issue: s.issue, key: s.key, committed: false };
    }

    // Phase 2 — UI-tester gate (same branch). FAIL excludes the slice so its
    // PR must not merge.
    const test = await sandbox.run({
      name: `slice-${s.issue}-uitest`,
      agent: sandcastle.claudeCode(APRON_MODEL),
      maxIterations: 50,
      prompt: uiTestPrompt(s),
    });
    const verdict = test.stdout
      ?.match(/<test-result>([^<]+)<\/test-result>/)?.[1]
      ?.trim();
    if (verdict?.startsWith("FAIL")) {
      console.error(`  ✗ UI-tester FAILED #${s.issue}: ${verdict} — slice excluded.`);
      return { issue: s.issue, key: s.key, committed: false };
    }
    // PASS or SKIPPED-NOT-UI → slice landed. Queue the squash-merge; CI gates it.
    enableAutoMerge(branch);
    return { issue: s.issue, key: s.key, committed: true };
  } finally {
    await sandbox.close();
    reclaimWorktree(branch);
  }
}

/**
 * Resume gate (idempotent restarts). A slice whose linked GitHub issue is
 * already closed has LANDED — its PR merged with "Closes #N". Pre-seeding
 * `merged` with these makes `npm run go` resumable: completed slices are
 * skipped and their dependents unblock, instead of being re-run — which for
 * a spine slice would produce 0 new commits, be misread as a failed spine,
 * and halt the whole run. Fails open: a gh error runs all.
 */
function alreadyLandedIssues(): Set<number> {
  try {
    const out = execFileSync(
      "gh",
      ["issue", "list", "--repo", REPO, "--state", "closed",
       "--limit", "300", "--json", "number"],
      { cwd: REPO_ROOT, encoding: "utf8" },
    );
    const closed = new Set<number>(
      (JSON.parse(out) as { number: number }[]).map((i) => i.number),
    );
    return new Set(SLICES.map((s) => s.issue).filter((n) => closed.has(n)));
  } catch (e) {
    console.error(`  ! resume-gate gh query failed (running ALL slices): ${e}`);
    return new Set<number>();
  }
}

export async function orchestrate() {
  const landed = alreadyLandedIssues();
  if (landed.size) {
    console.log(
      `Resume: ${landed.size} slice(s) already landed (issue closed) — `
        + `skipping ${[...landed].sort((a, b) => a - b).join(", ")}.`,
    );
  }
  const merged = new Set<number>(landed);
  const running = new Set<number>();
  let pending = SLICES.filter((s) => !landed.has(s.issue));

  while (pending.length || running.size) {
    const batch = nextBatch(pending, merged, running);
    if (!batch.length && !running.size) {
      throw new Error(`Deadlock: unrunnable slices remain: ${pending.map((s) => s.issue)}`);
    }
    batch.forEach((s) => {
      running.add(s.issue);
      pending = pending.filter((p) => p.issue !== s.issue);
    });
    // allSettled: a failing apron slice must not cancel its siblings.
    const settled = await Promise.allSettled(batch.map(runSlice));
    settled.forEach((o, i) => {
      const s = batch[i]!;
      running.delete(s.issue);
      if (o.status === "fulfilled" && o.value.committed) {
        merged.add(s.issue);
      } else {
        const why = o.status === "rejected" ? o.reason : "no commits produced";
        console.error(`  ✗ slice #${s.issue} (${s.key}): ${why}`);
        if (s.lane === "spine") {
          throw new Error(`Spine slice #${s.issue} failed — halting (downstream blocked).`);
        }
      }
    });
  }
  return { merged: [...merged].sort((a, b) => a - b) };
}

// Intentionally NOT auto-invoked. Run only after the operator runbook checklist:
// `npm run go`.
if (process.argv.includes("--go")) {
  orchestrate()
    .then((r) => console.log("done", r))
    .catch((e) => { console.error(e); process.exit(1); });
}
