from collections.abc import Callable
from copy import deepcopy
from typing import Any

from omnicoreagent.core.agents.llm_response import CONTINUATION_FIELDS

from omnicoreagent.core.types import Message, SessionState, ToolCall
from omnicoreagent.core.logging import logger


def _restored_call(call: Any) -> dict[str, Any]:
    """A validated tool call with its provider fields (Gemini's signature)."""
    call = call.model_dump() if hasattr(call, "model_dump") else deepcopy(dict(call))
    call_fields = call.pop("provider_specific_fields", None)
    function = dict(call.get("function") or {})
    function_fields = function.pop("provider_specific_fields", None)
    restored = ToolCall.model_validate({**call, "function": function}).model_dump()
    if call_fields:
        restored["provider_specific_fields"] = call_fields
    if function_fields:
        restored["function"]["provider_specific_fields"] = function_fields
    return restored


class AgentMessageHistoryLoader:
    """Rebuild clean LLM context from persisted conversation history.

    Observation blocks are transient tool feedback for the previous run, so they
    are intentionally not replayed into a new request. Assistant tool-call
    messages are restored only with their matching tool responses, preserving
    provider protocol integrity while keeping the next context window clean.
    """

    def __init__(self, agent_name: str):
        self.agent_name = agent_name

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
            self._message_from_record(message) if isinstance(message, dict) else message
            for message in stored_messages
        ]

    def _message_from_record(self, record: dict[str, Any]) -> Message:
        allowed_fields = {
            "role",
            "content",
            "metadata",
            "timestamp",
            "tool_call_id",
            "tool_calls",
        }
        return Message.model_validate(
            {key: value for key, value in record.items() if key in allowed_fields}
        )

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
        if (message.metadata or {}).get("transient_observation"):
            return

        self._clear_or_flush_pending(session_state=session_state)
        # Resend the runtime prefix the model saw with this query (for
        # example the current datetime), so the replayed context is identical.
        prefix = (message.metadata or {}).get("context_prefix") or ""
        session_state.messages.append(
            Message(role="user", content=prefix + (message.content or ""))
        )

    def _apply_assistant_message(
        self, message: Message, session_state: SessionState
    ) -> None:
        metadata = message.metadata or {}
        native_message = metadata.get("model_message") or {}
        calls = (
            message.tool_calls
            or native_message.get("tool_calls")
            or metadata.get("tool_calls", [])
        )
        if calls or metadata.get("has_tool_calls"):
            self._clear_or_flush_pending(session_state=session_state)
            session_state.assistant_with_tool_calls = {
                "role": "assistant",
                "content": native_message.get("content", message.content),
                # Provider continuation data goes back exactly as stored.
                **{
                    key: deepcopy(native_message[key])
                    for key in CONTINUATION_FIELDS
                    if key in native_message
                },
                "tool_calls": [_restored_call(call) for call in calls],
            }
            session_state.pending_tool_responses = []
            return

        self._clear_or_flush_pending(session_state=session_state)
        session_state.messages.append(
            Message(role="assistant", content=message.content)
        )

    def _apply_tool_message(
        self, message: Message, session_state: SessionState
    ) -> None:
        metadata = message.metadata or {}
        tool_call_id = message.tool_call_id or metadata.get("tool_call_id")
        if not tool_call_id:
            logger.warning("Skipping tool message without tool_call_id.")
            return

        pending = session_state.assistant_with_tool_calls
        expected = (
            {str(call["id"]) for call in pending["tool_calls"]} if pending else set()
        )
        if str(tool_call_id) not in expected:
            logger.warning("Skipping tool message without a matching pending call.")
            return
        if any(
            response["tool_call_id"] == str(tool_call_id)
            for response in session_state.pending_tool_responses
        ):
            logger.warning("Skipping duplicate tool response in conversation history.")
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
