#!/usr/bin/env python3
"""
Worker Profiles Example

The lead agent spawns workers with spawn_subagents and picks the kind of
worker for each task from the profiles you list: a cheap, fast model at low
effort to search, and a stronger one to write. Every worker spends the
lead's budgets.

Features covered:
- worker_profiles in agent_config
- A per-profile model, reasoning effort, tools and step cap
- Which profile and model each worker ran with, from the run's record

Build on: agent_with_sub_agents.py

Run:
    python cookbook/getting_started/agent_with_worker_profiles.py
"""

import asyncio
import os
import tempfile
from pathlib import Path

from omnicoreagent import OmniCoreAgent

from _bootstrap import model_config, require_llm_api_key, response_text

SMALL_MODEL = os.getenv("OMNICOREAGENT_SMALL_MODEL", "gpt-5.4-mini")


async def main():
    require_llm_api_key()
    workspace = Path(tempfile.mkdtemp(prefix="worker_profiles_"))
    files = workspace / "files" / "notes"
    files.mkdir(parents=True)
    (files / "api.md").write_text("# API\nTODO: document the retry limits.\nThe API returns JSON.\n")
    (files / "deploy.md").write_text("# Deploy\nTODO: say which region is the default.\n")
    (files / "intro.md").write_text("# Intro\nThis project answers support questions.\n")

    lead = OmniCoreAgent(
        name="lead",
        system_instruction=(
            "You plan the work and delegate it. Spawn an explorer first to find what "
            "is needed. When you have read its output, spawn a writer and put the "
            "findings in its task. Then answer briefly."
        ),
        model_config=model_config(max_tokens=1200),
        agent_config={
            "enable_subagents": True,
            "max_steps": 12,
            "workspace_config": {"workspace_dir": str(workspace)},
            "worker_profiles": [
                {
                    "name": "explorer",
                    "description": "Reads and searches the workspace files; never edits them.",
                    "model_config": {"model": SMALL_MODEL},
                    "reasoning_effort": "low",
                    "tools": ["read_file", "grep", "glob", "ls"],
                    "max_steps": 10,
                },
                {
                    "name": "writer",
                    "description": "Writes a clear document from findings it is given.",
                    "tools": ["read_file"],
                    "max_steps": 8,
                },
            ],
            "governance_config": {
                "budgets": {"request": [{"meter": "model_calls", "limit": 40, "on_exhausted": "terminate"}]},
            },
        },
    )

    result = await lead.run(
        "Find every TODO under notes/ and have a writer turn them into a short "
        "checklist, written to report/todo.md."
    )
    print(f"\nLead's answer:\n{response_text(result)}\n")

    trajectory = await lead.get_trajectory(run_id=result["run_id"])
    print("Workers the lead started:")
    for step in trajectory["steps"]:
        for call in step.get("tool_calls") or []:
            for worker in (call.get("subagent") or {}).get("workers") or []:
                print(f"  {worker['agent_name']}: profile {worker['profile']}, "
                      f"model {worker['model']}, effort {worker['reasoning_effort']}")

    checklist = workspace / "files" / "report" / "todo.md"
    print(f"\n{checklist.relative_to(workspace)}:\n{checklist.read_text() if checklist.exists() else '(not written)'}")
    spent = await lead.budget_status(result["run_id"])
    for entry in spent:
        print(f"Budget {entry['scope']} {entry['meter']}: {entry['spent']} of {entry['limit']}")
    await lead.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
