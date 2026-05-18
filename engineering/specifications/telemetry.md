# Telemetry Specification

This specification defines the target behavior contract for OmniCoreAgent
telemetry.

Read this with:

- `engineering/architecture/telemetry.md`
- `src/omnicoreagent/core/events`
- `src/omnicoreagent/background/event_log.py`
- `src/omnicoreagent/serve/sse.py`
- `src/omnicoreagent/serve/routes/runs.py`
- `src/omnicoreagent/serve/routes/sessions.py`

This is a design specification. It does not mean the implementation already
exists. When implementation begins, code and tests must move toward this
contract phase by phase.

---

## Scope

This specification covers:

- telemetry identity model
- event schema
- span schema
- trace schema
- recorder behavior
- context propagation
- storage contracts
- live/replay stream contract
- normalization contract
- redaction and payload policy
- migration from legacy runtime events and `EventRouter`
- required tests

This specification does not cover:

- observability dashboards
- observability UI
- alerting
- eval runner
- evaluator modules
- eval suite execution
- vendor exporters
- OpenTelemetry exporter implementation
- policy engine
- sandbox execution implementation

Telemetry must be built before observability and evaluation.

---

## OpenTelemetry Alignment

OmniCoreAgent telemetry uses the standard trace/span/event model so it can map
cleanly to OpenTelemetry later.

Rules:

- `trace_id`, `span_id`, and `parent_span_id` must be compatible with a
  hierarchical trace model.
- internal span kinds and event types are OmniCoreAgent-owned.
- OTLP export is a later adapter and must not define the internal schema.
- vendor integrations must consume normalized telemetry rather than adding
  runtime-specific tracing paths.

---

## Terminology

| Term | Meaning |
|------|---------|
| Telemetry event | Append-only point-in-time fact. |
| Telemetry span | Timed unit of work with parent/child relationship. |
| Telemetry trace | Complete execution graph for one logical run. |
| Trace recorder | Runtime API that records events and spans. |
| Telemetry store | Persistence boundary for traces, spans, and events. |
| Telemetry stream | Live/replay adapter over telemetry records. |
| Trace normalizer | Converts runtime telemetry into stable evaluator-ready schema. |
| Evidence reference | Stable reference to `trace_id`, `span_id`, `event_id`, or `sequence_number`. |

---

## Identity Contract

Telemetry records use these identifiers:

| Field | Required | Meaning |
|-------|----------|---------|
| `trace_id` | yes | Primary id for one telemetry trace. |
| `span_id` | yes for spans, optional for events | Timed operation id. |
| `parent_span_id` | optional | Parent span id inside the same trace. |
| `event_id` | yes for events | Point-in-time evidence id. |
| `parent_event_id` | optional | Parent event id when event causality is explicit. |
| `run_id` | optional | Runtime/background run id. |
| `session_id` | optional | Conversation continuity id. |
| `task_id` | optional | Background/evaluation task id. |
| `suite_id` | optional | Future evaluation suite id when a trace is captured for an eval task. |
| `agent_id` | optional | Stable application/runtime agent id when available. |
| `workflow_id` | optional | Workflow/orchestration id. |

Rules:

- `trace_id` is not derived from `session_id`.
- A session may contain many traces.
- `run_id` may equal `trace_id` in simple cases, but this must not be required.
- Every span belongs to exactly one trace.
- Every canonical telemetry event belongs to exactly one trace.
- Legacy runtime events that cannot be assigned to a trace must be marked
  `legacy_unbound=true` by the normalizer.

---

## Telemetry Event Schema

Every canonical telemetry event uses this structure:

```yaml
event_id: string
trace_id: string
span_id: string | null
parent_event_id: string | null
sequence_number: int
timestamp: datetime
event_type: string
actor:
  type: system | user | agent | model | tool | mcp_server | memory | workspace | guardrail | background | serve
  id: string | null
  name: string | null
input: object | null
output: object | null
error: object | null
  type: string
  message: string
  retryable: bool | null
  metadata: object
  stack: string | null
duration_ms: int | null
token_usage:
  prompt_tokens: int | null
  completion_tokens: int | null
  total_tokens: int | null
estimated_cost_usd: float | null
metadata: object
```

