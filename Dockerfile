# Overnight-loop container (PROJECT_BRIEF §7). Built at setup; the loop
# never modifies it (R5). Deps and CLIs are BAKED at build time — there is
# no package-registry egress at runtime.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git ca-certificates curl rsync nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    # headless LLM CLIs, baked (versions float at build time; the loop
    # re-verifies the flags it uses at preflight)
    && npm install -g @anthropic-ai/claude-code @openai/codex \
    && npm cache clean --force

# Non-root user (R6/§7). uid 1000 aligns with default host user for the
# bind-mounted repo.
RUN useradd --create-home --uid 1000 labuser
USER labuser
ENV HOME=/home/labuser

# Baked project environment OUTSIDE the mounted workspace, so the bind
# mount cannot shadow it and runtime never needs to resolve packages.
ENV UV_PROJECT_ENVIRONMENT=/home/labuser/venv
ENV UV_NO_SYNC=1
WORKDIR /workspace
COPY --chown=labuser pyproject.toml uv.lock /workspace/
RUN UV_NO_SYNC=0 uv sync --frozen --no-install-project \
    && rm /workspace/pyproject.toml /workspace/uv.lock

# In-container commits are local-only (R6); identity is fixed and obvious
# in the audit log.
RUN git config --global user.name "quantlab-overnight" \
    && git config --global user.email "overnight@quantlab.local" \
    && git config --global safe.directory /workspace

CMD ["bash"]
