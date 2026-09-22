# Native tool migration review

Current reassessment: [native runtime audit](native-runtime-reassessment.md) found
installed MCP 2.2 incompatibilities and remaining result/loop/provider issues.
The original implementation record below is historical; its offline MCP coverage
does not establish that the current SDK integration works.

The migration is implemented on `refactor/native-tool-runtime` in the isolated
`omnicoreagent-xml-discovery` worktree. The investigated source snapshot was main
`60da57a6dacd3f7796fffd9d198c0a7420aa6aad`. The original working branch was not modified.
See [the execution plan](native-tool-migration-plan.md) for the ordered commits
and validation gates. No live model calls, deployment, release or merge occurred.

## Resulting interaction

`OmniCoreAgent.run()` initializes the runtime, starts telemetry, checks input, loads
history and derives optional prompt instructions from a per-run native catalog.
Local, MCP, workspace, artifact, skill and configured-child bindings retain their
concrete identities, even when exposed names require collision-safe aliases.
The provider receives native function schemas and only allowlisted message fields.

The shared loop receives a `ModelTurn` containing content, calls, finish reason,
usage and retained provider fields. Calls take execution precedence when a turn
also contains text. Text alone is an answer, including XML-looking text. Invalid
JSON/schema arguments produce identified errors without effects; invalid provider
turns, refusals and limits produce explicit non-success outcomes. There is no XML
execution fallback.

The native runner resolves all calls against the turn-start catalog, records the
assistant request, then executes independent calls concurrently. Governance uses
the resolved provider/server and exact arguments. Each result is normalized,
guarded and optionally offloaded; the same JSON content enters active context and
persistent history with its provider call ID. Loop detection sees normalized
contents before changing artifact references or history redaction. Batches retain
successful siblings when another fails or times out. Cancellation records call
outcomes before it propagates, provided the history store remains available.

Configured children use native delegate functions; dynamic children use the
`spawn_subagents` array tool. Both keep ordinary child runs, workspace, cleanup,
authority checks and status propagation. Router/Sequential/Parallel workflow APIs,
examples and documentation have been deleted completely. Parallel tool batches
and child batches remain capabilities of the normal/deep agent.

Context selection and summaries account for call arguments and retain whole
assistant-call/result groups. Version-2 metadata preserves native model records.
Historical XML remains readable data and is never parsed as a new action. Explicit
transient metadata determines omitted observations; content prefixes do not.
Storage failures no longer silently start a fresh conversation.

## Streaming contract

`LLMConnection.llm_stream()` assembles indexed function fragments and emits text
immediately. Execution waits for a terminal model turn. `agent.stream()` and
SSE use the same loop as complete `run()`, not a second implementation. Text deltas
are labelled intermediate and carry root run identity, child actor identity,
sequence and trace/session IDs. The public queue holds at most 256 items; SSE holds
1000. Text delivery backpressures the producer; lifecycle queue overflow explicitly
fails the SSE stream. Closing consumers cancels execution and closes upstream
resources. Provider streaming does not retry after partial output.

Text fragments are live-only and independent of debug recording. Lifecycle replay
uses stored event IDs; persisted final answers remain subject to telemetry output
settings. Background execution retains durable lifecycle events and terminal
outcomes rather than token-fragment persistence. Consumers must check terminal
status; a partial text display is not a successful completed answer.

## Discovery inventory disposition

Every stable discovery ID has an explicit disposition below. Original evidence is
retained at [the investigated snapshot](xml-control-discovery/evidence/source-index.md).
This table records the current replacement rather than changing historical findings.

