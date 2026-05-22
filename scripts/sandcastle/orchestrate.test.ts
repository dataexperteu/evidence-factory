import assert from "node:assert/strict";
import test from "node:test";

import {
  decideSliceHostAction,
  prReferencesIssue,
  type PullRequestSummary,
  type SliceGithubState,
} from "./orchestrate.ts";

const branch = "slice/slice-1-tracer-bullet";

function pr(overrides: Partial<PullRequestSummary>): PullRequestSummary {
  return {
    number: 10,
    state: "OPEN",
    headRefName: branch,
    body: "",
    closingIssuesReferences: [],
    ...overrides,
  };
}

function state(overrides: Partial<SliceGithubState>): SliceGithubState {
  return {
    issueState: "OPEN",
    prs: [],
    ...overrides,
  };
}

test("skips a slice whose linked issue is already closed", () => {
  assert.deepEqual(
    decideSliceHostAction(state({ issueState: "CLOSED" }), 3, branch),
    { action: "skip", reason: "issue-closed" },
  );
});

test("skips a slice when the same branch already has a closed PR linked to the issue", () => {
  const linked = pr({
    number: 42,
    state: "CLOSED",
    body: "Closes #3",
  });

  assert.deepEqual(
    decideSliceHostAction(state({ prs: [linked] }), 3, branch),
    { action: "skip", reason: "handled-pr", pr: linked },
  );
});

test("reuses an open PR for the branch instead of rerunning the implementer", () => {
  const open = pr({ number: 43, body: "Work in progress" });

  assert.deepEqual(
    decideSliceHostAction(state({ prs: [open] }), 3, branch),
    { action: "reuse-open-pr", pr: open },
  );
});

test("prefers the kept open PR when closed duplicate PRs also exist", () => {
  const duplicate = pr({ number: 42, state: "CLOSED", body: "Closes #3" });
  const kept = pr({ number: 44, state: "OPEN", body: "Closes #3" });

  assert.deepEqual(
    decideSliceHostAction(state({ prs: [duplicate, kept] }), 3, branch),
    { action: "reuse-open-pr", pr: kept },
  );
});

test("does not treat a closed PR for another issue as handled", () => {
  assert.deepEqual(
    decideSliceHostAction(
      state({ prs: [pr({ state: "CLOSED", body: "Closes #99" })] }),
      3,
      branch,
    ),
    { action: "run-implementer" },
  );
});

test("recognizes GitHub closing references and body links", () => {
  assert.equal(
    prReferencesIssue(pr({ body: "", closingIssuesReferences: [{ number: 3 }] }), 3),
    true,
  );
  assert.equal(prReferencesIssue(pr({ body: "Fixes #3" }), 3), true);
  assert.equal(prReferencesIssue(pr({ body: "Refs #3" }), 3), false);
});
