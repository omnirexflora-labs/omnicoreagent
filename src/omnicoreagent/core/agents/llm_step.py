from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import dataclass
import time
from typing import Any

from omnicoreagent.core.continuation import continuation_summary
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
from omnicoreagent.core.llm import MODEL_RETRY_OBSERVER


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

        digest_canonicalizer = (
            telemetry_recorder.canonicalize_for_digest
            if telemetry_recorder is not None
            else None
        )
        try:
            if self.limits_enabled:
                self.usage_limits.check_before_request(usage=run_usage)

            if self.context_manager.should_trigger(session_state.messages):
                context_span = None
                before_evidence = context_evidence(
                    session_state.messages,
                    tools,
                    canonicalizer=digest_canonicalizer,
                )
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
                    after_evidence = context_evidence(
                        session_state.messages,
                        tools,
                        canonicalizer=digest_canonicalizer,
                    )
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
            context_summary = context_evidence(
                session_state.messages,
                tools,
                canonicalizer=digest_canonicalizer,
            )
            context_input = dict(context_summary)
            observation_ids = _context_observation_ids(session_state)
            new_observation_ids = [
                event_id
                for event_id in observation_ids
                if event_id not in session_state.delivered_observation_event_ids
            ]
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
                            "observation_event_ids": observation_ids,
                            "new_observation_event_ids": new_observation_ids,
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
                new_observation_event_ids=new_observation_ids,
            )
            session_state.delivered_observation_event_ids.update(new_observation_ids)
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
        purpose: str = "agent_turn",
        new_observation_event_ids: list[str] | None = None,
    ) -> tuple[Any, str | None, str | None, str | None]:
        stream_stats: dict[str, Any] = {
            "streaming": on_event is not None,
            "delta_count": 0,
            "visible_text_bytes": 0,
            "event_types": {},
        }
        timing: dict[str, float | None] = {"started": None, "first_delta": None}

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
                        if timing["first_delta"] is None:
                            timing["first_delta"] = time.perf_counter()
                        event_type = str(event.get("type", "unknown"))
                        stream_stats["delta_count"] += 1
                        event_types = stream_stats["event_types"]
                        event_types[event_type] = event_types.get(event_type, 0) + 1
                        text = event.get("text")
                        if isinstance(text, str):
                            stream_stats["visible_text_bytes"] += len(
                                text.encode("utf-8")
                            )
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
            "streaming": stream_stats["streaming"],
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
                "streaming": stream_stats["streaming"],
                "purpose": purpose,
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
                    "streaming": stream_stats["streaming"],
                    # Internal model work (context summaries) is marked so it
                    # is never mistaken for an agent decision.
                    "purpose": purpose,
                    # Tool observations this call delivers to the model for
                    # the first time: the observation -> next turn link.
                    "new_observation_event_ids": list(new_observation_event_ids or []),
                },
            )
            retries: list[dict[str, Any]] = []
            retry_token = MODEL_RETRY_OBSERVER.set(retries.append)
            timing["started"] = time.perf_counter()
            try:
                response = await request()
            finally:
                MODEL_RETRY_OBSERVER.reset(retry_token)
            normalized = normalize_model_turn(response)
            model_facts = self._model_call_facts(
                llm_connection,
                telemetry_recorder,
                timing=timing,
                purpose=purpose,
                retries=retries,
                normalized=normalized,
                usage=extract_response_usage(response),
            )
            response_payload = {
                "content": normalized.text,
                # Recorded only with model responses (capture="full"); the
                # recorder masks signatures and encrypted values.
                **(
                    {"continuation": normalized.provider_fields}
                    if normalized.provider_fields
                    else {}
                ),
                "tool_calls": [call.as_dict() for call in normalized.tool_calls],
                "tool_call_ids": [call.id for call in normalized.tool_calls],
                "finish_reason": normalized.finish_reason,
                "refusal": normalized.refusal,
                "usage": self._usage_payload(extract_response_usage(response)),
                "stream_stats": stream_stats,
            }
            standard_usage = _standard_token_usage(model_facts["tokens"])
            response_event = await telemetry_recorder.emit_event(
                "model_response",
                token_usage=standard_usage,
                estimated_cost_usd=model_facts["estimated_cost_usd"],
                actor=TelemetryActor(type=ActorType.MODEL),
                output=response_payload,
                metadata={
                    "model_span_id": span_context.span_id,
                    "model_call_event_id": model_call_event.event_id,
                    "context_span_id": context_span_id,
                    "context_digest": (context_evidence or {}).get("context_digest"),
                    "tool_call_ids": response_payload["tool_call_ids"],
                    # Facts about the call itself are metadata so they are
                    # recorded under every capture policy.
                    "model_call": model_facts,
                },
            )
            await telemetry_recorder.end_span(
                span_context.span_id,
                status=SpanStatus.OK,
                token_usage=standard_usage,
                estimated_cost_usd=model_facts["estimated_cost_usd"],
                output={
                    "tool_call_ids": response_payload["tool_call_ids"],
                    "context_span_id": context_span_id,
                    "context_digest": (context_evidence or {}).get("context_digest"),
                    "stream_stats": stream_stats,
                    "usage": response_payload["usage"],
                    **model_facts,
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
                output={
                    "stream_stats": stream_stats,
                    **self._model_call_facts(
                        llm_connection,
                        telemetry_recorder,
                        timing=timing,
                        purpose=purpose,
                        retries=locals().get("retries", []),
                    ),
                },
            )
            raise
        except Exception as exc:
            failed_facts = self._model_call_facts(
                llm_connection,
                telemetry_recorder,
                timing=timing,
                purpose=purpose,
                retries=locals().get("retries", []),
            )
            await telemetry_recorder.record_exception(
                exc,
                event_type="model_error",
                actor=TelemetryActor(type=ActorType.MODEL),
                metadata={
                    "model_span_id": span_context.span_id,
                    "model_call": failed_facts,
                },
            )
            await telemetry_recorder.end_span(
                span_context.span_id,
                status=SpanStatus.ERROR,
                output={"stream_stats": stream_stats, **failed_facts},
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            raise

    @staticmethod
    def _model_call_facts(
        llm_connection: Any,
        telemetry_recorder: Any,
        *,
        timing: dict[str, float | None],
        retries: list[dict[str, Any]],
        purpose: str = "agent_turn",
        normalized: ModelTurn | None = None,
        usage: Usage | None = None,
    ) -> dict[str, Any]:
        """Describe one model call: identity, settings, tokens, timing, attempts."""
        now = time.perf_counter()
        started = timing.get("started")
        first_delta = timing.get("first_delta")
        settings_getter = getattr(llm_connection, "request_settings", None)
        try:
            request_settings = settings_getter() if callable(settings_getter) else None
        except Exception:
            request_settings = None
        tokens = None
        if usage is not None:
            details = usage.details or {}
            tokens = {
                "input": usage.request_tokens,
                "output": usage.response_tokens,
                "total": usage.total_tokens,
            }
            if "cached_input_tokens" in details:
                tokens["cached_input"] = details["cached_input_tokens"]
            if "reasoning_tokens" in details:
                tokens["reasoning"] = details["reasoning_tokens"]
        response_metadata = normalized.response_metadata if normalized else {}
        cost = response_metadata.get("cost_usd")
        cost_source = "provider_response" if cost is not None else None
        estimate = getattr(llm_connection, "estimate_cost", None)
        if cost is None and usage is not None and callable(estimate):
            try:
                cost = estimate(usage)
            except Exception:
                cost = None
            cost_source = "price_table" if cost is not None else None
        return {
            "purpose": purpose,
            "request_settings": request_settings,
            "provider_response_id": response_metadata.get("id"),
            "provider_model": response_metadata.get("model"),
            "finish_reason": normalized.finish_reason if normalized else None,
            "refused": bool(normalized.refusal) if normalized else False,
            "tokens": tokens,
            # A LiteLLM price-table figure, not an invoiced amount.
            "estimated_cost_usd": cost,
            "cost_source": cost_source,
            "latency_ms": (
                round((now - started) * 1000, 3) if started is not None else None
            ),
            # First streamed output delivered to the caller (text deltas);
            # a turn that streams only tool-call deltas has none.
            "time_to_first_delta_ms": (
                round((first_delta - started) * 1000, 3)
                if started is not None and first_delta is not None
                else None
            ),
            "attempts": len(retries) + 1,
            "retries": [
                {
                    **retry,
                    "message": telemetry_recorder.redact_text(str(retry["message"])),
                }
                for retry in retries
            ],
            # Presence of provider continuation data (counts and a digest),
            # recorded under every capture policy; the values never are.
            **(
                {"continuation": summary}
                if (summary := continuation_summary(normalized)) is not None
                else {}
            ),
        }

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
                    context_evidence=context_evidence(
                        summary_messages,
                        canonicalizer=telemetry_recorder.canonicalize_for_digest,
                    ),
                    context_span_id=parent_span_id,
                    purpose="context_summary",
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


def _standard_token_usage(tokens: dict[str, Any] | None) -> dict[str, Any] | None:
    """Map model call tokens onto the standard span/event ``token_usage`` field."""
    if tokens is None:
        return None
    return {
        "prompt_tokens": tokens.get("input"),
        "completion_tokens": tokens.get("output"),
        "total_tokens": tokens.get("total"),
    }


def _context_observation_ids(session_state: SessionState) -> list[str]:
    """Observation events whose tool messages are in the model context, in order."""
    known = getattr(session_state, "observation_event_ids", None) or {}
    ids: list[str] = []
    for message in session_state.messages:
        record = message_record(message)
        if record.get("role") != "tool":
            continue
        event_id = known.get(record.get("tool_call_id"))
        if event_id is not None and event_id not in ids:
            ids.append(event_id)
    return ids
