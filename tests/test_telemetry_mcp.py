"""MCP evidence in the trace: server status, identity, calls, and reconnects.

Every server here is a real MCP 2 server (the probe and lifecycle fixtures).
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.token_usage import Usage

FIXTURES = Path(__file__).parent / "fixtures"
PROBE = str(FIXTURES / "mcp_probe_server.py")
LIFECYCLE = str(FIXTURES / "mcp_lifecycle_server.py")
_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


def _stdio(name: str, script: str = PROBE, **extra) -> dict:
    args = [script, "stdio"] if script == PROBE else [script]
    return {
        "name": name,
        "transport_type": "stdio",
        "command": sys.executable,
        "args": [*args, f"--test-tag={uuid.uuid4().hex}"],
        **extra,
    }


def _pids(server: dict) -> list[int]:
    found = subprocess.run(
        ["pgrep", "-f", "--", server["args"][-1]], capture_output=True, text=True
    )
    return [int(pid) for pid in found.stdout.split()]


class OneCallModel:
    """Calls one MCP tool, then answers."""

    def __init__(self, tool: str, arguments: str) -> None:
        self.tool, self.arguments, self.turn = tool, arguments, 0

    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        self.turn += 1
        usage = Usage(requests=1, request_tokens=10, response_tokens=2, total_tokens=12)
        if self.turn % 2:
            call = ToolRequest(f"call_{self.turn}", self.tool, self.arguments)
            return ModelTurn(tool_calls=(call,), finish_reason="tool_calls", usage=usage)
        return ModelTurn(content="done", finish_reason="stop", usage=usage)


async def _agent(servers, model) -> OmniCoreAgent:
    agent = OmniCoreAgent(
        name="mcp-telemetry",
        system_instruction="You use MCP tools.",
        model_config=_MODEL,
        mcp_tools=servers,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    await asyncio.wait_for(agent.connect_mcp_servers(), 60)
    agent.llm_connection = model
    return agent


async def _run(agent, session_id="mcp"):
    result = await asyncio.wait_for(agent.run("go", session_id=session_id), 60)
    return await agent.get_trajectory(result["trace_id"])


@pytest.mark.asyncio
async def test_run_header_lists_each_mcp_server_with_its_status():
    servers = [
        _stdio("weather", env={"API_TOKEN": "secret-token-value"}),
        {"name": "broken", "transport_type": "stdio", "command": "/nonexistent/mcp-server"},
    ]
    agent = await _agent(servers, OneCallModel("echo", '{"text": "hi"}'))
    try:
        trajectory = await _run(agent)
    finally:
        await agent.cleanup_mcp_servers()

    listed = {server["name"]: server for server in trajectory["harness"]["mcp_servers"]}
    weather = listed["weather"]
    assert weather["status"] == "connected"
    assert weather["transport_type"] == "stdio"
    assert weather["server_info"] == {"name": "probe-server", "version": "1.2.3"}
    assert weather["protocol_version"] == "2025-11-25"
    assert weather["tool_count"] == 6
    broken = listed["broken"]
    assert broken["status"] == "failed"
    assert broken["error"]
    # Commands, arguments, environment, URLs, and headers stay out of the header.
    assert "secret-token-value" not in str(trajectory["harness"])
    assert "command" not in weather and "env" not in weather


@pytest.mark.asyncio
async def test_an_mcp_call_names_its_server_in_the_trajectory():
    agent = await _agent([_stdio("weather")], OneCallModel("echo", '{"text": "hi"}'))
    try:
        trajectory = await _run(agent)
    finally:
        await agent.cleanup_mcp_servers()

    [call] = [c for step in trajectory["steps"] for c in step["tool_calls"]]
    assert (call["provider"], call["server"], call["outcome"]) == ("mcp", "weather", "success")
    assert call["reconnects"] == []


@pytest.mark.asyncio
async def test_a_reconnect_is_recorded_on_the_call_it_affected():
    server = _stdio("paged", LIFECYCLE)
    agent = await _agent([server], OneCallModel("echo", '{"text": "again"}'))
    try:
        await _run(agent, "before")
        [pid] = _pids(server)
        os.kill(pid, signal.SIGKILL)
        await asyncio.sleep(0.3)
        trajectory = await _run(agent, "after")
    finally:
        await agent.cleanup_mcp_servers()

    [call] = [c for step in trajectory["steps"] for c in step["tool_calls"]]
    assert call["outcome"] == "success"
    [reconnect] = call["reconnects"]
    assert reconnect["outcome"] == "reconnected"
    assert reconnect["mcp_server"] == "paged"
    assert reconnect["reason"]
    assert reconnect["event_id"] in call["event_ids"]


@pytest.mark.asyncio
async def test_a_failed_reconnect_is_recorded_with_its_error():
    server = _stdio("paged", LIFECYCLE)
    agent = await _agent([server], OneCallModel("echo", '{"text": "again"}'))
    try:
        await _run(agent, "before")
        [pid] = _pids(server)
        os.kill(pid, signal.SIGKILL)
        await asyncio.sleep(0.3)
        agent.mcp_client.servers[0]["command"] = "/nonexistent/mcp-server"
        trajectory = await _run(agent, "after")
    finally:
        await agent.cleanup_mcp_servers()

    [call] = [c for step in trajectory["steps"] for c in step["tool_calls"]]
    assert call["outcome"] == "error"
    [reconnect] = call["reconnects"]
    assert reconnect["outcome"] == "failed"
    assert reconnect["error"]


def test_readiness_reports_each_mcp_server():
    from fastapi.testclient import TestClient

    from omnicoreagent.serve import OmniServe, OmniServeConfig

    agent = OmniCoreAgent(
        name="ready-agent",
        system_instruction="Ready?",
        model_config=_MODEL,
        mcp_tools=[
            _stdio("weather"),
            {"name": "broken", "transport_type": "stdio", "command": "/nonexistent/mcp-server"},
        ],
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    app = OmniServe(agent=agent, config=OmniServeConfig(background_enabled=False)).app
    with TestClient(app) as client:
        body = client.get("/ready").json()

    assert body["ready"] is False
    assert body["mcp_connected"] is False
    assert body["mcp_servers"]["weather"]["status"] == "connected"
    assert body["mcp_servers"]["broken"]["status"] == "failed"
    assert body["mcp_servers"]["broken"]["error"]
