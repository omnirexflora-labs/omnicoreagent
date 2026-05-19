#!/usr/bin/env python3
"""Run a real application harness through BackgroundAgentManager.

This example uses the support operations domain tools from the real application
cookbook and executes them as a durable background task. It does not require an
LLM API key; the agent is deterministic so the background boundary is easy to
run, test, and inspect.

Run:
    uv run python cookbook/background_agents/real_application_background_task.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
from typing import Any

try:
    from cookbook.background_agents._bootstrap import ROOT_DIR  # noqa: F401
except ModuleNotFoundError:
    from _bootstrap import ROOT_DIR  # noqa: F401
from cookbook.real_applications.support_operations_agent import build_support_tools
from omnicoreagent import BackgroundAgentManager
from omnicoreagent.core.workspace.manager import Workspace


class SupportOperationsBackgroundAgent:
    """Deterministic support operations agent for background execution."""

    name = "support_operations_background_agent"
    system_instruction = "Resolve support operations tasks in the background."
    model_config = {"provider": "openai", "model": "gpt-5.4-mini"}
    agent_config: dict[str, Any] = {"enable_workspace_files": True}
    mcp_tools: list[dict[str, Any]] = []

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.tools = build_support_tools()
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        query: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, str]:
        self.calls.append({"query": query, "session_id": session_id, "run_id": run_id})
        workspace_path = workspace_path_from_query(query)

        customer = await self.tools.execute_tool(
            "lookup_customer",
            {"customer_id": "cust-001"},
        )
        orders = await self.tools.execute_tool(
            "recent_orders",
            {"customer_id": "cust-001"},
        )
        policy = await self.tools.execute_tool(
            "support_policy_search",
            {"query": "enterprise delayed shipment escalation"},
        )
        escalation = await self.tools.execute_tool(
            "create_escalation",
            {
                "ticket_id": "tck-1042",
                "severity": "medium",
                "summary": "Delayed enterprise shipment needs timeline and goodwill review.",
            },
        )

        note = render_ticket_note(
            customer=customer,
            orders=orders,
            policy=policy,
            escalation=escalation,
            session_id=session_id,
            run_id=run_id,
        )
        self.workspace.files.write_text(f"{workspace_path}/output.md", note)
        self.workspace.files.write_text(
            f"{workspace_path}/tickets/tck-1042.md",
            note,
        )

        return {
            "response": (
                "support background task complete; "
                "ticket=tck-1042; "
                f"workspace=/workspace/{workspace_path}"
            )
        }


def workspace_path_from_query(query: str) -> str:
    for line in query.splitlines():
        if line.startswith("workspace_path: /workspace/"):
            return line.removeprefix("workspace_path: /workspace/")
    raise ValueError("background run query did not include workspace_path guidance")


def render_ticket_note(
    *,
    customer: dict[str, Any],
    orders: dict[str, Any],
    policy: dict[str, Any],
    escalation: dict[str, Any],
    session_id: str | None,
    run_id: str | None,
) -> str:
    delayed_orders = [
        order for order in orders["orders"] if order.get("status") == "delayed"
    ]
    delayed_summary = ", ".join(order["id"] for order in delayed_orders)
    return "\n".join(
        [
            "# Support background task: tck-1042",
            "",
            f"- run_id: {run_id}",
            f"- session_id: {session_id}",
            f"- customer: {customer['name']} ({customer['customer_id']})",
            f"- plan: {customer['plan']}",
            f"- delayed_orders: {delayed_summary}",
            f"- policy: {policy['policy']}",
            f"- escalation_status: {escalation['status']}",
            "",
            "## Customer plan",
            "",
            "1. Confirm the latest shipment timeline.",
            "2. Send the customer-visible update.",
            "3. Review goodwill credit if the delay impact is material.",
            "",
        ]
    )


async def run_real_application_background_example(
    workspace_dir: str | Path | None = None,
) -> dict[str, Any]:
    workspace_root = Path(
        workspace_dir
        or Path(tempfile.gettempdir()) / "omnicoreagent_real_app_background_workspace"
    )
    workspace = Workspace.from_config(workspace_dir=workspace_root).ensure()
    agent = SupportOperationsBackgroundAgent(workspace)
    manager = BackgroundAgentManager(workspace=workspace, worker_id="real_app_worker")

    try:
        await manager.register_agent("support_ops", agent)
        await manager.register_task(
            task_id="support_ticket_tck_1042",
            agent_id="support_ops",
            query=(
                "Handle ticket tck-1042 for customer cust-001. "
                "Create a durable support note and escalation summary."
            ),
            schedule={"type": "manual"},
            timeout_seconds=10,
            retry_policy={"max_retries": 1, "initial_delay_seconds": 0},
        )

        run = await manager.run_now("support_ticket_tck_1042", wait=True)
        events = await manager.get_run_events(run.run_id)
        attempts = await manager.list_attempts(run.run_id)
        task_status = await manager.get_task_status("support_ticket_tck_1042")
        manager_status = await manager.get_manager_status()
        workspace_state = await manager.get_run_workspace(run.run_id)

        result = {
            "run_id": run.run_id,
            "status": run.status.value,
            "session_id": run.session_id,
            "workspace_root": str(workspace_root),
            "workspace_path": run.workspace_path,
            "result_preview": run.result_preview,
            "events": [event["event"] for event in events],
            "attempts": [attempt.model_dump(mode="json") for attempt in attempts],
            "task_status": task_status,
            "manager_status": manager_status,
            "workspace_files": [item["name"] for item in workspace_state["files"]],
            "agent_calls": agent.calls,
        }
    finally:
        await manager.shutdown()
    result["shutdown_status"] = await manager.get_manager_status()
    return result


async def main() -> None:
    result = await run_real_application_background_example()
    print(f"run_id={result['run_id']}")
    print(f"status={result['status']}")
    print(f"session_id={result['session_id']}")
    print(f"workspace_root={result['workspace_root']}")
    print(f"workspace={result['workspace_path']}")
    print(f"attempts={len(result['attempts'])}")
    print(f"runs={result['task_status']['runs']}")
    print(f"completed={result['task_status']['status_counts']['completed']}")
    print(f"manager_worker={result['manager_status']['worker_id']}")
    print(f"files={','.join(result['workspace_files'])}")
    print(f"events={','.join(result['events'])}")
    print(result["result_preview"])


if __name__ == "__main__":
    asyncio.run(main())
