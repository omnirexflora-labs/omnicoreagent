import pytest

from omnicoreagent.core.agents.message_history import AgentMessageHistoryLoader
from omnicoreagent.core.types import (
    AgentState,
    Message,
    SessionState,
    ToolCall,
    ToolCallMetadata,
    ToolFunction,
)
from omnicoreagent.core.agents.loop_detection import RobustLoopDetector


TOOL_ALPHA_ID = "11111111-1111-1111-1111-111111111111"
TOOL_BETA_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def session_state():
    return SessionState(
        messages=[],
        state=AgentState.IDLE,
        loop_detector=RobustLoopDetector(debug=False),
        assistant_with_tool_calls=None,
        pending_tool_responses=[],
    )


@pytest.fixture
def loader():
    return AgentMessageHistoryLoader(agent_name="test_agent")


@pytest.mark.asyncio
async def test_load_empty_history_leaves_messages_empty(loader, session_state):
    async def message_history(agent_name, session_id):
        return []

    await loader.load(
        message_history=message_history,
        session_id="chat-1",
        session_state=session_state,
    )

    assert session_state.messages == []


@pytest.mark.asyncio
async def test_load_skips_observation_user_messages(loader, session_state):
    async def message_history(agent_name, session_id):
        return [
            Message(role="user", content="start"),
            Message(
                role="user", content="<observations><tool>old</tool></observations>"
            ),
            Message(role="assistant", content="answer"),
        ]

    await loader.load(
        message_history=message_history,
        session_id="chat-2",
        session_state=session_state,
    )

    assert [message.content for message in session_state.messages] == [
        "start",
        "answer",
    ]


@pytest.mark.asyncio
async def test_load_skips_subagent_observation_user_messages(loader, session_state):
    async def message_history(agent_name, session_id):
        return [
            Message(role="user", content="start"),
            Message(
                role="user",
                content=(
                    "OBSERVATION RESULT FROM SUB-AGENTS\n"
                    "<observations><observation>old</observation></observations>\n"
                    "END OF OBSERVATIONS"
                ),
            ),
            Message(role="assistant", content="answer"),
        ]

    await loader.load(
        message_history=message_history,
        session_id="chat-subagents",
        session_state=session_state,
    )

    assert [message.content for message in session_state.messages] == [
        "start",
        "answer",
    ]


