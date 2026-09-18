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
- PII redaction never alters generated identifiers, digests, or payload
  references. Found after A5b: the credit-card pattern matched Luhn-valid
  digit runs inside hex identifiers (`child_trace_id`, `context_digest`), which
  randomly broke evidence links; this was the unexplained A4 test failure.
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
- Each JSONL record starts with its `trace_id`, so a record truncated by a
  crash can still be attributed to its trace. Found during A3: with sorted
  keys the identifier was near the end of the line and lost on truncation.

### A4. Delivery
- SSE resume with a cursor replays once; the live pump starts after the
  replay position, so a backlog larger than the queue no longer overflows.
- The legacy `/events/{session_id}` route honours `Last-Event-ID`.
- Duplicate-suppression memory is bounded.
- The store's own live follow uses the same cursor high-water mark instead of
  an unbounded set of delivered event IDs.

### A5. Lineage store sharing
- `BackgroundAgentManager.register_agent` aligns the agent with the manager's
  telemetry store/recorder, so background and attempt traces share one store.
  The agent keeps its own recording policy (config, exporters, payload store,
  privacy filter). An agent explicitly given a different store is rejected
  with a clear error instead of having its traces silently moved.
- Background trace creation is recorded as created only after the upsert
  succeeds; a failed upsert is retried rather than silently dropping later
  events.
- Rebinding telemetry (delegation or background registration) also rebinds
  every component that captured the recorder at build time: the governance
  engine, its sandbox runtime, and the dynamic subagent factory.
- A background event lost to a failed or timed-out write marks the
  background trace incomplete and partial. Found during A5: dropped events
  were silent and the trace still claimed complete evidence.

### A5b. Delegation and attempt identity
Split from A5 during implementation (2026-09-18).
- Configured and dynamic subagent paths record child trace/run IDs on success,
  error, and cancellation, in span attributes rather than only in the
  tool-result payload.
- Background attempts: each attempt's agent trace carries the attempt ID and
  number, and the background trace links every attempt's trace.
- A run stopped by a timeout (background attempt, `/run`, `/run/sync`) is
  recorded as `timeout`, not `cancelled`.
- Dynamic spawns record a `subagent.run` delegation span with the workspace
  output verification result.
- Trace families are ordered by lineage (parent before child, depth first).
  Found during A5b: ordering by start time listed a child before its parent
  when both started in the same clock tick.

### A6. Retention and memory bounds
- Payload retention is configured independently of trace retention
  (`payload_retention_days`).
- Payload pruning protects every payload referenced by a retained trace.
- Cleanup is automatic and observable (decided 2026-09-18): the configured
  window is applied once per agent before its first run and on demand with
  `agent.prune_telemetry()`; every cleanup is logged and reported by
  `agent.telemetry_retention_status()` and `GET /telemetry/retention`.
  `retention_days=None` keeps every trace.
- The default in-memory store keeps at most `memory_max_traces` finished
  traces (running traces are never evicted), and the recorder and background
  event log drop per-trace state for finished traces.

## Phase B: complete trajectory

### B1. Capture and storage policy
- Add `TelemetryConfig.capture` with `default` and `full`; `full` enables
  model prompts/responses and full tool payloads through the same redaction,
  truncation, and offload path. Explicit field settings still override.
- Durable storage by default (decided 2026-09-18): `storage="auto"` writes
  JSONL even when no workspace is configured, at the default local workspace
  path `./workspace/telemetry/traces.jsonl`. `storage="memory"` becomes an
  explicit opt-in. A cloud workspace still does not make telemetry a cloud
  dependency; it uses the same local file unless a path is configured. The
  effective store and path stay visible in trace metadata, and the docs state
  where traces live.
- The test suite points `OMNICOREAGENT_WORKSPACE_DIR` at a temporary directory
  so durable defaults never write into the repository.
- Found during B1 (pre-existing races exposed once telemetry did real I/O):
  shutdown cancelling an attempt while it was starting left the run RUNNING
  with a live heartbeat; a second cancellation could interrupt recording the
  interrupted attempt; a finished run's events could be read before its
  terminal event was recorded. Attempt start and interruption bookkeeping now
  complete under cancellation (bounded), and reading a finished run's events
  waits for its terminal event within the replay timeout.
- A6 had added a second payload-reference collector next to the existing
  `prune_telemetry_payloads()`; both now share `payload_references`.

### B2. Run header
- At `agent.run` start, record the harness snapshot (checklist item 2) in
  trace metadata, filling the existing `agent_version`, `prompt_version`,
  `tool_schema_version`, and `memory_config_version` fields. Versions are
  content hashes unless the caller supplies explicit values.
