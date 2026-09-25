FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# The archive server lives outside /app, in an environment of its own.
# mcp 2.x renamed FastMCP; this server is written for 1.x.
RUN python3 -m venv /opt/archive/venv \
 && /opt/archive/venv/bin/pip install --quiet "mcp>=1.9,<2"
COPY archive_server.py /opt/archive/server.py

WORKDIR /app
