import json
from types import SimpleNamespace

import pytest

from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.tool_action import ToolAction
from omnicoreagent.core.tools.tool_call_resolver import ToolCallResolver
from omnicoreagent.core.workspace.config import WorkspaceConfig
from omnicoreagent.core.workspace.tools import build_tool_registry_workspace_files
from omnicoreagent.core.types import ParsedResponse, ToolCallResult, ToolError


@pytest.fixture
def resolver():
    return ToolCallResolver()


@pytest.mark.asyncio
async def test_resolve_single_action_uses_local_registry(resolver):
    registry = ToolRegistry()

    @registry.register_tool(
        name="local_ping",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        description="Local ping tool.",
    )
    async def local_ping(value: str):
        return {"status": "success", "data": value}

    resolved = await resolver.resolve_single_action(
        action=ToolAction(
            tool_name="local_ping",
            parameters={"value": "runtime"},
            raw={"tool": "local_ping", "parameters": {"value": "runtime"}},
        ),
        sessions={},
        mcp_tools=None,
        local_tools=registry,
    )

    assert isinstance(resolved, ToolCallResult)
    assert resolved.tool_name == "local_ping"
    assert resolved.tool_args == {"value": "runtime"}
    assert resolved.tool_provider == "local"


@pytest.mark.asyncio
async def test_resolve_single_action_marks_builtin_workspace_tools(resolver, tmp_path):
    registry = ToolRegistry()
    build_tool_registry_workspace_files(
        registry=registry,
        workspace_config=WorkspaceConfig(workspace_dir=tmp_path / "workspace"),
    )

    resolved = await resolver.resolve_single_action(
        action=ToolAction(
            tool_name="read_file",
            parameters={"path": "notes.md"},
            raw={"tool": "read_file", "parameters": {"path": "notes.md"}},
        ),
        sessions={},
        mcp_tools=None,
        local_tools=registry,
    )

    assert isinstance(resolved, ToolCallResult)
    assert resolved.tool_name == "read_file"
    assert resolved.tool_provider == "workspace"


@pytest.mark.asyncio
async def test_resolve_single_action_uses_mcp_tools_before_local(resolver):
    registry = ToolRegistry()

    @registry.register_tool(
        name="search",
        inputSchema={"type": "object", "properties": {}, "required": []},
        description="Local search fallback.",
    )
    async def search():
        return {"status": "success", "data": "local"}

    resolved = await resolver.resolve_single_action(
        action=ToolAction(
            tool_name="search",
            parameters={"query": "runtime"},
            raw={"tool": "search", "parameters": {"query": "runtime"}},
        ),
        sessions={"server": {"session": object()}},
        mcp_tools={"server": [SimpleNamespace(name="search")]},
        local_tools=registry,
    )

    assert isinstance(resolved, ToolCallResult)
    assert resolved.tool_name == "search"
    assert resolved.tool_args == {"query": "runtime"}
    assert resolved.tool_executor.tool_handler.server_name == "server"


@pytest.mark.asyncio
async def test_resolve_single_action_rejects_tool_call_to_sub_agent(resolver):
    sub_agent = SimpleNamespace(name="research_agent")

    resolved = await resolver.resolve_single_action(
        action=ToolAction(
            tool_name="research_agent",
            parameters={"task": "inspect"},
            raw={"tool": "research_agent", "parameters": {"task": "inspect"}},
        ),
        sessions={},
        mcp_tools=None,
        local_tools=None,
        sub_agents=[sub_agent],
    )

    assert isinstance(resolved, ToolError)
    assert resolved.tool_name == "N/A"
    assert "is a sub-agent, not a tool" in resolved.observation


def test_parse_actions_handles_empty_and_single_action(resolver):
    empty = resolver.parse_actions(ParsedResponse(action=True, data=None))
    assert isinstance(empty, ToolError)
    assert empty.observation == "Invalid tool call request: No data provided"

    parsed = resolver.parse_actions(
        ParsedResponse(
            action=True,
            data=json.dumps({"tool": "local_ping", "parameters": {"value": "one"}}),
        )
    )
    assert parsed == [
        ToolAction(
            tool_name="local_ping",
            parameters={"value": "one"},
            raw={"tool": "local_ping", "parameters": {"value": "one"}},
        )
    ]


def test_parse_actions_rejects_malformed_payloads(resolver):
    invalid_json = resolver.parse_actions(ParsedResponse(action=True, data="{"))
    assert isinstance(invalid_json, ToolError)
    assert "Invalid JSON" in invalid_json.observation

    empty_list = resolver.parse_actions(ParsedResponse(action=True, data="[]"))
    assert isinstance(empty_list, ToolError)
    assert empty_list.observation == "Invalid tool call request: No actions provided"

    scalar_action = resolver.parse_actions(ParsedResponse(action=True, data="[1]"))
    assert isinstance(scalar_action, ToolError)
    assert scalar_action.observation == (
        "Invalid tool call request: Action #1 must be an object."
    )

    scalar_parameters = resolver.parse_actions(
        ParsedResponse(
            action=True,
            data=json.dumps({"tool": "search", "parameters": "query"}),
        )
    )
    assert isinstance(scalar_parameters, ToolError)
    assert scalar_parameters.observation == (
        "Invalid tool call request: Parameters for 'search' must be an object."
    )


def test_mcp_tools_for_action_disables_mcp_only_for_tools_retriever(resolver):
    mcp_tools = {"server": [SimpleNamespace(name="search")]}

    assert (
        resolver.mcp_tools_for_action(
            {"tool": "tools_retriever", "parameters": {"query": "mail"}},
            mcp_tools=mcp_tools,
        )
        is None
    )
    assert (
        resolver.mcp_tools_for_action(
            {"tool": "search", "parameters": {"query": "runtime"}},
            mcp_tools=mcp_tools,
        )
        is mcp_tools
    )


@pytest.mark.asyncio
async def test_resolve_keeps_mcp_available_after_tools_retriever(resolver):
    registry = ToolRegistry()

    @registry.register_tool(
        name="tools_retriever",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        description="Retrieve tools.",
    )
    async def tools_retriever(query: str):
        return {"status": "success", "data": query}

    resolved = await resolver.resolve(
        parsed_response=ParsedResponse(
            action=True,
            tool_calls=True,
            data=json.dumps(
                [
                    {"tool": "tools_retriever", "parameters": {"query": "search"}},
                    {"tool": "search", "parameters": {"query": "runtime"}},
                ]
            ),
        ),
        sessions={"server": {"session": object()}},
        mcp_tools={"server": [SimpleNamespace(name="search")]},
        local_tools=registry,
    )

    assert not isinstance(resolved, ToolError)
    assert [tool.tool_name for tool in resolved] == ["tools_retriever", "search"]
    assert resolved[0].tool_executor.tool_handler.local_tools is registry
    assert resolved[1].tool_executor.tool_handler.server_name == "server"
