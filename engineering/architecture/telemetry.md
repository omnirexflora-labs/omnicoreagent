# Telemetry Architecture

This is an internal architecture record under `engineering/architecture`, not
public product documentation.

Telemetry is the evidence layer of OmniCoreAgent. It records what happened
during agent execution in a form that can later support debugging,
observability, regression analysis, and the future OmniCoreAgent Agentic
Evaluation system.

This document replaces the older event-only architecture model. Runtime events
remain useful for SSE streaming and lightweight debugging, but they are not a
complete trace system and must not be treated as the evaluation evidence model.
The current runtime event contract remains documented in
`engineering/architecture/events.md` and `engineering/specifications/events.md`
until telemetry becomes the canonical runtime path.

## Purpose

OmniCoreAgent telemetry exists to:

- capture execution facts from the harness while an agent runs
- represent timed work as spans with parent/child relationships
- preserve point-in-time facts as append-only events
- build complete traces for one logical run
- provide stable evidence references for future evaluation
- support local debugging without requiring an observability product
- make future observability, exports, and evals consumers of the same evidence

Telemetry is runtime infrastructure. Observability is a later product layer on
top of telemetry. Evaluation is a later consumer of normalized telemetry.

```text
runtime execution
  -> telemetry recorder
  -> events + spans
  -> trace store
  -> trace normalizer
  -> future observability / future evaluation / future exporters
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
- OpenTelemetry exporter implementation
- LangSmith, Opik, Phoenix, Langfuse, Braintrust, or vendor integration
- policy engine
- sandbox logs

Those systems will be designed separately. They must consume telemetry instead
of bypassing it.

## Current Limitation Being Replaced

Current runtime events are session-scoped records:

```text
EventRouter -> EventStore -> Event -> compact event summary
```

They contain:

- `type`
- `payload`
- `agent_name`
- `timestamp`
- `event_id`
- `sequence`
- `run_id`

That model is useful, but incomplete. It does not provide:

- `trace_id`
- `span_id`
- `parent_span_id`
- `parent_event_id`
- standard `input`, `output`, and `error` fields
- duration for timed work
- token usage and estimated cost
- normalized actor identity
- trace-level metadata
- version metadata
- first-class memory, context, workspace, guardrail, model, and MCP records
- stable normalized evidence for future evaluators

The current `/events/{session_id}/trace` endpoint is therefore a compact event
summary, not a full trace. Future docs and code should avoid using it as the
canonical trace model.

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

Spans form a parent/child tree. This hierarchy is the main difference between
runtime events and real telemetry.

### Telemetry Trace

A telemetry trace is one logical execution graph.

For a normal interactive request, the trace usually starts at `agent.run`.
For OmniServe, it may start at `serve.request` and contain `agent.run`.
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
  -> TraceStore persists events and spans
  -> TraceNormalizer builds stable normalized trace
  -> future BehaviorExtractor consumes normalized trace
  -> future evaluators attach evidence-linked judgments
```

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
during migration, but stable telemetry requires documented event names.

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

Redis streams remain useful for live runtime events, but Redis streams are not
the canonical long-term trace store.

## Trace Normalizer

The normalizer converts raw runtime telemetry into a stable schema for future
evaluation.

Evaluators must consume normalized traces only. They must not depend on
runtime-specific payload shapes, internal class names, or legacy event types.

The normalizer should:

- sort spans and events deterministically
- validate parent/child references
- preserve evidence ids
- classify span and event kinds
- produce behavior-relevant summaries
- flag incomplete traces without hiding them

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

## Relationship to Current Runtime Events

Runtime events remain as a lightweight streaming layer during migration.

Short-term:

- existing `EventRouter` continues powering SSE and event replay
- current `Event` records can be adapted into telemetry events
- `get_trace()` remains a compact event summary

Target:

- runtime emits through `TelemetryRecorder`
- SSE can stream selected telemetry events
- event summary becomes a derived view
- trace retrieval reads canonical telemetry traces

The migration should avoid maintaining two unrelated truth sources.

## Relationship to OmniServe

OmniServe should eventually create `serve.request` spans for:

- `/run`
- `/run/sync`
- background task API calls

The served agent run should become a child of the serving span when the request
starts the agent directly.

SSE streaming should not require the full observability stack. It should stream
the subset of telemetry events needed by clients.

## Relationship to Background Agents

Background agents already persist lifecycle events, run ids, attempts,
workspace paths, and heartbeat records.

Telemetry should unify these facts into the trace model:

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
- Runtime events are not the full trace model.
- Every trace has a `trace_id`.
- Every span belongs to one trace.
- Every event belongs to one trace or is explicitly marked legacy/unbound.
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
- migration tests from current runtime events
- documentation updates
