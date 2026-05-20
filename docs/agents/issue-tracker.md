# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Prerequisites (one-off)

This repo is not yet a git repo or pushed to GitHub. Before any `gh issue` call works:

1. `git init && git add . && git commit -m "initial"`
2. Create the GitHub repo: `gh repo create <owner>/evidence-factory --source . --private --push`
3. Verify: `gh issue list` returns (an empty list, not an error).
4. Create the canonical triage labels (the skill doesn't auto-create them):
   ```sh
   gh label create needs-triage    --color BFD4F2 --description "Maintainer needs to evaluate"
   gh label create needs-info      --color FBCA04 --description "Waiting on reporter"
   gh label create ready-for-agent --color 0E8A16 --description "Fully specified, AFK-ready"
   gh label create ready-for-human --color 1D76DB --description "Needs human implementation"
   gh label create wontfix         --color CCCCCC --description "Will not be actioned"
   ```

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
