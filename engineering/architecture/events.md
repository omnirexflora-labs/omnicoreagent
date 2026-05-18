# Runtime Events Architecture

This is an internal architecture record under `engineering/architecture`, not
public product documentation. It defines the current runtime event boundary for
OmniCoreAgent.

Migration status: this document describes the current legacy runtime event and
SSE implementation only. The target execution evidence, replay, and streaming
architecture is `engineering/architecture/telemetry.md`.

Do not build new features on `EventRouter`. New runtime, serving, background,
trace, observability, or evaluation work must target telemetry.

Runtime events are the lightweight execution signal emitted while an agent is
running. They power live UIs, debugging, session history, compact trace
summaries, and background task lifecycle inspection in the current
implementation.

Runtime events are not the future trace/evaluation system. During migration,
current runtime events may be adapted into telemetry records, but telemetry is
the canonical future evidence model.

## Purpose

The event system exists to:

- record typed runtime facts for a session or background run
- stream those facts to application clients as they happen
- replay stored events for reconnect and debugging
- keep event state isolated by session, run, or task identity
- expose enough information to debug the agent without dumping raw internals

## Non-Goals

Runtime events do not own:

- policy decisions
- sandbox execution logs
- evaluation scores
- human feedback
- durable distributed tracing
- OpenTelemetry integration
- verifier contracts

Those layers will be designed separately. Runtime events must stay simple,
stable, and dependable so those later systems can consume them.

## Module Ownership

```text
core/events/
  base.py          # event types, payloads, validation, store interface
  event_router.py  # store selection and runtime facade
  in_memory.py     # local in-process store and pub/sub stream
  redis_stream.py  # Redis stream-backed event store
  trace.py         # compact debug trace derived from stored events

core/agents/
  events.py        # agent event emission helpers

serve/
  sse.py           # SSE formatting, live run stream, replay/live session stream
  routes/runs.py   # POST /run SSE entry point
  routes/sessions.py # GET /events/{session_id} and event list/trace endpoints
```

## Event Boundary

An `Event` contains:

- `type`
- `payload`
- `agent_name`
- `timestamp`
- `event_id`
- `sequence`
- `run_id`

The store key provides the session boundary. OmniServe attaches `session_id` to
SSE payloads so clients can identify the stream envelope without reading route
state.

The event store assigns `sequence` at append time. Sequence numbers are scoped
to the stream key, so they are stable for one session/run but are not global
across the whole system.

The runtime assigns `run_id` while an agent run is executing. A session event
feed may contain many run ids. A live `/run` SSE response filters to the run id
that it started, so concurrent runs on the same session do not stream each
other's events.

Background task events may also carry `task_id`, `run_id`, `attempt`,
`sequence`, and lease fields inside their typed payloads. Those fields belong to
background execution, not the base event envelope.

## Serving Flow

### `POST /run`

`/run` is the live execution stream.

1. Resolve the session id.
2. Create an internal run id for this request.
3. Capture the current event-store cursor for that session.
4. Start `agent.run(...)` inside the run-id context.
5. Stream events appended after the captured cursor whose `run_id` matches this
   request.
6. Yield a final `complete` SSE event with the normalized run result and run id.
7. End the session stream.

The cursor must be captured before `agent.run(...)` because early events such as
`user_message` can be emitted immediately. Streaming from an explicit cursor is
stronger than waiting for a subscriber task to become ready.

### `GET /events/{session_id}`

`/events/{session_id}` is reconnect/replay plus live follow.

1. Capture the current event-store cursor for the session.
2. Read stored events for the session.
3. Yield stored events in store order.
4. Stream events appended after the captured cursor.
5. De-duplicate by `event_id` across the replay/live boundary.

The cursor is captured before replay so events written during replay are still
available from the follow stream.

## Store Guarantees

Runtime event stores are intentionally limited to:

- `in_memory` for local development, tests, and single-process applications
- `redis_stream` for multi-process or production live streaming

Current guarantees:

- events are isolated by store key (`session_id`)
- events receive a monotonically increasing sequence number within the store key
- stored events can be listed per session
- live subscribers receive events for the subscribed session
- OmniServe SSE payloads include `session_id`
- `/run` streams intermediate runtime events before `complete`
- `/events/{session_id}` replays stored events and follows live events

Current limitations:

- in-memory events are process-local
- Redis stream events depend on Redis durability settings
- event ordering is per store append order, not a global distributed order
- event delivery is best-effort for active SSE clients
- switching event stores changes the active backing store and does not migrate
  prior events

These limitations are acceptable for runtime events. Stronger audit,
observability, and evaluation guarantees belong in the later trace/eval design.

## Invariants

- Runtime events must stay session-scoped.
- A run stream must not wait for the final result before sending intermediate
  runtime events.
- A reconnect stream must replay stored events before following live events.
- SSE responses must disable proxy buffering where OmniServe controls headers.
- Event stream failures must be logged; silent drops make production debugging
  impossible.
- Event route changes require tests for session isolation and event ordering.
