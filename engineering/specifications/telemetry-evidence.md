# Telemetry evidence contract

This is the portable, evaluator-facing contract for execution evidence. It
extends the operational trace model without changing the ownership boundary:
`TelemetryRecorder` writes, `TelemetryStore` persists, and evaluators consume a
normalized copy. The standalone envelope is defined by
[`portable-execution-evidence.schema.json`](portable-execution-evidence.schema.json)
and is identified as `omnicoreagent.execution-evidence/v1`.

## Versioning

New records use `schema_version=3`. Readers must accept older records and mark
unmappable relationships as missing evidence. A schema version describes the
record shape, not the agent or evaluator version. The outer evidence envelope
has its own `schema_version=1`; it contains JSON values only and does not expose
`TelemetryTrace`, `TelemetrySpan`, or `TelemetryEvent` instances. Consumers can
validate it using the JSON Schema without installing OmniCoreAgent. The
runtime-only adapter accessor is not part of the serialized contract.

## Capture states

Every payload-bearing input or output has a capture state:

| State | Meaning |
| --- | --- |
| `available` | The payload is present in the record. |
| `redacted` | The payload existed and sensitive values were removed. |
| `truncated` | The payload existed but only a bounded preview was retained. |
| `offloaded` | The redacted payload is available through a content-addressed reference. |
| `not_recorded` | Capture was disabled by policy before the payload was persisted. |
| `missing` | The runtime or adapter could not establish that the payload existed. |
| `inferred` | A derived adapter value is present but was not directly observed. |

`None` payloads without a capture descriptor are treated as legacy records,
not as proof that no value existed. A trace with any unavailable payload
descriptor is reported as `partial`; this includes intentional privacy policy
exclusions, so an evaluator cannot mistake a successful write for complete
evidence.

## Evidence descriptor

```json
{
  "state": "available|redacted|truncated|offloaded|not_recorded|missing|inferred",
  "source": "user|provider|runtime|tool|memory|workspace|guardrail|adapter|derived",
  "role": "request|context|model_response|tool_result|observation|final_output",
  "reference": "telemetry://payload/...",
  "content_type": "application/json",
  "checksum": "sha256...",
  "original_bytes": 1234,
  "recorded_bytes": 800,
  "policy_version": "...",
  "reason": "capture disabled by policy"
}
```

Only `state`, `source`, and `role` are required. References and checksums are
always computed over the redacted representation. `reason` is required for
`not_recorded`, `missing`, `truncated`, and `inferred` states.

## Causal references

Events and spans retain existing trace/span/event identifiers. Evaluation
extractors use these relationships:

- model response event -> requested tool call IDs;
- tool call -> model turn and resolution event (`tool_call_id`,
  `model_call_event_id`, `model_response_event_id`, `tool_requested_event_id`,
  `tool_resolved_event_id`);
- tool result -> tool call ID;
- observation -> tool result (`tool_result_event_id`, `tool_span_id`, or
  `tool_requested_event_id` for a rejected call) and next model turn (the
  receiving `model_call` lists it in `new_observation_event_ids` exactly once;
  `observation_event_ids` lists every observation still in its context);
- child run -> parent delegation call and parent result (`child_trace_id`,
  `child_run_id` on success, error, and cancellation);
- context change -> input message/group identifiers and resulting context;
  runtime-injected messages are `runtime_message` events whose digests match
  the next context;
- final answer -> terminal run, the model response that produced it
  (`final_model_response_event_id`), and output artifact references.

The event sequence is an ordering aid only. Parallel calls must remain
independent unless an explicit causal reference says otherwise.

Stream consumers may receive a store-local `stream_cursor` on replay/follow
event copies. It is a transport position for reconnecting from the same store,
not an evidence identity; evaluators must use `event_id` and `sequence_number`.
OmniServe emits that cursor as the SSE `id` field when available.
Clients may resume `/telemetry/events/stream` with either the `cursor` query
parameter or the standard `Last-Event-ID` header. A supplied cursor is replayed
before live follow; duplicate event IDs are suppressed at the SSE boundary.

Context digests identify the exact ordered message/tool catalog supplied to a
model without requiring content retention. Before hashing, the runtime applies
the telemetry privacy boundary (PII policy and configured secret-key redaction)
to the canonical message and tool records. The digest therefore never hashes
the unpermitted representation. Size truncation and offloading happen after
this canonicalization: an offloaded reference resolves to the same permitted
representation, while a truncated or disabled payload is explicitly a
reconstruction gap. Prompt payloads are added to the context/model records only
when `record_model_prompts` is enabled. Compression records before/after
digests and the groups dropped or replaced. A context summary model request is
a normal linked `model.call` span, so internal model work is not invisible to
evaluation.

When a provider stream is used, the `model.call` span records bounded stream
statistics (`streaming`, delta count, visible text byte count, and event-type
counts). It does not retain each token by default. A cancelled or failed stream
keeps those statistics with its terminal span status, while the complete
`model_response` payload remains governed by `record_model_responses`.

## Reading a run

`build_trajectory(trace)` (and `agent.get_trajectory(trace_id | run_id=...)`,
`GET /telemetry/runs/{run_id}/trajectory`,
`GET /telemetry/traces/{trace_id}/trajectory`) returns the
`omnicoreagent.trajectory/v1` view of one run: the request, the harness from
`run_configuration`, ordered steps with their context, model calls, tool calls
and observations, runtime messages, compressions, delegated child
trajectories, the final section, run totals, and the evidence status with its
capture gaps. It is derived from the trace and never changes it. Every event of
the trace appears exactly once; an event the reader cannot place is listed in
`other_events` rather than dropped. The trajectory checklist and the tests
behind each item are in the
[evidence coverage map](../architecture/telemetry-evidence-coverage.md).

