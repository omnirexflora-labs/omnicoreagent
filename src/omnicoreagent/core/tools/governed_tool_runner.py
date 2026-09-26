import asyncio
from typing import Any

from omnicoreagent.core.budgets import current_budgets
from omnicoreagent.core.runs import waiting_for_approval
from omnicoreagent.core.runtime.deadline import current_stop_reason, stop_after
from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor
from omnicoreagent.core.telemetry.recorder import redacts_governed_arguments
from omnicoreagent.core.types import (
    ToolCallResult,
)
from omnicoreagent.core.tools.tool_observation_guardrail import scrub_tool_results
from omnicoreagent.governance.calls import on_behalf_of
from omnicoreagent.governance.capabilities import tool_authority_requests
from omnicoreagent.governance.errors import (
    ApprovalRequiredError,
    GovernanceError,
    PolicyDeniedError,
    ToolArgumentsInvalid,
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
        telemetry_recorder: Any = None,
        result_guardrail: Any = None,
        telemetry_links: dict[str, Any] | None = None,
        telemetry_outcome: dict[str, Any] | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Authorize and execute one call.

        ``telemetry_links`` connects the call's records to the model turn and
        tool resolution that produced it; a ``tool_provider`` entry overrides
        the provider reported in telemetry (subagent calls execute through the
        local handler). ``telemetry_outcome`` receives the tool span ID and the
        ID of the event that recorded the call's outcome (result, error, or
        timeout), so the observation built from it can point back to them.
        """
        outcome = telemetry_outcome if telemetry_outcome is not None else {}
        if telemetry_recorder is None:
            async with stop_after(deadline_seconds):
                budgets = current_budgets()
                if budgets is not None and budgets.enabled:
                    await budgets.charge("tool_calls", 1)
                governance_error = await self._authorize_single_tool(single_tool)
                if governance_error is not None:
                    return self._governance_error_result(
                        single_tool=single_tool,
                        governance_error=governance_error,
                    )
                # Authority the tool asks for while it runs (a sandbox's
                # network, each command in it) is recorded against this call.
                with on_behalf_of(single_tool.tool_call_id, single_tool.tool_name, single_tool.tool_provider):
                    result = await single_tool.tool_executor.execute(
                        tool_args=single_tool.tool_args,
                        tool_name=single_tool.tool_name,
                    )
                if result_guardrail is not None:
                    result = scrub_tool_results([result], result_guardrail)[0]
                result.pop("_guardrail_telemetry", None)
                if self.governance_engine is not None:
                    return _redact_tool_result_args(result)
                return result

        links = dict(telemetry_links or {})
        reported_provider = links.pop("tool_provider", None) or single_tool.tool_provider
        telemetry_shape = _tool_telemetry_shape(single_tool)
        telemetry_input = {
            "tool_name": single_tool.tool_name,
            "tool_call_id": single_tool.tool_call_id,
            "tool_provider": reported_provider,
            "tool_server": single_tool.tool_server,
            # Under governance argument values are redacted, not omitted, so
            # the record still shows which arguments the call used.
            "tool_args": (
                {key: "[REDACTED]" for key in single_tool.tool_args}
                if redacts_governed_arguments(
                    telemetry_recorder, self.governance_engine is not None
                )
                and isinstance(single_tool.tool_args, dict)
                else single_tool.tool_args
            ),
        }
        span = await telemetry_recorder.start_span(
            name=single_tool.tool_name,
            kind=telemetry_shape["span_kind"],
            actor=telemetry_shape["actor"],
            input=telemetry_input,
        )
        outcome["tool_span_id"] = span.span_id
        relationship_metadata = {
            **links,
            "tool_call_id": single_tool.tool_call_id,
            "tool_span_id": span.span_id,
            "tool_name": single_tool.tool_name,
            "tool_provider": reported_provider,
            "tool_server": single_tool.tool_server,
        }
        # The deadline covers the call, not the recording of it: it starts
        # once the span is open, so a call stopped by its deadline is always
        # recorded as a timeout rather than left unfinished.
        async with stop_after(deadline_seconds):
            try:
                # A call that the run cannot afford is not made.
                budgets = current_budgets()
                if budgets is not None and budgets.enabled:
                    await budgets.charge("tool_calls", 1)
                governance_error = await self._authorize_single_tool(single_tool)
                if governance_error is not None:
                    result = self._governance_error_result(
                        single_tool=single_tool,
                        governance_error=governance_error,
                    )
                    # An ask waits for a person; it is not a refusal, and
                    # the trace says which it was. Arguments that could not
                    # be read were rejected, not refused by a rule.
                    waiting = isinstance(governance_error, ApprovalRequiredError)
                    unreadable = isinstance(governance_error, ToolArgumentsInvalid)
                    denied_event = await telemetry_recorder.emit_event(
                        telemetry_shape["error_event"],
                        actor=telemetry_shape["actor"],
                        input=telemetry_input if telemetry_shape["single_event"] else None,
                        output=result,
                        error={
                            "type": governance_error.__class__.__name__,
                            "message": str(governance_error),
                        },
                        metadata={
                            **relationship_metadata,
                            "phase": "approval"
                            if waiting
                            else "rejected"
                            if unreadable
                            else "authorization",
                        },
                    )
                    outcome["tool_result_event_id"] = denied_event.event_id
                    await telemetry_recorder.end_span(
                        span.span_id,
                        status=SpanStatus.SKIPPED if waiting else SpanStatus.ERROR,
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
                        metadata={**relationship_metadata, "phase": "execution"},
                    )
                # Authority the tool asks for while it runs (a sandbox's
                # network, each command in it) is recorded against this call.
                with on_behalf_of(single_tool.tool_call_id, single_tool.tool_name, single_tool.tool_provider):
                    result = await single_tool.tool_executor.execute(
                        tool_args=single_tool.tool_args,
                        tool_name=single_tool.tool_name,
                    )
                if result_guardrail is not None:
                    result = scrub_tool_results([result], result_guardrail)[0]
                guardrail_signal = result.pop("_guardrail_telemetry", None)
                if guardrail_signal is not None:
                    await telemetry_recorder.emit_event(
                        "guardrail_violation"
                        if guardrail_signal.get("action") == "blocked"
                        else "guardrail_check",
                        actor=TelemetryActor(type=ActorType.GUARDRAIL),
                        input={
                            "target": "tool_output",
                            "tool_name": single_tool.tool_name,
                            "tool_call_id": single_tool.tool_call_id,
                            "tool_provider": single_tool.tool_provider,
                            "tool_server": single_tool.tool_server,
                        },
                        output=guardrail_signal,
                        metadata={**relationship_metadata, "phase": "output_guardrail"},
                    )
                # The same rule as the call's recorded arguments: kept where
                # the capture records what the model sent anyway.
                if redacts_governed_arguments(
                    telemetry_recorder, self.governance_engine is not None
                ):
                    result = _redact_tool_result_args(result)
                telemetry_result = result
                # A tool that asked for authority while it ran (a sandbox's
                # network) and failed waiting for it is waiting, not failing.
                waiting = result.get("status") == "error" and waiting_for_approval(
                    single_tool.tool_call_id
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
                    error_event = await telemetry_recorder.emit_event(
                        telemetry_shape["error_event"],
                        **event_kwargs,
                        metadata={
                            **relationship_metadata,
                            "phase": "approval" if waiting else "result",
                        },
                    )
                    outcome["tool_result_event_id"] = error_event.event_id
                    await telemetry_recorder.end_span(
                        span.span_id,
                        status=SpanStatus.SKIPPED if waiting else SpanStatus.ERROR,
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
                    result_event = await telemetry_recorder.emit_event(
                        telemetry_shape["result_event"],
                        **event_kwargs,
                        metadata={**relationship_metadata, "phase": "result"},
                    )
                    outcome["tool_result_event_id"] = result_event.event_id
                    await telemetry_recorder.end_span(
                        span.span_id,
                        status=SpanStatus.OK,
                        output=telemetry_result,
                    )
                return result
            except asyncio.CancelledError:
                if current_stop_reason() == "timeout":
                    timeout_error = {
                        "type": "TimeoutError",
                        "message": "Tool execution exceeded its time limit",
                    }
                    timeout_event = await telemetry_recorder.emit_event(
                        telemetry_shape["error_event"],
                        actor=telemetry_shape["actor"],
                        input=telemetry_input if telemetry_shape["single_event"] else None,
                        error=timeout_error,
                        metadata={**relationship_metadata, "phase": "timeout"},
                    )
                    outcome["tool_result_event_id"] = timeout_event.event_id
                    await telemetry_recorder.end_span(
                        span.span_id, status=SpanStatus.TIMEOUT, error=timeout_error
                    )
                else:
                    await telemetry_recorder.end_span(
                        span.span_id, status=SpanStatus.CANCELLED
                    )
                raise
            except Exception as exc:
                # Authority a tool asks for while it runs (a sandbox's
                # network) is decided like the call's own: an ask waits for a
                # person and a refusal is a denial, not a tool failure.
                phase = (
                    "approval"
                    if isinstance(exc, ApprovalRequiredError)
                    else "authorization"
                    if isinstance(exc, GovernanceError)
                    else "exception"
                )
                if telemetry_shape["single_event"]:
                    exception_event = await telemetry_recorder.emit_event(
                        telemetry_shape["error_event"],
                        actor=telemetry_shape["actor"],
                        input=telemetry_input,
                        error={"type": exc.__class__.__name__, "message": str(exc)},
                        metadata={**relationship_metadata, "phase": phase},
                    )
                else:
                    exception_event = await telemetry_recorder.record_exception(
                        exc,
                        event_type=telemetry_shape["error_event"],
                        actor=telemetry_shape["actor"],
                        metadata={**relationship_metadata, "phase": phase},
                    )
                outcome["tool_result_event_id"] = exception_event.event_id
                await telemetry_recorder.end_span(
                    span.span_id,
                    status=SpanStatus.SKIPPED if phase == "approval" else SpanStatus.ERROR,
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
                tool_call_id=single_tool.tool_call_id,
            )
            await self.governance_engine.authorize_all(requests)
        except (GovernanceError, ValueError) as exc:
            if isinstance(exc, GovernanceError):
                return exc
            return ToolArgumentsInvalid(str(exc))
        return None

    def _governance_error_result(
        self, *, single_tool: ToolCallResult, governance_error: GovernanceError
    ) -> dict[str, Any]:
        return {
            "tool_name": single_tool.tool_name,
            "args": {},
            "status": "error",
            "data": None,
            "message": (
                f"Waiting for a person's approval: {governance_error}"
                if isinstance(governance_error, ApprovalRequiredError)
                else f"Invalid arguments for {single_tool.tool_name}: {governance_error}"
                if isinstance(governance_error, ToolArgumentsInvalid)
                else f"Governance denied tool execution: {governance_error}"
            ),
            "governance_error_code": getattr(
                governance_error, "code", type(governance_error).__name__
            ),
            "governance": getattr(governance_error, "metadata", {}),
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


def _redact_tool_result_args(result: dict[str, Any]) -> dict[str, Any]:
    if "args" not in result:
        return result
    sanitized = dict(result)
    sanitized["args"] = "[REDACTED]"
    return sanitized
