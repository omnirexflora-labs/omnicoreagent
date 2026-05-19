# Telemetry Architecture

This is an internal architecture record under `engineering/architecture`, not
public product documentation.

Telemetry is the evidence layer of OmniCoreAgent. It records what happened
during agent execution in a form that can later support debugging,
observability, regression analysis, and the future OmniCoreAgent Agentic
Evaluation system.

Live streaming and replay are required harness capabilities. They are served
from telemetry: `TelemetryRecorder` writes evidence, `TelemetryStore` holds the
trace records, and `TelemetryStream` is the live/replay adapter over that store.
Background run lifecycle evidence is the one runtime adapter exception:
`BackgroundEventLog` writes normalized background events and spans directly to
`TelemetryStore` because it owns background event ordering and workspace mirrors.

## Purpose

OmniCoreAgent telemetry exists to:

- capture execution facts from the harness while an agent runs
- represent timed work as spans with parent/child relationships
- preserve point-in-time facts as append-only events
- build complete traces for one logical run
- provide stable evidence references for future evaluation
- support local debugging and live streaming without requiring an observability
  product
- make future observability, exports, and evals consumers of the same evidence

Telemetry is runtime infrastructure. Observability is a later product layer on
top of telemetry. Evaluation is a later consumer of normalized telemetry.

```text
runtime execution
  -> telemetry recorder
  -> telemetry store
       -> telemetry stream -> OmniServe SSE / live clients
       -> trace normalizer -> future observability / future evaluation / future exporters

background event log
  -> telemetry store
       -> telemetry stream -> OmniServe SSE / live clients
```

## Non-Goals

This architecture does not implement or design:

- dashboards
- observability UI
- alerting
- eval runner
- evaluator modules
- benchmark suites
- feedback UI
- dashboards or vendor-specific observability UI
- policy engine
- sandbox logs

Those systems will be designed separately. They must consume telemetry instead
of bypassing it.

## Telemetry Ownership

Runtime evidence is emitted as trace-scoped telemetry:

```text
TelemetryRecorder -> TelemetryStore -> TelemetryStream -> OmniServe SSE
```

`TelemetryRecorder` is the only runtime write path. `TelemetryStore` is the
canonical evidence store. `TelemetryStream` is a live/replay adapter over that
same store. It is not a second event system.

Routes such as `/events/{session_id}/trace` may keep their HTTP shape for
developer ergonomics, but their data must be derived from telemetry traces and
events.

## Core Concepts

The terminology deliberately follows the standard telemetry shape used by
OpenTelemetry and modern LLM observability systems: traces contain spans, spans
represent timed work, and events record point-in-time facts. OmniCoreAgent owns
its internal schema first. Vendor exports and OTLP integration are later
adapters, not the foundation.

### Telemetry Event

A telemetry event is an append-only fact that happened at a point in time.

Examples:

- user message received
- model response parsed
- tool result observed
- guardrail violation detected
- context compressed
- workspace file written
- background run heartbeat emitted

Events are evidence. They must be stable enough to reference later from an
evaluation report.

### Telemetry Span

A telemetry span is a timed unit of work.

Examples:

- `agent.run`
- `agent.step`
- `model.call`
- `tool.batch`
- `tool.call`
- `mcp.tool.call`
- `memory.read`
- `workspace.write`
- `subagent.run`
- `background.run`
- `serve.request`

Spans form a parent/child tree. This hierarchy lets OmniCoreAgent preserve both
live progress updates and durable execution evidence without a separate event
router.

### Telemetry Trace

A telemetry trace is one logical execution graph.

For a normal interactive request, the trace usually starts at `agent.run`.
For OmniServe, the serving boundary starts a `serve.request` trace correlated
to the agent trace through `session_id` and `run_id`.
For a background task, it may start at `background.run` and contain `agent.run`.

### Telemetry Context

Telemetry context carries active identifiers through async execution:

- `trace_id`
- `span_id`
- `run_id`
- `session_id`
- `task_id`
- `workflow_id`

Context propagation must work across model calls, parallel tool batches,
subagents, background runs, and OmniServe request handling.

