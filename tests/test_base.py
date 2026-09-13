import pytest

from omnicoreagent.core.agents.base import BaseReactAgent


@pytest.mark.asyncio
async def test_run_prepares_internal_tools_once_for_prompt_and_execution(monkeypatch):
    agent = BaseReactAgent(
        agent_name="test_agent",
        max_steps=5,
        tool_call_timeout=10,
        enable_advanced_tool_use=True,
    )
    build_count = 0
    history = []

    async def fake_build_internal_tools(registry):
        nonlocal build_count
        build_count += 1

        @registry.register_tool(
            name="tools_retriever",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            description="Discover available tools.",
        )
        async def tools_retriever(query: str):
            return {"status": "success", "data": f"found:{query}"}

        @registry.register_tool(
            name="internal_ping",
            inputSchema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            description="Internal ping tool.",
        )
        async def internal_ping(value: str):
            return {"status": "success", "data": f"pong:{value}"}

        return registry

    monkeypatch.setattr(
        "omnicoreagent.core.tools.tool_runtime_registry.build_tool_registry_advance_tools_use",
        fake_build_internal_tools,
    )

    class FakeLLMConnection:
        def __init__(self):
            self.calls = 0

        async def llm_call(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                assert "tools_retriever" in messages[0].content
                assert "internal_ping" not in messages[0].content
                assert any(
                    getattr(message, "role", None) == "user"
                    and "run internal ping" in getattr(message, "content", "")
                    for message in messages
                )
                return {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "discover",
                                        "type": "function",
                                        "function": {
                                            "name": "tools_retriever",
                                            "arguments": '{"query":"internal ping tool"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            if self.calls == 2:
                assert "internal_ping" in [tool["function"]["name"] for tool in tools]
                return {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "ping",
                                        "type": "function",
                                        "function": {
                                            "name": "internal_ping",
                                            "arguments": '{"value":"runtime"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            return "done"

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def message_history(agent_name, session_id):
        return history

    result = await agent.run(
        system_prompt="system",
        query="run internal ping",
        llm_connection=FakeLLMConnection(),
        add_message_to_history=add_message_to_history,
        message_history=message_history,
        session_id="chat791",
    )

    tool_messages = [item for item in history if item["role"] == "tool"]
    assert result["answer"] == "done"
    assert build_count == 1
    assert len(tool_messages) == 2
    assert tool_messages[1]["metadata"]["tool"] == "internal_ping"
    assert "pong:runtime" in tool_messages[1]["content"]
