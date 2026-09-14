# Built-in Telemetry Migration Plan

The next phase is defined by [the evaluation-ready telemetry foundation](evaluation-telemetry-foundation.md)
and its [portable evidence contract](../specifications/telemetry-evidence.md).
The operational telemetry foundation remains the runtime source of truth;
this phase makes its evidence reconstructable and trustworthy for future
controlled (Harbor) and production evaluation paths.

This plan covers the telemetry work after the native tool runtime migration.
The goal is a complete, useful telemetry system owned by OmniCoreAgent itself.
External exporters such as LangSmith, Opik, and OTLP backends remain optional
adapters. They must never be required for a run, trace lookup, replay, API
stream, background job, or deep-agent execution to work.

The source snapshot for this plan is branch `refactor/native-tool-runtime`.
The plan was started at commit `8841795abda60388117ff5a1b32b3a633ede3a3a`;
implementation checkpoints are listed below as they land. The current
checkpoint is `d2a3246`.

## Current boundary

The current built-in path is:

```text
runtime -> TelemetryRecorder -> TelemetryStore -> TelemetryStream -> OmniServe SSE
```

Normal agent runs record model, tool, observation, memory, workspace,
guardrail, governance, and finalization evidence. `agent.stream()` delivers
live runtime events, while telemetry replay/follow is exposed separately by
the agent API and OmniServe telemetry SSE routes. The built-in path works with
no exporter or hosted tracing service installed.

The completed checkpoint provides adaptive in-memory/JSONL storage, explicit
retention and strictness policy, incomplete-trace marking for best-effort
persistence failures, linked child/background/serving traces, and explicit
trace-family lookup. Model prompts and responses remain excluded by default;
when enabled they use the configured redaction, truncation, and built-in
workspace/object-storage offload policy.

The remaining telemetry work is delivery hardening: verify reconnect behavior
under production load, finish public streaming guarantees, and keep
provider/model buffering separate from telemetry buffering. Those checks are
prerequisites for changing PromptGuard behavior or the MCP adapter, but they do
not require an external trace platform.

## Evaluation-evidence work sequence

The following sequence is now the active telemetry design and implementation
plan. Each unit is independently tested, committed, and pushed before the next
unit starts.

1. **Versioned evidence metadata.** Add schema version, execution surface,
   provenance, and explicit payload capture states to traces, spans, and
   events. Existing operational fields remain readable.
2. **Complete request trajectory.** Instrument request, context assembly,
   model turn, native tool request/resolution, executor result, post-guardrail
   observation, and finalization with stable causal identifiers.
3. **Context and lineage evidence.** Record context group/snapshot references,
   child/background/serving links, workspace artifacts, and explicit missing
   evidence without copying or erasing conversation history.
4. **Portable evidence adapters.** Validate normalized OmniCoreAgent traces and
   a minimal external adapter fixture. Harbor task/trial/verifier integration
   and production selection follow the portable contract but remain separate
   from the runtime recorder.
5. **Telemetry delivery hardening.** Make replay/follow cursors resumable,
   expose transport positions through SSE, and verify cancellation, overflow,
   reconnect, and provider buffering boundaries. Delivery remains separate from
   evidence identity.
6. **Evaluation layer.** Add task specifications, deterministic checks,
   evaluator judgments, comparisons, sampling, and release decisions only
   after the trace contract is proven. Evaluation records must reference facts
   and must not mutate them.

### Evaluation-evidence execution log