Rules:

- `event_id` must be stable and unique inside a trace.
- `sequence_number` is monotonically increasing inside one trace.
- `timestamp` must be UTC.
- `event_type` must come from the registry unless the record is explicitly
  marked experimental.
- `input`, `output`, and `error` must be standard fields. Event-specific data
  belongs in these fields or `metadata`, not arbitrary top-level fields.
- `duration_ms` is allowed for point events but required only when the event
  represents a completed operation without a span.
- token and cost fields must be present with null values when unknown.
- redaction must happen before persistence.

---

## Telemetry Span Schema

Every telemetry span uses this structure:

```yaml
span_id: string
trace_id: string
parent_span_id: string | null
name: string
kind: string
status: running | ok | error | cancelled | timeout | skipped
started_at: datetime
ended_at: datetime | null
duration_ms: int | null
actor:
  type: system | agent | model | tool | mcp_server | memory | workspace | guardrail | background | serve
  id: string | null
  name: string | null
input: object | null
output: object | null
error: object | null
  type: string
  message: string
  retryable: bool | null
  metadata: object
  stack: string | null
token_usage:
  prompt_tokens: int | null
  completion_tokens: int | null
  total_tokens: int | null
estimated_cost_usd: float | null
attributes: object
event_ids: list[string]
```

Rules:

- `span_id` must be stable and unique inside a trace.
- `parent_span_id` must refer to another span in the same trace or be null.
- root spans have `parent_span_id=null`.
- `ended_at` may be null for partial traces.
- if `ended_at` is null, `duration_ms` may be null.
- terminal spans must set `status`.
- spans should reference child events through `event_ids`.
- token usage and cost rollups can be null until computed.

---

## Telemetry Trace Schema

Every telemetry trace uses this structure:

```yaml
trace_id: string
run_id: string | null
session_id: string | null
task_id: string | null
suite_id: string | null
agent_id: string | null
workflow_id: string | null
root_span_id: string
status: running | completed | failed | cancelled | timeout | aborted_resource_guard | aborted_safety_guard | partial
started_at: datetime
ended_at: datetime | null
metadata:
  agent_name: string | null
  agent_version: string | null
  model_provider: string | null
  model: string | null
  prompt_version: string | null
  tool_schema_version: string | null
  memory_config_version: string | null
  constraint_config_version: string | null
  tags: list[string]
spans: list[TelemetrySpan]
events: list[TelemetryEvent]
```

Rules:

- a trace must contain exactly one root span.
- `root_span_id` must refer to a span in `spans`.
- partial traces are valid.
- failed or aborted traces must retain all evidence captured before failure.
- `metadata` must preserve version fields needed for future regression
  evaluation.
- trace status must not hide runtime control halts.

---

## Span Kind Registry

Foundation span kinds:

```text
agent.run
agent.step
model.call
context.assembly
context.compression
tool.batch
tool.call
mcp.tool.call
observation.pipeline
memory.read
memory.write
workspace.read
workspace.write
tool.offload
guardrail.check
subagent.run
workflow.route
background.task
background.run
serve.request
runtime.control
```

Reserved span kinds:

```text
verifier.run
eval.score
```

Rules:

- new span kinds require specification updates.
- reserved span kinds must not be emitted until their subsystem exists.
- span kind names use lowercase dotted names.

---

## Event Type Registry

Foundation event groups:

