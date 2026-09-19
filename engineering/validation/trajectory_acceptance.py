"""End-to-end trajectory acceptance for one ``agent.run()``.

The telemetry trajectory plan (engineering/architecture/
telemetry-trajectory-completion-plan.md) defines a ten-item checklist: a
reader must be able to see a run's request, harness, every step, every tool
call, every observation, context management, delegation, final answer, run
totals, and every capture gap. This script runs one scripted scenario that
exercises all of it and checks each item from the trajectory reader's output.

Modes:

    python engineering/validation/trajectory_acceptance.py --check-fixture
        Standard library only. Checks the committed, sanitized trajectory and
        portable evidence without importing OmniCoreAgent.

    PYTHONPATH=src .venv/bin/python engineering/validation/trajectory_acceptance.py --run
        Runs the scripted scenario directly (full capture), through OmniServe,
        and with the default privacy-first capture, in a temporary workspace.

    PYTHONPATH=src .venv/bin/python engineering/validation/trajectory_acceptance.py --write-fixtures
        Runs the direct scenario and rewrites the sanitized fixture.

    PYTHONPATH=src .venv/bin/python engineering/validation/trajectory_acceptance.py --live
        Runs a smaller scenario against a real model through LiteLLM. Reads
        LLM_API_KEY and OMNICOREAGENT_TEST_MODEL from the environment or from
        a .env file given with --env-file. The key is never printed or stored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "engineering" / "validation" / "fixtures" / "trajectory-acceptance"
QUERY = "Run the trajectory acceptance scenario."
FINAL_ANSWER = "ACCEPTANCE_COMPLETE"
CHILD_ANSWER = "child found b"
STEP_ONE_OUTCOMES = [
    ("c_ok", "success"),
    ("c_err", "error"),
    ("c_bad", "rejected"),
    ("c_slow", "timeout"),
    ("c_big", "success"),
]
MCP_SERVER = "acceptance_mcp"
MCP_TOOLS = {"weather", "tool_error", "protocol_error", "wait_long"}
# One parallel batch to a real MCP server: every outcome an MCP call can have.
MCP_STEP_OUTCOMES = [
    ("m_ok", "success"),
    ("m_tool_error", "error"),
    ("m_protocol_error", "error"),
    ("m_call_timeout", "error"),
    ("m_bad", "rejected"),
]


# --- checks (standard library only) -----------------------------------------


def trajectory_event_ids(trajectory: dict[str, Any]) -> set[str]:
    """Event IDs a trajectory accounts for, excluding nested child runs."""
    ids: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "child_trajectory":
                    continue
                if key in {"event_id", "model_call_event_id", "response_event_id"} and item:
                    ids.add(item)
                elif key == "event_ids" and isinstance(item, list):
                    ids.update(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit({k: v for k, v in trajectory.items() if k not in {"totals", "capture_gaps"}})
    return ids


def _all_calls(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    return [call for step in trajectory["steps"] for call in step["tool_calls"]]


def _mcp_step(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    return next(
        step["tool_calls"]
        for step in trajectory["steps"]
        if any(call["tool_call_id"] == "m_ok" for call in step["tool_calls"])
    )


def _agent_turns(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        call
        for step in trajectory["steps"]
        for call in step["model_calls"]
        if call["purpose"] == "agent_turn"
    ]


def check_trajectory(
    trajectory: dict[str, Any],
    *,
    surface: str,
    full_capture: bool,
    trace_event_ids: set[str] | None = None,
) -> list[str]:
    """Check every checklist item; return the names of the items checked."""
    checked: list[str] = []
    t = trajectory

    # 1. Request
    assert t["request"] is not None, "request missing"
    if full_capture:
        assert t["request"]["message"] == QUERY, t["request"]
    for key in ("trace_id", "run_id", "session_id"):
        assert t[key], key
    assert t["execution_surface"] == surface, t["execution_surface"]
    checked.append("1 request")

    # 2. Harness
    harness = t["harness"]
    assert harness["model"]["model"], harness["model"]
    assert harness["limits"]["max_steps"] == 12, harness["limits"]
    # The header records the limit the runtime enforced on the tool calls.
    assert harness["limits"]["tool_call_timeout"] == TOOL_CALL_TIMEOUT, harness["limits"]
    assert harness["context_management"]["strategy"] == "summarize_and_truncate"
    assert {"lookup", "explode", "slow", "big_report", "read_artifact", "delegate_researcher"} <= set(
        harness["tools"]["names"]
    ), harness["tools"]["names"]
    assert harness["tools"]["count"] == len(harness["tools"]["names"])
    assert MCP_TOOLS <= set(harness["tools"]["names"]), harness["tools"]["names"]
    [mcp_server] = harness["mcp_servers"]
    assert mcp_server["name"] == MCP_SERVER and mcp_server["status"] == "connected", mcp_server
    assert mcp_server["server_info"] == {"name": "acceptance-mcp", "version": "1.0.0"}, mcp_server
    assert mcp_server["protocol_version"] and mcp_server["tool_count"] == len(MCP_TOOLS), mcp_server
    assert harness["system_prompt"]["digest"]
    assert harness["fingerprints"]["privacy"] and harness["fingerprints"]["telemetry"]
    for version in ("agent_version", "prompt_version", "tool_schema_version", "memory_config_version"):
        assert t["versions"][version], version
    checked.append("2 harness")

    # 3. Steps and model calls
    assert [step["step"] for step in t["steps"]] == [1, 2, 3, 4, 5, 6], [s["step"] for s in t["steps"]]
    for call in _agent_turns(t):
        facts = call["facts"]
        assert facts["tokens"]["total"] > 0, facts
        assert facts["finish_reason"], facts
        assert facts["latency_ms"] is not None and facts["attempts"] >= 1, facts
        if full_capture:
            assert call["response"] is not None and call["request"]["messages"], call
        else:
            assert call["response_capture"]["state"] == "not_recorded", call
    first_call = _agent_turns(t)[0]
    if full_capture:
        raw = {tc["function"]["name"]: tc["function"]["arguments"] for tc in first_call["response"]["tool_calls"]}
        assert raw["slow"] == "{}", raw
    checked.append("3 steps")

    # 4. Tool calls
    step_one = t["steps"][0]["tool_calls"]
    assert [(c["tool_call_id"], c["outcome"]) for c in step_one] == STEP_ONE_OUTCOMES, [
        (c["tool_call_id"], c["outcome"]) for c in step_one
    ]
    malformed = next(c for c in step_one if c["tool_call_id"] == "c_bad")
    assert malformed["raw_arguments"] == "{broken", malformed
    assert malformed["rejection_reason"] == "invalid_arguments", malformed
    failed = next(c for c in step_one if c["tool_call_id"] == "c_err")
    # If this ever fails, say whether the trace lost a write (incomplete).
    assert failed["error"] and failed["error"]["message"], (failed, t["incomplete"])
    mcp_step = _mcp_step(t)
    assert [(c["tool_call_id"], c["outcome"]) for c in mcp_step] == MCP_STEP_OUTCOMES, [
        (c["tool_call_id"], c["outcome"]) for c in mcp_step
    ]
    mcp = {c["tool_call_id"]: c for c in mcp_step}
    for call_id in ("m_ok", "m_tool_error", "m_protocol_error", "m_call_timeout"):
        assert (mcp[call_id]["provider"], mcp[call_id]["server"]) == ("mcp", MCP_SERVER), mcp[call_id]
    assert "weather service unavailable" in mcp["m_tool_error"]["error"]["message"], mcp["m_tool_error"]
    assert "MCP error -32602" in mcp["m_protocol_error"]["error"]["message"], mcp["m_protocol_error"]
    assert "MCP error -32001" in mcp["m_call_timeout"]["error"]["message"], mcp["m_call_timeout"]
    assert mcp["m_bad"]["raw_arguments"] == "{broken", mcp["m_bad"]
    assert mcp["m_bad"]["rejection_reason"] == "invalid_arguments", mcp["m_bad"]
    if full_capture:
        assert '"temp": 31' in mcp["m_ok"]["observation"]["content"], mcp["m_ok"]["observation"]
    checked.append("4 tool calls")

    # 5. Observations reach the next model call exactly
    observation_ids = [c["observation"]["event_id"] for c in step_one]
    second_turn = _agent_turns(t)[1]
    assert second_turn["new_observation_event_ids"] == observation_ids, (
        second_turn["new_observation_event_ids"],
        observation_ids,
    )
    if full_capture:
        sent = {
            message.get("tool_call_id"): message.get("content")
            for message in second_turn["request"]["messages"]
            if message.get("role") == "tool"
        }
        for call in step_one:
            assert sent[call["tool_call_id"]] == call["observation"]["content"], call["tool_call_id"]
    mcp_observations = [c["observation"]["event_id"] for c in _mcp_step(t)]
    final_turn = _agent_turns(t)[-1]
    assert final_turn["new_observation_event_ids"] == mcp_observations, (
        final_turn["new_observation_event_ids"],
        mcp_observations,
    )
    checked.append("5 observations")

    # 6. Context management
    kinds = [message["kind"] for message in t["runtime_messages"]]
    assert kinds == ["current_datetime"], kinds
    assert [m["kind"] for m in t["steps"][1]["runtime_messages"]] == ["empty_response_retry"]
    compressions = [
        context for step in t["steps"] for context in step["context"] if context["type"] == "context_compression"
    ]
    assert compressions, "no context compression recorded"
    purposes = [call["purpose"] for step in t["steps"] for call in step["model_calls"]]
    assert "context_summary" in purposes, purposes
    assert t["totals"]["offloaded_results"], "no offloaded result recorded"
    read = next(c for c in _all_calls(t) if c["tool_name"] == "read_artifact")
    assert read["outcome"] == "success", read
    checked.append("6 context management")

    # 7. Delegation
    delegation = next(c for c in _all_calls(t) if c["subagent"])
    assert delegation["provider"] == "subagent", delegation["provider"]
    assert delegation["outcome"] == "success", delegation["outcome"]
    child = delegation["subagent"]["child_trajectory"]
    assert child is not None, "child trajectory missing"
    assert child["parent_trace_id"] == t["trace_id"]
    assert child["trace_id"] == delegation["subagent"]["child_trace_id"]
    assert [c["outcome"] for c in _all_calls(child)] == ["success"]
    if full_capture:
        assert child["final"]["output"]["response"] == CHILD_ANSWER
    checked.append("7 delegation")

    # 8. Final answer
    assert t["status"] == "completed", t["status"]
    assert t["final"]["type"] == "final_answer", t["final"]
    if full_capture:
        assert t["final"]["output"]["response"] == FINAL_ANSWER, t["final"]
    last_turn = _agent_turns(t)[-1]
    assert t["final"]["final_model_response_event_id"] == last_turn["response_event_id"]
    checked.append("8 final")

    # 9. Run totals
    totals = t["totals"]
    assert totals["steps"] == 6, totals["steps"]
    assert totals["model_calls"]["agent_turn"] == 6, totals["model_calls"]
    assert totals["model_calls"]["context_summary"] >= 1, totals["model_calls"]
    assert totals["tokens"]["total"] == sum(
        call["facts"]["tokens"]["total"]
        for step in t["steps"]
        for call in step["model_calls"]
        if call.get("facts") and call["facts"].get("tokens")
    ), totals["tokens"]
    assert totals["estimated_cost_usd"] and totals["cost_complete"], totals
    outcomes = totals["tool_calls"]["by_outcome"]
    # Local and MCP together: step 1, the artifact read, the delegation, and the MCP step.
    assert (outcomes["success"], outcomes["error"], outcomes["rejected"], outcomes["timeout"]) == (5, 4, 2, 1), outcomes
    assert totals["including_subagents"]["tokens"]["total"] > totals["tokens"]["total"]
    assert totals["duration_ms"] > 0
    checked.append("9 totals")

    # 10. Honesty
    if full_capture:
        assert t["evidence_status"] == "complete", (t["evidence_status"], t["capture_gaps"][:3])
        assert t["capture_gaps"] == [], t["capture_gaps"][:3]
    else:
        assert t["evidence_status"] == "partial", t["evidence_status"]
        assert any(gap["state"] == "not_recorded" for gap in t["capture_gaps"])
    if trace_event_ids is not None:
        assert trajectory_event_ids(t) == trace_event_ids, "trajectory does not account for every event"
    checked.append("10 honesty")
    return checked


def check_fixture() -> None:
    """Check the committed fixture using only the standard library."""
    trajectory = json.loads((FIXTURE_DIR / "trajectory.json").read_text())
    evidence = json.loads((FIXTURE_DIR / "evidence.json").read_text())
    assert evidence["contract"] == "omnicoreagent.execution-evidence/v1"
    assert evidence["trace"]["trace_id"] == trajectory["trace_id"]
    event_ids = {event["event_id"] for event in evidence["trace"]["events"]}
    checked = check_trajectory(
        trajectory, surface="interactive", full_capture=True, trace_event_ids=event_ids
    )
    jsonl = (FIXTURE_DIR / "traces.jsonl").read_text().splitlines()
    persisted = {
        json.loads(line)["payload"].get("event_id")
        for line in jsonl
        if json.loads(line)["record_type"] == "event"
    }
    assert event_ids <= persisted, "fixture JSONL lacks events of the trace"
    print(f"checked committed trajectory fixture: {len(checked)} checklist items")


# --- scenario (requires OmniCoreAgent) --------------------------------------


def _usage(input_tokens: int, output_tokens: int):
    from omnicoreagent.core.token_usage import Usage

    return Usage(
        requests=1,
        request_tokens=input_tokens,
        response_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


class LeadModel:
    """Scripted lead: parallel batch, empty reply, artifact read, delegation, MCP batch, answer."""

    def __init__(self) -> None:
        self.turn = 0

    def estimate_cost(self, usage) -> float:
        return usage.total_tokens * 1e-6

    async def llm_call(self, messages, tools=None, **kwargs):
        from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest

        if messages and isinstance(messages[0], dict):
            return ModelTurn(content="Summary of earlier work.", finish_reason="stop", usage=_usage(50, 8))
        self.turn += 1
        if self.turn == 1:
            calls = [
                ("c_ok", "lookup", '{"key": "a"}'),
                ("c_err", "explode", "{}"),
                ("c_bad", "lookup", "{broken"),
                ("c_slow", "slow", "{}"),
                ("c_big", "big_report", "{}"),
            ]
        elif self.turn == 2:
            return ModelTurn(content="", finish_reason="stop", usage=_usage(200, 1))
        elif self.turn == 3:
            transcript = "\n".join(
                str(
                    (message.get("content") if isinstance(message, dict) else None)
                    or getattr(message, "content", None)
                    or ""
                )
                for message in messages
            )
            match = re.search(r"Artifact ID: ([\w]+)", transcript)
            if match is None:
                raise AssertionError("offloaded artifact ID was not delivered to the model")
            calls = [("c_read", "read_artifact", json.dumps({"artifact_id": match.group(1)}))]
        elif self.turn == 4:
            calls = [("c_child", "delegate_researcher", '{"query": "check key b"}')]
        elif self.turn == 5:
            calls = [
                ("m_ok", "weather", '{"city": "Lagos"}'),
                ("m_tool_error", "tool_error", "{}"),
                ("m_protocol_error", "protocol_error", "{}"),
                ("m_call_timeout", "wait_long", "{}"),
                ("m_bad", "weather", "{broken"),
            ]
        else:
            return ModelTurn(content=FINAL_ANSWER, finish_reason="stop", usage=_usage(300, 4))
        return ModelTurn(
            tool_calls=tuple(ToolRequest(*call) for call in calls),
            finish_reason="tool_calls",
            usage=_usage(100 * self.turn, 10),
        )


class ChildModel:
    def __init__(self) -> None:
        self.turn = 0

    def estimate_cost(self, usage) -> float:
        return usage.total_tokens * 1e-6

    async def llm_call(self, messages, tools=None, **kwargs):
        from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest

        self.turn += 1
        if self.turn == 1:
            return ModelTurn(
                tool_calls=(ToolRequest("cc_1", "lookup", '{"key": "b"}'),),
                finish_reason="tool_calls",
                usage=_usage(40, 5),
            )
        return ModelTurn(content=CHILD_ANSWER, finish_reason="stop", usage=_usage(60, 4))


def _tools():
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Look up a value by key.")
    def lookup(key: str) -> dict:
        return {"key": key, "value": 42}

    @tools.register_tool("explode", description="Always fails.")
    def explode() -> dict:
        raise RuntimeError("synthetic tool failure")

    @tools.register_tool("slow", description="Takes longer than the tool time limit.")
    async def slow() -> dict:
        await asyncio.sleep(TOOL_CALL_TIMEOUT * 20)
        return {}

    @tools.register_tool("big_report", description="Return a large report.")
    def big_report() -> dict:
        return {"marker": "BIG_MARKER", "lines": [f"line {i} " * 4 for i in range(40)]}

    return tools


_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "scripted"}
# Long enough for the delegated child run on a heavily loaded machine (3 s
# timed out at load average 10); `slow` far exceeds it.
TOOL_CALL_TIMEOUT = 10

_AGENT_CONFIG = {
    "guardrail_mode": "off",
    "max_steps": 12,
    "tool_call_timeout": TOOL_CALL_TIMEOUT,
    "context_management": {
        "enabled": True,
        "mode": "sliding_window",
        "value": 8,
        "threshold_percent": 50,
        "strategy": "summarize_and_truncate",
        "preserve_recent": 4,
    },
    "tool_offload": {"enabled": True, "threshold_tokens": 50, "threshold_bytes": 500},
}


async def build_scripted_agent(*, full_capture: bool):
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

    telemetry = {"capture": "full"} if full_capture else {}
    child = OmniCoreAgent(
        name="researcher",
        system_instruction="You research one fact.",
        model_config=_MODEL,
        local_tools=_tools(),
        agent_config={"guardrail_mode": "off"},
        telemetry_config=telemetry,
    )
    await child.initialize()
    child.llm_connection = ChildModel()
    lead = OmniCoreAgent(
        name="lead",
        system_instruction="You lead the acceptance scenario.",
        model_config=_MODEL,
        local_tools=_tools(),
        mcp_tools=[
            {
                "name": MCP_SERVER,
                "transport_type": "stdio",
                "command": sys.executable,
                "args": [str(ROOT / "engineering" / "validation" / "fixtures" / "acceptance_mcp_server.py")],
                # Well below the agent's tool limit, so the MCP error is what stops it.
                "call_timeout": 2,
            }
        ],
        sub_agents=[child],
        agent_config=_AGENT_CONFIG,
        telemetry_config=telemetry,
    )
    await lead.initialize()
    lead.llm_connection = LeadModel()
    return lead


async def run_direct(*, full_capture: bool) -> tuple[Any, dict[str, Any], Any]:
    agent = await build_scripted_agent(full_capture=full_capture)
    # MCP connections belong to the event loop that uses them.
    await agent.connect_mcp_servers()
    try:
        result = await agent.run(QUERY, session_id="trajectory-acceptance")
        trace = await agent.telemetry_store.get_trace(result["trace_id"])
        return agent, await agent.get_trajectory(result["trace_id"]), trace
    finally:
        await agent.cleanup_mcp_servers()


def run_served() -> tuple[dict[str, Any], Any]:
    from fastapi.testclient import TestClient

    from omnicoreagent.serve import OmniServe, OmniServeConfig

    agent = asyncio.run(build_scripted_agent(full_capture=True))
    app = OmniServe(agent=agent, config=OmniServeConfig(background_enabled=False)).app
    # The app's startup connects the MCP server on the serving loop.
    with TestClient(app) as client:
        response = client.post("/run/sync", json={"query": QUERY, "session_id": "trajectory-served"})
        assert response.status_code == 200, response.text
        trajectory = client.get(f"/telemetry/runs/{response.json()['run_id']}/trajectory").json()
    trace = asyncio.run(agent.telemetry_store.get_trace(trajectory["trace_id"]))
    return trajectory, trace


def run_all_scripted() -> None:
    with tempfile.TemporaryDirectory(prefix="omni-trajectory-acceptance-") as workspace:
        os.environ["OMNICOREAGENT_WORKSPACE_DIR"] = workspace
        for full_capture in (True, False):
            _, trajectory, trace = asyncio.run(run_direct(full_capture=full_capture))
            checked = check_trajectory(
                trajectory,
                surface="interactive",
                full_capture=full_capture,
                trace_event_ids={event.event_id for event in trace.events},
            )
            label = "full" if full_capture else "default"
            print(f"direct run ({label} capture): {len(checked)} checklist items passed")
        trajectory, trace = run_served()
        checked = check_trajectory(
            trajectory,
            surface="serve",
            full_capture=True,
            trace_event_ids={event.event_id for event in trace.events},
        )
        print(f"served run (full capture): {len(checked)} checklist items passed")


# --- fixture ------------------------------------------------------------------

_ID = re.compile(r"\b(trace|span|event|run|telemetry_record)_[0-9a-f]{32}\b")


def _sanitizer(texts: list[str], workspace: str):
    aliases: dict[str, str] = {}
    counters: dict[str, int] = {}
    for text in texts:
        for match in _ID.finditer(text):
            value = match.group(0)
            if value not in aliases:
                kind = match.group(1)
                counters[kind] = counters.get(kind, 0) + 1
                aliases[value] = f"{kind}_{counters[kind]:04d}"

    def sanitize(text: str) -> str:
        text = _ID.sub(lambda match: aliases[match.group(0)], text)
        return text.replace(workspace, "<workspace>")

    return sanitize


def write_fixtures() -> None:
    from omnicoreagent.core.telemetry import OmniCoreEvidenceAdapter

    with tempfile.TemporaryDirectory(prefix="omni-trajectory-fixture-") as workspace:
        os.environ["OMNICOREAGENT_WORKSPACE_DIR"] = workspace
        agent, trajectory, trace = asyncio.run(run_direct(full_capture=True))
        check_trajectory(
            trajectory,
            surface="interactive",
            full_capture=True,
            trace_event_ids={event.event_id for event in trace.events},
        )
        evidence = OmniCoreEvidenceAdapter().import_trace(trace).model_dump()
        texts = {
            "trajectory.json": json.dumps(trajectory, indent=2, sort_keys=True, default=str) + "\n",
            "evidence.json": json.dumps(evidence, indent=2, sort_keys=True, default=str) + "\n",
            "traces.jsonl": Path(agent.telemetry_store.path).read_text(),
        }
        sanitize = _sanitizer(list(texts.values()), workspace)
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        for name, text in texts.items():
            (FIXTURE_DIR / name).write_text(sanitize(text))
    check_fixture()


# --- live ---------------------------------------------------------------------


def _load_env(env_file: str | None) -> tuple[str, str]:
    values = dict(os.environ)
    if env_file:
        for line in Path(env_file).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    key, model = values.get("LLM_API_KEY"), values.get("OMNICOREAGENT_TEST_MODEL")
    if not key or not model:
        raise SystemExit("--live needs LLM_API_KEY and OMNICOREAGENT_TEST_MODEL")
    return key, model


def run_live(env_file: str | None) -> dict[str, Any]:
    """A real model through LiteLLM: checks the facts only a provider supplies."""
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
    from omnicoreagent.core.telemetry import OmniCoreEvidenceAdapter, validate_portable_evidence_document
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    key, model = _load_env(env_file)
    tools = ToolRegistry()

    @tools.register_tool("lookup_order", description="Look up an order status by id.")
    def lookup_order(order_id: str) -> dict:
        return {"order_id": order_id, "status": "shipped"}

    async def scenario() -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="omni-trajectory-live-") as workspace:
            os.environ["OMNICOREAGENT_WORKSPACE_DIR"] = workspace
            agent = OmniCoreAgent(
                name="live-acceptance",
                system_instruction="Use the lookup_order tool, then answer in one short sentence.",
                model_config={"provider": "openai", "model": model, "api_key": key},
                local_tools=tools,
                agent_config={"guardrail_mode": "off", "max_steps": 4},
                telemetry_config={"capture": "full"},
            )
            result = await agent.run("What is the status of order A-17?", session_id="live-acceptance")
            trace = await agent.telemetry_store.get_trace(result["trace_id"])
            trajectory = await agent.get_trajectory(result["trace_id"])
            evidence = OmniCoreEvidenceAdapter().import_trace(trace).model_dump()
            validate_portable_evidence_document(evidence)
            dump = json.dumps(trace.model_dump(), default=str)
            turns = _agent_turns(trajectory)
            calls = _all_calls(trajectory)
            assert key not in dump, "API key found in the trace"
            assert trajectory["status"] == "completed"
            assert trajectory_event_ids(trajectory) == {e.event_id for e in trace.events}
            assert calls and calls[0]["outcome"] == "success", calls
            assert turns[1]["new_observation_event_ids"] == [calls[0]["observation"]["event_id"]]
            for turn in turns:
                facts = turn["facts"]
                assert facts["tokens"]["total"] > 0 and facts["provider_response_id"], facts
                assert facts["estimated_cost_usd"] and facts["cost_source"], facts
            totals = trajectory["totals"]
            return {
                "model": turns[0]["facts"]["provider_model"],
                "steps": totals["steps"],
                "tool_calls": totals["tool_calls"]["by_outcome"],
                "tokens": totals["tokens"],
                "estimated_cost_usd": totals["estimated_cost_usd"],
                "cost_complete": totals["cost_complete"],
                "provider_response_ids": len({t["facts"]["provider_response_id"] for t in turns}),
                "final_answer": trajectory["final"]["output"]["response"],
                "events_accounted": len(trace.events),
                "evidence_status": trajectory["evidence_status"],
                "capture_gaps": [
                    {
                        "type": gap["type"],
                        "state": gap["state"],
                        "record": next(
                            (
                                getattr(record, "event_type", None) or record.kind
                                for record in (*trace.events, *trace.spans)
                                if gap["id"] in (getattr(record, "event_id", None), getattr(record, "span_id", None))
                            ),
                            None,
                        ),
                    }
                    for gap in trajectory["capture_gaps"]
                ],
                "api_key_in_trace": False,
            }

    return asyncio.run(scenario())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-fixture", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--write-fixtures", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file")
    args = parser.parse_args()
    if args.run:
        run_all_scripted()
    elif args.write_fixtures:
        write_fixtures()
    elif args.live:
        print(json.dumps(run_live(args.env_file), indent=2))
    else:
        check_fixture()


if __name__ == "__main__":
    sys.exit(main())
