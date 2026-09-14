# Telemetry evidence contract

This is the portable, evaluator-facing contract for execution evidence. It
extends the operational trace model without changing the ownership boundary:
`TelemetryRecorder` writes, `TelemetryStore` persists, and evaluators consume a
normalized copy.

## Versioning

New records use `schema_version=3`. Readers must accept older records and mark
unmappable relationships as missing evidence. A schema version describes the
record shape, not the agent or evaluator version.

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
- tool call -> model turn and resolution event;
- tool result -> tool call ID;
- observation -> tool result and next model turn;
- child run -> parent delegation call and parent result;
- context change -> input message/group identifiers and resulting context;
- final answer -> terminal run and output artifact references.

The event sequence is an ordering aid only. Parallel calls must remain
independent unless an explicit causal reference says otherwise.

Context digests identify the exact ordered message/tool catalog supplied to a
model without requiring content retention. Prompt payloads are added to the
context/model records only when `record_model_prompts` is enabled. Compression
records before/after digests and the groups dropped or replaced. A context
summary model request is a normal linked `model.call` span, so internal model
work is not invisible to evaluation.

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
