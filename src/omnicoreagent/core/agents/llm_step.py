from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omnicoreagent.core.agents.llm_response import (
    extract_response_content,
    extract_response_usage,
)
from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor
from omnicoreagent.core.system_prompts import FAST_CONVERSATION_SUMMARY_PROMPT
from omnicoreagent.core.token_usage import (
    Usage,
    UsageLimitExceeded,
    UsageLimits,
    session_stats,
    usage,
)
from omnicoreagent.core.types import SessionState
from omnicoreagent.core.logging import logger


@dataclass
class AgentLlmStepResult:
    response: str | None = None
    error_result: dict[str, Any] | None = None


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
    ) -> AgentLlmStepResult:
        if debug:
            logger.info(f"Sending {len(session_state.messages)} messages to LLM")

        try:
            if self.limits_enabled:
                self.usage_limits.check_before_request(usage=run_usage)

            if self.context_manager.should_trigger(session_state.messages):
                session_state.messages = await self.context_manager.manage_context(
                    messages=session_state.messages,
                    summarize_fn=self._build_context_summarizer(llm_connection),
                )
                if debug:
                    logger.info(
                        f"Context managed: now {len(session_state.messages)} messages"
                    )

            response = await self._call_model(
                llm_connection=llm_connection,
                messages=session_state.messages,
                telemetry_recorder=telemetry_recorder,
            )
            if response:
                await self._record_response(
                    response=response,
                    run_usage=run_usage,
                    session_id=session_id,
                    telemetry_recorder=telemetry_recorder,
                    debug=debug,
                )
                response = extract_response_content(response)
            return AgentLlmStepResult(response=response)

        except UsageLimitExceeded as e:
            error_message = f"Usage limit error: {e}"
            logger.error(error_message)
            return AgentLlmStepResult(
                error_result={"answer": error_message, "usage": run_usage}
            )

        except Exception as e:
            error_message = "Model encountered an error, please do retry again"
            logger.error(f"{error_message}: {e}")
            return AgentLlmStepResult(
                error_result={"answer": error_message, "usage": run_usage}
            )

    async def _call_model(
        self,
        *,
        llm_connection: Any,
        messages: list[Any],
        telemetry_recorder: Any = None,
    ) -> Any:
        if telemetry_recorder is None:
            return await llm_connection.llm_call(messages)

        span_context = await telemetry_recorder.start_span(
            name="model.call",
            kind="model.call",
            actor=TelemetryActor(type=ActorType.MODEL),
            input={"message_count": len(messages)},
        )
        try:
            await telemetry_recorder.emit_event(
                "model_call",
                actor=TelemetryActor(type=ActorType.MODEL),
                input={"message_count": len(messages)},
            )
            response = await llm_connection.llm_call(messages)
            await telemetry_recorder.emit_event(
                "model_response",
                actor=TelemetryActor(type=ActorType.MODEL),
                output={
                    "content": extract_response_content(response, strip=False),
                    "usage": self._usage_payload(extract_response_usage(response)),
                },
            )
            await telemetry_recorder.end_span(
                span_context.span_id,
                status=SpanStatus.OK,
                output={"usage": self._usage_payload(extract_response_usage(response))},
            )
            return response
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

    def _build_context_summarizer(self, llm_connection: Any):
        async def summarize_for_context(messages):
            history_text = "\n".join(
                [
                    f"{message.role if hasattr(message, 'role') else message.get('role', 'unknown')}: "
                    f"{message.content if hasattr(message, 'content') else message.get('content', '')}"
                    for message in messages
                ]
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
            response = await llm_connection.llm_call(summary_messages)
            return extract_response_content(response, default="")

        return summarize_for_context

    async def _record_response(
        self,
        *,
        response: Any,
        run_usage: Usage,
        session_id: str,
        telemetry_recorder: Any = None,
        debug: bool,
    ):
        request_usage = extract_response_usage(response)
        if not request_usage:
            return

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
