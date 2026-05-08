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
async def test_load_accepts_persistence_records_with_extra_fields(loader, session_state):
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