| Unit | Status | Evidence |
| --- | --- | --- |
| 1. Versioned evidence metadata | Complete at `36d88f4` | 49 telemetry foundation/export tests passed; 202 runtime/telemetry tests passed; Ruff and diff checks passed. Traces, spans, and events preserve schema version, capture state, execution surface, provenance, and legacy evidence status. |
| 2. Complete request trajectory | Complete at `407b0d4` | 83 focused model/runtime/telemetry tests passed; Ruff and diff checks passed. Model turns expose tool catalog/count and finish metadata; requested/resolved calls, execution events, batch IDs, and exact post-offload observations retain causal links. |
| 3. Context and lineage evidence | Complete at `b67fb18` | Context assembly and compression preserve message/tool digests and opt-in prompt payloads; internal summary calls, memory reads/writes, workspace offloads, and subagent terminal links are correlated. Capture gaps mark traces partial and are surfaced by normalization. Focused suite: 102 passed. Full suite: 1,155 passed, 14 skipped, with one unrelated `tiktoken` encoding-cache failure in `tests/test_base.py::test_run_prepares_internal_tools_once_for_prompt_and_execution`. |
| 4. Portable evidence adapters | Complete at `adfa91d` | `OmniCoreEvidenceAdapter` normalizes and validates built-in traces, preserves cross-trace lineage, exposes final-output references and capture gaps, and returns a judgment-free portable view. `GenericTraceEvidenceAdapter` proves an external trace shape can be imported while preserving unknown events as experimental facts. Adapter/docs/runtime suite: 111 passed. |
| 5. Telemetry delivery hardening | Complete at `d2a3246` | Stream event copies carry store-local cursors, JSONL replay rebuilds cursor positions, SSE emits resumable `id` fields, and `/telemetry/events/stream` accepts `cursor` or `Last-Event-ID`. Focused stream/API suite: 65 passed. |

## Rules for every phase

1. Built-in telemetry is the runtime source of truth.
2. No paid service, external trace collector, or exporter is required for
   execution or streaming.
3. External exporters consume completed built-in traces and cannot change
   runtime behavior or identity.
4. Every trace, span, and event remains inspectable through local APIs.
5. Partial, failed, cancelled, timeout, and safety-halted traces are retained.
6. Redaction occurs before persistence, and secrets never become a required
   trace input.
7. Parent/child, run, session, task, and serving relationships are explicit
   and queryable.
8. Active text streaming and telemetry streaming remain separate contracts,
   but both are backed by the same runtime execution evidence.
9. Existing conversation memory, workspace storage, offload references, and
   background task stores are not erased or silently converted.
10. Each phase changes one contract, adds focused tests, runs the full suite,
    records live validation where applicable, and ends in its own commit.

## Migration units

### 1. Establish the built-in telemetry contract

Existing contract: callers may inject a store, recorder, stream, or exporters;
otherwise the facade creates an in-memory store and recorder.

Work:

- add a first-class `TelemetryConfig` path to the agent construction/config
  boundary;
- make built-in recording, redaction, truncation, strictness, and optional
  local payload references explicit;
- keep in-memory storage as the lightweight default and document JSONL as the
  built-in local durable option;
- ensure exporters are optional and fail independently from stored traces;
- expose effective telemetry configuration in trace metadata without exposing
  secrets.

Verification:

- construction with no telemetry integrations;
- custom redaction and model-recording settings;
- strict and non-strict store failures;
- JSONL restart/reload;
- exporter absence and exporter failure;
- normal run and public result remain functional with no exporter installed.

### 2. Correct execution lineage and trace correlation

Existing contract: normal runtime work shares one trace, but child agents,
background lifecycle, and serving boundaries may create separate traces that
only share `run_id` or `session_id`.

Work:

- define the built-in lineage model for agent, child, background, and serving
  traces;
- pass the parent telemetry store and recorder into dynamic and configured
  child agents;
- record parent trace/span and child trace identifiers on both sides of a
  delegation boundary;
- add a queryable trace-family/lineage operation so “all evidence for this
  run” does not depend on latest-trace ordering;
- preserve async context isolation for parallel tools and parallel children;
- make background and serving relationships explicit rather than relying on
  ambiguous shared `run_id` values.

Verification:

