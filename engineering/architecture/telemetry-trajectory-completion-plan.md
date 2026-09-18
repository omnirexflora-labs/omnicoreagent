# Telemetry trajectory completion plan

Branch `refactor/native-tool-runtime`, started at `c77a523` (2026-09-18).

## Goal

One call to `agent.run()` produces one trace from which the complete agent
trajectory can be read from A to Z without guessing. This holds whether the
call arrives directly or through OmniServe, and it depends on nothing outside
OmniCoreAgent. Harbor, the evaluation layer, and production feedback are
explicitly later work; they consume this trace and cannot start until it is
trustworthy and complete.

A trace is **trustworthy** when every claim it makes is true: a trace that
says `complete` has no capture gap, redaction always happens before storage,
cursors never skip evidence, and linked traces live in the same store.

A trace is **complete** when a reader can answer every question in the
checklist below from the trace alone.

## Trajectory checklist (definition of done)

| # | Area | A reader must be able to see |
| --- | --- | --- |
| 1 | Request | User input, `session_id` (supplied or generated), `run_id`, `trace_id`, entry point (`direct`, `serve`, `background`), start time. |
| 2 | Harness | Agent name/version, provider and model, model settings, system prompt hash (content under full capture), tool count, tool names and schema hash, max steps, context-management strategy, memory/guardrail/governance/privacy/telemetry configuration fingerprints. |
| 3 | Step | Step number; context supplied to the model (digest always, messages under full capture); model settings; output text (under full capture); tool calls with raw argument strings; finish reason; refusal/limit state; input, output, and total tokens; latency; provider response ID. |
| 4 | Tool call | Name, arguments, raw malformed arguments, rejection reason, resolved provider/tool, authority decision, start/end, status (`success`, `error`, `rejected`, `timeout`, `cancelled`), error, result. |
| 5 | Observation | The exact content returned to the model, linked to its tool result and to the model call that received it; any guardrail, truncation, or offload applied. |
| 6 | Context management | Memory reads/writes, compression/summarization with before/after sizes and dropped groups, offloads, runtime-injected messages. |
| 7 | Delegation | Subagent spawn with child trace/run IDs on success, error, and cancellation; the child's full trajectory is readable through the same reader. |
| 8 | Final | Final answer, terminal status and reason, the model response that produced it, output artifact references. |
| 9 | Run totals | Steps, model calls, input/output/total tokens, cost when known, tool calls by outcome, compressions, subagents, duration. |
| 10 | Honesty | Every payload that was not captured states why, and the trace is `partial` whenever any gap exists. |

Capture policy (decided 2026-09-18): privacy-first defaults stay. A single
opt-in `TelemetryConfig(capture="full")` records full model input/output and
tool payloads, still redacted. With the default, the trace marks model
input/output `not_recorded` and is `partial`.

## Working rules

Every unit follows the same sequence, and the next unit does not start until
the current one is finished:

1. Write the failing test(s) that reproduce the defect or the missing field.
2. Implement the smallest correct change.
3. Run the focused tests, then the full suite (`.venv/bin/python -m pytest -q`),
   then `ruff check` on changed files.
4. Record the result and commit hash in the log at the bottom of this file.
5. Commit the unit on its own.

A unit that reveals a larger problem is split and the plan is updated before
work continues.

## Phase A: trustworthy evidence

### A1. Recorder completeness and privacy
- `end_trace` computes capture gaps after the root span's final output is
  recorded, so a `not_recorded` final output makes the trace `partial`.
- The read-failure fallback stores `evidence_status=partial` and closes the
  root span.
- Error `message` and `stack` go through the privacy filter and inline
  credential redaction (`api_key=…`, `Bearer …`).
- An oversized stack keeps its tail, records a `stack_capture` truncation
  descriptor, and counts as a capture gap.
- Per-trace recorder state (`_trace_templates`, `_incomplete_trace_ids`,
  `_span_parent_contexts`, `_span_sources`) is released after `end_trace`.
- A payload failure is attributed to the trace it occurred in, never to the
  next trace started by the same recorder.
- If the privacy filter itself fails, the value is not persisted as-is: it is
  recorded as `not_recorded` with a reason.
- Secret-key redaction matches whole key words instead of substrings. Found
  during A1: the `token` pattern redacted every token count (`total_tokens`,
  `max_tokens`), so usage was unreadable and every model call counted as a
  capture gap.

### A2. Strict-mode failure isolation
- `agent.run` records an exception and ends a trace only when the current
  context belongs to its own trace; a strict telemetry failure never ends the
  parent (serve or delegation) trace.
- Strict exporter failures are wrapped in `TelemetryExportError` and still
  emit `telemetry_error` before propagating.

### A3. JSONL store integrity
- Reload restores the persisted `stream_cursor` for each event instead of
  renumbering; a corrupt line never shifts later cursors.
- Prune keeps cursors monotonic and keeps live subscribers attached (the store
  object and its subscriber registry survive compaction).
- The compacted file preserves original cross-trace event order.
- Corrupt or unreadable lines are counted and the affected trace, when
  identifiable, is marked incomplete.
- A timed-out write cannot run concurrently with the next write (writes are
  serialized on a single writer, not only by the async lock).

