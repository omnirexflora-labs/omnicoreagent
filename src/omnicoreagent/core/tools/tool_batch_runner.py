import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor
from omnicoreagent.core.tools.tool_batch_events import (
    assign_tool_call_ids,
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

TOOL_CALL_TIMEOUT_MESSAGE = (
    "Tool call timed out. Please try again or use a different approach."
)


class ToolBatchRunner:
    """Run resolved tool calls as a single parallel batch."""

    def __init__(self, agent_name: str, tool_call_timeout: int):
        self.agent_name = agent_name
        self.tool_call_timeout = tool_call_timeout

    def build_error_results(
        self,
        tool_call_results: list[ToolCallResult],
        error_message: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "tool_name": getattr(single_tool, "tool_name", "unknown"),
                "args": getattr(single_tool, "tool_args", {}),
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
    ) -> list[dict[str, Any]]:
        for single_tool in tool_call_results:
            session_state.loop_detector.record_tool_call(
                str(single_tool.tool_name),
                str(single_tool.tool_args),
                error_message,
            )

        for single_tool in tool_call_results:
            await add_message_to_history(
                role="tool",
                content=error_message,
                metadata={
                    "tool_call_id": single_tool.tool_call_id,
                    "tool": single_tool.tool_name,
                    "args": single_tool.tool_args,
                    "agent_name": self.agent_name,
                },
                session_id=session_id,
            )

        if telemetry_recorder is not None:
            await telemetry_recorder.emit_event(
                "tool_batch_error",
                actor=TelemetryActor(type=ActorType.TOOL),
                input={"tool_batch_name": tool_batch_name},
                error={"type": "ToolBatchExecutionError", "message": error_message},
            )

        return self.build_error_results(
            tool_call_results=tool_call_results,
            error_message=error_message,
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
        tool_batch_args = build_tool_batch_args(tool_call_results)
        assign_tool_call_ids(tool_call_results)
        tool_calls_metadata = build_tool_call_history_metadata(
            agent_name=self.agent_name,
            tool_call_results=tool_call_results,
        )

        await add_message_to_history(
            role="assistant",
            content=response,
            metadata=tool_calls_metadata.model_dump(),
            session_id=session_id,
        )
        session_state.messages.append(Message(role="assistant", content=response))

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

            observation = await parse_tool_observation(
                {
                    "status": (
                        "error"
                        if any(
                            result.get("status") == "error" for result in tool_outputs
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
                    output={"observation": obs_text},
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
            if telemetry_recorder is not None and batch_span is not None:
                await telemetry_recorder.emit_event(
                    "tool_batch_error",
                    actor=TelemetryActor(type=ActorType.TOOL),
                    error={
                        "type": "TimeoutError",
                        "message": obs_text,
                    },
                )
                await telemetry_recorder.end_span(
                    batch_span.span_id,
                    status=SpanStatus.TIMEOUT,
                    error={"type": "TimeoutError", "message": obs_text},
                )
            tools_results = await self.handle_execution_error(
                tool_call_results=tool_call_results,
                error_message=obs_text,
                session_state=session_state,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
                tool_batch_name=tool_batch_name,
                telemetry_recorder=telemetry_recorder,
            )
            return obs_text, tools_results

        except Exception as e:
            obs_text = f"Error executing tool: {str(e)}"
            logger.error(obs_text)
            if telemetry_recorder is not None and batch_span is not None:
                await telemetry_recorder.emit_event(
                    "tool_batch_error",
                    actor=TelemetryActor(type=ActorType.TOOL),
                    error={"type": e.__class__.__name__, "message": str(e)},
                )
                await telemetry_recorder.end_span(
                    batch_span.span_id,
                    status=SpanStatus.ERROR,
                    error={"type": e.__class__.__name__, "message": str(e)},
                )
            tools_results = await self.handle_execution_error(
                tool_call_results=tool_call_results,
                error_message=obs_text,
                session_state=session_state,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
                tool_batch_name=tool_batch_name,
                telemetry_recorder=telemetry_recorder,
            )
            return obs_text, tools_results

    async def _execute_single_tool(
        self,
        *,
        single_tool: ToolCallResult,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        session_id: str | None,
        telemetry_recorder: Any = None,
    ) -> dict[str, Any]:
        if telemetry_recorder is None:
            return await single_tool.tool_executor.execute(
                agent_name=self.agent_name,
                tool_args=single_tool.tool_args,
                tool_name=single_tool.tool_name,
                tool_call_id=single_tool.tool_call_id,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
            )

        span = await telemetry_recorder.start_span(
            name=single_tool.tool_name,
            kind="tool.call",
            actor=TelemetryActor(type=ActorType.TOOL, name=single_tool.tool_name),
            input={
                "tool_name": single_tool.tool_name,
                "tool_args": single_tool.tool_args,
                "tool_call_id": single_tool.tool_call_id,
            },
        )
        try:
            await telemetry_recorder.emit_event(
                "tool_call",
                actor=TelemetryActor(type=ActorType.TOOL, name=single_tool.tool_name),
                input={
                    "tool_name": single_tool.tool_name,
                    "tool_args": single_tool.tool_args,
                    "tool_call_id": single_tool.tool_call_id,
                },
            )
            result = await single_tool.tool_executor.execute(
                agent_name=self.agent_name,
                tool_args=single_tool.tool_args,
                tool_name=single_tool.tool_name,
                tool_call_id=single_tool.tool_call_id,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
            )
            if result.get("status") == "error":
                await telemetry_recorder.emit_event(
                    "tool_error",
                    actor=TelemetryActor(
                        type=ActorType.TOOL, name=single_tool.tool_name
                    ),
                    output=result,
                    error={
                        "type": "ToolError",
                        "message": result.get("message") or "Tool returned error",
                    },
                )
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.ERROR,
                    output=result,
                    error={
                        "type": "ToolError",
                        "message": result.get("message") or "Tool returned error",
                    },
                )
            else:
                await telemetry_recorder.emit_event(
                    "tool_result",
                    actor=TelemetryActor(
                        type=ActorType.TOOL, name=single_tool.tool_name
                    ),
                    output=result,
                )
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.OK,
                    output=result,
                )
            return result
        except Exception as exc:
            await telemetry_recorder.record_exception(
                exc,
                event_type="tool_error",
                actor=TelemetryActor(type=ActorType.TOOL, name=single_tool.tool_name),
            )
            await telemetry_recorder.end_span(
                span.span_id,
                status=SpanStatus.ERROR,
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            raise