| ID | Disposition and retained behavior | Current boundary | Evidence |
|---|---|---|---|
| X01 | Retained organizational system-instruction markup; native response prompt replaces control instructions. | [core/system_prompts/builder.py](../../src/omnicoreagent/core/system_prompts/builder.py) | [test_prompt_context.py](../../tests/test_prompt_context.py) |
| X02 | Rewritten: text answers and native calls; no thought/final tags or ban on XML task content. | [core/system_prompts/base.py](../../src/omnicoreagent/core/system_prompts/base.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X03 | Deleted XML examples and argument coercion; schemas and strict JSON objects define calls. | [core/model_protocol.py](../../src/omnicoreagent/core/model_protocol.py) | [test_model_protocol.py](../../tests/test_model_protocol.py) |
| X04 | Rewritten observation and memory instructions for correlated JSON results. | [core/system_prompts/base.py](../../src/omnicoreagent/core/system_prompts/base.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X05 | Deleted rendered-tool registry and regex activation; capabilities come from native bindings. | [core/agents/initial_messages.py](../../src/omnicoreagent/core/agents/initial_messages.py) | [test_prompt_context.py](../../tests/test_prompt_context.py) |
| X06 | Rewritten per-run discovery; BM25 unlocks native schemas only for the next turn. | [core/tools/native_catalog.py](../../src/omnicoreagent/core/tools/native_catalog.py) | [test_native_catalog.py](../../tests/test_native_catalog.py) |
| X07 | Rewritten spawn instructions and typed subagents array. | [core/system_prompts/extensions.py](../../src/omnicoreagent/core/system_prompts/extensions.py) | [test_subagents.py](../../tests/test_subagents.py) |
| X08 | Deleted agent-call prompt/registry; configured children use delegate schemas. | [core/tools/native_catalog.py](../../src/omnicoreagent/core/tools/native_catalog.py) | [test_agent_stream.py](../../tests/test_agent_stream.py) |
| X09 | Retained workspace behavior with native instructions and trusted provider identity. | [core/workspace/tools.py](../../src/omnicoreagent/core/workspace/tools.py) | [test_governed_tool_runner.py](../../tests/test_governed_tool_runner.py) |
| X10 | Retained artifact reference/readback strategy; JSON payloads and actual offload events. | [core/tools/tool_result_offloader.py](../../src/omnicoreagent/core/tools/tool_result_offloader.py) | [test_tool_result_offloader.py](../../tests/test_tool_result_offloader.py) |
| X11 | Retained XML skill catalog as data; native skill tools and instructions. | [core/skills/manager.py](../../src/omnicoreagent/core/skills/manager.py) | [test_prompt_context.py](../../tests/test_prompt_context.py) |
| X12 | Deleted XML block/parser paths; normalized provider call arrays identify batches. | [core/agents/llm_response.py](../../src/omnicoreagent/core/agents/llm_response.py) | [test_model_protocol.py](../../tests/test_model_protocol.py) |
| X13 | Deleted XML argument extraction; reject malformed/duplicate-key/non-object JSON. | [core/model_protocol.py](../../src/omnicoreagent/core/model_protocol.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X14 | Deleted XML precedence: calls execute even with text; text-only turn completes. | [core/agents/base.py](../../src/omnicoreagent/core/agents/base.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X15 | Deleted tag repair messages; correlated argument errors and explicit invalid-provider outcomes. | [core/agents/native_tools.py](../../src/omnicoreagent/core/agents/native_tools.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X16 | Deleted thought extraction/display; assistant content and optional reasoning remain distinct fields. | [core/model_protocol.py](../../src/omnicoreagent/core/model_protocol.py) | [test_model_protocol.py](../../tests/test_model_protocol.py) |
| X17 | Rewritten adapters: preserve turns; stream indexed fragments and close resources. | [core/llm.py](../../src/omnicoreagent/core/llm.py) | [test_model_stream.py](../../tests/test_model_stream.py) |
| X18 | Deleted ParsedResponse/action layer; ModelTurn and ToolRequest now carry control. | [core/model_protocol.py](../../src/omnicoreagent/core/model_protocol.py) | [test_model_protocol.py](../../tests/test_model_protocol.py) |
| X19 | Deleted string-to-value guessing; validate exact JSON against schemas. | [core/tools/native_catalog.py](../../src/omnicoreagent/core/tools/native_catalog.py) | [test_native_catalog.py](../../tests/test_native_catalog.py) |
| X20 | Deleted old resolver; deterministic aliases resolve to concrete provider/server/name. | [core/tools/native_catalog.py](../../src/omnicoreagent/core/tools/native_catalog.py) | [test_native_catalog.py](../../tests/test_native_catalog.py) |
| X21 | Deleted alternate delegation tool-error advice; ordinary correlated call errors. | [core/agents/native_tools.py](../../src/omnicoreagent/core/agents/native_tools.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X22 | Deleted generated batch-call IDs/history wrappers; retain provider IDs in assistant metadata. | [core/agents/native_tools.py](../../src/omnicoreagent/core/agents/native_tools.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X23 | Retained executor, corrected falsy values/MCP errors/structured blocks and sync thread execution. | [core/tools/tool_executor.py](../../src/omnicoreagent/core/tools/tool_executor.py) | [test_tool_executor.py](../../tests/test_tool_executor.py) |
| X24 | Rewritten per-call timeout/cancellation; preserve successful siblings and cancelled results. | [core/agents/native_tools.py](../../src/omnicoreagent/core/agents/native_tools.py) | [test_agent_stream.py](../../tests/test_agent_stream.py) |
| X25 | Deleted reparsing observations; normalized JSON envelopes feed the next request. | [core/agents/native_tools.py](../../src/omnicoreagent/core/agents/native_tools.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X26 | Deleted display/batch formatter; retained normalized loop signatures and result offloading. | [core/tools/tool_result_offloader.py](../../src/omnicoreagent/core/tools/tool_result_offloader.py) | [test_tool_result_offloader.py](../../tests/test_tool_result_offloader.py) |
| X27 | Deleted XML observations builder; one JSON tool message per provider ID. | [core/agents/native_tools.py](../../src/omnicoreagent/core/agents/native_tools.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X28 | Deleted user-role observation appender; active and stored tool roles match. | [core/agents/native_tools.py](../../src/omnicoreagent/core/agents/native_tools.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X29 | Retained output guardrail and authority behavior; checks precede persistence/offload. | [core/tools/governed_tool_runner.py](../../src/omnicoreagent/core/tools/governed_tool_runner.py) | [test_governed_tool_runner.py](../../tests/test_governed_tool_runner.py) |
| X30 | Retained normalized loop detector; deleted XML repair/failure handler. Signatures precede offload. | [core/agents/loop_detection.py](../../src/omnicoreagent/core/agents/loop_detection.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X31 | Retained configured-child invocation/cleanup; runtime-only parameters excluded from schemas. | [core/agents/subagent_runner.py](../../src/omnicoreagent/core/agents/subagent_runner.py) | [test_subagent_runner.py](../../tests/test_subagent_runner.py) |
| X32 | Deleted configured-child XML aggregator; parent receives ordinary identified tool results. | [core/agents/native_tools.py](../../src/omnicoreagent/core/agents/native_tools.py) | [test_agent_stream.py](../../tests/test_agent_stream.py) |
| X33 | Retained dynamic factory; typed array requests, explicit child status, shared workspace. | [core/subagents.py](../../src/omnicoreagent/core/subagents.py) | [test_subagents.py](../../tests/test_subagents.py) |
| X34 | Deleted RouterAgent and its exports/examples/docs; no fallback. | REMOVED | [test_import_startup.py](../../tests/test_import_startup.py) |
| X35 | Deleted SequentialAgent and ParallelAgent and their exports/examples/docs; no fallback. | REMOVED | [test_import_startup.py](../../tests/test_import_startup.py) |
| X36 | Retained message types; reconstruct call groups and model fields from versioned metadata. | [core/agents/message_history.py](../../src/omnicoreagent/core/agents/message_history.py) | [test_message_history.py](../../tests/test_message_history.py) |
| X37 | Retained stores/history; fixed SQLite JSON agent filtering, no history erasure. | [core/memory_store/sql_db_memory.py](../../src/omnicoreagent/core/memory_store/sql_db_memory.py) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X38 | Adapted selection/summarization to indivisible call-result groups and argument token accounting. | [core/interaction_history.py](../../src/omnicoreagent/core/interaction_history.py) | [test_interaction_history.py](../../tests/test_interaction_history.py) |
| X39 | Deleted unused summary-memory-constructor prompt/export after caller audit; active conversation summary strategy remains. | [core/system_prompts/summaries.py](../../src/omnicoreagent/core/system_prompts/summaries.py) | Caller audit: only its definition and export existed |
| X40 | Rewritten final result extraction/outcomes; status and reason propagate to facade/serving. | [core/agents/run_outcome.py](../../src/omnicoreagent/core/agents/run_outcome.py) | [test_runtime_telemetry_wiring.py](../../tests/test_runtime_telemetry_wiring.py) |
| X41 | Retained lifecycle replay; added independent live-only text, bounded delivery and cancellation. | [core/runtime/streaming.py](../../src/omnicoreagent/core/runtime/streaming.py) | [test_agent_stream.py](../../tests/test_agent_stream.py) |
| X42 | Retained background lifecycle; returned error outcomes fail attempts and propagate cancellation. | [background](../../src/omnicoreagent/background) | [test_background_agent.py](../../tests/test_background_agent.py) |
| X43 | Retained XML/SVG/MDX/task files and artifact format support; no text execution parser. | [core/workspace](../../src/omnicoreagent/core/workspace) | [test_native_runtime.py](../../tests/test_native_runtime.py) |
| X44 | Updated current docs/examples/tests; snapshot-pinned discovery remains historical evidence. | DOCS | [test_real_application_examples.py](../../tests/test_real_application_examples.py) |

## Scenario verification

| Scenario | Current evidence and outcome |
|---|---|
| Text, XML task content and mixed text/calls | `test_native_runtime`, `test_model_protocol`: XML remains literal; only calls execute; mixed text survives with calls. |
| Local single/batch calls | Native runtime tests verify exact arguments, stable IDs, JSON results and next-turn messages. |
| MCP | Catalog tests verify collision/server identity; full-stack smoke exercises MCP result normalization, authority and continuation with an offline session. |
| Workspace/artifact/skill | Real-application and governed-runner tests exercise actual workspace writes/readback, offloaded JSON and trusted tool provenance; skills suites cover registration, script authority and catalog prompts. |
| Configured/dynamic child | Native runtime, subagent and stream tests cover delegation, typed spawn, cleanup, child identity and returned outcomes. |
| Malformed/empty/unknown calls | Protocol/native tests cover invalid JSON, duplicate keys/IDs, schema failures, empty turns, unavailable functions and bounded loop termination. |
| Failure/timeout/cancellation | Executor/native/public-stream/background tests cover falsy success, MCP error blocks, failed siblings, timeout and correlated cancellation history. |
| Context pressure | Interaction-history/context/summarizer tests verify group selection and render token accounting. |
| Continued session | Real native batches round-trip through in-memory and SQLite; history tests cover reordered, missing and duplicate call-result records and XML-era data. |
| Serving/background | Sync and SSE production-boundary fixtures and background supervisor tests use the same facade and explicit terminal outcomes. |
| Live streaming | Provider and public tests yield text while the completion gate is blocked; verify tool execution waits, bounded queues, child actors, close/cancel, error terminal and usage tails. |
| Retired workflows | Deleted; no supported invocation path. |

## Verification and limitations

Final validation:

- Full offline suite: **1,027 passed, 13 skipped, 2 deselected** (200.31 seconds).
  Command: `PYTHONPATH="$PWD/src" ../omnicoreagent/.venv/bin/python -m pytest -q -ra -m 'not requires_network and not requires_api_key and not OpenAIIntegration'`.
- After the final cancellation-during-history-write correction: **26 passed** in
  `test_native_runtime.py` and `test_agent_stream.py` (5.31 seconds).
- Source and wheel build succeeded with `uv build`; the wheel includes native
  catalog/stream modules and excludes retired workflow/XML executors.
- Ruff passed for changed Python files relative to the investigated main snapshot;
  `git diff --check` passed. All 31 documentation navigation pages exist, all local
  review links resolve, and all 44 inventory IDs have dispositions.
- Streaming cookbook smoke passed with a fake async stream, without a model call.
- Skips: seven unavailable MongoDB task-store cases, four Redis/MongoDB memory and
  serving cases, and two S3/R2 workspace credential cases. Network/API-key marked
  cases were deselected. No live provider verification is claimed.

 Tests use offline
provider fixtures and local workspace/SQLite storage. The preserved behavior tests
assert effects, authority, correlation, persistence and outcomes; obsolete tests
asserting XML strings and deleted dispatch methods were removed.

- Provider/model capabilities still require deployment verification. The installed
  OpenAI and LiteLLM adapter shapes are tested; no claim is made that every model
  accepts native tools, every JSON Schema keyword, reasoning fields or
  `stream_options.include_usage`. Unsupported parameters fail explicitly rather
  than silently dropping tools. Next check: selected deployment models with text,
  parallel functions, malformed JSON, refusal, usage-only tail and cancellation.
- Multimodal complete content blocks are retained; streaming content-block deltas
  are not implemented and fail explicitly. Next check: identify a needed provider
  contract before extending the text/function stream protocol.
- PostgreSQL/Redis/MongoDB and remote MCP/provider transports need live integration
  verification where credentials/services are unavailable. SQLite is exercised.
- Cancelling synchronous Python tools stops waiting but cannot kill thread side
  effects. Next step if forcible termination is needed: a separately scoped process
  execution boundary, not a claim that cancellation rolls back tool effects.
- History writes are not transactional across an entire batch. Process death or
  store failure may leave incomplete groups; reconstruction omits incomplete
  control groups. No automatic retry of side effects is introduced. Concurrent
  independent runs sharing one agent/session may interleave history; applications
  should serialize a session or use distinct session IDs. A durable transactional
  interaction journal is a separate storage project.
- Existing XML-era sessions are retained as data, not replayed as actions. Previously
  truncated or redacted information cannot be recovered. There is no XML parser,
  workflow fallback or automatic stored-history rewrite.
- Model reasoning text is retained only in explicitly supported provider fields;
  other provider-specific opaque payloads need a defined contract before support.
  No untraced active-runtime XML-control caller remains; external imports of
  removed internals must migrate and cannot be enumerated from this repository.


## Separate operational follow-up

At deliberately tiny offload thresholds, preview/reference overhead can exceed
original result size. The existing offload threshold strategy is unchanged; this
is a tuning/efficiency issue rather than a native-control blocker. MCP reconnection
can attempt a duplicate connection before detecting an already connected server;
that transport lifecycle behavior is unchanged and is not an XML fallback.

The final audit also closes tool/model/step cancellation spans, marks returned
child failures correctly, and avoids hanging a public consumer when its producer
is cancelled independently. Cancellation waits for an in-flight history write to
finish before reconciling remaining rows. A stuck storage backend can therefore
delay cleanup; store availability/timeouts remain operational requirements.

## Live OpenAI follow-up — 2026-09-14

The offline-only qualification above describes the original migration checkpoint.
Subsequent authorized live testing found and corrected OpenAI adapter compatibility
failures. OpenAI now uses its SDK directly. Nine live Luna scenarios pass,
including native batches, history continuation, errors/timeouts, public and HTTP
streaming, cancellation, configured/dynamic children, workspace output, and a
background run. See the [live validation report](../validation/luna-live-validation.md)
for failures found, exact configuration, measured results and remaining limits.
Luna tools were tested with `reasoning_effort="none"` on Chat Completions;
reasoning-enabled Responses integration is not implemented.
