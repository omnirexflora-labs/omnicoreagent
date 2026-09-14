# Evaluation-ready telemetry foundation

This document defines the evidence boundary for OmniCoreAgent before an
evaluation runner is added. It is based on the Agentic Evaluation Architecture
v2.2 design and the current native-tool runtime. It does not implement
evaluators, release gates, Harbor execution, or production sampling.

## Purpose

An evaluation must be able to answer what can be concluded about one task
attempt from the task requirements and the evidence that was actually
captured. Telemetry supplies execution evidence; it must not turn an
interpretation into a fact.

The system has two execution entry paths:

```text
controlled run (Harbor or another execution adapter)
  -> application adapter -> OmniCoreAgent telemetry

production observation (normal/deep/background OmniCoreAgent run)
  -> OmniCoreAgent telemetry

both paths -> portable trace -> future evaluation and feedback workflow
```

Harbor remains responsible for controlled task execution, environments,
trajectories, and verifiers. OmniCoreAgent remains responsible for its own
runtime evidence. An adapter may import an existing trace or invoke a
controlled run; the evaluation layer must not require execution control merely
to evaluate production evidence.

## Facts, evidence, judgments, and decisions

Telemetry records facts and evidence references. It does not assert that a task
was correct, safe, grounded, or well recovered.

| Layer | Example | Owner |
| --- | --- | --- |
| Observed fact | Tool `update_customer` returned `status=success`. | Runtime/tool |
| Evidence | The tool result payload is available at event `event_123`; an independent read is absent. | Telemetry/adapter |
| Judgment | The final answer claims an update that is not independently verified. | Evaluator |
| Decision | Release requirement `state_change_verified` failed. | Evaluation policy |

Every derived judgment must name its method and version and reference the
evidence it used. A model judge's confidence is a judgment value, not a
calibrated probability unless calibration evidence is supplied.

## Trace identity and provenance

A trace is one execution evidence graph. It may contain an agent run, child
runs, tool calls, context operations, and serving or background boundaries.
Separate traces may be linked when a boundary creates a child execution.

The trace envelope must preserve:

- `trace_id`: stable evidence graph identity;
- `run_id`: one runtime request or background attempt;
- `session_id`: conversation continuity, which may contain many runs;
- `task_id`: a background task or externally supplied task identity;
- `parent_trace_id` and `parent_span_id`: the creating execution boundary;
- application, agent, model, prompt, tool schema, memory, guardrail, privacy,
  and telemetry versions;
- execution surface (`interactive`, `background`, `serve`, or `controlled`);
- optional external provenance such as evaluation case, trial, environment,
  deployment, and adapter identifiers;
- trace status and an explicit account of incomplete or unavailable evidence.

External evaluation identifiers are correlation metadata. They do not make the
runtime depend on an evaluation package or on Harbor.

## Span and event contract

Spans represent timed work and form a parent/child tree. Events are immutable
point-in-time facts attached to a span. A sequence number is the store's
arrival order; timestamps and parent/causal identifiers preserve concurrency
and relationships. Evaluators must not infer a sequential dependency merely
because two parallel tool events have adjacent sequence numbers.

The supported execution evidence is organized as follows:

| Boundary | Required evidence |
| --- | --- |
| Request | User input/reference, session/run identity, input capture status, and external request metadata. |
| Context assembly | Message/group identifiers, instructions and history supplied or referenced, available tool catalog identity/version, memory reads, compression/truncation/offload changes, and missing evidence. |
| Model turn | Provider/model identity, request configuration, supplied context reference, response text when permitted, native calls with provider IDs and raw JSON arguments, finish reason, refusal/limit state, usage, and latency. |
| Tool request | Requested call ID/name/arguments, originating model turn, turn-start catalog version, and resolution outcome. |
| Tool execution | Resolved provider/server/tool identity, authority decision, arguments used, start/end, status, timeout/cancellation, raw result capture, and error. |
| Observation | The exact post-guardrail, post-transformation, post-offload representation delivered to the model, with references to the tool result and any redaction/truncation/offload. |
| Delegation | Parent call, child trace/run link, delegated task/context reference, child terminal outcome, workspace handoff verification, and parent result. |
| Finalization | Delivered final answer, terminal status/reason, output artifacts, and whether evidence capture completed. |
| Follow-up | Later user correction, feedback, external outcome, or production signal linked to the original run. |

The runtime may omit sensitive payload bodies under policy, but it must retain
the fact that a body was unavailable and why. `not_recorded`, `redacted`,
`truncated`, `offloaded`, and `missing` are different evidence states.

## Result and observation separation

Tool execution has at least two distinct representations:

1. the result returned by the executor;
2. the observation after governance, guardrails, normalization, offloading,
   and formatting that is appended to the next model request.

The two records share the provider call ID and a causal reference. A tool may
return success while the observation is blocked or incomplete. A model can
only be evaluated for using information that the observation evidence shows it
received.

File operations remain tool evidence. Workspace paths, artifact references,
and read/write/delete status are attached to their originating calls. A shell
or Python tool may touch additional files internally; those accesses are not
claimed unless the tool supplies deeper filesystem evidence.

## Context and persistence

Active messages, persisted history, summaries, truncation, and offloaded
payloads must reconstruct the same provider-valid interaction groups. The trace
records the context boundary and references rather than copying a complete
conversation into every event.

New records identify assistant call groups and correlated tool results with
explicit metadata. Historical XML-looking content remains task data and is
never reparsed as control. Legacy sessions are readable evidence; they may be
marked incomplete when the old representation cannot establish a native
relationship.

## Privacy and capture policy

Capture is decided before persistence. Model prompts/responses are opt-in;
tool arguments/results, user input, memory, workspace, and public output use
their configured privacy boundaries. Secrets are redacted before hashing or
offloading. A reference never grants access to an unredacted payload.

Payload capture metadata must include, when applicable:

- capture state;
- source and content type;
- original and recorded byte sizes;
- checksum of the redacted payload;
- offload reference;
- policy/configuration fingerprint;
- reason for omission or transformation.

This allows an evaluator to distinguish “the application did not produce a
result” from “the result existed but telemetry policy did not retain it.”

## Controlled and production adapters

Controlled adapters may attach `case_id`, `trial_id`, `environment_id`,
`evaluation_id`, `adapter`, and verifier references to the trace envelope.
Harbor trajectories and verifier outputs can be imported as linked evidence;
OmniCoreAgent telemetry remains the source for the application's internal
model/tool/context behavior.

Production adapters select eligible traces without blocking live requests.
Sampling, evaluator retries, backlog, and release decisions belong to the
future evaluation service. A production trace may have no expected answer or
reproducible environment; the result must say which conclusions are
unsupported.

## Initial implementation units

1. Add versioned trace/span/event evidence metadata and explicit payload capture
   states without changing execution behavior.
2. Instrument request, context, model turn, tool request/result, observation,
   and finalization boundaries with stable causal identifiers.
3. Record context snapshots and child/background/serving links without
   duplicating active or persisted conversations.
4. Add portable import/export validation for an OmniCoreAgent trace and a
   minimal external adapter fixture. Harbor integration and evaluators follow
   these contracts in a later change.

Each unit requires focused tests, a full regression run, a clean commit, and a
push. No evaluator, release decision, or model-training path is part of this
foundation work.
