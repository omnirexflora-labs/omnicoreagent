"""
OmniServe SSE (Server-Sent Events) Utilities.

Provides utilities for streaming telemetry events via SSE.
"""

import asyncio
import contextlib
import json
from dataclasses import dataclass
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, AsyncGenerator
from uuid import uuid4

from omnicoreagent.core.logging import logger

from .serialization import normalize_event, normalize_run_result
from .state import get_agent_name
from .telemetry import build_run_kwargs, finish_serve_trace, start_serve_trace

if TYPE_CHECKING:
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent as AgentType
else:
    AgentType = Any

_SSE_EVENT_QUEUE_SIZE = 1000
_EVENT_REPLAY_TIMEOUT_SECONDS = 10
_TASK_CANCEL_TIMEOUT_SECONDS = 2


@dataclass
class _EventStreamFailure:
    error: Exception


def format_sse_event(event_type: str, data: dict) -> str:
    """
    Format data as an SSE event string.

    Args:
        event_type: The event type (e.g., 'message', 'tool_call', 'complete')
        data: The event data to send

    Returns:
        SSE-formatted string
    """
    json_data = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {json_data}\n\n"


def _normalize_event_for_sse(event: Any, session_id: str) -> tuple[str, dict[str, Any]]:
    """Normalize a telemetry event and attach the serving session boundary."""
    event_data = normalize_event(event)
    event_data["session_id"] = session_id
    metadata = event_data.get("metadata") or {}
    if isinstance(metadata, dict):
        for key in ("run_id", "task_id", "agent_id", "workflow_id"):
            if metadata.get(key) is not None:
                event_data.setdefault(key, metadata[key])

    event_type = event_data.get("event_type") or event_data.get("type", "event")
    if hasattr(event_type, "value"):
        event_type = event_type.value
    event_data.setdefault("type", str(event_type))

    return str(event_type), event_data


def _event_matches_run(event_data: dict[str, Any], run_id: str | None) -> bool:
    if run_id is None:
        return True
    return event_data.get("run_id") == run_id


async def _pump_session_events(
    agent: AgentType,
    session_id: str,
    queue: asyncio.Queue[Any],
    cursor: str | None,
    run_id: str | None = None,
) -> None:
    """Forward live telemetry events into a local queue for the SSE generator."""
    try:
        async for event in _stream_telemetry_after(agent, session_id, cursor, run_id):
            if not await _put_stream_item(queue, event):
                return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(f"OmniServe SSE: Event stream unavailable: {exc}")
        await _put_stream_item(queue, _EventStreamFailure(exc))


async def _put_stream_item(queue: asyncio.Queue[Any], item: Any) -> bool:
    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        overflow = _EventStreamFailure(RuntimeError("SSE event queue overflow"))
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        await queue.put(overflow)
        return False


async def _get_telemetry_stream_cursor(
    agent: AgentType,
    session_id: str,
    run_id: str | None = None,
) -> str | None:
    cursor_method = getattr(agent, "get_telemetry_stream_cursor", None)
    if callable(cursor_method):
        result = cursor_method(session_id=session_id, run_id=run_id)
        if isawaitable(result):
            return await result
        return result

    return None


async def _stream_telemetry_after(
    agent: AgentType,
    session_id: str,
    cursor: str | None,
    run_id: str | None = None,
) -> AsyncGenerator[Any, None]:
    stream_after_method = getattr(agent, "stream_telemetry_after", None)
    if callable(stream_after_method):
        async for event in stream_after_method(
            cursor=cursor,
            session_id=session_id,
            run_id=run_id,
        ):
            yield event
        return

    return


async def _get_telemetry_events_after_cursor(
    agent: AgentType,
    session_id: str,
    cursor: str | None,
    run_id: str | None = None,
) -> list[Any]:
    events_after_method = getattr(agent, "get_telemetry_events_after", None)
    if callable(events_after_method):
        result = events_after_method(cursor=cursor, session_id=session_id, run_id=run_id)
        if isawaitable(result):
            return await result
        return result

    return []


async def _cancel_task(task: asyncio.Task[Any] | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            timeout=_TASK_CANCEL_TIMEOUT_SECONDS,
        )


async def _run_agent_with_timeout(
    agent: AgentType,
    query: str,
    session_id: str,
    timeout_seconds: int | None,
    run_id: str,
) -> Any:
    run_coro = agent.run(query, **build_run_kwargs(agent, session_id=session_id, run_id=run_id))
    if timeout_seconds and timeout_seconds > 0:
        return await asyncio.wait_for(run_coro, timeout=timeout_seconds)
    return await run_coro