## Delivery failure and latency policy

Persistence and exporter calls have independent configurable timeouts. The
defaults are five seconds per persistence operation and five seconds per
exporter; `None` explicitly disables the corresponding bound. A best-effort
recorder marks the affected trace incomplete when a store operation times out
or fails, then lets the application continue. A strict recorder propagates the
failure. Exporters are optional: a timed-out or failed exporter produces a
`telemetry_error` event identifying that exporter while the stored execution
trace remains usable. Strict mode propagates exporter failure during explicit
finalization. Exporters are run one at a time with an individual bound, so one
slow destination cannot consume an unbounded request-finalization interval.

Evaluation jobs are not run by `end_trace`; they must consume finalized traces
as an independent asynchronous workload with their own queue, retries, and
resource limits.

## Adapter boundary

`OmniCoreEvidenceAdapter` accepts a stored OmniCoreAgent trace or its serialized
form, normalizes it, validates causal identity, and returns a
`PortableExecutionEvidence` view. Its `trace` field and `model_dump()` are
plain JSON mappings; `import_document()` validates and re-imports the versioned
envelope. It reports final-output references and missing/capture-gap evidence
while leaving the trace facts unchanged. A controlled adapter may attach task,
case, trial, environment, and verifier metadata before handing the same view to
a future evaluator.

`validate_portable_evidence_document()` checks a document against the published
JSON Schema, which ships inside the package
(`omnicoreagent/core/telemetry/schemas/portable-execution-evidence.schema.json`,
identical to the copy in this directory), then checks identity and
relationships. The schema types the runtime's evidence metadata: model call
facts (`model_call`), run totals (`run_summary`), the run header
(`run_configuration`), and link fields such as `tool_call_id`,
`model_response_event_id`, `tool_result_event_id`, and
`new_observation_event_ids`. A capture descriptor whose state is
`not_recorded`, `missing`, `truncated`, or `inferred` must carry a `reason`.
Every `facts` and `final_output_references` entry of kind `event` or `span`
must name a record in the trace. `import_document()` accepts any document the
schema accepts (unknown fields, span kinds, and actor types are kept in the
portable copy), raises only `EvidenceValidationError` for invalid input, and
recomputes evidence completeness: a document claiming `complete` whose records
contain capture gaps is returned as `partial`, with an `evidence_status_claim`
entry and the gaps in `missing_evidence`.

`GenericTraceEvidenceAdapter` is a small vendor-neutral import fixture. It maps
common agent/model/tool span names to the foundation kinds and preserves unknown
event types as experimental facts. It retains supplied timestamps, durations,
usage, errors, capture descriptors, provenance, and parent relationships. When
an external payload omits a required value, the serialized field is `null` and
`missing_evidence` records the unknown field; the adapter does not fabricate a
successful or complete execution. A value the adapter must supply to satisfy
the schema (span kind, actor, an event's span) is kept, recorded as `inferred`
in `missing_evidence`, and named in `external_inferred_fields`; an absent event
type becomes the experimental `external_event`; an invalid (for example
negative) cost is `null`. Any unknown or inferred field makes the evidence
`partial`, whatever the external producer claimed. The adapter never modifies
its input. This proves that production evidence does
not require OmniCoreAgent's internal classes; Harbor integration can use the
same boundary later without making the runtime depend on Harbor.

## ATIF and OpenTelemetry alignment

Harbor's [Agent Trajectory Interchange Format (ATIF)](https://www.harborframework.com/docs/agents/trajectory-format)
is a JSON trajectory format for messages, agent responses, tool executions,
observations, metrics, and multi-agent relationships. Its sequential `steps`
model and extensible `extra` fields are useful for controlled-run trajectory
exchange. OmniCoreAgent's evidence envelope reuses those concepts where they
fit, but retains a span/event graph, independent request/tool/observation
identifiers, cross-trace parent links, capture states, and explicit missing
evidence. An ATIF exporter/adapter can map steps to this contract later; the
runtime does not depend on Harbor.

The [OpenTelemetry GenAI agent conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)
provide common `invoke_agent`, model, and tool span semantics and recommend
standard provider/operation attributes. OmniCoreAgent keeps its own stable
internal event registry and maps model/provider, tool, status, usage, and error
fields to OTel exporters. OTel conventions are still marked development, so
their names remain an export mapping rather than a compatibility requirement for
the portable evidence document.

## Required normalized fields

The normalized trace must expose:

- execution identity and provenance;
- terminal status, termination reason, and evidence completeness;
- ordered spans with parent relationships and timing;
- events with stable IDs, sequence, timing, actor, and capture descriptors;
- model/provider/configuration identity and usage;
- native calls, resolved tool identity, arguments, result, observation, and
  authority outcome;
- memory, context, workspace, guardrail, delegation, serving, and background
  boundaries when present;
- explicit missing/redacted/truncated/offloaded evidence;
- no evaluator score or release decision unless supplied by a separate
  evaluation record.

## Evaluation record boundary

Evaluation records are separate from runtime traces. A future evaluation record
will reference one or more trace IDs and include:

- task requirements and environment assumptions;
- evaluator/rubric identity and version;
- deterministic checks, model judgments, or human assessments;
- evidence references supporting each finding;
- uncertainty and unsupported-claim reasons;
- aggregate result and release/investigation decision.

Appending evaluation results to a trace may be supported as a linked artifact,
but it must not mutate the original execution facts.