| Group | Event Types |
|-------|-------------|
| Agent lifecycle | `agent_start`, `agent_step`, `agent_end` |
| User input | `user_message`, `system_instruction` |
| Model interaction | `model_call`, `model_response`, `model_error` |
| Tool interaction | `tool_call`, `tool_result`, `tool_error`, `tool_retry` |
| Tool batches | `tool_batch_start`, `tool_batch_end`, `tool_batch_error` |
| MCP | `mcp_tool_call`, `mcp_tool_result`, `mcp_tool_error` |
| Approval | `approval_request`, `approval_granted`, `approval_denied` |
| Memory | `memory_read`, `memory_write`, `memory_update`, `memory_eviction` |
| Context | `context_compression`, `context_dropped`, `context_restored` |
| Observation | `observation_pipeline_start`, `observation_pipeline_end`, `observation_pipeline_error` |
| Workspace | `workspace_read`, `workspace_write`, `workspace_delete`, `workspace_offload` |
| Guardrails | `guardrail_check`, `guardrail_violation` |
| Runtime controls | `resource_guard_warning`, `resource_guard_halt`, `safety_guard_halt` |
| Reasoning | `planning_step`, `reflection`, `retry` |
| Subagents | `subagent_spawn`, `subagent_result`, `subagent_error` |
| Workflow | `workflow_route`, `workflow_handoff`, `workflow_join` |
| Background | `background_run_queued`, `background_run_started`, `background_run_heartbeat`, `background_run_completed`, `background_run_failed`, `background_run_cancelled`, `background_run_timeout` |
| Serving | `serve_request_start`, `serve_request_end`, `serve_request_error` |
| Finalization | `final_answer`, `final_state` |
| Errors | `runtime_error`, `uncaught_exception`, `telemetry_error` |

Rules:

- current legacy `EventType` values can be adapted into these names during
  migration.
- arbitrary string events must be marked experimental and should not be used by
  future evaluators.
- event types must not encode dynamic ids.

---

## Recorder Contract

The telemetry recorder exposes these operations:

```python
start_trace(...)
end_trace(...)
start_span(...)
end_span(...)
emit_event(...)
record_exception(...)
current_context()
```

Rules:

- recorder calls must be safe inside async execution.
- recorder context must propagate through parallel tool execution.
- recorder context must propagate into subagents with explicit parent linkage.
- recorder context must propagate into background runs.
- recorder must persist partial traces when execution fails, times out, or is
  cancelled.
- recorder must apply redaction before store writes.
- recorder must not raise into the agent hot path for non-critical telemetry
  storage failures unless telemetry is configured as strict.

Strict mode:

- intended for tests, CI, and evaluation capture.
- telemetry write failures fail the run or mark the trace incomplete.

Best-effort mode:

- intended for production serving where agent execution should continue if
  telemetry storage has a transient issue.
- failures are recorded as local telemetry errors when possible.

---

## Context Propagation Contract

Telemetry context contains:

```yaml
trace_id: string
span_id: string | null
run_id: string | null
session_id: string | null
task_id: string | null
suite_id: string | null
agent_id: string | null
workflow_id: string | null
```

Rules:

- context is stored in context variables or an equivalent async-safe mechanism.
- nested spans default to the current span as parent.
- parallel tool calls share the same trace and parent `tool.batch` span.
- each tool call gets its own child `tool.call` or `mcp.tool.call` span.
- subagent execution is represented as child spans inside the parent trace in
  the foundation design.
- linked traces are out of scope until a future specification defines
  `parent_trace_id`, trace links, and cross-trace evidence rules.
- background runs create or resume telemetry context from `run_id` and
  `session_id`.

---

## Telemetry Store Contract

Foundation stores:

```text
in_memory
jsonl
```

Later stores:

```text
sqlite
postgres
object_storage
```

Store interface:

```python
append_event(trace_id: str, event: TelemetryEvent) -> None
start_span(trace_id: str, span: TelemetrySpan) -> None
end_span(trace_id: str, span_id: str, patch: dict) -> None
upsert_trace(trace: TelemetryTrace) -> None
get_trace(trace_id: str) -> TelemetryTrace | None
list_traces(filter: TraceFilter) -> list[TelemetryTrace]
```

Rules:

- stores must preserve trace-local sequence order.
- JSONL records must be append-friendly.
- in-memory store is for tests and local development.
- Redis stream is not the canonical trace store.
- storage failure behavior depends on recorder strict/best-effort mode.

