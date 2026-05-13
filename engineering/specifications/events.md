# Runtime Events Specification

This specification defines the behavior contract for OmniCoreAgent runtime
events.

Read this with:

- `engineering/architecture/events.md`
- `src/omnicoreagent/core/events`
- `src/omnicoreagent/core/agents/events.py`
- `src/omnicoreagent/serve/sse.py`
- `src/omnicoreagent/serve/routes/runs.py`
- `src/omnicoreagent/serve/routes/sessions.py`

When this specification changes, implementation and tests must change with it.

---

## Scope

This specification covers:

- event model and validation
- event store interface
- event router behavior
- OmniServe SSE run streaming
- OmniServe session event replay/follow
- session isolation expectations
- current delivery guarantees
- required tests

This specification does not cover:

- future trace/evaluation/feedback-loop design
- policy decision records
- sandbox execution logs
- external observability vendor integrations
- OpenTelemetry spans

---

## Event Model

An event record has:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `type` | `EventType \| str` | yes | Runtime event type |
| `payload` | typed payload | yes | Event-specific payload |
| `agent_name` | `str` | yes | Agent that emitted the event |
| `timestamp` | `datetime` | yes | UTC timestamp; defaults at construction |
| `event_id` | `str` | yes | Unique event id; defaults to UUID |
| `sequence` | `int \| None` | no | Monotonic append sequence assigned by the event store |
| `run_id` | `str \| None` | no | Run boundary for events emitted during one agent execution |

The base event model does not store `session_id`. The event store key provides
the session boundary. API and SSE serializers must attach `session_id` where
clients need a self-contained envelope.

---

## Event Store Interface

Every event store implements:

```python
async def append(session_id: str, event: Event) -> None: ...
async def get_events(session_id: str) -> list[Event]: ...
async def stream(session_id: str) -> AsyncIterator[Event]: ...
async def get_stream_cursor(session_id: str) -> str | None: ...
async def stream_after(session_id: str, cursor: str | None) -> AsyncIterator[Event]: ...
async def get_events_after(session_id: str, cursor: str | None) -> list[Event]: ...
```

Rules:

- `append` validates the event before storing it.
- `get_events` returns a snapshot list for the requested session.
- `stream` yields live events for the requested session from the current tail.
- `get_stream_cursor` returns the current stream cursor for the requested
  session.
- `stream_after` yields stored or live events appended after the cursor.
- `get_events_after` returns a snapshot of stored events appended after the
  cursor.
- Stores must isolate events by `session_id`.
- Stores must not emit one session's events into another session's stream.
- Stores assign `sequence` during `append`.
- `sequence` is monotonically increasing inside one store key.
- `sequence` is not globally ordered across sessions, processes, or stores.

Backend-specific behavior:

- `in_memory`: process-local history, per-session sequence counters, and live
  subscriber queues.
- `redis_stream`: Redis stream-backed history/live stream. Sequence assignment
  and `XADD` happen atomically in Redis so stream order and sequence order do
  not diverge.

---

## Event Router

`EventRouter` owns active store selection.

Supported backend names:

```text
in_memory
redis_stream
```

Rules:

- Unknown backend names fall back to `in_memory`.
- Redis import failures fall back to `in_memory`.
- Switching event stores selects a new backing store.
- Switching stores does not migrate previously stored events.
- SQL and MongoDB are not valid runtime event stream backends.
- Use SQL, Redis, or MongoDB task stores for background task state; use
  `redis_stream` when runtime events must stream across processes.

---

## OmniServe SSE Contracts

### SSE Format

All SSE chunks use:

```text
event: <event_type>
data: <json payload>

```

Runtime event SSE payloads must include:

- all normalized event fields
- `session_id`
- `sequence` when the event has been appended through an event store
- `run_id` when the event was emitted inside an agent run

Session lifecycle chunks use:

```json
{"session_id": "...", "status": "started|streaming|ended"}
```

Error chunks use:

```json
{"session_id": "...", "error": "..."}
```

### `POST /run`

`POST /run` starts a live agent run stream.

Required behavior:

- yield `event: session` with `status=started`
- capture the event stream cursor before calling `agent.run(...)`
- assign an internal `run_id` for the run stream
- stream runtime events as they arrive
- filter runtime events to the assigned `run_id`
- preserve the session boundary in every runtime event payload
- enforce configured request timeout
- yield `event: complete` after `agent.run(...)` returns
- yield `event: error` when the run fails or times out
- yield `event: session` with `status=ended` before the generator ends
- cancel internal stream/run tasks during cleanup

The `complete` event is the normalized return value from `agent.run(...)`, not a
runtime `Event`. It includes the `run_id` used by the stream.

### `GET /events/{session_id}`

`GET /events/{session_id}` replays stored events and follows live events.

Required behavior:

- yield `event: session` with `status=streaming`
- capture the event stream cursor before reading historical events
- replay stored events in store order
- follow events appended after the captured cursor
- de-duplicate events by `event_id` across the replay/live boundary
- attach `session_id` to every runtime event payload
- yield `event: error` if replay or streaming fails
- cancel internal stream tasks during cleanup

This endpoint is long-lived by design.

---

## HTTP Headers

OmniServe SSE responses must include:

```text
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

`X-Accel-Buffering: no` is required so Nginx-like proxies do not buffer the
stream and deliver events only after the run finishes.

---

## Delivery Guarantees

Current guarantees:

- per-session isolation
- monotonic per-session sequence assignment at append time
- stored replay per session
- live follow per session
- no intentional buffering in OmniServe SSE helpers
- best-effort active-client delivery

Current non-guarantees:

- no global event ordering across sessions
- no exactly-once delivery to disconnected SSE clients
- no migration when switching event backends
- no full audit-grade trace model

---

## Required Tests

Runtime event changes require tests for:

- event model serialization and validation
- store snapshot behavior
- event router trace construction from stored events
- `/run` SSE yields runtime events before `complete`
- `/run` SSE attaches `session_id` to runtime event payloads
- `/events/{session_id}` replays stored events before live events
- `/events/{session_id}` does not stream another session's events
- timeout/error paths yield SSE error chunks
- proxy buffering headers are present on SSE routes