- one local tool and one parallel tool batch;
- dynamic deep-agent spawn with workspace output;
- configured child agent;
- background execution and retry/cancel/timeout;
- `/run` and `/run/sync` serving boundaries;
- concurrent sessions and concurrent runs sharing one session.

### 3. Make the trace store complete without external infrastructure

Existing contract: `InMemoryTelemetryStore` supports live/replay and
`JsonlTelemetryStore` supports local append/reload; there is no required
external telemetry backend.

Work:

- define retention and restart behavior for in-memory and JSONL stores;
- make JSONL writes safe enough for concurrent runtime use and clear about
  partial records;
- add a built-in local durable option at the application boundary where
  needed, without making cloud observability a dependency;
- preserve trace/event cursors across reload where the store contract promises
  replay;
- surface incomplete evidence instead of silently returning a successful
  trace when persistence failed.

Verification:

- process restart and JSONL recovery;
- concurrent writes;
- malformed record recovery;
- cursor replay and reconnect;
- trace normalization after partial failure.

### 4. Finish telemetry-backed streaming and API behavior

Existing contract: text deltas and tool events can be delivered live; telemetry
events can be replayed/followed through agent APIs and OmniServe SSE.

Work:

- verify every public event has stable run/session/trace correlation;
- make telemetry SSE replay and live follow behavior deterministic;
- define queue overflow and disconnect behavior;
- keep `/run` final responses and trace retrieval usable after cancellation,
  timeout, or handled failure;
- document that full traces are finalized at run completion while events are
  available during execution;
- identify remaining provider/model buffering separately from telemetry.

Verification:

- direct `stream()`;
- telemetry replay from a cursor;
- live telemetry follow;
- OmniServe `/run` SSE and telemetry SSE;
- cancellation and timeout;
- reconnect after a client disconnect.

### 5. Connect governance and PromptGuard evidence

This phase does not redesign either subsystem yet. It ensures their decisions
are represented consistently in the built-in trace before their behavior is
changed.

Work:

- record policy requests, decisions, approvals, sandbox outcomes, and denied
  operations with stable evidence IDs;
- record PromptGuard checks, blocks, and tool-output scrubbing outcomes;
- distinguish input safety decisions from capability authorization;
- include effective policy and guardrail configuration fingerprints where
  useful, without storing secrets.

Verification:

- allow, deny, approval, budget, and sandbox governance cases;
- safe, suspicious, dangerous, and critical guardrail cases;
- local, workspace, artifact, MCP, and subagent boundaries.

### 6. Upgrade the MCP adapter against the installed v2 SDK

This phase comes after lineage and evidence are reliable.

Work:

- adapt stdio, SSE, and streamable HTTP construction to the installed MCP v2
  signatures;
- preserve concrete server/tool identity through resolution, governance,
  observations, and telemetry;
- define connect, reconnect, cleanup, timeout, and partial-server behavior;
- remove or replace configuration fields that do not control behavior;
- run real local MCP server smoke tests and API/telemetry checks.

No MCP implementation work belongs in the earlier phases.

## First implementation checkpoint

The earlier operational telemetry units are already present. The evaluation
evidence units below extend them without changing PromptGuard detection
semantics, MCP behavior, context strategy, workspace storage, or model
streaming. They keep runtime facts separate from future evaluator judgments.

The next checkpoint is portable evidence adapters (migration unit 4): validate
normalized traces and an independent adapter fixture before adding evaluators.
Delivery hardening and production tests for replay/follow cursors, reconnects,
bounded queues, cancellation, and provider buffering follow that boundary. MCP
v2 remains a separate later unit because its installed
SDK compatibility issue is already known and intentionally deferred.

## Decisions recorded before the persistence/API phase

These decisions apply to the built-in telemetry path and do not require a
vendor exporter or hosted tracing service.

### Storage selection