Required indexes for durable stores:

- `trace_id`
- `run_id`
- `session_id`
- `task_id`
- `suite_id`
- `agent_id`
- `workflow_id`
- `model`
- `status`
- `started_at`
- `ended_at`
- `agent_version`
- `prompt_version`
- `tool_schema_version`
- `memory_config_version`
- `constraint_config_version`

---

## Telemetry Stream Contract

The telemetry stream replaces `EventRouter` as the long-term streaming and
replay boundary.

Interface:

```python
get_stream_cursor(scope: TelemetryStreamScope) -> str | None
stream_after(scope: TelemetryStreamScope, cursor: str | None) -> AsyncIterator[TelemetryEvent]
get_events_after(scope: TelemetryStreamScope, cursor: str | None) -> list[TelemetryEvent]
```

Scope:

```yaml
trace_id: string | null
run_id: string | null
session_id: string | null
task_id: string | null
event_types: list[string] | null
```

Rules:

- the stream reads from telemetry records, not a separate event source.
- stream cursors must support replay/follow behavior for SSE reconnect.
- stream scopes must isolate sessions, runs, and traces.
- `/run` SSE streams events for the trace/run it started.
- `/events/{session_id}` can remain during migration but should become a
  telemetry stream view.
- stream failures must not corrupt the stored trace.
- Redis streams may be used as an implementation backend for live fanout, but
  Redis stream records are not a separate canonical evidence model.

Replay/follow behavior:

- capture the current stream cursor before replay begins.
- replay stored telemetry events after the client cursor and up to the captured
  cursor.
- switch to live follow after the captured cursor.
- de-duplicate by `event_id` when replay and live follow overlap.
- preserve trace-local `sequence_number` ordering for replayed records.
- never mix records from another `trace_id`, `run_id`, `session_id`, or
  `task_id` outside the requested scope.

---

## Normalization Contract

The trace normalizer converts raw telemetry into normalized traces for future
evaluation.

Output requirements:

- deterministic ordering
- validated parent/child span references
- validated event/span references
- canonical event type names
- canonical span kind names
- explicit incomplete/missing evidence markers
- behavior summary inputs preserved for future extraction

Rules:

- evaluators consume normalized traces only.
- normalizer must not discard errors.
- normalizer must not hide incomplete traces.
- unsupported legacy events must be preserved as `legacy_event` records with
  metadata explaining the source type.

---

## Redaction and Payload Contract

Telemetry configuration must support:

```yaml
record_inputs: bool
record_outputs: bool
record_model_prompts: bool
record_model_responses: bool
record_tool_results: bool
max_payload_bytes: int
redact_keys: list[string]
offload_large_payloads: bool
offload_target: workspace | object_storage
strict: bool
```

Rules:

- redaction runs before persistence.
- keys matching `redact_keys` are replaced with a redaction marker.
- payloads larger than `max_payload_bytes` are summarized and optionally
  offloaded.
- offloaded payloads must store a reference, size, content type when known, and
  checksum when available.
- secrets must not be stored by default.

---

## Runtime Coverage Requirements

Telemetry must eventually capture:

- agent run start/end
- agent step start/end
- user message
- model call input/output/error
- token usage
- estimated cost
- tool batch start/end/error
- individual tool call input/output/error
- MCP tool call input/output/error
- observation pipeline result
- guardrail checks and violations
- memory reads and writes
- context compression and dropped/restored fields
- workspace reads/writes/offloads
- subagent spawn/result/error
- background task/run lifecycle and heartbeat
- OmniServe request start/end/error
- final answer
- final state when available

Foundation implementation can phase these in, but the schema must support them
from the start.

---

## Migration From Legacy Runtime Events

Existing runtime events and `EventRouter` exist only during migration.

Target ownership:

```text
TelemetryRecorder -> TelemetryStore -> TelemetryStream -> OmniServe SSE
```

There must not be a permanent parallel stack:

