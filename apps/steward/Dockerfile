# The repository steward, served by OmniServe.
#
# Built from the repository root:  docker build -f apps/steward/Dockerfile .
# The image carries the runtime with the extras the steward needs — the API
# server, Postgres for memory/run state/budgets, Redis for the task store, and
# E2B for sandboxes (never the Docker socket from inside a container). Secrets
# come from the environment at run time; none are baked in.

FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN uv pip install --system --no-cache ".[serve,postgres,redis,e2b,tokenizer]"

FROM python:3.12-slim
LABEL description="OmniCoreAgent repository steward (production proving)"
RUN useradd --create-home appuser \
    && apt-get update && apt-get install -y --no-install-recommends curl git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
WORKDIR /app
COPY apps/steward/ /app/apps/steward/
RUN mkdir -p /app/workspace && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
CMD ["omniserve", "run", "--agent", "/app/apps/steward/agent.py", "--host", "0.0.0.0", "--port", "8000"]
