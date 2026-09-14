import asyncio
from collections.abc import Callable
from typing import Any

from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor
from omnicoreagent.core.types import (
    ToolCallResult,
)
from omnicoreagent.governance.capabilities import tool_authority_requests
from omnicoreagent.governance.errors import (
    GovernanceError,
    PolicyDeniedError,
)


class GovernedToolRunner:
    """Authorize and execute one resolved call with telemetry."""

    def __init__(
        self,
        agent_name: str,
        governance_engine: Any = None,
    ):
        self.agent_name = agent_name
        self.governance_engine = governance_engine

    async def execute(
        self,
        *,
        single_tool: ToolCallResult,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str | None,
        telemetry_recorder: Any = None,
    ) -> dict[str, Any]:
        if telemetry_recorder is None:
            governance_error = await self._authorize_single_tool(single_tool)
            if governance_error is not None:
                return await self._governance_error_result(
                    single_tool=single_tool,
                    governance_error=governance_error,
                    add_message_to_history=add_message_to_history,
                    session_id=session_id,
                )
            result = await single_tool.tool_executor.execute(
                agent_name=self.agent_name,
                tool_args=single_tool.tool_args,
                tool_name=single_tool.tool_name,
                tool_call_id=single_tool.tool_call_id,
                add_message_to_history=_governed_history_writer(
                    add_message_to_history,
                    redact_args=self.governance_engine is not None,
                ),
                session_id=session_id,
            )
            if self.governance_engine is not None:
                return _redact_tool_result_args(result)
            return result

        telemetry_shape = _tool_telemetry_shape(single_tool)
        telemetry_input = {
            "tool_name": single_tool.tool_name,
            "tool_call_id": single_tool.tool_call_id,
            "tool_provider": single_tool.tool_provider,
            "tool_server": single_tool.tool_server,
        }
        if self.governance_engine is None:
            telemetry_input["tool_args"] = single_tool.tool_args
        span = await telemetry_recorder.start_span(
            name=single_tool.tool_name,
            kind=telemetry_shape["span_kind"],
            actor=telemetry_shape["actor"],
            input=telemetry_input,
        )
        try:
            governance_error = await self._authorize_single_tool(single_tool)
            if governance_error is not None:
                result = await self._governance_error_result(
                    single_tool=single_tool,
                    governance_error=governance_error,
                    add_message_to_history=add_message_to_history,
                    session_id=session_id,
                )
                await telemetry_recorder.emit_event(
                    telemetry_shape["error_event"],
                    actor=telemetry_shape["actor"],
                    input=telemetry_input if telemetry_shape["single_event"] else None,
                    output=result,
                    error={
                        "type": governance_error.__class__.__name__,
                        "message": str(governance_error),
                    },
                )
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.ERROR,
                    output=result,
                    error={
                        "type": governance_error.__class__.__name__,
                        "message": str(governance_error),
                    },
                )
                return result
            if not telemetry_shape["single_event"]:
                await telemetry_recorder.emit_event(
                    telemetry_shape["call_event"],
                    actor=telemetry_shape["actor"],
                    input=telemetry_input,
                )
            result = await single_tool.tool_executor.execute(
                agent_name=self.agent_name,
                tool_args=single_tool.tool_args,
                tool_name=single_tool.tool_name,
                tool_call_id=single_tool.tool_call_id,
                add_message_to_history=_governed_history_writer(
                    add_message_to_history,
                    redact_args=self.governance_engine is not None,
                ),
                session_id=session_id,
            )
            if self.governance_engine is not None:
                result = _redact_tool_result_args(result)
            telemetry_result = _telemetry_tool_result(
                result,
                redact_args=self.governance_engine is not None,
            )
            if result.get("status") == "error":
                event_kwargs = {
                    "actor": telemetry_shape["actor"],
                    "output": telemetry_result,
                    "error": {
                        "type": "ToolError",
                        "message": result.get("message") or "Tool returned error",
                    },
                }
                if telemetry_shape["single_event"]:
                    event_kwargs["input"] = telemetry_input
                await telemetry_recorder.emit_event(
                    telemetry_shape["error_event"],
                    **event_kwargs,
                )
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.ERROR,
                    output=telemetry_result,
                    error={
                        "type": "ToolError",
                        "message": result.get("message") or "Tool returned error",
                    },
                )
            else:
                event_kwargs = {
                    "actor": telemetry_shape["actor"],
                    "output": telemetry_result,
                }
                if telemetry_shape["single_event"]:
                    event_kwargs["input"] = telemetry_input
                await telemetry_recorder.emit_event(
                    telemetry_shape["result_event"],
                    **event_kwargs,
                )
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.OK,
                    output=telemetry_result,
                )
            return result
        except asyncio.CancelledError:
            await telemetry_recorder.end_span(span.span_id, status=SpanStatus.CANCELLED)
            raise
        except Exception as exc:
            if telemetry_shape["single_event"]:
                await telemetry_recorder.emit_event(
                    telemetry_shape["error_event"],
                    actor=telemetry_shape["actor"],
                    input=telemetry_input,
                    error={"type": exc.__class__.__name__, "message": str(exc)},
                )
            else:
                await telemetry_recorder.record_exception(
                    exc,
                    event_type=telemetry_shape["error_event"],
                    actor=telemetry_shape["actor"],
                )
            await telemetry_recorder.end_span(
                span.span_id,
                status=SpanStatus.ERROR,
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            raise

    async def _authorize_single_tool(
        self,
        single_tool: ToolCallResult,
    ) -> GovernanceError | None:
        if self.governance_engine is None:
            return None
        if single_tool.tool_provider == "mcp" and not single_tool.tool_server:
            return PolicyDeniedError(
                "MCP tool execution requires a concrete server identity.",
                metadata={
                    "tool": single_tool.tool_name,
                    "tool_provider": single_tool.tool_provider,
                    "reason_code": "unknown_target",
                },
            )
        try:
            requests = tool_authority_requests(
                tool_name=single_tool.tool_name,
                tool_args=single_tool.tool_args,
                tool_provider=single_tool.tool_provider,
                tool_server=single_tool.tool_server,
                actor=self.agent_name,
            )
            await self.governance_engine.authorize_all(requests)
        except (GovernanceError, ValueError) as exc:
            if isinstance(exc, GovernanceError):
                return exc
            return PolicyDeniedError(str(exc))
        return None

    async def _governance_error_result(
        self,
        *,
        single_tool: ToolCallResult,
        governance_error: GovernanceError,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str | None,
    ) -> dict[str, Any]:
        message = f"Governance denied tool execution: {governance_error}"
        metadata = {
            "tool_call_id": single_tool.tool_call_id,
            "tool": single_tool.tool_name,
            "args": "[REDACTED]",
            "agent_name": self.agent_name,
            "governance_error_code": getattr(
                governance_error,
                "code",
                governance_error.__class__.__name__,
            ),
            "governance": getattr(governance_error, "metadata", {}),
        }
        await add_message_to_history(
            role="tool",
            content=message,
            metadata=metadata,
            session_id=session_id,
        )
        return {
            "tool_name": single_tool.tool_name,
            "args": {},
            "status": "error",
            "data": None,
            "message": message,
            "governance": metadata["governance"],
        }


def _tool_telemetry_shape(single_tool: ToolCallResult) -> dict[str, Any]:
    if single_tool.tool_provider == "mcp":
        return {
            "span_kind": "mcp.tool.call",
            "call_event": "mcp_tool_call",
            "result_event": "mcp_tool_result",
            "error_event": "mcp_tool_error",
            "single_event": False,
            "actor": TelemetryActor(
                type=ActorType.MCP_SERVER,
                name=single_tool.tool_server or single_tool.tool_name,
            ),
        }
    workspace_shape = _workspace_tool_telemetry_shape(single_tool.tool_name)
    if single_tool.tool_provider == "workspace" and workspace_shape is not None:
        return workspace_shape
    artifact_shape = _artifact_tool_telemetry_shape(single_tool.tool_name)
    if single_tool.tool_provider == "artifact" and artifact_shape is not None:
        return artifact_shape
    return {
        "span_kind": "tool.call",
        "call_event": "tool_call",
        "result_event": "tool_result",
        "error_event": "tool_error",
        "single_event": False,
        "actor": TelemetryActor(type=ActorType.TOOL, name=single_tool.tool_name),
    }


def _workspace_tool_telemetry_shape(tool_name: str) -> dict[str, Any] | None:
    if tool_name not in {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "insert_file",
        "delete_file",
        "move_file",
        "clear_files",
        "glob",
        "grep",
    }:
        return None
    if tool_name in {"ls", "read_file", "glob", "grep"}:
        span_kind = "workspace.read"
        event = "workspace_read"
    elif tool_name in {"delete_file", "clear_files"}:
        span_kind = "workspace.delete"
        event = "workspace_delete"
    else:
        span_kind = "workspace.write"
        event = "workspace_write"
    return {
        "span_kind": span_kind,
        "call_event": event,
        "result_event": event,
        "error_event": event,
        "single_event": True,
        "actor": TelemetryActor(type=ActorType.WORKSPACE, name=tool_name),
    }


def _artifact_tool_telemetry_shape(tool_name: str) -> dict[str, Any] | None:
    if tool_name not in {
        "read_artifact",
        "tail_artifact",
        "search_artifact",
        "list_artifacts",
    }:
        return None
    return {
        "span_kind": "workspace.read",
        "call_event": "workspace_read",
        "result_event": "workspace_read",
        "error_event": "workspace_read",
        "single_event": True,
        "actor": TelemetryActor(type=ActorType.WORKSPACE, name=tool_name),
    }


def _telemetry_tool_result(
    result: dict[str, Any],
    *,
    redact_args: bool,
) -> dict[str, Any]:
    if not redact_args or "args" not in result:
        return result
    sanitized = dict(result)
    sanitized["args"] = "[REDACTED]"
    return sanitized


def _redact_tool_result_args(result: dict[str, Any]) -> dict[str, Any]:
    if "args" not in result:
        return result
    sanitized = dict(result)
    sanitized["args"] = "[REDACTED]"
    return sanitized


def _governed_history_writer(
    add_message_to_history: Callable[[str, str, dict | None], Any],
    *,
    redact_args: bool,
) -> Callable[[str, str, dict | None], Any]:
    if not redact_args:
        return add_message_to_history

    async def add_redacted_message(
        role: str,
        content: str,
        metadata: dict | None = None,
        session_id: str | None = None,
    ) -> Any:
        if metadata and "args" in metadata:
            metadata = dict(metadata)
            metadata["args"] = "[REDACTED]"
        return await add_message_to_history(
            role=role,
            content=content,
            metadata=metadata,
            session_id=session_id,
        )

    return add_redacted_message
