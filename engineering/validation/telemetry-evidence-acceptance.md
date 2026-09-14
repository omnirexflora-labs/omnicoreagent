# Telemetry evidence acceptance — remote live run

This checkpoint tests whether a persisted OmniCoreAgent execution can be handed
to a reviewer as evidence for a concrete quality requirement. It ran on the
remote host `abiorh-hetzner` from branch `refactor/native-tool-runtime` at
`70e605df997e19370fdca5304a3eb883293c892c`.

The runtime used Python 3.14.4, OpenAI `gpt-5.6-luna` through LiteLLM 1.100.1
and OpenAI SDK 2.54.0. The credential was copied to a mode-600 temporary env
file for the run and removed afterward. No credential was committed.

The acceptance requirement was:

> The final answer must report `ACCEPTANCE_READY` only after reading the marker
> from the large tool result artifact.

Telemetry used JSONL persistence, model prompt/response capture enabled, a
200,000-byte telemetry payload limit, local tool-result offload, and the default
privacy boundary.

## Results

The transformed-observation scenario completed a real native-tool run and then
opened a fresh `JsonlTelemetryStore` over the persisted file. The evidence export
passed portable-contract validation and re-imported byte-for-byte at the JSON
envelope level.

| Boundary | Evidence observed |
| --- | --- |
| Request and context | `user_message` plus four `context_assembly` snapshots containing messages, tool catalogs, and context digests. Context payloads carry `redacted` capture state under the default privacy policy. |
| Executor result | `tool_result` for `bulk_report`, call ID `call_ihpkvsN9HGpQxeFvon9PfI1p`, 6,032 captured bytes, marker present. |
| Transformation | `workspace_offload` records the same call ID and artifact reference. |
| Model observation | `tool_observation` points to the same call ID through `observation_for`; the delivered message is a 690-byte offload preview. |
| Follow-up read | `workspace_read` records the native `read_artifact` call and the marker is present in its captured output. |
| Final result | `final_answer` contains `ACCEPTANCE_READY`. |
| Persistence/export | JSONL reload, standalone schema validation, and portable re-import all passed. |

The complete event and span inventory, identifiers, capture states, and checks
are in [telemetry-evidence-acceptance-results.json](telemetry-evidence-acceptance-results.json).
The raw portable traces were generated outside the repository and are not
committed because they contain full model/tool payloads, even though this
fixture used synthetic data.

The failure scenario used a real local tool that raised an exception. Its
error event and final answer survived persistence and export. The capture
restriction scenario disabled model and tool-result capture; the run still
completed successfully while the exported evidence reported `partial` status
and explicit `not_recorded` gaps. This confirms execution outcome and evidence
completeness remain separate.

## Verification

- Remote live acceptance scenarios: **3 passed, 0 failed**.
- Remote focused telemetry suite: **134 passed**.
- Local full regression: **1,177 passed, 14 skipped**.
- MCP v2 transport was intentionally deferred as previously agreed.

The local machine produced an intermittent native shutdown fault during one
live process. The same diagnostic completed cleanly on the remote host, so the
acceptance result is based on the remote execution. The live fixture uses a
temporary workspace; production artifact-retention and MCP verification remain
separate follow-up work.