- Record the real entry point: `interactive` (the existing vocabulary for a
  direct call), `serve` when OmniServe invokes the run, `background` for
  background attempts. Traces inherit the surface of the context they start in.
- `run()` accepts optional caller metadata (tags and external identifiers)
  that is stored as provenance without changing execution.

### B3. Model step record
- `agent.step` spans carry the step number.
- Every `model.call` records input/output/total tokens (verified against a
  real provider response, not only the scripted model), model settings,
  provider response ID, latency, and time to first token when streaming.
- Raw tool-call argument strings are recorded with the model response.
- Model call facts live in event metadata (kept under every capture policy).
  Found during B3: provider retries inside `llm_call` were invisible, so a
  call that failed twice then succeeded looked clean; every retry is now
  recorded with its error. The span output also carried the refusal text past
  the response capture policy; it now records only whether the model refused.
  Keys ending in a unit (`_ms`, `_seconds`, `_bytes`) are no longer treated as
  secrets. Time to first token is recorded as `time_to_first_delta_ms` because
  tool-call-only streams expose no measurable first token.

### B3b. Cost and standard usage fields
Added by the reassessment after B3 (2026-09-18).
- Record the provider-reported cost of each call (LiteLLM `response_cost`) as
  `cost_usd` in the model call facts; `null` when the provider supplies none.
- Populate the standard `token_usage` and `cost_usd` fields of the
  `model.call` span and `model_response` event. They were never set by the
  runtime, so the OTel/LangSmith/Opik exporters and portable consumers saw no
  token usage or cost.

### B4. Tool record
- Malformed arguments keep the raw string and the parse error in
  `tool_requested`.
- Timeout and cancellation are distinct span statuses and error types.
- Tool spans and governance decisions carry `tool_call_id`, `batch_id`, the
  originating model event ID, and the `tool_resolved` event ID.
- Subagent calls report their real provider identity.
- Under governance, arguments used are recorded as redacted rather than
  omitted, and delegation parameters follow the same redaction as tool
  arguments (they were stored unredacted).

### B5. Observation and context links
- `tool_observation` references its tool result event and tool span.
- The next `context_assembly` records which observation event IDs it
  delivered to the model, which gives the observation → next model turn link.
- Runtime-injected messages (stuck and empty-response nudges) are events.
- Every `model.call` records its purpose (`agent_turn` or `context_summary`)
  so internal model work is distinguishable from agent turns.

### B6. Finalization and run totals
- `final_answer` references the model response event that produced it and
  any output artifact references.
- A run summary (checklist item 9) is computed at `end_trace` and stored on
  the root span output and the `final_answer` event.
- Serve traces report the agent's real outcome: `/run/sync` recorded
  `completed` even when the agent returned an error, and a cancelled request
  left the serve trace `running`.

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

## Reassessment after B3 (2026-09-18)

Checklist status: items 1, 2, 7, and the runtime side of 10 are done; item 3 is
done except cost and the standard usage fields (B3b); items 4, 5, 6, 8, and 9
remain (B4 to B6); the reader (B7), the portable import checks (B8), and the
proof (C1, C2) follow. A real trace with the new events still validates
against the portable JSON schema. Deliberately not planned now: pagination for
trace-family endpoints, and retaining pre-guardrail raw tool results (kept as
a hash by design).

## Out of scope for this plan

Harbor integration, the evaluation layer, production sampling, follow-up and
feedback linking, and the MCP v2 adapter. Each is planned separately once
Phase C passes.

## Execution log

