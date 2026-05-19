"""Personal operations assistant built on OmniCoreAgent."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from omnicoreagent import MemoryRouter, OmniCoreAgent, ToolRegistry

try:
    from cookbook.real_applications._bootstrap import (
        model_config,
        require_llm_api_key,
        response_text,
    )
except ModuleNotFoundError:
    from _bootstrap import model_config, require_llm_api_key, response_text


def build_personal_tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("calendar_agenda")
    def calendar_agenda(date: str) -> dict:
        return {
            "date": date,
            "events": [
                {
                    "time": "09:30",
                    "title": "Product review",
                    "location": "Zoom",
                },
                {
                    "time": "14:00",
                    "title": "Customer renewal prep",
                    "location": "HQ",
                },
            ],
        }

    @tools.register_tool("task_list")
    def task_list(owner: str) -> dict:
        return {
            "owner": owner,
            "tasks": [
                {
                    "id": "task-17",
                    "title": "Send renewal notes",
                    "priority": "high",
                },
                {
                    "id": "task-22",
                    "title": "Review product launch risks",
                    "priority": "medium",
                },
            ],
        }

    @tools.register_tool("preference_profile")
    def preference_profile(user_id: str) -> dict:
        return {
            "user_id": user_id,
            "working_hours": "09:00-17:30",
            "communication_style": "brief, direct, action-oriented",
            "focus_blocks": ["10:00-12:00"],
        }

    @tools.register_tool("draft_email")
    def draft_email(recipient: str, subject: str, notes: str) -> dict:
        return {
            "recipient": recipient,
            "subject": subject,
            "draft": (
                f"Subject: {subject}\n\n"
                f"Hi {recipient},\n\n{notes}\n\nBest,\nAssistant"
            ),
            "status": "drafted",
        }

    return tools


def build_memory_router(
    *,
    memory_backend: str = "in_memory",
    workspace_dir: Path | str = "tmp/real_apps/personal_assistant",
) -> MemoryRouter:
    if memory_backend == "sql" and not os.getenv("DATABASE_URL"):
        db_path = Path(workspace_dir) / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    return MemoryRouter(memory_backend)


def build_agent(
    workspace_dir: Path | str = "tmp/real_apps/personal_assistant",
    *,
    memory_backend: str = "in_memory",
) -> OmniCoreAgent:
    return OmniCoreAgent(
        name="personal_operations_assistant",
        system_instruction="""You are a personal operations assistant.
Use calendar, task, preference, and email tools together when possible. Keep
private working notes in workspace files and write a daily brief before the
final answer. The final answer should summarize the user-facing plan and name
the workspace brief that was saved.""",
        model_config=model_config(max_tokens=1100),
        local_tools=build_personal_tools(),
        memory_router=build_memory_router(
            memory_backend=memory_backend,
            workspace_dir=workspace_dir,
        ),
        agent_config={
            "max_steps": 10,
            "tool_call_timeout": 20,
            "enable_workspace_files": True,
            "guardrail_config": {"strict_mode": True},
            "workspace_config": {
                "workspace_backend": "local",
                "workspace_dir": str(workspace_dir),
            },
        },
    )


async def main() -> None:
    require_llm_api_key()

    memory_backend = os.getenv("OMNICOREAGENT_COOKBOOK_MEMORY_BACKEND", "in_memory")
    agent = build_agent(memory_backend=memory_backend)
    result = await agent.run(
        "Prepare my day for 2026-05-19. Check my calendar, tasks, and preferences, "
        "draft a short email update for Jordan about renewal prep, and save the "
        "brief to briefs/2026-05-19.md.",
        session_id="real_app_personal_operations",
    )

    print(response_text(result))
    print(f"trace_id={result['trace_id']}")
    print(f"run_id={result['run_id']}")
    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
