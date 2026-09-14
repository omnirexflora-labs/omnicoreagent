"""Execute a complete native tool turn and persist correlated feedback."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy

from omnicoreagent.core.model_protocol import ModelTurn
from omnicoreagent.core.tools.local_tool_handler import LocalToolHandler
from omnicoreagent.core.tools.mcp_tool_handler import MCPToolHandler
from omnicoreagent.core.tools.tool_executor import ToolExecutor
from omnicoreagent.core.tools.tool_observation_guardrail import scrub_tool_results
from omnicoreagent.core.types import AgentState, ToolCallResult
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor


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
):
    assistant = turn.assistant_message()
    stored_calls = deepcopy(assistant["tool_calls"])
    if agent.governance_engine is not None:
        for stored in stored_calls:
            try:
                args = json.loads(stored["function"]["arguments"])
                stored["function"]["arguments"] = json.dumps(
                    {key: "[REDACTED]" for key in args}
                )
            except (ValueError, TypeError):
                stored["function"]["arguments"] = "{}"
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

    async def deferred_history(**kwargs):
        # Only approved/offloaded results are persisted below, exactly once.
        pass

    # Resolve against the schemas supplied for this turn. Discovery may only
    # unlock calls for the next request, never race siblings in this batch.
    resolutions = {}
    for request in turn.tool_calls:
        try:
            resolutions[request.id] = catalog.resolve(request)
        except ValueError as exc:
            resolutions[request.id] = exc

    async def one(request):
        resolved = None
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
                    sessions or {}, binding.server, guardrail=agent.guardrail
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
            async with asyncio.timeout(agent.tool_call_timeout):
                result = await agent.governed_tool_runner.execute(
                    single_tool=resolved,
                    add_message_to_history=deferred_history,
                    session_id=session_id,
                    telemetry_recorder=telemetry_recorder,
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
            }
        except Exception as exc:
            result = {
                "tool_name": request.name,
                "args": {},
                "status": "error",
                "data": None,
                "message": str(exc),
            }
        result = scrub_tool_results([result], agent.guardrail)[0]
        # Loop signatures use normalized, guarded contents before artifact IDs
        # and governed-history redaction can change their representation.
        signature_args = arguments if resolved is not None else request.arguments
        signature_result = {
            key: value for key, value in result.items() if key != "args"
        }
        session_state.loop_detector.record_tool_call(
            result["tool_name"],
            json.dumps(signature_args, sort_keys=True, default=str),
            json.dumps(signature_result, sort_keys=True, default=str),
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
            )
        content = json.dumps(result, ensure_ascii=False, default=str)
        return {"role": "tool", "content": content, "tool_call_id": request.id}

    batch_span = None
    if telemetry_recorder is not None:
        batch_args = []
        for request in turn.tool_calls:
            try:
                arguments = request.decode_arguments()
                batch_args.append(
                    {key: "[REDACTED]" for key in arguments}
                    if agent.governance_engine
                    else arguments
                )
            except ValueError:
                batch_args.append({"invalid_arguments": True})
        payload = {"tool_count": len(turn.tool_calls), "tool_batch_args": batch_args}
        batch_span = await telemetry_recorder.start_span(
            name="tool.batch",
            kind="tool.batch",
            actor=TelemetryActor(type=ActorType.TOOL),
            input=payload,
        )
        await telemetry_recorder.emit_event("tool_batch_start", input=payload)

    persisted_ids = set()

    async def persist_one(result):
        normalized = json.loads(result["content"])
        await add_message_to_history(
            role="tool",
            content=result["content"],
            session_id=session_id,
            metadata={
                "agent_name": agent.agent_name,
                "interaction_version": 2,
                "tool_call_id": result["tool_call_id"],
                "tool": normalized["tool_name"],
                "args": normalized.get("args", {}),
            },
        )
        session_state.messages.append(result)
        persisted_ids.add(result["tool_call_id"])

    async def persist_results(results):
        for result in results:
            if result["tool_call_id"] in persisted_ids:
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
        await persist_results(results)
        session_state.state = AgentState.OBSERVING
        if telemetry_recorder is not None:
            await telemetry_recorder.emit_event(
                "observation_pipeline_end", output={"tool_count": len(results)}
            )
            await telemetry_recorder.emit_event(
                "tool_batch_end", output={"tool_count": len(results)}
            )
            await telemetry_recorder.end_span(batch_span.span_id, status=SpanStatus.OK)
    except BaseException as exc:
        for task in tasks:
            if not task.done():
                task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        if isinstance(exc, asyncio.CancelledError):
            await persist_results(
                [result for result in outcomes if isinstance(result, dict)]
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
