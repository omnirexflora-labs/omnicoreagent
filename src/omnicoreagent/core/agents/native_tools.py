"""Execute a complete native tool turn and persist correlated feedback."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from omnicoreagent.core.agents.loop_detection import ToolInteraction
from omnicoreagent.core.model_protocol import ModelTurn
from omnicoreagent.core.runtime.deadline import stop_after
from omnicoreagent.core.tools.local_tool_handler import LocalToolHandler
from omnicoreagent.governance.errors import PolicyDeniedError
from omnicoreagent.core.tools.mcp_tool_handler import MCPToolHandler
from omnicoreagent.core.tools.tool_executor import ToolExecutor
from omnicoreagent.core.types import AgentState, ToolCallResult
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor


@dataclass
class ToolFeedback:
    message: dict
    metadata: dict
    interaction: ToolInteraction
    observation_event_id: str | None = None


class CallbackHandler:
    def __init__(self, callback):
        self.callback = callback

    async def call(self, tool_name, tool_args):
        return await self.callback(tool_args)


async def execute_native_turn(
    agent,
    *,
    turn: ModelTurn,
    catalog,
    local_tools,
    sessions,
    session_state,
    session_id,
    add_message_to_history,
    run_usage,
    telemetry_recorder=None,
    model_call_span_id: str | None = None,
    model_call_event_id: str | None = None,
    model_response_event_id: str | None = None,
    agent_step_span_id: str | None = None,
):
    # Decode once. Freeze resolution against the schemas supplied for this turn;
    # discovery cannot unlock a sibling in the same batch.
    decoded_arguments = {}
    resolutions = {}
    rejection_reasons: dict[str, str] = {}
    for request in turn.tool_calls:
        try:
            arguments = request.decode_arguments()
        except ValueError as exc:
            resolutions[request.id] = exc
            rejection_reasons[request.id] = "invalid_arguments"
            continue
        decoded_arguments[request.id] = arguments
        try:
            resolutions[request.id] = catalog.resolve(request, arguments=arguments)
        except ValueError as exc:
            resolutions[request.id] = exc
            rejection_reasons[request.id] = (
                "unknown_tool"
                if request.name.lower() not in catalog.visible
                else "arguments_rejected"
            )
    # Event IDs of each call's request/resolution records, so the execution
    # records can point back to them.
    call_links: dict[str, dict[str, str | None]] = {}

    assistant = turn.assistant_message()
    # History keeps the real arguments so the model sees its own past calls in
    # later runs; governance redacts them in telemetry, not here.
    stored_calls = deepcopy(assistant["tool_calls"])
    metadata = {
        "agent_name": agent.agent_name,
        "interaction_version": 2,
        "has_tool_calls": True,
        "tool_calls": stored_calls,
        "model_message": {**assistant, "tool_calls": stored_calls},
    }
    await add_message_to_history(
        role="assistant", content=turn.text, metadata=metadata, session_id=session_id
    )
    session_state.messages.append(assistant)
    session_state.state = AgentState.TOOL_CALLING

    async def one(request):
        resolved = None
        outcome: dict = {}
        signature_args = deepcopy(decoded_arguments.get(request.id, request.arguments))
        try:
            resolution = resolutions[request.id]
            if isinstance(resolution, ValueError):
                raise resolution
            binding, arguments = resolution
            if binding.provider == "subagent":

                async def delegate(params):
                    if agent.governance_engine is not None:
                        from omnicoreagent.governance.capabilities import (
                            subagent_spawn_authority_requests,
                        )

                        # A governed agent must not reach tools through a
                        # child that nothing governs.
                        if not _is_governed(binding.agent):
                            raise PolicyDeniedError(
                                f"Delegation refused: agent '{binding.agent.name}' is not "
                                "governed. A governed agent can only delegate to agents "
                                "with governance enabled."
                            )

                        child_tools = getattr(binding.agent, "local_tools", None)
                        await agent.governance_engine.authorize_all(
                            subagent_spawn_authority_requests(
                                subagent_specs=[
                                    {
                                        "name": binding.agent.name,
                                        "task": params.get("query", ""),
                                    }
                                ],
                                tool_names=[
                                    tool["name"]
                                    for tool in child_tools.get_available_tools()
                                ]
                                if child_tools
                                else [],
                                mcp_servers=list(
                                    getattr(binding.agent, "mcp_tools", {}) or {}
                                ),
                                memory_scope=session_id,
                            )
                        )
                    name, result = await agent.subagent_runner.run(
                        {"agent": binding.agent.name, "parameters": params},
                        [binding.agent],
                        session_id,
                        telemetry_recorder=telemetry_recorder,
                        redact_parameters=agent.governance_engine is not None,
                    )
                    if isinstance(result, BaseException):
                        raise result
                    if isinstance(result, dict) and isinstance(
                        result.get("metric"), Usage
                    ):
                        run_usage.incr(result["metric"])
                    return {
                        "status": result.get("status", "success")
                        if isinstance(result, dict)
                        else "success",
                        "data": result,
                    }

                handler = CallbackHandler(delegate)
            elif binding.provider == "discovery":

                async def discover(params):
                    return {
                        "status": "success",
                        "data": catalog.discover(params["query"]),
                    }

                handler = CallbackHandler(discover)
            elif binding.provider == "mcp":
                handler = MCPToolHandler(
                    sessions or {},
                    binding.server,
                    guardrail=agent.guardrail,
                    telemetry_recorder=telemetry_recorder,
                    tool_call_id=request.id,
                )
            else:
                handler = LocalToolHandler(local_tools)
            resolved = ToolCallResult(
                ToolExecutor(handler),
                binding.name,
                arguments,
                request.id,
                binding.provider if binding.provider != "subagent" else "local",
                binding.server,
            )
            # The deadline records why it stopped the call, so the tool record
            # reports a timeout distinctly from a cancelled run.
            async with stop_after(agent.tool_call_timeout):
                result = await agent.governed_tool_runner.execute(
                    single_tool=resolved,
                    telemetry_recorder=telemetry_recorder,
                    result_guardrail=agent.guardrail,
                    telemetry_links={
                        "batch_id": batch_id,
                        "model_call_event_id": model_call_event_id,
                        "model_response_event_id": model_response_event_id,
                        **call_links.get(request.id, {}),
                        "tool_provider": binding.provider,
                    },
                    telemetry_outcome=outcome,
                )
        except asyncio.CancelledError:
            result = {
                "tool_name": request.name,
                "args": {},
                "status": "error",
                "data": None,
                "message": "Tool execution cancelled",
                "error_type": "cancelled",
            }
        except asyncio.TimeoutError:
            result = {
                "tool_name": request.name,
                "args": {},
                "status": "error",
                "data": None,
                "message": "Tool execution timed out",
                "error_type": "timeout",
            }
        except Exception as exc:
            result = {
                "tool_name": request.name,
                "args": {},
                "status": "error",
                "data": None,
                "message": str(exc),
            }
        # Loop signatures use normalized, guarded contents before artifact IDs
        # and governed-history redaction can change their representation.
        signature_result = {
            key: value for key, value in result.items() if key != "args"
        }
        binding = catalog.bindings.get(request.name.lower())
        if request.name.lower() not in catalog.visible:
            binding = None
        if isinstance(signature_result.get("governance"), dict):
            signature_result["governance"] = {
                key: value
                for key, value in signature_result["governance"].items()
                if key not in {"request_id", "decision_id"}
            }
        if (
            binding
            and binding.provider == "subagent"
            and isinstance(signature_result.get("data"), dict)
        ):
            # Child-run accounting/trace IDs change even when its answer does not.
            # Do not strip similarly named fields from ordinary business payloads.
            signature_result["data"] = {
                key: value
                for key, value in signature_result["data"].items()
                if key not in {"metric", "run_id", "trace_id", "session_id"}
            }
        interaction = ToolInteraction(
            provider=binding.provider if binding else "unavailable",
            server=binding.server if binding else None,
            name=binding.name if binding else request.name.lower(),
            arguments=signature_args,
            result={
                key: value
                for key, value in signature_result.items()
                if key != "tool_name"
            },
        )
        original_data = result.get("data")
        result = agent.tool_result_offloader.maybe_offload_result(
            result,
            session_id,
            tool_call_result=resolved,
        )
        if telemetry_recorder is not None and result.get("data") != original_data:
                await telemetry_recorder.emit_event(
                    "workspace_offload",
                    actor=TelemetryActor(type=ActorType.WORKSPACE),
                output={
                    "tool_call_id": request.id,
                    "tool_name": result["tool_name"],
                        "reference": result.get("data"),
                    },
                    metadata={
                        "batch_id": batch_id,
                        "tool_call_id": request.id,
                        "model_call_event_id": model_call_event_id,
                        "model_response_event_id": model_response_event_id,
                        "phase": "tool_result_offload",
                    },
                )
        content = json.dumps(result, ensure_ascii=False, default=str)
        observation_event_id = None
        if telemetry_recorder is not None:
            observation_event = await telemetry_recorder.emit_event(
                "tool_observation",
                actor=TelemetryActor(type=ActorType.MODEL),
                output={
                    "tool_call_id": request.id,
                    "tool_name": result["tool_name"],
                    "message": {
                        "role": "tool",
                        "tool_call_id": request.id,
                        "content": content,
                    },
                },
                metadata={
                    "batch_id": batch_id,
                    "tool_call_id": request.id,
                    "model_call_event_id": model_call_event_id,
                    "model_response_event_id": model_response_event_id,
                    "observation_for": request.id,
                    # What this observation was built from; a rejected call
                    # has no execution record, only its request.
                    "tool_requested_event_id": call_links.get(request.id, {}).get(
                        "tool_requested_event_id"
                    ),
                    "tool_span_id": outcome.get("tool_span_id"),
                    "tool_result_event_id": outcome.get("tool_result_event_id"),
                },
            )
            observation_event_id = observation_event.event_id
            session_state.observation_event_ids[request.id] = observation_event_id
        return ToolFeedback(
            message={"role": "tool", "content": content, "tool_call_id": request.id},
            metadata={
                "agent_name": agent.agent_name,
                "interaction_version": 2,
                "tool_call_id": request.id,
                "tool": result["tool_name"],
                "args": result.get("args", {}),
                "observation_event_id": observation_event_id,
            },
            interaction=interaction,
            observation_event_id=observation_event_id,
        )

    batch_span = None
    batch_id = None
    if telemetry_recorder is not None:
        batch_args = []
        for request in turn.tool_calls:
            arguments = decoded_arguments.get(request.id)
            batch_args.append(
                {"invalid_arguments": True}
                if arguments is None
                else {key: "[REDACTED]" for key in arguments}
                if agent.governance_engine
                else arguments
            )
        payload = {"tool_count": len(turn.tool_calls), "tool_batch_args": batch_args}
        batch_span = await telemetry_recorder.start_span(
            name="tool.batch",
            kind="tool.batch",
            actor=TelemetryActor(type=ActorType.TOOL),
            input=payload,
        )
        batch_id = batch_span.span_id
        batch_metadata = {
            "batch_id": batch_id,
            "agent_step_span_id": agent_step_span_id,
            "model_call_span_id": model_call_span_id,
            "model_call_event_id": model_call_event_id,
            "model_response_event_id": model_response_event_id,
            "tool_call_ids": [request.id for request in turn.tool_calls],
        }
        await telemetry_recorder.emit_event(
            "tool_batch_start",
            input=payload,
            metadata=batch_metadata,
        )
        for request in turn.tool_calls:
            arguments = decoded_arguments.get(request.id)
            requested_input = {
                "tool_call_id": request.id,
                "tool_name": request.name,
                "arguments": (
                    {key: "[REDACTED]" for key in arguments}
                    if arguments is not None and agent.governance_engine
                    else arguments
                ),
                # The exact argument text the model produced, including text
                # that is not valid JSON; redacted whole under governance.
                "raw_arguments": (
                    "[REDACTED]" if agent.governance_engine else request.arguments
                ),
            }
            resolution = resolutions[request.id]
            rejection_reason = rejection_reasons.get(request.id)
            resolution_output: dict = {
                "status": "resolved" if isinstance(resolution, tuple) else "rejected",
            }
            if rejection_reason is not None:
                resolution_output["rejection_reason"] = rejection_reason
            if isinstance(resolution, tuple):
                binding, _ = resolution
                resolution_output.update(
                    {
                        "provider": binding.provider,
                        "server": binding.server,
                        "resolved_name": binding.name,
                    }
                )
            else:
                resolution_output.update(
                    {
                        "error_type": resolution.__class__.__name__,
                        "error": str(resolution),
                    }
                )
            relationship_metadata = {
                **batch_metadata,
                "tool_call_id": request.id,
                "rejection_reason": rejection_reason,
            }
            requested_event = await telemetry_recorder.emit_event(
                "tool_requested",
                actor=TelemetryActor(type=ActorType.MODEL),
                input=requested_input,
                output=resolution_output,
                metadata=relationship_metadata,
            )
            links = {"tool_requested_event_id": requested_event.event_id}
            if isinstance(resolution, tuple):
                resolved_event = await telemetry_recorder.emit_event(
                    "tool_resolved",
                    actor=TelemetryActor(type=ActorType.SYSTEM),
                    input=requested_input,
                    output=resolution_output,
                    metadata={
                        **relationship_metadata,
                        "tool_requested_event_id": requested_event.event_id,
                    },
                )
                links["tool_resolved_event_id"] = resolved_event.event_id
            call_links[request.id] = links

    persisted_ids = set()

    async def persist_one(feedback):
        message = feedback.message
        await add_message_to_history(
            role="tool",
            content=message["content"],
            session_id=session_id,
            metadata=feedback.metadata,
        )
        session_state.messages.append(message)
        persisted_ids.add(message["tool_call_id"])

    async def persist_results(results):
        for result in results:
            if result.message["tool_call_id"] in persisted_ids:
                continue
            write = asyncio.create_task(persist_one(result))
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                # Finish the in-flight write before reconciling this batch, so
                # cancellation between rows cannot append a completed row twice.
                await write
                raise

    tasks = [asyncio.create_task(one(request)) for request in turn.tool_calls]
    try:
        results = await asyncio.gather(*tasks)
        session_state.loop_detector.record_round(
            [result.interaction for result in results]
        )
        await persist_results(results)
        session_state.state = AgentState.OBSERVING
        if telemetry_recorder is not None:
            observation_ids = [
                result.observation_event_id
                for result in results
                if result.observation_event_id
            ]
            await telemetry_recorder.emit_event(
                "observation_pipeline_end",
                output={
                    "tool_count": len(results),
                    "observation_event_ids": observation_ids,
                },
                metadata={
                    "batch_id": batch_id,
                    "model_call_event_id": model_call_event_id,
                    "model_response_event_id": model_response_event_id,
                },
            )
            await telemetry_recorder.emit_event(
                "tool_batch_end",
                output={"tool_count": len(results)},
                metadata={
                    "batch_id": batch_id,
                    "model_call_event_id": model_call_event_id,
                    "model_response_event_id": model_response_event_id,
                },
            )
            await telemetry_recorder.end_span(batch_span.span_id, status=SpanStatus.OK)
    except BaseException as exc:
        for task in tasks:
            if not task.done():
                task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        if isinstance(exc, asyncio.CancelledError):
            await persist_results(
                [result for result in outcomes if isinstance(result, ToolFeedback)]
            )
        if telemetry_recorder is not None and batch_span is not None:
            await telemetry_recorder.end_span(
                batch_span.span_id,
                status=SpanStatus.CANCELLED
                if isinstance(exc, asyncio.CancelledError)
                else SpanStatus.ERROR,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        raise


def _is_governed(child: Any) -> bool:
    """Whether a child agent has, or will have, a governance engine."""
    inner = getattr(child, "agent", None)
    if getattr(inner, "governance_engine", None) is not None:
        return True
    config = (getattr(child, "agent_config", None) or {}).get("governance_config") or {}
    return bool(config.get("enabled"))
