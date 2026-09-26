"""Full capture records what it says it records (stranger test S3, 2026-09-26).

A code runner built from the docs alone ran under governance with
`capture="full"` and found every tool result's `args` replaced by
`[REDACTED]`, and no arguments at all on the calls a `run_code` program made.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent import OmniCoreAgent, ToolRegistry
from test_telemetry_tool_record import ScriptedModel

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("get_order_total")
    def get_order_total(customer_id: str) -> dict:
        """The order total for a customer."""
        return {"customer_id": customer_id, "total": 3}

    return tools


async def _governed(capture: str, model) -> OmniCoreAgent:
    agent = OmniCoreAgent(
        name="evidence",
        system_instruction="x",
        model_config=MODEL,
        local_tools=_tools(),
        telemetry_config={"capture": capture},
        agent_config={
            "governance_config": {"enabled": True, "profile": "interactive-dev"},
            "enable_workspace_files": False,
            "guardrail_mode": "off",
        },
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize(("capture", "expected"), [("full", {"customer_id": "C-7"}), ("default", "[REDACTED]")])
async def test_a_governed_call_keeps_its_arguments_at_full_capture(capture, expected, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = await _governed(capture, ScriptedModel(("c1", "get_order_total", '{"customer_id": "C-7"}')))
    result = await agent.run("go")
    (call,) = (await agent.get_trajectory(result["trace_id"]))["steps"][0]["tool_calls"]
    await agent.cleanup()

    assert call["result"]["args"] == expected


@pytest.mark.asyncio
async def test_a_model_call_a_budget_stopped_has_every_field_and_says_so():
    """The observability page's loop read `call["facts"]["tokens"]` and raised
    KeyError on a model call a budget had stopped before it was made."""
    from test_budget_enforcement import PricedModel, _agent as _budgeted

    agent = await _budgeted(
        PricedModel(), budgets={"request": [{"meter": "model_cost_usd", "limit": 0.000001}]}
    )
    result = await agent.run("go")
    assert result["status"] == "awaiting_budget"

    trajectory = await agent.get_trajectory(result["trace_id"])
    (call,) = [c for step in trajectory["steps"] for c in step["model_calls"]]
    assert call["outcome"] == "no_response"
    assert call["facts"] is None and call["response"] is None and call["error"] is None


@pytest.mark.asyncio
async def test_a_call_that_asks_while_it_runs_is_awaiting_approval_not_an_error(tmp_path, monkeypatch):
    """A sandbox command asked to turn the network on while it ran; the run
    paused for a person, but the call was counted as an `error`."""
    from omnicoreagent.governance.errors import ApprovalRequiredError

    monkeypatch.chdir(tmp_path)
    tools = ToolRegistry()

    @tools.register_tool("fetch")
    async def fetch(url: str) -> dict:
        """Fetch a URL."""
        # What a sandbox does when a command asks for the network: the ask
        # is recorded on the run for this call, and the command fails.
        from omnicoreagent.core.runs import current_run
        from omnicoreagent.governance.calls import current_tool_call

        await current_run().add_approval(
            {
                "approval_id": "approval_net",
                "request_digest": "digest",
                "status": "pending",
                "capability": "sandbox.network.configure",
                "tool_call_id": current_tool_call().tool_call_id,
                "tool_name": "fetch",
            }
        )
        raise ApprovalRequiredError("Matched ask policy rule.")

    agent = OmniCoreAgent(
        name="asker",
        system_instruction="x",
        model_config=MODEL,
        local_tools=tools,
        telemetry_config={"capture": "full"},
        agent_config={
            "governance_config": {"enabled": True, "profile": "interactive-dev"},
            "enable_workspace_files": False,
            "guardrail_mode": "off",
        },
    )
    await agent.initialize()
    agent.llm_connection = ScriptedModel(("c1", "fetch", '{"url": "https://example.com"}'))
    result = await agent.run("go")
    trajectory = await agent.get_trajectory(result["trace_id"])
    await agent.cleanup()

    (call,) = [c for step in trajectory["steps"] for c in step["tool_calls"]]
    assert call["outcome"] == "awaiting_approval"


@pytest.mark.asyncio
async def test_arguments_that_cannot_be_read_are_rejected_not_denied(tmp_path, monkeypatch):
    """Round two: `glob` with `path: ""` came back as `denied`, "Governance
    denied tool execution: path must be a non-empty string", though no rule
    decided it."""
    monkeypatch.chdir(tmp_path)
    agent = OmniCoreAgent(
        name="reader",
        system_instruction="x",
        model_config=MODEL,
        telemetry_config={"capture": "full"},
        agent_config={
            "governance_config": {"enabled": True, "profile": "interactive-dev"},
            "guardrail_mode": "off",
        },
    )
    await agent.initialize()
    agent.llm_connection = ScriptedModel(("c1", "glob", '{"pattern": "*.py", "path": ""}'))
    result = await agent.run("list python files")
    trajectory = await agent.get_trajectory(result["trace_id"])
    await agent.cleanup()

    (call,) = [c for step in trajectory["steps"] for c in step["tool_calls"]]
    assert call["outcome"] == "rejected"
    assert "Governance denied" not in json.dumps(call["result"])
    assert "Invalid arguments" in call["result"]["message"]