```text
EventRouter -> EventStore
TelemetryRecorder -> TelemetryStore
```

Mapping examples:

| Legacy Runtime Event | Telemetry Event | Span |
|----------------------|-----------------|------|
| `user_message` | `user_message` | `agent.run` |
| `agent_message` | `model_response` | `model.call` |
| `agent_thought` | `planning_step` or metadata event | `agent.step` |
| `tool_call_started` | `tool_batch_start` plus child `tool_call` events | `tool.batch` |
| `tool_call_result` | `tool_result` | `tool.call` |
| `tool_call_error` | `tool_error` | `tool.call` |
| `sub_agent_call_started` | `subagent_spawn` | `subagent.run` |
| `sub_agent_call_result` | `subagent_result` | `subagent.run` |
| `background_agent_status` | background lifecycle event | `background.run` |
| `final_answer` | `final_answer` | `agent.run` |

Rules:

- old `/events/{session_id}` behavior must keep working until it is deliberately
  reimplemented on `TelemetryStream` or replaced by a new route.
- current `get_trace()` should be documented as event summary until replaced.
- migration must avoid two unrelated sources of truth.
- once telemetry becomes canonical, SSE must stream selected telemetry events.
- no new feature should depend on `EventRouter`.
- `EventRouter` and legacy event stores should be removed after OmniServe,
  background agents, and runtime emission are telemetry-native.

---

## Evaluation Evidence Requirements

The future evaluation architecture requires telemetry evidence for:

- outcome correctness
- trajectory quality
- tool discipline
- recovery quality
- risk profile
- longitudinal risk
- efficiency
- evidence grounding
- memory and context behavior
- coordination quality
- final response quality

Telemetry must therefore preserve:

- final output
- final state when available
- tool inputs and outputs subject to redaction
- failed tool calls and retry attempts
- approval requests and decisions
- memory/context operations
- workspace mutations
- subagent and handoff boundaries
- runtime guard warnings and halts
- cost, latency, token, and step counts
- event/span references for every evaluator claim

Evaluation is out of scope for this phase. These are trace requirements only.

---

## Required Tests

Telemetry implementation phases require tests for:

- event schema serialization and validation
- span schema serialization and validation
- trace schema serialization and validation
- trace id/span id/event id generation
- parent/child span linking
- context propagation across async tasks
- parallel tool batch child spans
- subagent parent linkage
- background run telemetry context
- OmniServe request root spans
- partial trace persistence on error
- timeout and cancellation trace status
- token/cost fields with known and unknown values
- redaction of configured keys
- payload size truncation/offload references
- JSONL append/read behavior
- legacy runtime event migration
- telemetry stream replay/follow behavior
- guard test or review check that new telemetry, serving, and background code
  does not import or instantiate `EventRouter` except inside explicitly named
  migration adapters
- normalized trace deterministic ordering
- missing evidence markers

No telemetry implementation phase is complete until tests prove both successful
and failed execution paths.

---

## Acceptance Criteria For This Design PR

This design PR is acceptable when:

- architecture and specification are accepted
- telemetry owns the target streaming path:
  `TelemetryRecorder -> TelemetryStore -> TelemetryStream -> OmniServe SSE`
- legacy runtime events and `EventRouter` are documented only as migration
  concerns
- the spec defines the schema, recorder, store, stream, normalizer, redaction,
  migration, and future evaluation evidence contracts
- docs clearly separate telemetry from observability and evaluation

## Acceptance Criteria For Future Foundation Implementation

The future telemetry foundation implementation is acceptable when:

- implementation has a canonical event/span/trace schema
- recorder can capture agent/model/tool/final-answer basics
- telemetry store can persist and retrieve traces locally
- telemetry stream can replay/follow selected telemetry events
- OmniServe SSE is backed by `TelemetryStream`
- the implementation PR includes an explicit `EventRouter` removal or migration
  completion plan for remaining call sites
- normalizer can produce deterministic normalized traces
- future evaluation can reference `trace_id`, `span_id`, and `event_id`
