"""E6a: code mode, a `run_code` tool running Python in Monty.

The model writes a short Python program; chosen tools are functions it can
call. The program runs in Monty (a Python subset in a separate worker process)
with no filesystem, network, or environment access, under time, memory, and
call limits. Every tool call from code goes through the same governed path as
a direct call (policy, write-ahead run record, telemetry), and appears nested
under the `run_code` call in the trajectory.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("pydantic_monty", reason="code mode needs omnicoreagent[codemode]")

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent  # noqa: E402
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry  # noqa: E402
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy  # noqa: E402
from test_run_suspend import RecordingModel  # noqa: E402
from test_execute_tool import _MODEL  # noqa: E402


def _tools(calls: list):
    tools = ToolRegistry()

    @tools.register_tool("price", description="Price of a product.")
    def price(sku: str) -> dict:
        calls.append(("price", sku))
        return {"status": "success", "data": {"sku": sku, "price": {"A": 3, "B": 4}[sku]}}

    @tools.register_tool("refund", description="Refunds an order.")
    def refund(order: str) -> dict:
        calls.append(("refund", order))
        return {"status": "success", "data": {"refunded": order}}

    return tools


async def _agent(model, calls, *, code_mode=None, governance=None):
    config = {
        "guardrail_mode": "off",
        "enable_workspace_files": False,
        "code_mode": {"enabled": True, **(code_mode or {})},
    }
    if governance is not None:
        config["governance_config"] = governance
    agent = OmniCoreAgent(
        name="coder",
        system_instruction="Use run_code.",
        model_config=_MODEL,
        local_tools=_tools(calls),
        agent_config=config,
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


def _code_call(code: str, call_id="k1"):
    return [(call_id, "run_code", json.dumps({"code": code}))]


def _result(model, call_id="k1"):
    message = next(m for m in model.calls[-1] if m.get("tool_call_id") == call_id)
    return json.loads(message["content"])


PROGRAM = """
total = 0
for sku in ["A", "B"]:
    total += price(sku=sku)["price"]
print("summed", total)
total
"""


@pytest.mark.asyncio
async def test_a_program_calls_tools_and_returns_its_value_and_output():
    calls: list = []
    model = RecordingModel(_code_call(PROGRAM), "the total is 7")
    agent = await _agent(model, calls)

    result = await agent.run("what do A and B cost together?", session_id="code")
    code_result = _result(model)

    assert result["response"] == "the total is 7"
    assert code_result["status"] == "success"
    assert code_result["data"]["result"] == 7
    assert "summed 7" in code_result["data"]["output"]
    assert code_result["data"]["tool_calls"] == 2
    assert calls == [("price", "A"), ("price", "B")]
    assert "run_code" in model.tools_offered[0]


@pytest.mark.asyncio
async def test_every_call_from_code_is_governed_and_nested_in_the_trajectory():
    calls: list = []
    policy = build_default_policy("interactive-dev")
    policy.rules.deny.insert(
        0,
        PolicyRule(rule_id="no_refunds", effect=PolicyEffect.DENY, capability="tool.local.call",
                   target={"tool_name": "refund"}),
    )
    program = """
try:
    refund(order="o-1")
    outcome = "refunded"
except Exception as error:
    outcome = "refused: " + str(error)
price(sku="A")
outcome
"""
    model = RecordingModel(_code_call(program), "done")
    agent = await _agent(model, calls, governance={"enabled": True, "policy": policy})

    result = await agent.run("refund o-1", session_id="gov")
    code_result = _result(model)

    assert calls == [("price", "A")], "the denied refund never ran"
    assert code_result["data"]["result"].startswith("refused:")
    trajectory = await agent.get_trajectory(result["trace_id"])
    (run_code,) = [c for s in trajectory["steps"] for c in s["tool_calls"]]
    assert run_code["tool_name"] == "run_code"
    inner = {c["tool_name"]: c for c in run_code["code_calls"]}
    assert inner["refund"]["outcome"] == "denied" and inner["price"]["outcome"] == "success"
    assert all(c["tool_call_id"].startswith("k1.") for c in run_code["code_calls"])
    record = await agent.get_run(result["run_id"])
    parents = {c["tool_call_id"]: c.get("parent_tool_call_id") for c in record["tool_calls"]}
    assert parents["k1.1"] == "k1" and parents["k1.2"] == "k1"


@pytest.mark.asyncio
async def test_code_has_no_filesystem_and_only_the_chosen_tools():
    calls: list = []
    model = RecordingModel(
        _code_call("open('/etc/hostname').read()", "k1"),
        _code_call("refund(order='o-1')", "k2"),
        "done",
    )
    agent = await _agent(model, calls, code_mode={"tools": ["price"]})

    await agent.run("try things", session_id="limits")
    no_file = json.loads(next(m for m in model.calls[1] if m.get("tool_call_id") == "k1")["content"])
    no_tool = _result(model, "k2")

    assert no_file["status"] == "error" and "PermissionError" in no_file["message"]
    assert no_tool["status"] == "error" and "NameError" in no_tool["message"]
    assert calls == []


@pytest.mark.asyncio
async def test_a_program_is_stopped_at_its_limits():
    calls: list = []
    model = RecordingModel(
        _code_call("while True:\n    pass", "k1"),
        _code_call("for i in range(5):\n    price(sku='A')", "k2"),
        "done",
    )
    agent = await _agent(model, calls, code_mode={"max_duration_seconds": 0.5, "max_tool_calls": 2})

    await agent.run("spin", session_id="spin")
    timed = json.loads(next(m for m in model.calls[1] if m.get("tool_call_id") == "k1")["content"])
    too_many = _result(model, "k2")

    assert timed["status"] == "error" and "time limit" in timed["message"]
    assert too_many["status"] == "error" and "at most 2 tool calls" in too_many["message"]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_run_code_is_offered_only_when_code_mode_is_enabled():
    model = RecordingModel("hi")
    agent = OmniCoreAgent(
        name="plain", system_instruction="x", model_config=_MODEL, local_tools=_tools([]),
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    await agent.initialize()
    agent.llm_connection = model

    await agent.run("hi", session_id="plain")

    assert "run_code" not in model.tools_offered[0]


def test_run_code_is_governed_as_its_own_capability():
    from omnicoreagent.governance.capabilities import tool_authority_requests

    (request,) = tool_authority_requests(tool_name="run_code", tool_args={"code": "1"}, tool_provider="code")

    assert (request.capability, request.execution_surface, request.risk_level) == ("code.run", "code", "medium")
