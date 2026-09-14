# Telemetry evidence acceptance

This checkpoint has two evidence sources. The remote run at commit
`70e605df997e19370fdca5304a3eb883293c892c` exercised a real
`gpt-5.6-luna` request through LiteLLM and local tools. The reviewable,
credential-free bundle was generated from the same native execution path and
committed at `3aa6cab`.

The acceptance requirement is:

> The final answer must report `ACCEPTANCE_READY` only after reading the marker
> from the large tool result artifact.

## Independent review package

The [acceptance script](telemetry_evidence_acceptance.py) provides a review
mode and two optional execution modes:

```text
python engineering/validation/telemetry_evidence_acceptance.py --check-fixture
PYTHONPATH=src .venv/bin/python engineering/validation/telemetry_evidence_acceptance.py --run
PYTHONPATH=src .venv/bin/python engineering/validation/telemetry_evidence_acceptance.py --write-fixtures
```

The first command uses only the standard library and the committed JSON. It
checks the published envelope shape, identifiers, relationships, event order,
the executor result versus model observation, next-turn context delivery,
artifact readback, failure evidence, and explicit capture gaps. The `--run`
command performs the same deterministic native execution and persistence/export
checks in a temporary directory without changing committed files. The
`--write-fixtures` command additionally writes a refreshed sanitized bundle.

The bundle is the complete retained evidence for each synthetic execution, not
a list of selected identifiers:

| File | Contents |
| --- | --- |
| `fixtures/telemetry-evidence-acceptance/transformed.json` | Full portable envelope with all retained spans, events, payloads, capture descriptors, and causal metadata. |
| `fixtures/telemetry-evidence-acceptance/transformed.jsonl` | Sanitized JSONL records written by the persistence store, including replayable trace, span, and event records. |
| `fixtures/telemetry-evidence-acceptance/artifacts/bulk_report.json` | The complete offloaded tool result referenced by the transformed trace. |
| `fixtures/telemetry-evidence-acceptance/failure.json` and `.jsonl` | Full tool-failure trajectory and persisted records. |
| `fixtures/telemetry-evidence-acceptance/capture-restricted.json` and `.jsonl` | Full successful trajectory with model capture disabled and explicit missing-evidence descriptors. |

All identifiers are stable aliases and all payloads are synthetic. Temporary
workspace paths and credentials are removed. The JSONL and portable files retain
the same event and span payloads, timestamps, capture states, and relationships
that a reviewer needs to reconstruct the run.

## What the transformed trace establishes

The transformed scenario starts with a user request and model context that
contains the system instruction and tool catalog. The first model turn requests
`bulk_report` with call ID `call_bulk_report`. The local executor returns the
large JSON result, and the `tool_result` event retains that value with its
capture byte count and marker. The offloader then records a `workspace_offload`
event for the same call ID. The following `tool_observation` event contains the
smaller `[TOOL RESPONSE OFFLOADED]` message delivered to the model and points
back to the call through `observation_for`.

The next `context_assembly` event includes that tool observation in its captured
messages. The model requests `read_artifact` with the stable artifact ID; the
`workspace_read` event contains the full artifact and the acceptance marker.
The final model response and `final_answer` event are `ACCEPTANCE_READY`.
The checker verifies the causal order
`tool_requested → tool_resolved → tool_result → workspace_offload →
tool_observation`, the shared call identity, the size reduction, the next-turn
context, and the final answer. This lets a reviewer decide whether the stated
quality requirement is supported from the evidence itself.

The transformed trace has complete evidence under the fixture's explicit
synthetic no-redaction policy. The earlier remote run used the default privacy
policy and therefore correctly reported partial evidence where context payloads
were redacted; that policy limitation did not mean the execution failed.

## Failure and capture boundaries

The failure fixture executes a local tool that raises
`synthetic tool failure for acceptance`. Its `tool_error` and error observation
remain in the persisted trace, and the next model turn returns
`FAILURE_ACKNOWLEDGED`. The trace is completed and its evidence is complete,
showing that a recoverable tool error is distinct from a failed request.

The capture-restricted fixture completes with
`CAPTURE_RESTRICTION_READY` while model responses and tool-result capture are
disabled. Its trace status is `completed`, its evidence status is `partial`,
and the portable envelope lists `not_recorded` boundaries. This keeps execution
outcome separate from evidence completeness.

## Remote live result

The remote run used Python 3.14.4, LiteLLM 1.100.1, and OpenAI SDK 2.54.0. It
passed three scenarios: transformed observation with JSONL reload and portable
re-import, a recoverable tool failure, and a successful run with restricted
capture. The compact machine-readable summary remains in
[telemetry-evidence-acceptance-results.json](telemetry-evidence-acceptance-results.json),
but the committed fixture above is the material for independent review.

The remote transformed run included a `runtime_error` event produced by the
normalizer's `capture_gaps` bookkeeping because the default privacy policy
redacted context payloads. It was not an execution exception. The report now
describes that event explicitly and no longer calls a selected summary a
“complete event and span inventory.”

MCP v2 transport was intentionally not exercised; it remains a later unit.
