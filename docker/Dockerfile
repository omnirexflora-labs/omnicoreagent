# OmniServe Docker Image
#
# Multi-stage build for smaller production image
#
# Build:  docker build -t omnicoreagent-serve:latest -f docker/Dockerfile .
# Run:    docker run -p 8000:8000 -e LLM_API_KEY=xxx omnicoreagent-serve:latest

FROM python:3.12-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast package management
RUN pip install --no-cache-dir uv

# Create app directory
WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY README.md .
COPY LICENSE .
COPY src/ src/

# Install dependencies
RUN uv pip install --system --no-cache ".[serve]"

# ======================================================================
# Production image
# ======================================================================
FROM python:3.12-slim

# Add labels
LABEL maintainer="OmniRexFlora Labs <hello@omnirexflora.com>"
LABEL description="OmniServe - Production-ready API server for AI agents"

# Create non-root user for security
RUN useradd --create-home appuser

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/omniserve /usr/local/bin/omniserve

# Create app directory
WORKDIR /app

# Copy source code (for agent files)
COPY src/ src/

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default environment variables
ENV OMNICOREAGENT_SERVE_HOST=0.0.0.0
ENV OMNICOREAGENT_SERVE_PORT=8000
ENV OMNICOREAGENT_SERVE_ENABLE_DOCS=true
ENV OMNICOREAGENT_SERVE_CORS_ENABLED=true
ENV OMNICOREAGENT_SERVE_REQUEST_LOGGING=true

# Default command: quickstart with OpenAI
CMD ["omniserve", "quickstart", "--provider", "openai", "--model", "gpt-4o-mini"]
