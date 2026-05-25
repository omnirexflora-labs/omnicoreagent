import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor
from omnicoreagent.core.tools.tool_batch_events import (
    assign_tool_call_ids,
    build_tool_call_history_content,
    build_tool_batch_args,
    build_tool_batch_name,
    build_tool_call_history_metadata,
)
from omnicoreagent.core.types import (
    Message,
    SessionState,
    ToolCallResult,
)
from omnicoreagent.core.logging import logger
from omnicoreagent.governance.capabilities import tool_authority_requests
from omnicoreagent.governance.errors import (
    GovernanceError,
    PolicyDeniedError,
    UngovernedCapabilityError,
)

TOOL_CALL_TIMEOUT_MESSAGE = (
    "Tool call timed out. Please try again or use a different approach."
)


class ToolBatchRunner:
    """Run resolved tool calls as a single parallel batch."""

    def __init__(
        self,
        agent_name: str,
        tool_call_timeout: int,
        governance_engine: Any = None,
    ):
        self.agent_name = agent_name
        self.tool_call_timeout = tool_call_timeout
        self.governance_engine = governance_engine

    def build_error_results(
        self,
        tool_call_results: list[ToolCallResult],
        error_message: str,
        *,
        redact_args: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            {
                "tool_name": getattr(single_tool, "tool_name", "unknown"),
                "args": (
                    _redacted_args(getattr(single_tool, "tool_args", {}))
                    if redact_args
                    else getattr(single_tool, "tool_args", {})
                ),
                "status": "error",
                "data": None,
                "message": error_message,
            }
            for single_tool in tool_call_results
        ]

    async def handle_execution_error(
        self,
        tool_call_results: list[ToolCallResult],
        error_message: str,
        session_state: SessionState,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str | None,
        tool_batch_name: str,
        telemetry_recorder: Any = None,
        record_history: bool = True,
        emit_telemetry: bool = True,
    ) -> list[dict[str, Any]]:
        governance_enabled = self.governance_engine is not None
        for single_tool in tool_call_results:
            tool_args = (
                _redacted_args(single_tool.tool_args)
                if governance_enabled
                else single_tool.tool_args
            )
            session_state.loop_detector.record_tool_call(
                str(single_tool.tool_name),
                str(tool_args),
                error_message,
            )

        if record_history:
            for single_tool in tool_call_results:
                tool_args = (
                    _redacted_args(single_tool.tool_args)
                    if governance_enabled
                    else single_tool.tool_args
                )
                await add_message_to_history(
                    role="tool",
                    content=error_message,
                    metadata={
                        "tool_call_id": single_tool.tool_call_id,
                        "tool": single_tool.tool_name,
                        "args": tool_args,
                        "agent_name": self.agent_name,
                    },
                    session_id=session_id,
                )

        if telemetry_recorder is not None and emit_telemetry:
            await telemetry_recorder.emit_event(
                "tool_batch_error",
                actor=TelemetryActor(type=ActorType.TOOL),
                input={"tool_batch_name": tool_batch_name},
                error={"type": "ToolBatchExecutionError", "message": error_message},
            )

        return self.build_error_results(
            tool_call_results=tool_call_results,
            error_message=error_message,
            redact_args=governance_enabled,
        )

    async def start(
        self,
        tool_call_results: list[ToolCallResult],
        response: str,
        session_state: SessionState,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str | None,
        telemetry_recorder: Any = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        tool_batch_name = build_tool_batch_name(tool_call_results)
        governance_enabled = self.governance_engine is not None
        tool_batch_args = build_tool_batch_args(
            tool_call_results,
            redact=governance_enabled,
        )
        assign_tool_call_ids(tool_call_results)
        tool_calls_metadata = build_tool_call_history_metadata(
            agent_name=self.agent_name,
            tool_call_results=tool_call_results,
            redact_args=governance_enabled,
        )
        history_content = build_tool_call_history_content(
            response,
            tool_call_results,
            redact=governance_enabled,
        )

        await add_message_to_history(
            role="assistant",
            content=history_content,
            metadata=tool_calls_metadata.model_dump(),
            session_id=session_id,
        )
        session_state.messages.append(Message(role="assistant", content=history_content))

        return tool_batch_name, tool_batch_args

    async def execute(
        self,
        tool_call_results: list[ToolCallResult],
        session_state: SessionState,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str | None,
        tool_batch_name: str,
        tool_batch_args: list[dict[str, Any]],
        parse_tool_observation: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        build_tool_results_observation: Callable[
            [list[ToolCallResult], list[dict[str, Any]], SessionState, str | None],
            str,
        ],
        telemetry_recorder: Any = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        batch_span = None
        observation_span = None
        if telemetry_recorder is not None:
            batch_span = await telemetry_recorder.start_span(
                name="tool.batch",
                kind="tool.batch",
                actor=TelemetryActor(type=ActorType.TOOL),
                input={
                    "tool_batch_name": tool_batch_name,
                    "tool_batch_args": tool_batch_args,
                    "tool_count": len(tool_call_results),
                },
            )
            await telemetry_recorder.emit_event(
                "tool_batch_start",
                actor=TelemetryActor(type=ActorType.TOOL),
                input={
                    "tool_batch_name": tool_batch_name,
                    "tool_batch_args": tool_batch_args,
                    "tool_count": len(tool_call_results),
                },
            )
        try:
            async with asyncio.timeout(self.tool_call_timeout):
                tool_outputs = await asyncio.gather(
                    *[
                        self._execute_single_tool(
                            single_tool=single_tool,
                            add_message_to_history=add_message_to_history,
                            session_id=session_id,
                            telemetry_recorder=telemetry_recorder,
                        )
                        for single_tool in tool_call_results
                    ]
                )
        except asyncio.TimeoutError:
            obs_text = TOOL_CALL_TIMEOUT_MESSAGE
            logger.warning(obs_text)
            await self._record_batch_failure_telemetry(
                telemetry_recorder=telemetry_recorder,
                batch_span=batch_span,
                observation_span=observation_span,
                error_type="TimeoutError",
                error_message=obs_text,
                span_status=SpanStatus.TIMEOUT,
                emit_observation_error=False,
            )
            tools_results = await self.handle_execution_error(
                tool_call_results=tool_call_results,
                error_message=obs_text,
                session_state=session_state,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
                tool_batch_name=tool_batch_name,
                telemetry_recorder=telemetry_recorder,
                emit_telemetry=False,
            )
            return obs_text, tools_results

        except Exception as e:
            obs_text = f"Error executing tool: {str(e)}"
            logger.error(obs_text)
            await self._record_batch_failure_telemetry(
                telemetry_recorder=telemetry_recorder,
                batch_span=batch_span,
                observation_span=observation_span,
                error_type=e.__class__.__name__,
                error_message=str(e),
                span_status=SpanStatus.ERROR,
                emit_observation_error=False,
            )
            tools_results = await self.handle_execution_error(
                tool_call_results=tool_call_results,
                error_message=obs_text,
                session_state=session_state,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
                tool_batch_name=tool_batch_name,
                telemetry_recorder=telemetry_recorder,
                emit_telemetry=False,
            )
            return obs_text, tools_results

        try:
            if telemetry_recorder is not None:
                observation_span = await telemetry_recorder.start_span(
                    name="observation.pipeline",
                    kind="observation.pipeline",
                    actor=TelemetryActor(type=ActorType.SYSTEM),
                    input={
                        "tool_batch_name": tool_batch_name,
                        "tool_count": len(tool_outputs),
                    },
                )
                await telemetry_recorder.emit_event(
                    "observation_pipeline_start",
                    actor=TelemetryActor(type=ActorType.SYSTEM),
                    input={
                        "tool_batch_name": tool_batch_name,
                        "tool_count": len(tool_outputs),
                    },
                )

            async with asyncio.timeout(self.tool_call_timeout):
                observation = await parse_tool_observation(
                    {
                        "status": (
                            "error"
                            if any(
                                result.get("status") == "error"
                                for result in tool_outputs
                            )
                            else "success"
                        ),
                        "tools_results": tool_outputs,
                    }
                )

                tools_results = observation.get("tools_results", [])
                obs_text = build_tool_results_observation(
                    tool_call_results,
                    tools_results,
                    session_state,
                    session_id,
                )

            if telemetry_recorder is not None:
                await telemetry_recorder.emit_event(
                    "observation_pipeline_end",
                    actor=TelemetryActor(type=ActorType.SYSTEM),
                    output={
                        "observation": obs_text,
                        "tool_count": len(tools_results),
                    },
                )
                if observation_span is not None:
                    await telemetry_recorder.end_span(
                        observation_span.span_id,
                        status=SpanStatus.OK,
                        output={
                            "observation": obs_text,
                            "tool_count": len(tools_results),
                        },
                    )
                if "[TOOL RESPONSE OFFLOADED]" in obs_text:
                    await telemetry_recorder.emit_event(
                        "workspace_offload",
                        actor=TelemetryActor(type=ActorType.WORKSPACE),
                        output={
                            "tool_batch_name": tool_batch_name,
                            "offloaded": True,
                        },
                    )
                await telemetry_recorder.emit_event(
                    "tool_batch_end",
                    actor=TelemetryActor(type=ActorType.TOOL),
                    output={"tool_count": len(tools_results), "status": "completed"},
                )
                await telemetry_recorder.end_span(
                    batch_span.span_id,
                    status=SpanStatus.OK,
                    output={"tool_count": len(tools_results), "observation": obs_text},
                )

            return obs_text, tools_results

        except asyncio.TimeoutError:
            obs_text = TOOL_CALL_TIMEOUT_MESSAGE
            logger.warning(obs_text)
            await self._record_batch_failure_telemetry(
                telemetry_recorder=telemetry_recorder,
                batch_span=batch_span,
                observation_span=observation_span,
                error_type="TimeoutError",
                error_message=obs_text,
                span_status=SpanStatus.TIMEOUT,
                emit_observation_error=True,
            )
            tools_results = await self.handle_execution_error(
                tool_call_results=tool_call_results,
                error_message=obs_text,
                session_state=session_state,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
                tool_batch_name=tool_batch_name,
                telemetry_recorder=telemetry_recorder,
                record_history=False,
                emit_telemetry=False,
            )
            return obs_text, tools_results

        except Exception as e:
            obs_text = f"Error executing tool: {str(e)}"
            logger.error(obs_text)
            await self._record_batch_failure_telemetry(
                telemetry_recorder=telemetry_recorder,
                batch_span=batch_span,
                observation_span=observation_span,
                error_type=e.__class__.__name__,
                error_message=str(e),
                span_status=SpanStatus.ERROR,
                emit_observation_error=True,
            )
            tools_results = await self.handle_execution_error(
                tool_call_results=tool_call_results,
                error_message=obs_text,
                session_state=session_state,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
                tool_batch_name=tool_batch_name,
                telemetry_recorder=telemetry_recorder,
                record_history=False,
                emit_telemetry=False,
            )
            return obs_text, tools_results

    async def _record_batch_failure_telemetry(
        self,
        *,
        telemetry_recorder: Any,
        batch_span: Any,
        observation_span: Any,
        error_type: str,
        error_message: str,
        span_status: SpanStatus,
        emit_observation_error: bool,
    ) -> None:
        if telemetry_recorder is None or batch_span is None:
            return
        error = {"type": error_type, "message": error_message}
        if emit_observation_error:
            await telemetry_recorder.emit_event(
                "observation_pipeline_error",
                actor=TelemetryActor(type=ActorType.SYSTEM),
                error=error,
            )
            if observation_span is not None:
                await telemetry_recorder.end_span(
                    observation_span.span_id,
                    status=span_status,
                    error=error,
                )
        await telemetry_recorder.emit_event(
            "tool_batch_error",
            actor=TelemetryActor(type=ActorType.TOOL),
            error=error,
        )
        await telemetry_recorder.end_span(
            batch_span.span_id,
            status=span_status,
            error=error,
        )

    async def _execute_single_tool(
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
                    input=telemetry_input
                    if telemetry_shape["single_event"]
                    else None,
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
        if single_tool.tool_provider == "mcp":
            return UngovernedCapabilityError(
                "MCP governance is not implemented until the MCP enforcement phase.",
                metadata={
                    "tool": single_tool.tool_name,
                    "tool_provider": single_tool.tool_provider,
                    "tool_server": single_tool.tool_server,
                    "reason_code": "ungoverned_capability",
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


def _redacted_args(tool_args: dict[str, Any]) -> dict[str, str]:
    if not tool_args:
        return {}
    return {key: "[REDACTED]" for key in tool_args}


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