async def _drain_event_queue(
    queue: asyncio.Queue[Any],
    session_id: str,
    seen_event_ids: set[str] | None = None,
    run_id: str | None = None,
) -> AsyncGenerator[str, None]:
    while True:
        try:
            event = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if isinstance(event, _EventStreamFailure):
            raise event.error
        event_type, event_data = _normalize_event_for_sse(event, session_id)
        if not _event_matches_run(event_data, run_id):
            continue
        event_id = event_data.get("event_id")
        if seen_event_ids is not None:
            if event_id is not None and str(event_id) in seen_event_ids:
                continue
            if event_id is not None:
                seen_event_ids.add(str(event_id))
        yield format_sse_event(event_type, event_data)


async def run_agent_stream(
    agent: AgentType,
    query: str,
    session_id: str,
    *,
    timeout_seconds: int | None = None,
) -> AsyncGenerator[str, None]:
    """
    Run the agent and stream live telemetry events plus the final result via SSE.

    Args:
        agent: The OmniCoreAgent instance to run
        query: The user query
        session_id: Session ID for the conversation

    Yields:
        SSE-formatted event strings
    """
    yield format_sse_event("session", {"session_id": session_id, "status": "started"})

    event_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_SSE_EVENT_QUEUE_SIZE)
    pump_task: asyncio.Task[Any] | None = None
    run_task: asyncio.Task[Any] | None = None
    next_event_task: asyncio.Task[Any] | None = None
    seen_event_ids: set[str] = set()
    run_id = f"run_{uuid4().hex}"
    serve_trace = None

    try:
        serve_trace = await start_serve_trace(
            agent,
            method="POST",
            path="/run",
            session_id=session_id,
            run_id=run_id,
            query=query,
            streaming=True,
        )
        cursor = await _get_telemetry_stream_cursor(agent, session_id, run_id)
        pump_task = asyncio.create_task(
            _pump_session_events(agent, session_id, event_queue, cursor, run_id)
        )
        run_task = asyncio.create_task(
            _run_agent_with_timeout(agent, query, session_id, timeout_seconds, run_id)
        )

        while True:
            if next_event_task is None:
                next_event_task = asyncio.create_task(event_queue.get())

            done, _ = await asyncio.wait(
                {run_task, next_event_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if run_task in done:
                response = await run_task

                if next_event_task in done:
                    event = next_event_task.result()
                    next_event_task = None
                    if isinstance(event, _EventStreamFailure):
                        raise event.error
                    event_type, event_data = _normalize_event_for_sse(
                        event,
                        session_id,
                    )
                    if _event_matches_run(event_data, run_id):
                        event_id = event_data.get("event_id")
                        if event_id is not None:
                            seen_event_ids.add(str(event_id))
                        yield format_sse_event(event_type, event_data)
                else:
                    await _cancel_task(next_event_task)
                    next_event_task = None

                try:
                    events_after_cursor = await asyncio.wait_for(
                        _get_telemetry_events_after_cursor(
                            agent, session_id, cursor, run_id
                        ),
                        timeout=_EVENT_REPLAY_TIMEOUT_SECONDS,
                    )
                except Exception as exc:
                    logger.error(f"OmniServe SSE: Event catch-up error: {exc}")
                    events_after_cursor = []
                    yield format_sse_event(
                        "error",
                        {"error": str(exc), "session_id": session_id, "run_id": run_id},
                    )

                for event in events_after_cursor:
                    event_type, event_data = _normalize_event_for_sse(
                        event,
                        session_id,
                    )
                    if not _event_matches_run(event_data, run_id):
                        continue
                    event_id = event_data.get("event_id")
                    if event_id is not None and str(event_id) in seen_event_ids:
                        continue
                    if event_id is not None:
                        seen_event_ids.add(str(event_id))
                    yield format_sse_event(event_type, event_data)

                async for event_chunk in _drain_event_queue(
                    event_queue,
                    session_id,
                    seen_event_ids,
                    run_id,
                ):
                    yield event_chunk

                normalized = normalize_run_result(
                    response,
                    agent_name=get_agent_name(agent),
                )
                complete_payload = {
                    "session_id": session_id,
                    **normalized,
                }
                complete_payload["run_id"] = normalized.get("run_id") or run_id

                await finish_serve_trace(
                    serve_trace,
                    output={
                        "status": "completed",
                        "agent_trace_id": complete_payload.get("trace_id"),
                    },
                )
                serve_trace = None
                yield format_sse_event(
                    "complete",
                    complete_payload,
                )
                break

            if next_event_task in done:
                event = next_event_task.result()
                next_event_task = None
                if isinstance(event, _EventStreamFailure):
                    raise event.error
                event_type, event_data = _normalize_event_for_sse(event, session_id)
                if not _event_matches_run(event_data, run_id):
                    continue
                event_id = event_data.get("event_id")
                if event_id is not None:
                    seen_event_ids.add(str(event_id))
                yield format_sse_event(event_type, event_data)
                continue

    except asyncio.TimeoutError:
        logger.error(f"OmniServe SSE: Agent run timed out after {timeout_seconds}s")
        await finish_serve_trace(
            serve_trace,
            status="timeout",
            error={"type": "TimeoutError", "message": "Request timed out"},
        )
        serve_trace = None
        yield format_sse_event(
            "error",
            {
                "error": "Request timed out",
                "session_id": session_id,
                "run_id": run_id,
            },
        )

    except Exception as e:
        logger.error(f"OmniServe SSE: Agent run error: {e}")
        await finish_serve_trace(
            serve_trace,
            status="failed",
            error={"type": e.__class__.__name__, "message": str(e)},
        )
        serve_trace = None
        yield format_sse_event(
            "error",
            {
                "error": str(e),
                "session_id": session_id,
                "run_id": run_id,
            },
        )
    finally:
        if serve_trace is not None:
            await finish_serve_trace(
                serve_trace,
                status="cancelled",
                error={"type": "CancelledError", "message": "SSE stream closed"},
            )
        await _cancel_task(next_event_task)
        await _cancel_task(run_task)
        await _cancel_task(pump_task)

    yield format_sse_event("session", {"session_id": session_id, "status": "ended"})


async def stream_session_events(
    agent: AgentType,
    session_id: str,
    run_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Replay stored telemetry events, then stream live session telemetry via SSE.

    The live subscriber starts before historical replay to avoid a gap where
    events can be written after the snapshot but before the stream is active.

    Args:
        agent: The agent
        session_id: Session ID to stream events for
        run_id: Optional run ID to isolate one run inside a shared session

    Yields:
        SSE-formatted event strings
    """
    yield format_sse_event(
        "session", {"session_id": session_id, "run_id": run_id, "status": "streaming"}
    )

    event_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_SSE_EVENT_QUEUE_SIZE)
    pump_task: asyncio.Task[Any] | None = None
    seen_event_ids: set[str] = set()

    try:
        cursor = await _get_telemetry_stream_cursor(agent, session_id, run_id)
        pump_task = asyncio.create_task(
            _pump_session_events(agent, session_id, event_queue, cursor, run_id)
        )

        try:
            replay_events = await asyncio.wait_for(
                _get_telemetry_events_after_cursor(agent, session_id, None, run_id),
                timeout=_EVENT_REPLAY_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.error(f"OmniServe SSE: Event replay error: {exc}")
            replay_events = []
            yield format_sse_event(
                "error",
                {"error": str(exc), "session_id": session_id, "run_id": run_id},
            )

        for event in replay_events:
            event_type, event_data = _normalize_event_for_sse(event, session_id)
            if not _event_matches_run(event_data, run_id):
                continue
            event_id = event_data.get("event_id")
            if event_id is not None:
                seen_event_ids.add(str(event_id))
            yield format_sse_event(event_type, event_data)

        while True:
            event = await event_queue.get()
            if isinstance(event, _EventStreamFailure):
                raise event.error

            event_type, event_data = _normalize_event_for_sse(event, session_id)
            if not _event_matches_run(event_data, run_id):
                continue
            event_id = event_data.get("event_id")
            if event_id is not None and str(event_id) in seen_event_ids:
                continue

            if event_id is not None:
                seen_event_ids.add(str(event_id))
            yield format_sse_event(event_type, event_data)
    except Exception as e:
        logger.error(f"OmniServe SSE: Event replay error: {e}")
        yield format_sse_event(
            "error", {"error": str(e), "session_id": session_id, "run_id": run_id}
        )
    finally:
        await _cancel_task(pump_task)

    yield format_sse_event(
        "session", {"session_id": session_id, "run_id": run_id, "status": "ended"}
    )
