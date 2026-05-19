"""Support operations agent built on OmniCoreAgent."""

from __future__ import annotations

import asyncio
from pathlib import Path

from omnicoreagent import MemoryRouter, OmniCoreAgent, ToolRegistry

from _bootstrap import model_config, require_llm_api_key, response_text


def build_support_tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("lookup_customer")
    def lookup_customer(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "name": "Ada Ventures",
            "plan": "enterprise",
            "region": "EMEA",
            "account_health": "green",
        }

    @tools.register_tool("recent_orders")
    def recent_orders(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "orders": [
                {"id": "ord-1001", "status": "delivered", "value": 4200},
                {"id": "ord-1002", "status": "delayed", "value": 1800},
            ],
        }

    @tools.register_tool("support_policy_search")
    def support_policy_search(query: str) -> dict:
        return {
            "query": query,
            "policy": (
                "Enterprise delayed shipments above $1000 require a support note, "
                "customer-visible timeline, and optional goodwill credit review."
            ),
        }

    @tools.register_tool("create_escalation")
    def create_escalation(ticket_id: str, severity: str, summary: str) -> dict:
        return {
            "ticket_id": ticket_id,
            "severity": severity,
            "summary": summary,
            "status": "queued_for_specialist",
        }

    return tools


def build_agent(
    workspace_dir: Path | str = "tmp/real_apps/support_ops",
) -> OmniCoreAgent:
    return OmniCoreAgent(
        name="support_operations_agent",
        system_instruction="""You are a support operations agent.
Use customer, order, and policy tools together when possible. Keep a durable
ticket note in workspace files before returning the customer-facing plan.""",
        model_config=model_config(max_tokens=1100),
        local_tools=build_support_tools(),
        memory_router=MemoryRouter("in_memory"),
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

    agent = build_agent()
    result = await agent.run(
        "Handle ticket tck-1042 for customer cust-001. The customer says order ord-1002 "
        "is delayed and wants a clear plan. Gather the account, order, and policy context, "
        "create an escalation if needed, and save notes at tickets/tck-1042.md.",
        session_id="real_app_support_ops",
    )

    print(response_text(result))
    print(f"trace_id={result['trace_id']}")
    print(f"run_id={result['run_id']}")
    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
