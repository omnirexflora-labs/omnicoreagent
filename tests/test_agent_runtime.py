from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnicoreagent.core.runtime.harness_tools import (
    available_tools,
    prepare_dynamic_subagents,
)
from omnicoreagent.core.runtime import builder, execution
from omnicoreagent.core.runtime.normalization import normalize_local_tools
from omnicoreagent.core.runtime.summaries import extract_summary_text, render_history
from omnicoreagent.core.tools.local_tools_registry import Tool


def test_normalize_local_tools_converts_list_to_registry():
    def echo(value: str) -> str:
        return value

    registry = normalize_local_tools(
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

    assert normalize_local_tools(registry) is registry


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

    assert available_tools(mcp_client, local_tools) == [
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
    assert extract_summary_text(response) == expected


def test_render_history_uses_defaults_for_partial_messages():
    assert (
        render_history(
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

    assert prepare_dynamic_subagents(
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


def test_build_agent_runtime_wires_components(monkeypatch):
    calls = {}
    agent = SimpleNamespace(enable_advanced_tool_use=True)

    def fake_create_llm_runtime(**kwargs):
        calls["llm_runtime"] = kwargs
        return "mcp-client", "llm-connection"

    def fake_configure_memory_router(**kwargs):
        calls["memory"] = kwargs

    def fake_create_react_agent(**kwargs):
        calls["react_agent"] = kwargs
        return agent

    def fake_prepare_dynamic_subagents(**kwargs):
        calls["subagents"] = kwargs
        return "subagent-factory", "local-tools"

    def fake_index_tools_for_advanced_use(**kwargs):
        calls["tool_index"] = kwargs

    monkeypatch.setattr(
        builder.construction,
        "create_llm_runtime",
        fake_create_llm_runtime,
    )
    monkeypatch.setattr(
        builder.construction,
        "build_agent_settings",
        lambda agent_config: "agent-settings",
    )
    monkeypatch.setattr(
        builder.construction,
        "configure_memory_router",
        fake_configure_memory_router,
    )
    monkeypatch.setattr(
        builder.construction,
        "create_react_agent",
        fake_create_react_agent,
    )
    monkeypatch.setattr(
        builder.harness_tools,
        "prepare_dynamic_subagents",
        fake_prepare_dynamic_subagents,
    )
    monkeypatch.setattr(
        builder.harness_tools,
        "index_tools_for_advanced_use",
        fake_index_tools_for_advanced_use,
    )

    components = builder.build_agent_runtime(
        model_config={"provider": "openai", "model": "gpt-4o"},
        mcp_tools=[{"name": "server"}],
        local_tools="input-local-tools",
        agent_config={"enable_subagents": True},
        memory_router="memory-router",
        event_router="event-router",
        prompt_builder="prompt-builder",
        existing_subagent_factory=None,
        guardrail="guardrail",
        guardrail_mode="full",
        summarize_fn="summarize",
        debug=True,
    )

    assert components == builder.AgentRuntimeComponents(
        agent=agent,
        mcp_client="mcp-client",
        llm_connection="llm-connection",
        local_tools="local-tools",
        subagent_factory="subagent-factory",
    )
    assert calls["subagents"]["event_router"] == "event-router"
    assert calls["subagents"]["memory_router"] == "memory-router"
    assert calls["memory"]["summarize_fn"] == "summarize"
    assert calls["react_agent"]["guardrail"] == "guardrail"
    assert calls["tool_index"] == {
        "enabled": True,
        "local_tools": "local-tools",
    }


def test_blocked_guardrail_response_returns_none_without_guardrail():
    assert (
        execution.blocked_guardrail_response(
            guardrail=None,
            query="hello",
            session_id="session",
            agent_name="agent",
        )
        is None
    )


def test_blocked_guardrail_response_returns_none_for_safe_input():
    guardrail = SimpleNamespace(
        check=lambda query: SimpleNamespace(is_safe=True, message="", to_dict=dict)
    )

    assert (
        execution.blocked_guardrail_response(
            guardrail=guardrail,
            query="hello",
            session_id="session",
            agent_name="agent",
        )
        is None
    )


def test_blocked_guardrail_response_formats_unsafe_input():
    result = SimpleNamespace(
        is_safe=False,
        message="unsafe",
        to_dict=lambda: {"is_safe": False, "message": "unsafe"},
    )
    guardrail = SimpleNamespace(check=lambda query: result)

    response = execution.blocked_guardrail_response(
        guardrail=guardrail,
        query="bad",
        session_id="session",
        agent_name="agent",
    )

    assert response == {
        "response": (
            "I'm sorry, but I cannot process this request due to safety concerns: "
            "unsafe"
        ),
        "session_id": "session",
        "agent_name": "agent",
        "guardrail_result": {"is_safe": False, "message": "unsafe"},
    }


def test_build_agent_run_kwargs_uses_empty_mcp_state_when_disconnected():
    assert execution.build_agent_run_kwargs(
        mcp_client=None,
        local_tools="local-tools",
        session_id="session",
        sub_agents=None,
    ) == {
        "sessions": {},
        "mcp_tools": {},
        "local_tools": "local-tools",
        "session_id": "session",
        "sub_agents": None,
    }


def test_build_agent_run_kwargs_uses_connected_mcp_state():
    worker = object()
    mcp_client = SimpleNamespace(
        sessions={"server": "session"},
        available_tools={"server": ["tool"]},
    )

    assert execution.build_agent_run_kwargs(
        mcp_client=mcp_client,
        local_tools="local-tools",
        session_id="session",
        sub_agents={"worker": worker},
    ) == {
        "sessions": {"server": "session"},
        "mcp_tools": {"server": ["tool"]},
        "local_tools": "local-tools",
        "session_id": "session",
        "sub_agents": {"worker": worker},
    }


def test_format_run_response_keeps_plain_response_without_usage_allocation():
    def fail_usage_getter():
        raise AssertionError("usage getter should not be called")

    assert execution.format_run_response(
        response="done",
        session_id="session",
        agent_name="agent",
        usage_getter=fail_usage_getter,
    ) == {"response": "done", "session_id": "session", "agent_name": "agent"}


def test_format_run_response_increments_usage_for_metric_response():
    usage = SimpleNamespace()
    cumulative_usage = SimpleNamespace(incr=lambda value: setattr(value, "seen", True))

    response = execution.format_run_response(
        response={"answer": "done", "usage": usage},
        session_id="session",
        agent_name="agent",
        usage_getter=lambda: cumulative_usage,
    )

    assert response == {
        "response": "done",
        "session_id": "session",
        "agent_name": "agent",
        "metric": usage,
    }
    assert getattr(usage, "seen") is True
