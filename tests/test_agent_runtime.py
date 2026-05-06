from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnicoreagent.core.runtime import agent_runtime
from omnicoreagent.core.tools.local_tools_registry import Tool


def test_normalize_local_tools_converts_list_to_registry():
    def echo(value: str) -> str:
        return value

    registry = agent_runtime.normalize_local_tools(
        [
            Tool(
                name="echo",
                description="Echo value",
                inputSchema={"type": "object"},
                function=echo,
            )
        ]
    )

    assert registry.get_tool("ECHO").name == "echo"


def test_normalize_local_tools_keeps_existing_registry():
    registry = SimpleNamespace(get_available_tools=lambda: [])

    assert agent_runtime.normalize_local_tools(registry) is registry


def test_available_tools_combines_mcp_dicts_objects_and_local_tools():
    mcp_client = SimpleNamespace(
        available_tools={
            "server": [
                {
                    "name": "dict_tool",
                    "description": "Dict MCP tool",
                    "inputSchema": {"type": "object"},
                },
                SimpleNamespace(
                    name="object_tool",
                    description="Object MCP tool",
                    inputSchema={"type": "object"},
                ),
            ]
        }
    )
    local_tools = SimpleNamespace(
        get_available_tools=lambda: [
            {
                "name": "local_tool",
                "description": "Local tool",
                "inputSchema": {},
                "type": "local",
            }
        ]
    )

    assert agent_runtime.available_tools(mcp_client, local_tools) == [
        {
            "name": "dict_tool",
            "description": "Dict MCP tool",
            "inputSchema": {"type": "object"},
            "type": "mcp",
        },
        {
            "name": "object_tool",
            "description": "Object MCP tool",
            "inputSchema": {"type": "object"},
            "type": "mcp",
        },
        {
            "name": "local_tool",
            "description": "Local tool",
            "inputSchema": {},
            "type": "local",
        },
    ]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("plain text", "plain text"),
        (
            {"choices": [{"message": {"content": "dict content"}}]},
            "dict content",
        ),
        (SimpleNamespace(text="text content"), "text content"),
        (SimpleNamespace(content="object content"), "object content"),
    ],
)
def test_extract_summary_text_common_response_shapes(response, expected):
    assert agent_runtime.extract_summary_text(response) == expected


def test_render_history_uses_defaults_for_partial_messages():
    assert (
        agent_runtime.render_history(
            [
                {"role": "user", "content": "hello"},
                {"content": "missing role"},
                {"role": "assistant"},
            ]
        )
        == "user: hello\nunknown: missing role\nassistant: \n"
    )


def test_prepare_dynamic_subagents_skips_when_disabled():
    factory = object()
    local_tools = object()

    assert agent_runtime.prepare_dynamic_subagents(
        enabled=False,
        existing_factory=factory,
        base_model_config={},
        mcp_tools=[],
        local_tools=local_tools,
        agent_config={},
        prompt_builder=None,
        event_router=None,
        memory_router=None,
        debug=False,
    ) == (factory, local_tools)