## Target Data Flow

```text
OmniServe / application call
  -> TelemetryRecorder starts root span
  -> agent runtime starts agent.run span
  -> model/tool/memory/workspace/context/subagent spans emit events
  -> TelemetryStore persists events and spans
       -> TelemetryStream serves replay/follow to OmniServe SSE
       -> TraceNormalizer builds stable normalized trace
            -> future BehaviorExtractor consumes normalized trace
            -> future evaluators attach evidence-linked judgments
```

## Implementation Status

Telemetry is being moved into the runtime in phases.

The foundation phase provides:

- canonical telemetry dataclasses for traces, spans, events, actors, token usage,
  errors, and trace metadata
- async context propagation through `TelemetryContext`
- `TelemetryRecorder`
- in-memory telemetry storage
- JSONL telemetry storage
- `TelemetryStream`
- deterministic trace normalization
- runtime evidence emission helpers

The runtime facade wiring phase provides:

- automatic telemetry initialization on `OmniCoreAgent`
- one `agent.run` trace for every `OmniCoreAgent.run(...)` call
- a unique `run_id` for every run, even when multiple runs share one
  `session_id`
- explicit `run_id` injection for serving/event/telemetry correlation during
  migration
- `user_message`, `guardrail_violation`, `final_answer`, and `runtime_error`
  events at the facade boundary
- completed, failed, cancelled, and safety-aborted trace statuses
- runtime helper methods for trace lookup and telemetry stream replay/follow
- public `run(...)` responses include `trace_id` and `run_id` so application
  code can retrieve or stream the exact trace it just created
- OmniServe session telemetry routes support `run_id` filtering so same-session
  concurrent runs can be replayed, followed, and summarized without mixing
  evidence
- injected `telemetry_store`, `telemetry_recorder`, and `telemetry_stream`
  components must share the same store instance

The runtime loop instrumentation phase provides:

- child `agent.step` spans under the active `agent.run` trace
- child `model.call` spans and `model_call` / `model_response` /
  `model_error` events
- child `tool.batch` spans with `tool_batch_start`, `tool_batch_end`, and
  `tool_batch_error` events
- child `tool.call` spans with `tool_call`, `tool_result`, and `tool_error`
  events for each tool in a parallel batch
- child `mcp.tool.call` spans with `mcp_tool_call`, `mcp_tool_result`, and
  `mcp_tool_error` events for MCP server tools
- workspace file tool spans as `workspace.read`, `workspace.write`, or
  `workspace.delete` with matching workspace events
- memory read/write spans and `memory_read` / `memory_write` events around the
  memory router
- context compression spans with `context_compression` and `context_dropped`
  events
- observation pipeline start/end/error events after tool execution and before
  the model sees formatted observations
- workspace offload events when large tool results are replaced by workspace
  references
- artifact read/tail/search/list tools recorded as workspace reads because
  offloaded artifacts live inside the workspace artifacts namespace
- subagent execution spans with `subagent_spawn`, `subagent_result`, and
  `subagent_error` events
- usage-limit halts as `runtime.control` spans and `resource_guard_halt` events
- background run lifecycle spans and events, including workspace writes for
  run snapshots and event-log appends
- OmniServe `serve.request` traces/events for synchronous and SSE run
  boundaries, correlated to the same `session_id` and `run_id` as the agent run

Remaining runtime coverage should be added directly through telemetry. No
parallel side-channel stream or feature-specific event store should be
introduced.

Runtime controls may also emit telemetry:

- resource guard warning
- resource guard halt
- safety guard halt
- approval requested
- approval granted
- approval denied

Runtime controls may stop execution. Evaluators do not stop live execution in
the foundation telemetry phase.

## Identity Model

Use separate identifiers for separate concerns:

| Identifier | Meaning |
|------------|---------|
| `trace_id` | Telemetry execution id. Primary key for one trace. |
| `span_id` | Timed operation id inside a trace. |
| `event_id` | Point-in-time evidence id. |
| `run_id` | Runtime/background run id. |
| `session_id` | Conversation continuity id. |
| `task_id` | Background/evaluation task id when present. |
| `workflow_id` | Workflow/orchestration id when present. |

