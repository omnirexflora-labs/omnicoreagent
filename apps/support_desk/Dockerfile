# The support desk, served by OmniServe.
#
# Built from the repository root:  docker build -f apps/support_desk/Dockerfile .
# The image carries the runtime with the extras the desk needs: the API server,
# Postgres for memory and run state, Redis for the task store, OpenTelemetry
# for the optional OTLP exporter. Secrets come from the environment at run
# time; none are baked in.

FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
# The package version comes from git tags (uv-dynamic-versioning); there is
# no git history in an image, so the version is given here instead.
ARG PACKAGE_VERSION=0.0.0+docker
ENV UV_DYNAMIC_VERSIONING_BYPASS=$PACKAGE_VERSION
RUN uv pip install --system --no-cache ".[serve,postgres,redis,otel,tokenizer]"

FROM python:3.12-slim
LABEL description="OmniCoreAgent support desk (production readiness)"
RUN useradd --create-home appuser \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
WORKDIR /app
COPY apps/support_desk/agent.py /app/apps/support_desk/agent.py
# The workspace holds the traces; data holds the orders and the refund ledger.
# Both are volumes in the Compose file.
RUN mkdir -p /app/workspace /app/data && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
CMD ["omniserve", "run", "--agent", "/app/apps/support_desk/agent.py", "--host", "0.0.0.0", "--port", "8000"]
