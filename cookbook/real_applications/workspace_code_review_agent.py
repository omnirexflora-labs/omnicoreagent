"""Workspace code review agent using built-in workspace command tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

from omnicoreagent import MemoryRouter, OmniCoreAgent

from _bootstrap import model_config, require_llm_api_key, response_text


def seed_workspace(workspace_dir: Path) -> None:
    files_dir = workspace_dir / "files"
    (files_dir / "src").mkdir(parents=True, exist_ok=True)
    (files_dir / "tests").mkdir(parents=True, exist_ok=True)
    billing_file = files_dir / "src" / "billing.py"
    if not billing_file.exists():
        billing_file.write_text(
            "\n".join(
                [
                    "def calculate_total(items):",
                    "    total = 0",
                    "    for item in items:",
                    "        total += item['price']",
                    "    return total",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    test_file = files_dir / "tests" / "test_billing.py"
    if not test_file.exists():
        test_file.write_text(
            "\n".join(
                [
                    "from src.billing import calculate_total",
                    "",
                    "",
                    "def test_calculate_total():",
                    "    assert calculate_total([{'price': 5}, {'price': 7}]) == 12",
                    "",
                ]
            ),
            encoding="utf-8",
        )


def build_agent(
    workspace_dir: Path | str = "tmp/real_apps/code_review",
) -> OmniCoreAgent:
    workspace_path = Path(workspace_dir)
    seed_workspace(workspace_path)

    return OmniCoreAgent(
        name="workspace_code_review_agent",
        system_instruction="""You are a code review agent operating only inside workspace files.
Use ls, glob, grep, read_file, edit_file, and write_file to inspect and update files.
Write your final review to reviews/billing-review.md.""",
        model_config=model_config(max_tokens=1200),
        memory_router=MemoryRouter("in_memory"),
        agent_config={
            "max_steps": 12,
            "tool_call_timeout": 20,
            "enable_workspace_files": True,
            "workspace_config": {
                "workspace_backend": "local",
                "workspace_dir": str(workspace_path),
            },
        },
    )


async def main() -> None:
    require_llm_api_key()

    agent = build_agent()
    result = await agent.run(
        "Review the billing module. Inspect the workspace, search for calculate_total, "
        "update src/billing.py so negative prices are rejected, and write a concise "
        "review at reviews/billing-review.md.",
        session_id="real_app_workspace_code_review",
    )

    print(response_text(result))
    print(f"trace_id={result['trace_id']}")
    print(f"run_id={result['run_id']}")
    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
