from collections.abc import Callable
from typing import Any

from omnicoreagent.core.types import Message, SessionState
from omnicoreagent.core.utils import logger, track


class AgentMessageHistoryLoader:
    """Rebuild clean LLM context from persisted conversation history.

    Observation blocks are transient tool feedback for the previous run, so they
    are intentionally not replayed into a new request. Assistant tool-call
    messages are restored only with their matching tool responses, preserving
    provider protocol integrity while keeping the next context window clean.
    """

    def __init__(self, agent_name: str):
        self.agent_name = agent_name

    @track("memory_processing")
    async def load(
        self,
        *,
        message_history: Callable[..., Any],
        session_id: str,
        session_state: SessionState,
    ) -> None:
        stored_messages = await message_history(
            agent_name=self.agent_name, session_id=session_id
        )
        if not stored_messages:
            return

        for message in self._validated_messages(stored_messages):
            self._apply_message(message=message, session_state=session_state)

    def _validated_messages(self, stored_messages: list[Any]) -> list[Message]:
        return [
            Message.model_validate(message) if isinstance(message, dict) else message
            for message in stored_messages
        ]

    def _apply_message(self, message: Message, session_state: SessionState) -> None:
        if message.role == "user":
            self._apply_user_message(message=message, session_state=session_state)
            return
        if message.role == "assistant":
            self._apply_assistant_message(message=message, session_state=session_state)
            return
        if message.role == "tool":
            self._apply_tool_message(message=message, session_state=session_state)
            return
        logger.warning(f"Unknown message role encountered: {message.role}")

    def _apply_user_message(
        self, message: Message, session_state: SessionState
    ) -> None:
        if message.content.strip().startswith("<observations>"):
            return

        self._clear_or_flush_pending(session_state=session_state)
        session_state.messages.append(Message(role="user", content=message.content))

    def _apply_assistant_message(
        self, message: Message, session_state: SessionState
    ) -> None:
        metadata = message.metadata
        if metadata and metadata.has_tool_calls:
            self._clear_or_flush_pending(session_state=session_state)
            session_state.assistant_with_tool_calls = {
                "role": "assistant",
                "content": message.content,
                "tool_calls": (
                    [tool_call.model_dump() for tool_call in metadata.tool_calls]
                    if metadata.tool_calls
                    else []
                ),
            }
            session_state.pending_tool_responses = []
            return

        self._clear_or_flush_pending(session_state=session_state)
        session_state.messages.append(Message(role="assistant", content=message.content))

    def _apply_tool_message(
        self, message: Message, session_state: SessionState
    ) -> None:
        metadata = message.metadata
        tool_call_id = metadata.tool_call_id if metadata else message.tool_call_id
        if not tool_call_id:
            logger.warning("Skipping tool message without tool_call_id.")
            return

        session_state.pending_tool_responses.append(
            {
                "role": "tool",
                "content": message.content,
                "tool_call_id": str(tool_call_id),
            }
        )
        self.flush_pending(session_state=session_state)

    def _clear_or_flush_pending(self, session_state: SessionState) -> None:
        if self.flush_pending(session_state=session_state):
            return
        self.discard_pending(session_state=session_state)

    def flush_pending(self, session_state: SessionState) -> bool:
        if not session_state.assistant_with_tool_calls:
            return True

        expected = {
            str(tool_call["id"])
            for tool_call in session_state.assistant_with_tool_calls.get(
                "tool_calls", []
            )
        }
        actual = {
            str(response["tool_call_id"])
            for response in session_state.pending_tool_responses
        }
        if expected - actual:
            return False

        session_state.messages.append(session_state.assistant_with_tool_calls)
        session_state.messages.extend(session_state.pending_tool_responses)
        session_state.assistant_with_tool_calls = None
        session_state.pending_tool_responses = []
        return True

    def discard_pending(self, session_state: SessionState) -> None:
        if not (
            session_state.assistant_with_tool_calls
            or session_state.pending_tool_responses
        ):
            return

        logger.warning("Discarding incomplete tool-call history before new turn.")
        session_state.assistant_with_tool_calls = None
        session_state.pending_tool_responses = []
