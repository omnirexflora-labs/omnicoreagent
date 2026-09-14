from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from omnicoreagent.core.agents.llm_response import (
    extract_response_content,
    extract_response_usage,
    normalize_model_turn,
)
from omnicoreagent.core.telemetry import (
    ActorType,
    SpanStatus,
    TelemetryActor,
    TraceStatus,
)
from omnicoreagent.core.system_prompts import FAST_CONVERSATION_SUMMARY_PROMPT
from omnicoreagent.core.token_usage import (
    Usage,
    UsageLimitExceeded,
    UsageLimits,
    session_stats,
    usage,
)
from omnicoreagent.core.types import SessionState
from omnicoreagent.core.model_protocol import ModelTurn
from omnicoreagent.core.logging import logger
from omnicoreagent.core.interaction_history import context_evidence, message_record


@dataclass
class AgentLlmStepResult:
    response: ModelTurn | None = None
    error_result: dict[str, Any] | None = None
    model_call_span_id: str | None = None
    model_call_event_id: str | None = None
    model_response_event_id: str | None = None


class AgentLlmStepRunner:
    def __init__(
        self,
        *,
        agent_name: str,
        context_manager: Any,
        usage_limits: UsageLimits,
        limits_enabled: bool,
        request_limit: int,
    ):
        self.agent_name = agent_name
        self.context_manager = context_manager
        self.usage_limits = usage_limits
        self.limits_enabled = limits_enabled
        self.request_limit = request_limit

    async def run(
        self,
        *,
        session_state: SessionState,
        llm_connection: Any,
        run_usage: Usage,
        session_id: str,
        telemetry_recorder: Any = None,
        debug: bool = False,
        tools: list[dict[str, Any]] | None = None,
        on_event: Any = None,
    ) -> AgentLlmStepResult:
        if debug:
            logger.info(f"Sending {len(session_state.messages)} messages to LLM")

        try:
            if self.limits_enabled:
                self.usage_limits.check_before_request(usage=run_usage)

            if self.context_manager.should_trigger(session_state.messages):
                context_span = None
                before_evidence = context_evidence(session_state.messages, tools)
                compression_input = dict(before_evidence)
                if telemetry_recorder is not None and telemetry_recorder.config.record_model_prompts:
                    compression_input["messages"] = [
                        message_record(message) for message in session_state.messages
                    ]
                try:
                    if telemetry_recorder is not None:
                        context_span = await telemetry_recorder.start_span(
                            name="context.compression",
                            kind="context.compression",
                            actor=TelemetryActor(type=ActorType.SYSTEM),
                            input=compression_input,
                        )
                    session_state.messages = await self.context_manager.manage_context(
                        messages=session_state.messages,
                        summarize_fn=self._build_context_summarizer(
                            llm_connection,
                            telemetry_recorder=telemetry_recorder,
                            parent_span_id=(
                                context_span.span_id if context_span is not None else None
                            ),
                        ),
                    )
                    after_evidence = context_evidence(session_state.messages, tools)
                    compression_output = {
                        "before": before_evidence,
                        "after": after_evidence,
                        "dropped_message_digests": [
                            digest
                            for digest in before_evidence["message_digests"]
                            if digest not in after_evidence["message_digests"]
                        ],
                        "stats": self.context_manager.get_stats(),
                    }
                    if telemetry_recorder is not None:
                        await telemetry_recorder.emit_event(
                            "context_compression",
                            actor=TelemetryActor(type=ActorType.SYSTEM),
                            input=compression_input,
                            output=compression_output,
                        )
                        if context_span is not None:
                            await telemetry_recorder.end_span(
                                context_span.span_id,
                                status=SpanStatus.OK,
                                output=compression_output,
                            )
                except Exception as exc:
                    if telemetry_recorder is not None and context_span is not None:
                        await telemetry_recorder.emit_event(
                            "context_dropped",
                            actor=TelemetryActor(type=ActorType.SYSTEM),
                            input=compression_input,
                            error={
                                "type": exc.__class__.__name__,
                                "message": str(exc),
                            },
                        )
                        await telemetry_recorder.end_span(
                            context_span.span_id,
                            status=SpanStatus.ERROR,
                            error={
                                "type": exc.__class__.__name__,
                                "message": str(exc),
                            },
                        )
                    raise
                if debug:
                    logger.info(
                        f"Context managed: now {len(session_state.messages)} messages"
                    )

            context_span = None
            context_summary = context_evidence(session_state.messages, tools)
            context_input = dict(context_summary)
            if telemetry_recorder is not None:
                if telemetry_recorder.config.record_model_prompts:
                    context_input["messages"] = [
                        message_record(message) for message in session_state.messages
                    ]
                    context_input["tools"] = tools or []
                context_span = await telemetry_recorder.start_span(
                    name="context.assembly",
                    kind="context.assembly",
                    actor=TelemetryActor(type=ActorType.SYSTEM),
                    input=context_input,
                    attributes={
                        "context_digest": context_summary["context_digest"],
                        "message_digests": context_summary["message_digests"],
                        "tool_names": context_summary["tool_names"],
                    },
                )
                try:
                    await telemetry_recorder.emit_event(
                        "context_assembly",
                        actor=TelemetryActor(type=ActorType.SYSTEM),
                        input=context_input,
                        output=context_summary,
                        metadata={
                            "context_span_id": context_span.span_id,
                            "context_digest": context_summary["context_digest"],
                        },
                    )
                except Exception as exc:
                    await telemetry_recorder.end_span(
                        context_span.span_id,
                        status=SpanStatus.ERROR,
                        error={"type": exc.__class__.__name__, "message": str(exc)},
                    )
                    raise
                else:
                    await telemetry_recorder.end_span(
                        context_span.span_id,
                        status=SpanStatus.OK,
                        output=context_summary,
                    )

            (
                response,
                model_call_span_id,
                model_call_event_id,
                model_response_event_id,
            ) = await self._call_model(
                llm_connection=llm_connection,
                messages=session_state.messages,
                tools=tools,
                on_event=on_event,
                telemetry_recorder=telemetry_recorder,
                context_evidence=context_summary,
                context_span_id=(context_span.span_id if context_span else None),
            )
            if response is None:
                raise ValueError("Provider returned no response")
            if response is not None:
                await self._record_response(
                    response=response,
                    run_usage=run_usage,
                    session_id=session_id,
                    telemetry_recorder=telemetry_recorder,
                    debug=debug,
                )
                response = normalize_model_turn(response)
            return AgentLlmStepResult(
                response=response,
                model_call_span_id=model_call_span_id,
                model_call_event_id=model_call_event_id,
                model_response_event_id=model_response_event_id,
            )

        except UsageLimitExceeded as e:
            error_message = f"Usage limit error: {e}"
            logger.error(error_message)
            if telemetry_recorder is not None:
                await self._record_resource_guard_halt(
                    telemetry_recorder=telemetry_recorder,
                    error_message=error_message,
                    run_usage=run_usage,
                )
            return AgentLlmStepResult(
                error_result={
                    "answer": error_message,
                    "usage": run_usage,
                    "_trace_status": TraceStatus.ABORTED_RESOURCE_GUARD.value,
                    "status": "error",
                    "termination_reason": "resource_limit",
                }
            )

        except Exception as e:
            error_message = "Model encountered an error, please do retry again"
            logger.error(f"{error_message}: {e}")
            return AgentLlmStepResult(
                error_result={
                    "answer": error_message,
                    "usage": run_usage,
                    "status": "error",
                    "termination_reason": "provider_error",
                }
            )

    async def _call_model(
        self,
        *,
        llm_connection: Any,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        on_event: Any = None,
        telemetry_recorder: Any = None,
        context_evidence: dict[str, Any] | None = None,
        context_span_id: str | None = None,
    ) -> tuple[Any, str | None, str | None, str | None]:
        async def request():
            if on_event is None:
                return await llm_connection.llm_call(messages, tools=tools)
            response = None
            async with aclosing(
                llm_connection.llm_stream(messages, tools=tools)
            ) as stream:
                async for event in stream:
                    if event["type"] == "turn_complete":
                        if response is not None:
                            raise ValueError("Provider stream returned multiple turns")
                        response = event["turn"]
                    else:
                        if response is not None:
                            raise ValueError(
                                "Provider stream emitted text after completion"
                            )
                        await on_event(event)
            if response is None:
                raise ValueError("Provider stream ended without a complete turn")
            return response

        if telemetry_recorder is None:
            return await request(), None, None, None

        tool_names = sorted(
            str(tool.get("function", {}).get("name", tool.get("name", "")))
            for tool in tools or []
        )
        model_input = {
            "message_count": len(messages),
            "context_digest": (context_evidence or {}).get("context_digest"),
        }
        if telemetry_recorder.config.record_model_prompts:
            model_input["messages"] = [message_record(message) for message in messages]
            model_input["tools"] = tools or []

        span_context = await telemetry_recorder.start_span(
            name="model.call",
            kind="model.call",
            actor=TelemetryActor(type=ActorType.MODEL),
            input=model_input,
            attributes={
                "context_digest": (context_evidence or {}).get("context_digest"),
                "context_span_id": context_span_id,
            },
        )
        try:
            model_call_event = await telemetry_recorder.emit_event(
                "model_call",
                actor=TelemetryActor(type=ActorType.MODEL),
                input={
                    **model_input,
                    "tool_count": len(tools or []),
                    "tool_names": tool_names,
                    "model_span_id": span_context.span_id,
                },
                metadata={
                    "model_span_id": span_context.span_id,
                    "context_span_id": context_span_id,
                    "context_digest": (context_evidence or {}).get("context_digest"),
                },
            )
            response = await request()
            normalized = normalize_model_turn(response)
            response_payload = {
                "content": normalized.text,
                "tool_calls": [call.as_dict() for call in normalized.tool_calls],
                "tool_call_ids": [call.id for call in normalized.tool_calls],
                "finish_reason": normalized.finish_reason,
                "refusal": normalized.refusal,
                "usage": self._usage_payload(extract_response_usage(response)),
            }
            response_event = await telemetry_recorder.emit_event(
                "model_response",
                actor=TelemetryActor(type=ActorType.MODEL),
                output=response_payload,
                metadata={
                    "model_span_id": span_context.span_id,
                    "model_call_event_id": model_call_event.event_id,
                    "context_span_id": context_span_id,
                    "context_digest": (context_evidence or {}).get("context_digest"),
                    "tool_call_ids": response_payload["tool_call_ids"],
                },
            )
            await telemetry_recorder.end_span(
                span_context.span_id,
                status=SpanStatus.OK,
                output={
                    "finish_reason": normalized.finish_reason,
                    "refusal": normalized.refusal,
                    "tool_call_ids": response_payload["tool_call_ids"],
                    "context_span_id": context_span_id,
                    "context_digest": (context_evidence or {}).get("context_digest"),
                    "usage": response_payload["usage"],
                },
            )
            return (
                response,
                span_context.span_id,
                model_call_event.event_id,
                response_event.event_id,
            )
        except asyncio.CancelledError:
            await telemetry_recorder.end_span(
                span_context.span_id,
                status=SpanStatus.CANCELLED,
            )
            raise
        except Exception as exc:
            await telemetry_recorder.record_exception(
                exc,
                event_type="model_error",
                actor=TelemetryActor(type=ActorType.MODEL),
            )
            await telemetry_recorder.end_span(
                span_context.span_id,
                status=SpanStatus.ERROR,
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            raise

    def _build_context_summarizer(
        self,
        llm_connection: Any,
        *,
        telemetry_recorder: Any = None,
        parent_span_id: str | None = None,
    ):
        async def summarize_for_context(messages):
            from omnicoreagent.core.interaction_history import (
                render_message,
                message_record,
            )

            history_text = "\n".join(
                f"{message_record(message).get('role', 'unknown')}: {render_message(message)}"
                for message in messages
            )
            summary_messages = [
                {
                    "role": "system",
                    "content": FAST_CONVERSATION_SUMMARY_PROMPT,
                },
                {
                    "role": "user",
                    "content": f"Here is the conversation history: {history_text}",
                },
            ]
            if telemetry_recorder is None:
                response = await llm_connection.llm_call(summary_messages)
            else:
                response, _, _, _ = await self._call_model(
                    llm_connection=llm_connection,
                    messages=summary_messages,
                    telemetry_recorder=telemetry_recorder,
                    context_evidence=context_evidence(summary_messages),
                    context_span_id=parent_span_id,
                )
            return extract_response_content(response, default="")

        return summarize_for_context

    async def _record_resource_guard_halt(
        self,
        *,
        telemetry_recorder: Any,
        error_message: str,
        run_usage: Usage,
    ) -> None:
        span = await telemetry_recorder.start_span(
            name="runtime.control",
            kind="runtime.control",
            actor=TelemetryActor(type=ActorType.SYSTEM),
            input={
                "control": "usage_limit",
                "usage": self._usage_payload(run_usage),
            },
        )
        await telemetry_recorder.emit_event(
            "resource_guard_halt",
            actor=TelemetryActor(type=ActorType.SYSTEM),
            input={
                "control": "usage_limit",
                "usage": self._usage_payload(run_usage),
            },
            error={"type": "UsageLimitExceeded", "message": error_message},
        )
        await telemetry_recorder.end_span(
            span.span_id,
            status=SpanStatus.ERROR,
            error={"type": "UsageLimitExceeded", "message": error_message},
        )

    async def _record_response(
        self,
        *,
        response: Any,
        run_usage: Usage,
        session_id: str,
        telemetry_recorder: Any = None,
        debug: bool,
    ):
        request_usage = extract_response_usage(response) or Usage(requests=1)

        usage.incr(request_usage)
        run_usage.incr(request_usage)

        if not self.limits_enabled:
            return

        self.usage_limits.check_tokens(run_usage)
        remaining_tokens = self.usage_limits.remaining_tokens(run_usage)
        used_tokens = run_usage.total_tokens
        used_requests = run_usage.requests
        remaining_requests = self.request_limit - used_requests
        session_stats.update(
            {
                "used_requests": used_requests,
                "used_tokens": used_tokens,
                "remaining_requests": remaining_requests,
                "remaining_tokens": remaining_tokens,
                "request_tokens": request_usage.request_tokens,
                "response_tokens": request_usage.response_tokens,
                "total_tokens": request_usage.total_tokens,
            }
        )
        if debug:
            logger.info(
                f"API Call Stats - Requests: {used_requests}/{self.request_limit}, "
                f"Tokens: {used_tokens}/{self.usage_limits.total_tokens_limit}, "
                f"Request Tokens: {request_usage.request_tokens}, "
                f"Response Tokens: {request_usage.response_tokens}, "
                f"Total Tokens: {request_usage.total_tokens}, "
                f"Remaining Requests: {remaining_requests}, "
                f"Remaining Tokens: {remaining_tokens}"
            )

    def _usage_payload(self, request_usage: Usage | None) -> dict[str, Any] | None:
        if request_usage is None:
            return None
        return {
            "requests": request_usage.requests,
            "request_tokens": request_usage.request_tokens,
            "response_tokens": request_usage.response_tokens,
            "total_tokens": request_usage.total_tokens,
            "total_time": request_usage.total_time,
            "details": request_usage.details,
        }
