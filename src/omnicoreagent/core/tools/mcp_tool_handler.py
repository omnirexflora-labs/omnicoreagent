from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from omnicoreagent.core.tools.base_tool_handler import BaseToolHandler
from omnicoreagent.core.tools.mcp_results import is_mcp_call_result, mcp_result_text

if TYPE_CHECKING:
    from omnicoreagent.core.guardrails import PromptInjectionGuard

logger = logging.getLogger(__name__)

# JSON-RPC code the MCP SDK uses when the connection closed.
CONNECTION_CLOSED = -32000


def is_dropped_session(error: BaseException) -> bool:
    """The session is gone (server exited, HTTP session terminated)."""
    # Imported here: agents without MCP servers never load the SDK.
    from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
    from mcp import MCPError

    if isinstance(error, (ClosedResourceError, BrokenResourceError, EndOfStream)):
        return True
    if isinstance(error, MCPError):
        message = error.message.lower()
        # The SDK reports an ended HTTP session as "Session terminated"; a
        # restarted server answers an unknown session with "Session not found".
        return error.code == CONNECTION_CLOSED or (
            error.code == -32600
            and "session" in message
            and ("terminated" in message or "not found" in message)
        )
    return False


def mcp_error_result(
    error: BaseException, *, reconnect_error: BaseException | None = None
) -> dict[str, Any]:
    """An error result that keeps the MCP error code, message, and data."""
    from mcp import MCPError

    if isinstance(error, MCPError):
        code, message, data = error.code, error.message, error.data
    else:
        code = CONNECTION_CLOSED if is_dropped_session(error) else None
        message, data = str(error) or error.__class__.__name__, None
    text = f"MCP error {code}: {message}" if code is not None else message
    if reconnect_error is not None:
        text += f" (reconnect failed: {reconnect_error})"
    return {
        "status": "error",
        "data": {"mcp_error": {"code": code, "message": message, "data": data}},
        "message": text,
    }


class MCPToolHandler(BaseToolHandler):
    """Execute tools from one resolved MCP server session."""

    def __init__(
        self,
        sessions: dict[str, Any],
        server_name: str,
        guardrail: PromptInjectionGuard | None = None,
        telemetry_recorder: Any = None,
        tool_call_id: str | None = None,
    ):
        self.sessions = sessions
        self.server_name = server_name
        self.guardrail = guardrail
        self.telemetry_recorder = telemetry_recorder
        self.tool_call_id = tool_call_id

    async def call(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        try:
            info = self.sessions[self.server_name]
        except KeyError as exc:
            raise ValueError(
                f"MCP server '{self.server_name}' is not connected."
            ) from exc

        try:
            result = await self._call_once(info, tool_name, tool_args)
        except Exception as exc:
            if not is_dropped_session(exc) or info.get("reconnect") is None:
                return mcp_error_result(exc)
            # A dropped session is reconnected once and the call retried.
            try:
                await info["reconnect"]()
            except Exception as reconnect_error:
                await self._record_reconnect(exc, info, error=reconnect_error)
                return mcp_error_result(exc, reconnect_error=reconnect_error)
            await self._record_reconnect(exc, info)
            try:
                result = await self._call_once(info, tool_name, tool_args)
            except Exception as retry_error:
                return mcp_error_result(retry_error)
        return self._scrub_mcp_result(tool_name, result)

    async def _record_reconnect(
        self,
        cause: BaseException,
        info: dict[str, Any],
        *,
        error: BaseException | None = None,
    ) -> None:
        """Record the reconnect on the tool call it happened in."""
        if self.telemetry_recorder is None:
            return
        from omnicoreagent.core.telemetry import ActorType, TelemetryActor

        dropped = mcp_error_result(cause)["data"]["mcp_error"]
        await self.telemetry_recorder.emit_event(
            "mcp_reconnect",
            actor=TelemetryActor(type=ActorType.MCP_SERVER, name=self.server_name),
            error=(
                {"type": error.__class__.__name__, "message": str(error)}
                if error is not None
                else None
            ),
            metadata={
                "tool_call_id": self.tool_call_id,
                "mcp_server": self.server_name,
                "outcome": "failed" if error is not None else "reconnected",
                "reason": f"MCP error {dropped['code']}: {dropped['message']}",
                "reconnects": info.get("reconnects", 0),
            },
        )

    async def _call_once(
        self, info: dict[str, Any], tool_name: str, tool_args: dict[str, Any]
    ) -> Any:
        if not info.get("connected", True):
            from mcp import MCPError

            raise MCPError(
                code=CONNECTION_CLOSED,
                message=info.get("last_error") or "Connection closed",
            )
        return await info["session"].call_tool(
            tool_name, tool_args, read_timeout_seconds=info.get("call_timeout")
        )

    def _scrub_mcp_result(self, tool_name: str, result: Any) -> Any:
        """Scrub MCP tool result through guardrails at the client boundary."""
        if not self.guardrail:
            return result

        text = None
        if is_mcp_call_result(result):
            # Structured content reaches the model too, so it is checked.
            text = mcp_result_text(result)
        elif isinstance(result, dict):
            text = str(result.get("data") or result.get("message") or "")
        elif isinstance(result, str):
            text = result

        if not text or not text.strip():
            return result

        check = self.guardrail.check(text)
        if check.threat_level.value in ("dangerous", "critical"):
            logger.warning(
                f"Guardrail blocked MCP response from '{tool_name}' on "
                f"server '{self.server_name}': {check.threat_level.value} "
                f"(score: {check.threat_score})"
            )
            return {
                "status": "error",
                "data": None,
                "message": f"[MCP response blocked by guardrail: {check.message}]",
                "_guardrail_telemetry": {
                    "source": "mcp_tool_output",
                    "tool_name": tool_name,
                    "field": "content",
                    "action": "blocked",
                    "threat_level": check.threat_level.value,
                    "threat_score": check.threat_score,
                    "input_hash": check.input_hash,
                    "message": check.message,
                },
            }

        return result