@pytest.mark.asyncio
async def test_load_accepts_dict_messages(loader, session_state):
    async def message_history(agent_name, session_id):
        return [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    await loader.load(
        message_history=message_history,
        session_id="chat-3",
        session_state=session_state,
    )

    assert [message.role for message in session_state.messages] == [
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_load_accepts_persistence_records_with_extra_fields(
    loader, session_state
):
    async def message_history(agent_name, session_id):
        return [
            {
                "id": "message-1",
                "role": "user",
                "content": "My name is Alice.",
                "session_id": session_id,
                "status": "active",
                "summary_id": None,
                "metadata": {"agent_name": agent_name},
            },
            {
                "id": "message-2",
                "role": "assistant",
                "content": "Hi Alice.",
                "session_id": session_id,
                "status": "active",
                "metadata": {"agent_name": agent_name},
            },
        ]

    await loader.load(
        message_history=message_history,
        session_id="chat-extra-fields",
        session_state=session_state,
    )

    assert [message.content for message in session_state.messages] == [
        "My name is Alice.",
        "Hi Alice.",
    ]


@pytest.mark.asyncio
async def test_load_pairs_assistant_tool_call_with_tool_responses(
    loader, session_state
):
    tool_metadata = ToolCallMetadata(
        has_tool_calls=True,
        tool_call_id=TOOL_ALPHA_ID,
        tool_calls=[
            ToolCall(
                id=TOOL_ALPHA_ID,
                function=ToolFunction(name="alpha", arguments='{"value": "one"}'),
            ),
            ToolCall(
                id=TOOL_BETA_ID,
                function=ToolFunction(name="beta", arguments='{"value": "two"}'),
            ),
        ],
    )

    async def message_history(agent_name, session_id):
        return [
            Message(role="assistant", content="<tool_calls />", metadata=tool_metadata),
            Message(
                role="tool",
                content="alpha result",
                metadata=ToolCallMetadata(tool_call_id=TOOL_ALPHA_ID),
            ),
            Message(
                role="tool",
                content="beta result",
                metadata=ToolCallMetadata(tool_call_id=TOOL_BETA_ID),
            ),
        ]

    await loader.load(
        message_history=message_history,
        session_id="chat-4",
        session_state=session_state,
    )

    assert len(session_state.messages) == 3
    assert session_state.messages[0]["role"] == "assistant"
    assert session_state.messages[1]["tool_call_id"] == TOOL_ALPHA_ID
    assert session_state.messages[2]["tool_call_id"] == TOOL_BETA_ID
    assert session_state.assistant_with_tool_calls is None
    assert session_state.pending_tool_responses == []


@pytest.mark.asyncio
async def test_load_keeps_incomplete_tool_batch_pending(loader, session_state):
    tool_metadata = ToolCallMetadata(
        has_tool_calls=True,
        tool_call_id=TOOL_ALPHA_ID,
        tool_calls=[
            ToolCall(
                id=TOOL_ALPHA_ID,
                function=ToolFunction(name="alpha", arguments="{}"),
            ),
            ToolCall(
                id=TOOL_BETA_ID,
                function=ToolFunction(name="beta", arguments="{}"),
            ),
        ],
    )

    async def message_history(agent_name, session_id):
        return [
            Message(role="assistant", content="<tool_calls />", metadata=tool_metadata),
            Message(
                role="tool",
                content="alpha result",
                metadata=ToolCallMetadata(tool_call_id=TOOL_ALPHA_ID),
            ),
        ]

    await loader.load(
        message_history=message_history,
        session_id="chat-5",
        session_state=session_state,
    )

    assert session_state.messages == []
    assert session_state.assistant_with_tool_calls is not None
    assert len(session_state.pending_tool_responses) == 1


@pytest.mark.asyncio
async def test_load_drops_incomplete_tool_batch_before_next_user_turn(
    loader, session_state
):
    tool_metadata = ToolCallMetadata(
        has_tool_calls=True,
        tool_calls=[
            ToolCall(
                id=TOOL_ALPHA_ID,
                function=ToolFunction(name="alpha", arguments="{}"),
            ),
            ToolCall(
                id=TOOL_BETA_ID,
                function=ToolFunction(name="beta", arguments="{}"),
            ),
        ],
    )

    async def message_history(agent_name, session_id):
        return [
            Message(role="assistant", content="<tool_calls />", metadata=tool_metadata),
            Message(
                role="tool",
                content="alpha result",
                metadata=ToolCallMetadata(tool_call_id=TOOL_ALPHA_ID),
            ),
            Message(role="user", content="next request"),
        ]

    await loader.load(
        message_history=message_history,
        session_id="chat-6",
        session_state=session_state,
    )

    assert [message.content for message in session_state.messages] == ["next request"]
    assert session_state.assistant_with_tool_calls is None
    assert session_state.pending_tool_responses == []


@pytest.mark.asyncio
async def test_load_skips_tool_message_without_tool_call_id(loader, session_state):
    async def message_history(agent_name, session_id):
        return [Message(role="tool", content="orphan tool")]

    await loader.load(
        message_history=message_history,
        session_id="chat-7",
        session_state=session_state,
    )

    assert session_state.messages == []
    assert session_state.pending_tool_responses == []


@pytest.mark.parametrize(
    "metadata",
    [
        {"agent_name": "test_agent", "tool": "lookup", "args": {"id": "001"}},
        {"agent_name": "test_agent", "type": "history_summary", "summarizes": ["a"]},
        {"agent_name": "test_agent", "sub_agent_results": True},
        {"agent_calls": [{"name": "worker"}], "custom": {"nested": [1, False]}},
    ],
)
def test_message_round_trip_preserves_general_metadata(metadata):
    message = Message.model_validate(
        {"role": "assistant", "content": "result", "metadata": metadata}
    )
    assert message.model_dump()["metadata"] == metadata


@pytest.mark.asyncio
async def test_load_only_pairs_unique_results_for_pending_calls(loader, session_state):
    calls = [
        ToolCall(id=call_id, function=ToolFunction(name="same_tool", arguments="{}"))
        for call_id in (TOOL_ALPHA_ID, TOOL_BETA_ID)
    ]

    async def message_history(**kwargs):
        return [
            Message(role="tool", content="orphan", tool_call_id="unrelated"),
            Message(role="assistant", content="", tool_calls=calls),
            Message(role="tool", content="unrelated", tool_call_id="unrelated"),
            Message(
                role="tool",
                content="beta",
                tool_call_id=TOOL_BETA_ID,
                metadata={"agent_name": "test_agent"},
            ),
            Message(role="tool", content="duplicate", tool_call_id=TOOL_BETA_ID),
            Message(role="tool", content="alpha", tool_call_id=TOOL_ALPHA_ID),
            Message(role="tool", content="late duplicate", tool_call_id=TOOL_ALPHA_ID),
            Message(role="assistant", content="done"),
        ]

    await loader.load(
        message_history=message_history,
        session_id="paired",
        session_state=session_state,
    )
    assert len(session_state.messages) == 4
    assert [m["content"] for m in session_state.messages[1:3]] == ["beta", "alpha"]
    assert {m["tool_call_id"] for m in session_state.messages[1:3]} == {
        TOOL_ALPHA_ID,
        TOOL_BETA_ID,
    }
    assert session_state.pending_tool_responses == []
    assert session_state.assistant_with_tool_calls is None


@pytest.mark.asyncio
async def test_real_tool_batch_history_survives_a_new_agent_run():
    from copy import deepcopy
    from omnicoreagent.core.agents.base import BaseReactAgent
    from omnicoreagent.core.memory_store.memory_router import MemoryRouter
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    class Model:
        def __init__(self, responses):
            self.responses = iter(responses)
            self.requests = []

        async def llm_call(self, messages, tools=None):
            self.requests.append(
                deepcopy(
                    [
                        m.model_dump(exclude_none=True)
                        if hasattr(m, "model_dump")
                        else m
                        for m in messages
                    ]
                )
            )
            return next(self.responses)

    memory = MemoryRouter("in_memory")
    registry = ToolRegistry()
    executed = []

    @registry.register_tool(name="lookup")
    async def lookup(key: str):
        executed.append(key)
        if key == "missing":
            raise ValueError("record missing")
        return {"status": "success", "data": "record found"}

    def agent():
        return BaseReactAgent(
            "test_agent", 5, 2, tool_offload_config={"enabled": False}
        )

    async def run(instance, model, query):
        return await instance.run(
            system_prompt="test",
            query=query,
            llm_connection=model,
            add_message_to_history=memory.store_message,
            message_history=memory.get_messages,
            local_tools=registry,
            session_id="continued",
        )

    model = Model(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "lookup_1",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"key":"present"}',
                                    },
                                },
                                {
                                    "id": "lookup_2",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"key":"missing"}',
                                    },
                                },
                            ],
                        }
                    }
                ]
            },
            "One found, one missing.",
        ]
    )
    await run(agent(), model, "look up both records")
    stored = await memory.get_messages("continued", "test_agent")
    tool_records = [m for m in stored if m["role"] == "tool"]
    assert len(tool_records) == 2
    assert all(
        "args" in m["metadata"] and "tool" in m["metadata"] for m in tool_records
    )

    # Summary and delegation metadata share the same storage boundary.
    await memory.store_message(
        "user",
        "Previous work summary",
        {
            "agent_name": "test_agent",
            "type": "history_summary",
            "summarizes": ["older"],
        },
        "continued",
    )
    await memory.store_message(
        "assistant",
        "Worker completed",
        {"agent_name": "test_agent", "sub_agent_results": True},
        "continued",
    )

    resumed = Model(["Remembered."])
    await run(agent(), resumed, "what happened?")
    messages = resumed.requests[0]
    assistant = next(m for m in messages if m.get("tool_calls"))
    results = [m for m in messages if m["role"] == "tool"]
    expected_ids = {m["metadata"]["tool_call_id"] for m in tool_records}
    assert {call["id"] for call in assistant["tool_calls"]} == expected_ids
    assert {m["tool_call_id"] for m in results} == expected_ids
    import json

    assert {
        json.loads(m["content"])["data"] or json.loads(m["content"])["message"]
        for m in results
    } == {"record found", "record missing"}
    assert all("metadata" not in m for m in results)
    contents = [m["content"] for m in messages]
    assert "look up both records" in contents
    assert "One found, one missing." in contents
    assert "Previous work summary" in contents
    assert "Worker completed" in contents
    assert contents[-1].endswith("what happened?")
    assert executed == ["present", "missing"]  # Stored requests never execute again.