| Unit | Status | Commit | Evidence |
| --- | --- | --- | --- |
| A1 | Complete | `1abc441` | 10 new recorder/redaction tests; telemetry, runtime, loop, LLM-step and governance suites 158 passed; full suite 1,187 passed, 14 skipped; acceptance `--check-fixture` and `--run` passed; ruff clean. |
| A2 | Complete | `1a23a8a` | 5 new runtime tests (strict exporter error and timeout, strict store failure, strict finalization failure, cancellation during strict failure) all leave the parent trace running and the parent context restored. Strict exporter failures now raise `TelemetryExportError` (exporter name, original exception as `__cause__`) and record `telemetry_error`; 2 existing tests updated to that contract. Full suite 1,192 passed, 14 skipped; acceptance checks passed; ruff clean. |
| A3 | Complete | `fca989d` | 7 new store tests: cursors survive reload, a corrupt line (counted in `skipped_records`, trace marked incomplete and partial), embedded upsert events, prune with a live follower, compaction plus reload, a timed-out write followed by the next write, and loading the previous record format. Full suite 1,199 passed, 14 skipped; acceptance checks passed; ruff clean. |
| A4 | Complete | `32fa6ae` | 3 new tests: resuming with a 1,200-event backlog then following live (previously always overflowed), the legacy `/events/{session_id}` route honouring `Last-Event-ID`, and bounded duplicate tracking. Full suite 1,202 passed, 14 skipped; acceptance checks passed; ruff clean. Open item: `test_configured_child_inherits_parent_telemetry_and_is_linked` failed once in about 62 runs of the combined serve/telemetry suites and could not be reproduced (0 in 150 isolated runs, 0 in 30 baseline runs at `fca989d`); the test now reports the child exception if it recurs. |
| A5 | Complete | `1c7f820` | 8 new tests: registration shares the manager store and keeps the agent's recording policy; an explicit different store is rejected; governance, sandbox, and subagent-factory recorders are rebound on registration and on delegation; a failed or slow first upsert no longer loses the background trace, and lost events mark it partial; with default settings a background run's family (lifecycle trace plus attempt trace) is complete from the agent (verified empty under the old behavior). Full suite 1,210 passed, 14 skipped; acceptance checks passed; ruff clean. |
| A5b | Complete | `082ac12` | 13 new tests: configured delegation records child trace/run IDs on success, error, and cancellation (IDs in event metadata, retained under every capture policy); dynamic spawns get a delegation span with workspace output verification and keep the child identity when the child raises; background retry attempts carry their attempt ID/number and task ID; timeouts (direct deadline, background attempt, `/run/sync`) are recorded as `timeout` while caller cancellation stays `cancelled`; families list parents first even with equal start times. Full suite 1,223 passed, 14 skipped; acceptance checks passed; ruff clean. The A4 open item was traced to identifier redaction and is fixed in the next commit. |
| A1 follow-up | Complete | `4c0dbac` | Card numbers must be standalone tokens. 2 new privacy tests (identifiers, digests, references, and IDs inside free text are never altered; standalone and hyphen-joined card numbers are still redacted). The lineage test that failed 2 in 300 runs now passes 300 of 300. Full suite 1,225 passed, 14 skipped; acceptance checks passed; ruff clean. Resolves the A4 open item. |
| A6 | Complete | `0247a99` | 8 new tests: independent payload retention and config validation; payload references collected from descriptors and offloaded stubs; pruning removes expired traces and orphaned payloads while keeping every payload a kept trace references; automatic cleanup runs once per agent and is reported in the status; the in-memory store evicts only the oldest finished traces and keeps live followers attached; the default memory store is bounded; the background event log forgets finished runs; `GET /telemetry/retention`. Full suite 1,233 passed, 14 skipped; acceptance checks passed; ruff clean. Phase A complete. |
| B1 | Complete | `56cdecf` | 12 new tests: capture presets (fill only unset fields, round-trip, full capture records model responses); durable JSONL default in the workspace directory; memory as explicit opt-out; cloud workspaces keep a local file; one shared store per file; default agents and manager share it; a default agent's trace survives a restart; shutdown during attempt start records the run (verified RUNNING without the fix); a finished run's events include its terminal event. 2 cloud-workspace tests moved to the new contract. Docs updated. Full suite 1,245 passed, 14 skipped; acceptance checks passed; ruff clean; nothing written to the repository workspace. |
| B2 | Complete | `3ece487` | 12 new tests on a real `OmniCoreAgent` run with a scripted model: the `run_configuration` header (model and settings, limits, context/memory/offload config, features, fingerprints, tool catalog, system prompt digest) is recorded before the first step, links to the first `context_assembly` digests, never contains credentials, and survives `record_outputs=False`; trace versions are filled, stable across identical harnesses and changed by a different prompt; explicit `agent_version` wins; prompt text follows the capture policy; `run(tags=, provenance=)`; surfaces `interactive`, `serve`, `background`. Observability guide updated. Full suite 1,257 passed, 14 skipped; acceptance checks passed; ruff clean. |
| B3 | Complete | `b46504a` | 7 new model-step tests (tokens incl. cached and reasoning, provider response ID and served model, request settings, latency, time to first streamed delta, retries on success and failure, facts kept with `record_outputs=False`, raw arguments exact under full capture, refusal text not leaked). Live LiteLLM check (`gpt-5.4-mini`, non-streaming and streaming, local tool): real tokens (1,471 in / 21 out; 1,024 cached), `chatcmpl-...` IDs, latency, first delta 1.57 s on the streamed answer, API key absent from both traces. Full suite 1,264 passed, 14 skipped; acceptance checks passed; ruff clean. |
