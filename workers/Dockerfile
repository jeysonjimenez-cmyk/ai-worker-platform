FROM python:3.12-slim

# Install uv from official image (frozen version — update intentionally)
COPY --from=ghcr.io/astral-sh/uv:0.7.12 /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests first — these layers are cached unless deps change
COPY pyproject.toml uv.lock ./

# Install production dependencies only, into the project venv
RUN uv sync --frozen --no-dev

# Copy source after deps to keep the dep-install layer cached
COPY worker_base/ ./worker_base/
COPY worker_echo/ ./worker_echo/

# Activate the venv so the container uses installed deps directly (no uv sync at runtime)
ENV PATH="/app/.venv/bin:$PATH"
# Credentials and API URL are injected at runtime — never baked in
ENV WORKER_POLL_WAIT=30

CMD ["python", "-m", "worker_echo"]
