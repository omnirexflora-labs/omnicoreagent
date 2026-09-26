"""E6b: a program pauses for approval and continues where it stopped.

When a tool call inside a program needs approval, the paused program is dumped
to bytes, signed, and stored on the run's record; the run waits like any other.
On resume the program is restored and the call carries the decision, so nothing
the program already did runs again. A stored program that does not verify is
refused.
"""

from __future__ import annotations

import base64
import json

import pytest

pytest.importorskip("pydantic_monty", reason="code mode needs omnicoreagent[codemode]")

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent  # noqa: E402
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry  # noqa: E402
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy  # noqa: E402
from test_run_suspend import RecordingModel  # noqa: E402
from test_execute_tool import _MODEL  # noqa: E402

KEY = "test-snapshot-key"


def _tools(ledger: list):
    tools = ToolRegistry()

    @tools.register_tool("price", description="Price of a product.")
    def price(sku: str) -> dict:
        ledger.append(f"price:{sku}")
        return {"status": "success", "data": {"price": 5}}

    @tools.register_tool("refund", description="Refunds an order.")
    def refund(order: str) -> dict:
        ledger.append(f"refund:{order}")
        return {"status": "success", "data": {"refunded": order}}

    return tools


def _policy():
    policy = build_default_policy("interactive-dev")
    policy.rules.ask.insert(
        0,
        PolicyRule(rule_id="ask_refund", effect=PolicyEffect.ASK, capability="tool.local.call",
                   target={"tool_name": "refund"}),
    )
    return policy


PROGRAM = """
first = price(sku="A")["price"]
outcome = refund(order="o-1")
second = price(sku="B")["price"]
[first, outcome, second]
"""

DENIED_PROGRAM = """
first = price(sku="A")["price"]
try:
    refund(order="o-1")
    note = "refunded"
except Exception as error:
    note = "refused: " + str(error)
[first, note]
"""


async def _agent(model, ledger, *, memory_router=None, key=KEY):
    agent = OmniCoreAgent(
        name="coder",
        system_instruction="Use run_code.",
        model_config=_MODEL,
        local_tools=_tools(ledger),
        memory_router=memory_router,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "code_mode": {"enabled": True, "snapshot_key": key},
            "governance_config": {"enabled": True, "policy": _policy()},
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


def _program_call(code, call_id="k1"):
    return [(call_id, "run_code", json.dumps({"code": code}))]


@pytest.mark.asyncio
async def test_a_program_pauses_for_approval_and_continues_where_it_stopped():
    ledger: list = []
    model = RecordingModel(_program_call(PROGRAM), "all done")
    agent = await _agent(model, ledger)

    paused = await agent.run("refund o-1", session_id="cm", run_id="run_cm")

    assert paused["status"] == "awaiting_approval"
    (approval,) = paused["approvals"]
    assert approval["tool_name"] == "refund" and approval["tool_call_id"].startswith("k1.")
    # Which order: a program's call is not in the conversation, and its
    # approval carried no arguments (docs pass, 2026-09-27).
    assert approval["arguments"] == {"order": "o-1"}
    assert ledger == ["price:A"], "the program stopped at the call that needs approval"
    record = await agent.get_run("run_cm")
    assert record["approvals"][0]["arguments"] == {"order": "o-1"}
    assert record["code_programs"]["k1"]["paused_call_id"] == approval["tool_call_id"]

    await agent.resolve_approval("run_cm", approval["approval_id"], decision="approve", approver="alice")
    result = await agent.resume("run_cm")

    assert result["response"] == "all done"
    assert ledger == ["price:A", "refund:o-1", "price:B"], "nothing before the pause ran again"
    code_result = json.loads(next(m for m in model.calls[-1] if m.get("tool_call_id") == "k1")["content"])
    assert code_result["data"]["result"] == [5, {"refunded": "o-1"}, 5]


@pytest.mark.asyncio
async def test_a_denied_call_is_raised_inside_the_resumed_program():
    ledger: list = []
    model = RecordingModel(_program_call(DENIED_PROGRAM), "left it alone")
    agent = await _agent(model, ledger)
    paused = await agent.run("refund o-1", session_id="cm2", run_id="run_cm2")
    (approval,) = paused["approvals"]

    await agent.resolve_approval(
        "run_cm2", approval["approval_id"], decision="deny", approver="bob", note="not this order"
    )
    await agent.resume("run_cm2")

    assert ledger == ["price:A"]
    code_result = json.loads(next(m for m in model.calls[-1] if m.get("tool_call_id") == "k1")["content"])
    assert code_result["data"]["result"][1].startswith("refused:")
    assert "not this order" in code_result["data"]["result"][1]


@pytest.mark.asyncio
async def test_a_stored_program_that_does_not_verify_is_refused():
    ledger: list = []
    model = RecordingModel(_program_call(PROGRAM), "done")
    agent = await _agent(model, ledger)
    paused = await agent.run("refund o-1", session_id="cm3", run_id="run_cm3")
    (approval,) = paused["approvals"]
    await agent.resolve_approval("run_cm3", approval["approval_id"], decision="approve", approver="a")

    # Someone edits the stored program in the database.
    record = await agent.get_run("run_cm3")
    blob = base64.b64decode(record["code_programs"]["k1"]["snapshot"])
    record["code_programs"]["k1"]["snapshot"] = base64.b64encode(blob[:-5] + b"xxxxx").decode()
    version = record.pop("version")
    await agent.memory_router.save_run_state(record, expected_version=version)

    await agent.resume("run_cm3")

    code_result = json.loads(next(m for m in model.calls[-1] if m.get("tool_call_id") == "k1")["content"])
    assert code_result["status"] == "error" and "could not be verified" in code_result["message"]
    assert ledger == ["price:A"], "the tampered program never ran"


@pytest.mark.asyncio
async def test_a_paused_program_resumes_in_another_process_with_a_configured_key():
    ledger: list = []
    model = RecordingModel(_program_call(PROGRAM), "done")
    agent = await _agent(model, ledger)
    paused = await agent.run("refund o-1", session_id="cm4", run_id="run_cm4")
    (approval,) = paused["approvals"]
    await agent.resolve_approval("run_cm4", approval["approval_id"], decision="approve", approver="a")

    # A second agent, as after a restart: same store, same configured key.
    survivor = await _agent(model, ledger, memory_router=agent.memory_router)
    result = await survivor.resume("run_cm4")

    assert result["response"] == "done"
    assert ledger == ["price:A", "refund:o-1", "price:B"]
