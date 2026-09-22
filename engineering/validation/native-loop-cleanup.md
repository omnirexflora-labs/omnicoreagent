# Native loop and execution cleanup — 2026-09-14

Branch `refactor/native-tool-runtime`, starting at audit commit `b3466e9`.
User scope: fix native loop detection and redundant execution machinery; **defer
MCP repairs outside this PR**. No MCP transport/API compatibility fix is included.

## Changes and checkpoints

- `b7d06e0`: replace `RobustLoopDetector` with `NativeLoopDetector` and compare
  complete tool rounds. **70 tests passed** for this checkpoint.
- `9d06a73`: remove executor/governance history callbacks, the no-op native writer,
  redundant redaction wrappers and feedback JSON decoding; decode arguments once.
  Preserve ordinary business dictionaries containing `data`/`message` fields.
  **83 focused tests passed**, including real native history for allowed/denied calls.
- Final follow-up: ignore generated governance request/decision IDs and configured
  child run IDs/metrics only in loop signatures; retain them in actual feedback and
  history. Additional deterministic tests verify both cases. Remove unused session
  fixture scaffolding from executor tests. Update documentation and live validation.

## Loop contract

A call signature includes concrete provider, server, original tool name, decoded
arguments and guarded results before offloading. Unavailable calls use an explicit
unavailable identity. Invalid aliased calls retain their resolved identity. Each
complete batch contributes one sorted multiset of call signatures; order is ignored,
while multiplicity is preserved. A single large batch cannot trigger repetition.

Five identical rounds, or five repeats of a cycle of up to five rounds, trigger
`STUCK`. Changing arguments or output from any sibling changes the round and counts
as progress. The next model request has no tool schemas and asks for an answer from
available results. If a model still requests tools, the runtime rejects execution
with `tool_loop`. Session resets clear the detector. Cancellation feedback is still
persisted, but an interrupted batch does not become a completed comparison round.

Call IDs, decision IDs, child accounting, assistant prose and offload paths do not
count as progress. Arbitrary timestamps or IDs inside ordinary business results are
not stripped: the detector identifies exact repeated results, not semantic task
progress. It does not replace `max_steps` or decide whether two side-effecting tools
are safe to run concurrently. Sync thread timeout still cannot guarantee stopping
the underlying function; this change makes no stronger cancellation claim.

## Execution/result contract

`execute_native_turn` owns the assistant request and one final tool-history write per
call ID. `ToolExecutor` executes/normalizes; `GovernedToolRunner` authorizes, redacts
arguments and records telemetry. Neither receives a history callback anymore.
`ToolFeedback` carries the serialized message, history metadata and loop interaction
without reparsing its own JSON. Cancellation reconciliation still finishes an
in-flight history write before retrying persistence, preventing duplicate rows.

Ordinary dictionaries are data, including `{"data":0,"unit":"kg"}` and bare
`message` objects. Explicit envelopes use `status` (`success`, `partial`, `error`)
and only `data`, `message`, `error` alongside it. Pure `error`/optional `message`
objects retain their defined error contract. An envelope-shaped business dictionary
can be explicitly nested inside a success envelope. Schema validation, result
normalization, guardrails, native fragment assembly, discovery, memory grouping and
offloading remain necessary application responsibilities; they were not removed.

## Final verification

- Full regression suite with an isolated real Redis server: **1,056 passed,
  11 skipped, 2 deselected**, one upstream Starlette/AnyIO deprecation warning;
  140.30 seconds. Redis was started on an ephemeral localhost port, used only for
  tests, then terminated. Nine MongoDB tests and two S3/R2 tests skipped because
  their services/credentials were unavailable. Network/API-key-marked tests were
  deselected; explicit provider validation below ran separately.
- Final focused check after test-scaffolding cleanup: **52 passed** (native runtime,
  detector, governed execution and provider observer), 4.63 seconds.
- Ruff, source/wheel builds, diff whitespace checks and removal scans passed.
  No old detector, no-op history writer or redundant governed history wrapper
  remains in production source.
- **14/14 live scenarios passed** on `gpt-5.6-luna` through production LiteLLM,
  using native Chat Completions and the existing `reasoning_effort="none"` setting.
  This is not an application Responses/high-reasoning validation.

| Live scenario | Evidence |
|---|---|
| Distinct parallel tools / business payload | Both actual functions rendezvous before returning; two correlated results; zero/unit/message fields preserved in history and next request. |
| Bounded native polling | Final run self-stopped after one poll; no fabricated runtime-cutoff claim. Earlier real-model run reached five rounds and verified tools disabled on request six. |
| Advanced discovery / context | Hidden schema absent initially; retrieval unlocked it, real tool executed, context compressed once with complete call/result IDs. |
| Offloaded artifact readback | Long tool result offloaded; model used native artifact tools to retrieve a marker absent from preview. |
| Skill readback | Two native `read_skill_file` calls read a temporary skill and evidence file. |
| Literal XML content | Plain XML task text returned without execution. |
| Native batch / continued session | Two receipt effects with exact arguments and correlated IDs; later run recalled both without rerunning tools. |
| Tool error / timeout | Two recoverable failures returned to the model. |
| Public stream with tool continuation | Text deltas plus actual native tool execution and continuation. |
| Cancellation | Upstream provider stream closed. |
| Configured child streaming | Actual parent/child runs completed through the shared native loop. |
| HTTP SSE | 1,080 deltas; first at 1.62 seconds, before the 12.13-second run finished; one terminal response. |
| Dynamic deep agent / workspace | Parent spawned a worker, verified its workspace file, and streamed child events. |
| Background execution | Durable task completed with native model/tool execution and lifecycle events. |

The original live polling fixture demanded exactly five rounds. One repeat of the
matrix failed that assertion, and a targeted repeat established that the model
self-stopped after one poll. The corrected fixture tests the actual safety bound
(at most five single-call rounds), records model versus runtime stop, and requires
schemas to be disabled whenever the five-round limit is reached. Production logic
was not relaxed. Deterministic tests still require five-round cutoff for repeated
successes, invalid aliases, unavailable tools, governed denials and child answers.
The raw record retains the earlier cutoff evidence and superseded fixture failures.

[Live results and fixture history](native-loop-cleanup-results.json).

Reproduce the ordinary suite and opt-in live validation:

```bash
OMNICOREAGENT_TEST_REDIS_URL=redis://127.0.0.1:PORT/15 \
  uv run --no-sync python -m pytest -q -ra \
  -m 'not requires_network and not requires_api_key and not OpenAIIntegration'
uv run --no-sync python engineering/validation/live_native_runtime.py \
  --require-litellm --env-file /path/to/authorized.env --report /tmp/native-live.json
```

## Public entry points and remaining scope

Normal and deep execution use `OmniCoreAgent.run()` / `.stream()`. Deep behavior is
`agent_config={"enable_subagents": True}`, enabling native `spawn_subagents` and
shared workspace support. Configured children use `sub_agents`. No separate
`DeepAgent` class is needed. `BackgroundAgentManager` is the durable scheduling
layer around those same agent runs; OmniServe exposes them over HTTP/SSE.

MCP compatibility, pagination and transport lifecycle findings remain deferred and
must not be advertised as working. The provider-specific lossless continuation /
Responses work remains a separate follow-up. Other provider credentials, real
MongoDB, S3 and R2 were not validated here. The full suite's mocked MCP tests do not
establish real MCP compatibility. This checkpoint verifies the stated non-MCP
OpenAI/LiteLLM matrix, not every provider or every possible tool implementation.