### A4. Delivery
- SSE resume with a cursor replays once; the live pump starts after the
  replay position, so a backlog larger than the queue no longer overflows.
- The legacy `/events/{session_id}` route honours `Last-Event-ID`.
- Duplicate-suppression memory is bounded.

### A5. Lineage store sharing
- `BackgroundAgentManager.register_agent` aligns the agent with the manager's
  telemetry store/recorder, so background and attempt traces share one store.
- Background trace creation is recorded as created only after the upsert
  succeeds; a failed upsert is retried rather than silently dropping later
  events.
- A configured child initialized before delegation re-binds its governance
  and sandbox recorders when it inherits telemetry.
- Configured and dynamic subagent paths record child trace/run IDs on success,
  error, and cancellation, in span attributes rather than only in the
  tool-result payload.

### A6. Retention and memory bounds
- Payload retention is configured independently of trace retention.
- Payload pruning protects every payload referenced by a retained trace and
  runs as an explicit, observable operation.
- The default in-memory store has a bounded retention policy, and the recorder
  and background event log drop per-trace state for finished traces.

## Phase B: complete trajectory

### B1. Capture policy
- Add `TelemetryConfig.capture` with `default` and `full`; `full` enables
  model prompts/responses and full tool payloads through the same redaction,
  truncation, and offload path. Explicit field settings still override.

### B2. Run header
- At `agent.run` start, record the harness snapshot (checklist item 2) in
  trace metadata, filling the existing `agent_version`, `prompt_version`,
  `tool_schema_version`, and `memory_config_version` fields. Versions are
  content hashes unless the caller supplies explicit values.
- Record the real entry point: `direct` for a direct call, `serve` when
  OmniServe invokes the run, `background` for background attempts.
- `run()` accepts optional caller metadata (tags and external identifiers)
  that is stored as provenance without changing execution.

### B3. Model step record
- `agent.step` spans carry the step number.
- Every `model.call` records input/output/total tokens (verified against a
  real provider response, not only the scripted model), model settings,
  provider response ID, latency, and time to first token when streaming.
- Raw tool-call argument strings are recorded with the model response.

### B4. Tool record
- Malformed arguments keep the raw string and the parse error in
  `tool_requested`.
- Timeout and cancellation are distinct span statuses and error types.
- Tool spans and governance decisions carry `tool_call_id`, `batch_id`, the
  originating model event ID, and the `tool_resolved` event ID.
- Subagent calls report their real provider identity.
- Under governance, arguments used are recorded as redacted rather than
  omitted.

### B5. Observation and context links
- `tool_observation` references its tool result event and tool span.
- The next `context_assembly` records which observation event IDs it
  delivered to the model, which gives the observation → next model turn link.
- Runtime-injected messages (stuck and empty-response nudges) are events.

### B6. Finalization and run totals
- `final_answer` references the model response event that produced it and
  any output artifact references.
- A run summary (checklist item 9) is computed at `end_trace` and stored on
  the root span output and the `final_answer` event.

### B7. Trajectory reader
- `agent.get_trajectory(run_id=… | trace_id=…)` returns a documented, ordered
  structure: header, steps (context, model call, tool calls, observations),
  final answer, totals, capture gaps, and nested child trajectories.
- OmniServe exposes the same structure at
  `GET /telemetry/runs/{run_id}/trajectory`.

### B8. Portable contract
- The JSON schema covers the new header, summary, and link fields.
- `import_document` validates against the JSON schema, raises
  `EvidenceValidationError` for all invalid input, and recomputes evidence
  status instead of trusting a claimed `complete`.
- `GenericTraceEvidenceAdapter` marks defaulted values as `inferred` or
  `missing` and downgrades to `partial` when fields are missing.

## Phase C: proof

### C1. End-to-end acceptance
A deterministic scripted-model scenario runs through `agent.run()` directly
and through OmniServe. It covers several steps, a parallel tool batch, a
malformed-argument call, a failing tool, a tool timeout, a large offloaded
result, context compression, a subagent, and a final answer. The test walks
the trajectory reader and asserts every checklist item. The sanitized trace is
committed as a fixture, and a live LiteLLM run confirms token usage from a real
provider.

### C2. Documentation
Update `telemetry-evidence-coverage.md` (correcting the overstated
observation claims), `telemetry-evidence.md`, the observability guide, and the
migration plan log.

## Out of scope for this plan

Harbor integration, the evaluation layer, production sampling, follow-up and
feedback linking, and the MCP v2 adapter. Each is planned separately once
Phase C passes.

## Execution log

| Unit | Status | Commit | Evidence |
| --- | --- | --- | --- |
| A1 | Complete | `1abc441` | 10 new recorder/redaction tests; telemetry, runtime, loop, LLM-step and governance suites 158 passed; full suite 1,187 passed, 14 skipped; acceptance `--check-fixture` and `--run` passed; ruff clean. |
| A2 | Complete | (this commit) | 5 new runtime tests (strict exporter error and timeout, strict store failure, strict finalization failure, cancellation during strict failure) all leave the parent trace running and the parent context restored. Strict exporter failures now raise `TelemetryExportError` (exporter name, original exception as `__cause__`) and record `telemetry_error`; 2 existing tests updated to that contract. Full suite 1,192 passed, 14 skipped; acceptance checks passed; ruff clean. |