The effective default is adaptive: an agent with an explicitly configured
workspace uses local JSONL when no telemetry store is injected; an agent with
no explicit durable workspace keeps the lightweight in-memory store. The
configuration can always override this with `memory` or `jsonl`, and an
explicitly injected store wins over either default. A JSONL path is derived
from the local workspace when it is not supplied. Cloud workspace backends do
not silently turn telemetry into a cloud dependency; callers select a durable
telemetry store deliberately.

### Child trace shape

Child executions remain separate traces linked by `parent_trace_id` and
`parent_span_id`. The parent delegation span records the child trace and run
IDs. This preserves child retries, budgets, worker lifetimes, and retention
boundaries, while a family query reconstructs the complete execution graph.

### Trace API shape

`/telemetry/traces` remains an individual-trace listing endpoint with filters
and bounded results. Exact trace retrieval remains stable. Linked families are
requested explicitly through `/telemetry/traces/{trace_id}/family`; implicit
family expansion would make pagination, authorization, and retention behavior
ambiguous.

### Model I/O capture

Model prompts and responses stay excluded by default. When debugging requires
them, an explicit capture policy enables them through the same key redaction,
size truncation, and optional workspace offload path used by other telemetry
payloads. Model/provider metadata, token usage, tool identity, status, and
redacted tool arguments/results remain the default evidence surface.

### Retention

Retention is configurable independently for JSONL trace records and offloaded
payload references. The default is a bounded local retention window; callers
can explicitly choose unlimited retention or an age/size policy appropriate to
their deployment. Cleanup must be explicit and observable, and must not erase
active traces or leave references to already-deleted payloads.

### Persistence failure

Telemetry persistence is best effort by default: a store failure marks the
trace incomplete and the agent run may continue. `strict` is an explicit
production policy that propagates persistence failures and fails the run. The
effective strictness and storage policy are included in trace metadata so an
operator can distinguish dropped evidence from an execution failure.

The storage, retention, and failure policies will each land with focused tests
before the old open questions are treated as implementation-complete.

## Implementation checkpoints

| Checkpoint | Commit | Result |
| --- | --- | --- |
| Built-in recording policy | `8f1e509` | First-class recorder policy, redaction fingerprint, strictness and model-I/O defaults. |
| Linked child traces | `0461767`, `1720316`, `9395e03` | Dynamic/configured child propagation, family lookup/API, and parent span child IDs. |
| Background lineage | `ff2f580` | Background lifecycle trace is installed as the parent context and reconstructed agents use the canonical store. Focused background suites: 125 passed. |
| Built-in storage and retention | `94d3659`, `918a8a6` | Adaptive memory/JSONL selection, explicit overrides, effective-store metadata, and configurable local trace-age cleanup. Full suite: 1079 passed, 14 skipped. |
| Incomplete best-effort traces | `6d38423` | Non-strict persistence loss is marked on the trace while strict mode still fails the operation. Focused suite: 73 passed. |
| Serving lineage and run families | `ae3a463` | Serving request traces become explicit parents of agent traces; run-ID family lookup is available alongside exact-trace lookup. Focused serving tests: 4 passed. |
| Guardrail evidence boundary | `4dbdf87` | Tool outputs are scrubbed before result telemetry and flagged/blocked decisions carry structured guardrail evidence. Focused security/runtime tests: 100 passed. |
| Governance evidence assertion | `a8d27fd` | Policy request and deny events are asserted to share the active trace and session; full regression: 1082 passed, 14 skipped. |
| Live stream overflow | `0cac745` | In-memory subscriber eviction now delivers an explicit overflow failure so SSE clients can reconnect from a cursor; full regression: 1083 passed, 14 skipped. |
| Built-in payload offload | `2beea51` | Redacted oversized telemetry payloads are stored content-addressably in local/workspace storage, with read/prune APIs and strict/best-effort failure behavior; full regression: 1092 passed, 14 skipped. |
| Payload failure lineage | `1355ab9` | Payload persistence failures are attributed to the trace being created, including nested child traces; full regression: 1093 passed, 14 skipped. |
