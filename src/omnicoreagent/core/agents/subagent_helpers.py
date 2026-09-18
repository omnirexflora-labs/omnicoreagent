import asyncio
import inspect
from typing import Any
from uuid import uuid4

from omnicoreagent.core.telemetry import (
    ActorType,
    SpanStatus,
    TelemetryActor,
    TraceFilter,
)


def resolve_agent(agent_name: str, sub_agents: list):
    for agent in sub_agents:
        if agent.name == agent_name:
            return agent
    raise ValueError(f"Sub-agent '{agent_name}' not found")


def build_kwargs(agent, provided_params: dict):
    sig = inspect.signature(agent.run)
    kwargs = {}

    for name, param in sig.parameters.items():
        if name == "self":
            continue

        if name in provided_params:
            kwargs[name] = provided_params[name]
            continue

        if param.default is inspect.Parameter.empty:
            raise ValueError(
                f"Missing required parameter '{name}' for agent '{agent.name}'"
            )

    return kwargs


def new_child_run_id() -> str:
    """Assign a delegated child's run id before it starts.

    Knowing the id up front keeps the delegation linked to the child trace
    even when the child raises or is cancelled before returning a result.
    """
    return f"run_{uuid4().hex}"


def accepts_run_id(agent: Any) -> bool:
    try:
        parameters = inspect.signature(agent.run).parameters
    except (TypeError, ValueError):
        return False
    return "run_id" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


async def find_child_trace_id(
    recorder: Any,
    *,
    run_id: str | None,
    parent_trace_id: str | None,
) -> str | None:
    """Find the trace a delegated child recorded for ``run_id``.

    Used when the child did not return a result carrying its trace id.
    """
    store = getattr(recorder, "store", None)
    if store is None or run_id is None:
        return None
    config = getattr(recorder, "config", None)
    timeout = getattr(config, "persistence_timeout_seconds", None)
    try:
        operation = store.list_traces(TraceFilter(run_id=run_id))
        traces = (
            await operation
            if timeout is None
            else await asyncio.wait_for(operation, timeout=timeout)
        )
    except Exception:
        return None
    for trace in reversed(traces):
        if parent_trace_id is None or trace.parent_trace_id == parent_trace_id:
            return trace.trace_id
    return None


async def finish_delegation(
    telemetry_recorder: Any,
    span: Any,
    *,
    agent_name: str,
    session_id: str | None,
    spawn_event_id: str | None,
    parent_context: Any,
    child_run_id: str | None,
    child_trace_id: str | None,
    status: SpanStatus,
    output: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    workspace_output: dict[str, Any] | None = None,
) -> None:
    """Record a delegation outcome with the child's identity.

    The identity goes into the terminal event metadata, which every capture
    policy retains, as well as the delegation span output.
    """
    if telemetry_recorder is None:
        return
    if child_trace_id is None:
        child_trace_id = await find_child_trace_id(
            telemetry_recorder,
            run_id=child_run_id,
            parent_trace_id=(
                parent_context.trace_id if parent_context is not None else None
            ),
        )
    identity = {"child_trace_id": child_trace_id, "child_run_id": child_run_id}
    terminal_event = await telemetry_recorder.emit_event(
        "subagent_result" if status == SpanStatus.OK else "subagent_error",
        actor=TelemetryActor(type=ActorType.AGENT, name=agent_name),
        input={"session_id": session_id, "agent_name": agent_name},
        output=output,
        error=error,
        metadata={
            "subagent_span_id": span.span_id if span else None,
            "spawn_event_id": spawn_event_id,
            **identity,
        },
    )
    if span is None:
        return
    span_output = {
        "agent_name": agent_name,
        "status": {
            SpanStatus.OK: "success",
            SpanStatus.CANCELLED: "cancelled",
        }.get(status, "error"),
        **identity,
        "spawn_event_id": spawn_event_id,
        "terminal_event_id": terminal_event.event_id,
    }
    if workspace_output is not None:
        span_output["workspace_output"] = workspace_output
    await telemetry_recorder.end_span(
        span.span_id,
        status=status,
        output=span_output,
        error=error,
    )