`trace_id` is not the same as `session_id`. A session may have many traces.
`trace_id` is not the same as `run_id`. A background run may map directly to a
trace, but the runtime should not assume they are always identical.

## Canonical Span Tree

The target execution hierarchy should support this shape:

```text
serve.request
  agent.run
    agent.step
      context.assembly
      memory.read
      model.call
      tool.batch
        tool.call
        mcp.tool.call
      observation.pipeline
      memory.write
      workspace.write
    subagent.run
      agent.run
        model.call
        tool.call
```

Background execution should use the same telemetry model:

```text
background.task
  background.run
    agent.run
      agent.step
```

## Span Kinds

The first-class span kinds are:

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
workspace.delete
tool.offload
guardrail.check
subagent.run
workflow.route
background.task
background.run
serve.request
runtime.control
```

Reserved span kinds for later phases:

```text
verifier.run
eval.score
```

These reserved kinds are part of the long-term schema direction, but they must
not be emitted until verification and evaluation are implemented.

## Event Type Groups

Telemetry events should cover these groups:

- agent lifecycle
- user input
- model interaction
- tool interaction
- MCP interaction
- approval flow
- memory
- context
- workspace
- guardrails
- runtime controls
- reasoning
- subagents
- workflows
- background lifecycle
- serving
- finalization
- errors

The event registry must stay explicit. Arbitrary string events can be accepted
only when marked experimental, but stable telemetry requires documented event
names.

## Trace Recorder

The Trace Recorder is the runtime-facing API. Runtime code should not write
directly to the store.

Responsibilities:

- create traces
- start/end spans
- emit events
- attach input/output/error data according to redaction policy
- compute duration
- attach token usage and estimated cost when available
- preserve parent/child relationships
- persist partial traces on failure or cancellation
- expose current telemetry context to nested runtime components

The recorder must be safe to call from concurrent tool execution and subagent
runs.

## Trace Store

Foundation storage should be simple and reliable:

- `in_memory` for tests and local development
- local JSONL for persisted local traces

Later storage backends:

- SQLite for small teams and local durability
- PostgreSQL for production querying
- object storage for archival

Redis streams may be a live fanout implementation for `TelemetryStream`, but
Redis streams are not the canonical long-term trace store.

## Telemetry Stream

The telemetry stream is the live/replay adapter over telemetry records. It is
not another truth source.

Responsibilities:

- stream selected telemetry events for one session, run, or trace
- replay stored telemetry events from a cursor
- support SSE consumers without requiring dashboards or observability services
- preserve session and run isolation
- expose cursors for reconnect and catch-up
- avoid creating another evidence source outside telemetry

The stream is a view over `TelemetryStore`; it is not a separate truth source.

Initial stream backends may reuse implementation ideas from current in-memory
queues and Redis streams, but the public contract should be telemetry-native.

Trace retrieval follows the same evidence boundary:

- exact trace lookup is by `trace_id`
- run lookup is by `run_id`
- latest session lookup is by deterministic trace ordering for `session_id`
- partial, failed, cancelled, timeout, safety-halted, and resource-halted traces
  are returned as evidence, not hidden behind summaries
- OmniServe trace summary endpoints read the latest telemetry trace, not a
  separate session event summary

## Trace Exporters

Exporters are adapters over normalized telemetry. They do not own trace
identity, persistence, streaming, or evaluation.

The exporter boundary is:

```text
TelemetryStore -> TelemetryNormalizer -> TelemetryExporter -> external backend
```

Supported exporter responsibilities:

- map OmniCoreAgent spans/events to OpenTelemetry-shaped span records
- send traces through OTLP/HTTP when the optional OpenTelemetry dependency is
  installed
- provide vendor presets for OTLP-compatible backends such as LangSmith and
  Opik
- support JSONL and in-memory exporters for local testing and debugging
- preserve OmniCoreAgent identifiers as attributes:
  `omnicoreagent.trace_id`, `omnicoreagent.span_id`, `omnicoreagent.run_id`,
  `omnicoreagent.session_id`
- fail independently from trace persistence unless strict telemetry mode is
  enabled

Exporters are intentionally outbound-only. They must not become another storage
layer and must not change the internal telemetry schema to match one vendor.

## Trace Normalizer

The normalizer converts raw runtime telemetry into a stable schema for future
evaluation.

Evaluators must consume normalized traces only. They must not depend on
runtime-specific payload shapes or internal class names.

The normalizer should:

- sort spans and events deterministically
- validate parent/child references
- preserve evidence ids
- generate deterministic evidence ids for synthetic normalizer findings
- classify span and event kinds
- produce behavior-relevant summaries
- flag incomplete traces without hiding them
- preserve errors and statuses exactly

## Evidence Contract

Every future evaluation judgment must be able to reference evidence:

- `trace_id`
- `span_id`
- `event_id`
- `sequence_number`

If a telemetry record cannot be referenced later, it is not sufficient evidence.

Reports must be able to say:

```text
tool_discipline.score evidence: [event_004, span_tool_batch_001]
```

## Redaction and Payload Policy

Telemetry must support privacy and cost control from the first implementation.

Configuration should control:

- whether inputs are recorded
- whether outputs are recorded
- whether tool results are recorded inline
- maximum payload bytes
- redacted key names
- large payload offload
- whether model prompts and completions are stored

Large payloads should be summarized inline and optionally stored in workspace or
object storage by reference.

Telemetry must never force secrets into traces.

## Runtime Evidence Ownership

Runtime evidence has one canonical path:

- runtime emits through `TelemetryRecorder`
- `TelemetryStore` owns durable trace/span/event records
- `TelemetryStream` powers live/replay streaming
- OmniServe SSE streams selected telemetry events
- compact event summaries are derived views
- trace retrieval reads canonical telemetry traces

There is no separate runtime event layer or old event store. New runtime work
must emit telemetry directly instead of adding a parallel visibility stack.

## Relationship to OmniServe

OmniServe creates `serve.request` traces for:

- `/run`
- `/run/sync`

The served agent run is correlated through `session_id` and `run_id`. Direct
parent-child trace linking between `serve.request` and `agent.run` is a later
trace-link feature, not required for the foundation telemetry phase.

SSE streaming should not require the full observability stack. It should stream
the subset of telemetry events needed by clients.

## Relationship to Background Agents

Background agents already persist lifecycle events, run ids, attempts,
workspace paths, and heartbeat records.

Telemetry should unify these facts into the trace model. Current runtime
coverage emits `background.run`; `background.task` is reserved for task-level
schedule/config telemetry when that layer is wired.

- `background.task`
- `background.run`
- `agent.run`
- heartbeat events
- lease events
- retry attempts
- cancellation requests
- run workspace references

Background run events are execution evidence and must remain recoverable after
restart when a durable task store is configured.

## Relationship to Future Evaluation

The future OmniCoreAgent Agentic Evaluation system requires trace-native,
evidence-linked execution records.

The evaluation architecture requires more than a final response and a flat event
log. It needs complete trajectories with stable evidence references, state
changes, tool behavior, recovery behavior, risk signals, memory/context
behavior, coordination boundaries, latency, tokens, and cost.

Telemetry must support these evaluation dimensions:

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

Evaluation is not implemented in this telemetry phase. The telemetry design must
only ensure the evidence exists.

## Invariants

- Telemetry is the source of execution evidence.
- Every trace has a `trace_id`.
- Every span belongs to one trace.
- Every event belongs to one trace.
- Spans preserve parent/child relationships.
- Events preserve stable ids and sequence numbers.
- Partial traces are valid traces.
- Missing evidence is represented explicitly.
- Redaction policy applies before persistence.
- Evaluators consume normalized traces, not raw runtime objects.
- Observability, dashboards, exporters, and eval runner are out of scope for the
  telemetry foundation.

## Implementation Discipline

No coding phase should start until the telemetry specification is accepted.

Each implementation phase must include:

- schema tests
- context propagation tests
- parent/child span tests
- concurrent tool batch tests
- redaction tests
- partial trace tests
- telemetry stream replay/follow tests from stored telemetry events
- documentation updates
