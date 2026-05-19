#!/usr/bin/env python3
"""Serve a real application harness with OmniServe.

Run:
    uv run omniserve run --agent cookbook/omniserve/real_application_agent.py

Then inspect:
    curl http://localhost:8000/health
    curl http://localhost:8000/tools
    curl -X POST http://localhost:8000/run/sync \
      -H "Content-Type: application/json" \
      -d '{"query": "Handle ticket tck-1042 for customer cust-001."}'
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from cookbook.real_applications.support_operations_agent import build_agent  # noqa: E402


def create_agent(workspace_dir: str | Path = "tmp/omniserve_real_app_support_ops"):
    """Return the support operations real app as an OmniServe-loadable agent."""
    return build_agent(workspace_dir=workspace_dir)
