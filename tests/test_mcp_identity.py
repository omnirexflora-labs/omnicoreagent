"""The configured MCP server name is the identity everywhere.

The probe server reports itself as ``probe-server`` 1.2.3; every test
configures a different name, so a test passes only if the configured name,
not the reported one, reaches routing, governance, and telemetry.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.config import MCPToolConfig, normalize_mcp_tools
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.governance import GovernanceEngine, policy_from_mapping
from omnicoreagent.mcp_clients_connection.client import MCPClient

PROBE = str(Path(__file__).parent / "fixtures" / "mcp_probe_server.py")
_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


def _stdio(name: str) -> dict:
    return {
        "name": name,
        "transport_type": "stdio",
        "command": sys.executable,
        "args": [PROBE, "stdio"],
    }


def _policy(server: str) -> dict:
    return {
        "name": "mcp-identity",
        "mode": "strict",
        "rules": {
            "allow": [
                {
                    "rule_id": "start_configured_server",
                    "capability": "mcp.server.start",
                    "target": {"mcp_server": server},
                },
                {
                    "rule_id": "call_forecast",
                    "capability": "tool.mcp.call",
                    "target": {"mcp_server": server, "tool_name": "forecast"},
                },
            ]
        },
    }


@pytest.mark.asyncio
async def test_a_server_is_stored_under_its_configured_name():
    client = MCPClient(servers=[_stdio("weather")])
    await client.connect_to_servers()
    try:
        assert list(client.sessions) == ["weather"]
        assert list(client.available_tools) == ["weather"]
        info = client.sessions["weather"]
        assert info["server_info"] == {"name": "probe-server", "version": "1.2.3"}
        assert info["protocol_version"] == "2025-11-25"
        assert client.state.resolve_server_name("weather") == "weather"
    finally:
        await client.cleanup()


@pytest.mark.asyncio
async def test_two_servers_reporting_the_same_name_both_connect():
    client = MCPClient(servers=[_stdio("weather"), _stdio("maps")])
    await client.connect_to_servers()
    try:
        assert sorted(client.sessions) == ["maps", "weather"]
    finally:
        await client.cleanup()


@pytest.mark.asyncio
async def test_governance_authorizes_the_configured_name_not_the_reported_one():
    # The policy knows only the configured name; the server reports another.
    engine = GovernanceEngine(policy_from_mapping(_policy("weather")))
    client = MCPClient(servers=[_stdio("weather")], governance_engine=engine)
    await client.connect_to_servers()
    try:
        assert list(client.sessions) == ["weather"]
    finally:
        await client.cleanup()


class ForecastModel:
    def __init__(self) -> None:
        self.turns = 0

    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        self.turns += 1
        usage = Usage(requests=1, request_tokens=10, response_tokens=2, total_tokens=12)
        if self.turns == 1:
            call = ToolRequest("c1", "forecast", '{"city": "Lagos"}')
            return ModelTurn(tool_calls=(call,), finish_reason="tool_calls", usage=usage)
        return ModelTurn(content="Sunny in Lagos.", finish_reason="stop", usage=usage)


@pytest.mark.asyncio
async def test_an_agent_routes_governs_and_records_mcp_calls_by_configured_name():
    agent = OmniCoreAgent(
        name="identity-agent",
        system_instruction="You check the weather.",
        model_config=_MODEL,
        mcp_tools=[_stdio("weather")],
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "governance_config": {"enabled": True, "policy": _policy("weather")},
        },
    )
    await agent.initialize()
    await agent.connect_mcp_servers()
    agent.llm_connection = ForecastModel()
    try:
        result = await agent.run("weather in Lagos?", session_id="identity")
        trace = await agent.telemetry_store.get_trace(result["trace_id"])
    finally:
        await agent.cleanup_mcp_servers()

    span = next(span for span in trace.spans if span.kind == "mcp.tool.call")
    assert span.actor.name == "weather"
    assert span.status == "ok"
    [decision] = [
        event
        for event in trace.events
        if event.event_type == "policy_decision_allow"
        and (event.metadata or {}).get("tool_call_id") == "c1"
    ]
    assert decision.metadata["matched_rule_ids"] == ["call_forecast"]
    assert decision.metadata["mcp_server"] == "weather"
    assert result["response"] == "Sunny in Lagos."


def test_an_unnamed_server_gets_the_same_name_every_time():
    first = MCPToolConfig(command="npx", args=["-y", "weather-server"])
    again = MCPToolConfig(command="npx", args=["-y", "weather-server"])
    other = MCPToolConfig(command="npx", args=["-y", "maps-server"])
    remote = MCPToolConfig(transport_type="streamable_http", url="https://mcp.example.com/mcp")

    assert first.name == again.name
    assert first.name != other.name
    assert first.name.startswith("npx_")
    assert remote.name == MCPToolConfig(
        transport_type="streamable_http", url="https://mcp.example.com/mcp"
    ).name
    assert [server["name"] for server in normalize_mcp_tools([{"command": "npx"}])] == [
        MCPToolConfig(command="npx").name
    ]


def test_readiness_finds_servers_by_configured_name():
    from types import SimpleNamespace

    from omnicoreagent.serve.readiness import _mcp_connected

    client = MCPClient(servers=[_stdio("weather")])
    client.state.sessions["weather"] = {"connected": True, "session": object()}
    agent = SimpleNamespace(mcp_tools=[_stdio("weather")], mcp_client=client)

    assert _mcp_connected(agent) is True
