# Sandcastle sandbox image for the Evidence Factory AFK harness (PRD #1 / #2).
# Base structure proven by the dataexperteu/agentic-ai harness, in turn proven
# by jacobisak2/madsen's .sandcastle/Dockerfile.

FROM node:22-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl jq ca-certificates build-essential python3 python3-pip python3-venv \
 && rm -rf /var/lib/apt/lists/*

# GitHub CLI (agents open PRs; gh used by acceptance scripts).
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
 && apt-get update && apt-get install -y gh && rm -rf /var/lib/apt/lists/*

# Align container user to host UID/GID: avoids worktree chown churn (madsen pattern).
ARG AGENT_UID=1000
ARG AGENT_GID=1000
RUN groupmod -g $AGENT_GID node && usermod -u $AGENT_UID -g $AGENT_GID -d /home/agent -m -l agent node

# Playwright (system deps + Chromium) for the mandatory live-env E2E tests.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN npm install -g playwright@1.59.1 \
 && mkdir -p /opt/playwright \
 && playwright install-deps chromium \
 && playwright install chromium \
 && chown -R $AGENT_UID:$AGENT_GID /opt/playwright

USER ${AGENT_UID}:${AGENT_GID}

# Claude Code CLI.
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/home/agent/.local/bin:$PATH"

# Benign marker — lets in-container tooling detect that it is running inside
# the Evidence Factory sandcastle sandbox (e.g. to gate destructive operations).
ENV EVIDENCE_FACTORY_SANDBOX=1

# Sandcastle bind-mounts the git worktree at /home/agent/workspace and runs
# the agent there. Container stays alive; sandcastle drives it.
WORKDIR /home/agent/workspace
ENTRYPOINT ["sleep", "infinity"]
