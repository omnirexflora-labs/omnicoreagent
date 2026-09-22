# Native runtime reassessment — 2026-09-14

Follow-up: [native loop and execution cleanup](../validation/native-loop-cleanup.md)
repairs N06 and the lossy data/message heuristics in N07, and removes redundant
history callbacks/JSON round-trips. MCP repairs are explicitly deferred outside
this PR at the user’s request. The findings below record the audited snapshot.

Audited branch: `refactor/native-tool-runtime`, snapshot `ab7552c` (after Cencori removal).
This report supersedes blanket completion claims in the earlier migration review.
The XML **control** protocol has been replaced. That does **not** establish that
all installed integrations work. In particular, current MCP 2.2 boundaries fail.
The cleanup accompanying this report does not repair those open defects.

## What actually works, and what the evidence covers

| Path | Current conclusion | Evidence |
|---|---|---|
| Local single/multiple native calls | Implemented and tested; actual functions execute with JSON arguments and provider IDs. | `tests/test_native_runtime.py`; previous real Luna/LiteLLM run below. |
| Parallel tool execution | Yes. `execute_native_turn` creates a task per call, gathers them, and persists results in request order. The added rendezvous test proves two different functions start before either finishes; this is stronger than counting two requests. | `test_native_batch_starts_distinct_tools_before_either_completes`; [runner](../../src/omnicoreagent/core/agents/native_tools.py). |
| Failure/timeout within a batch | Tests preserve successful siblings, correlated failures, and cancellation history. Async timeout does not establish that a synchronous worker thread stops. | Native runtime and agent stream tests; [registry](../../src/omnicoreagent/core/tools/local_tools_registry.py). |
| MCP | **Resolved (2026-09-19).** Was not working with the installed SDK; fixed by the [MCP v2 completion plan](mcp-v2-completion-plan.md). stdio, streamable HTTP, and SSE (with OAuth) work against real servers, including the official reference server; every MCP boundary check in the audit passes. | [Diagnostic results](../validation/native-boundary-audit-results.json); real-server tests `tests/test_mcp_*.py`; [interop check](../validation/mcp_interop.py). |
| Workspace/artifact/skills | Native registry/authority/result paths remain in use. Workspace and offload effects have offline tests and previous live deep-agent evidence. Skill registration/authority has offline evidence, not a new live-model skill run. | Governed runner, workspace, skill and real-application tests; earlier live report. |
| Configured children and dynamic deep agent | Delegate functions and typed `spawn_subagents` arrays reach ordinary child runs. Parallel child batches remain; they are not the deleted ParallelAgent workflow. | Native/subagent/stream suites; previous live configured-child and dynamic-child scenarios. |
| History/context/offloading | Native assistant calls and `role=tool` results survive tested stores/context grouping. These remain necessary; native APIs require matching result IDs. | History/interaction/native tests, including continued sessions and results before offload. |
| Streaming/serving/background | Shared native loop is implemented and previously live-tested through LiteLLM/OpenAI, SSE and background runs. This does not prove every provider or an MCP-enabled background run. | [Production checkpoint](../validation/production-litellm.md), [raw live results](../validation/production-litellm-results.json). |
| Provider reasoning continuation | **Incomplete.** Normalization retains `reasoning_content`, not arbitrary provider fields/tool-call extras. Responses execution and lossless provider continuation are not integrated into the app. | [Response normalization](../../src/omnicoreagent/core/agents/llm_response.py), [stream assembly](../../src/omnicoreagent/core/model_stream.py), [request allowlist](../../src/omnicoreagent/core/llm.py); diagnostic. |

Previous live validation used `gpt-5.6-luna` through production LiteLLM and passed
nine scenarios, including two local calls in one native batch, continued history,
tool errors/timeouts, public streaming, cancellation, children, workspace, SSE and
background execution. **None of those nine scenarios called a real MCP server.**
No new paid model call was made for this reassessment.

## Actual invocation and return path

The facade constructs a LiteLLM connection and runtime registries. On a run,
`ToolRuntimeRegistry.prepare_tools` registers enabled workspace, artifact and skill
functions. `NativeToolCatalog` combines those with local tools, connected MCP
schemas, configured delegate functions and optional discovery. It produces native
function definitions with aliases bound to provider/server/original-name identity.
Advanced retrieval reveals schemas on the next turn only.

`AgentLlmStepRunner` applies context selection and sends those schemas with messages
to `LLMConnection`. Complete responses and assembled streams become `ModelTurn` and
`ToolRequest` records. A text-only response completes the run. Calls take precedence
over accompanying text; refusal/content-filter/length outcomes prevent execution.
Malformed arguments become correlated error results; XML-looking text never invokes
a tool. Streamed text is emitted before completion, but calls wait for complete
argument fragments and the terminal model turn.

`execute_native_turn` resolves the whole batch against the turn-start catalog,
validates JSON against the declared schema, selects the local/MCP/delegate/discovery
handler, authorizes the concrete target, then runs calls concurrently. The executor
normalizes results; guards, loop recording and optional offload run before the
result is persisted and appended as a JSON `role=tool` message with its call ID.
The next model request receives the assistant call list and matching results.
The context/history code groups these messages rather than parsing XML wrappers.

This architecture supports native functions. LiteLLM translates provider wire
formats; it does not execute our Python functions, authorize workspace access,
manage MCP sessions, choose our memory policy or stop repeated agent actions.

## Open defects and required repair units

| ID / priority | Confirmed behavior or unresolved constraint | Components to change together; acceptance gate |
|---|---|---|
| N01 / blocker | Real stdio initialization fails: `float + datetime.timedelta`. `MCPClient` still passes `timedelta(seconds=300)` to MCP 2.2's float timeout. `serverInfo` is also obsolete; current SDK exposes `server_info`. Public connection setup logs/returns failures and may leave the agent with zero tools. | `mcp_clients_connection/client.py`; real connection/list/call/failure propagation tests, including public facade and background startup. Diagnose all failed connections explicitly. |
| N02 / blocker | `NativeToolCatalog` and public `available_tools` access SDK `Tool.inputSchema`; MCP 2.2 exposes `input_schema`. Real SDK objects raise `AttributeError`. Local registry dictionary key `inputSchema` is our own contract and is a separate case. | Native catalog + harness listing + fixtures. Use real SDK types; test duplicate original names on two servers, discovery and exact target execution. |
| N03 / blocker | `ToolExecutor` reads `isError`/`structuredContent` attributes. Current SDK uses `is_error`/`structured_content`. A real error with structured data becomes **success**, and structured data disappears. The old test uses a `SimpleNamespace` with obsolete fields. | Executor + result guards/offload/history + SDK-backed tests. Preserve errors and structured/block results through the next model request; test successful siblings too. |
| N04 / blocker | Streamable HTTP rejects the supplied `headers` keyword before connecting. MCP 2.2 accepts a configured `httpx2.AsyncClient`; the transport now yields two streams, while our code expects three. Existing timeout/auth construction must move with client lifetime ownership. | `transports.py`, OAuth client boundary, dependency declaration if importing `httpx2` directly. Real HTTP server test must verify headers, timeouts, calls and closure. SSE has a different API and needs its own test. |
| N05 / high | `_load_server_tools` reads one page and ignores `next_cursor`. Successful servers with paginated catalogs can silently lose tools. | MCP loader + catalog discovery; real or SDK-backed multi-page test, including failure midway and repeated cursors. Source finding, not yet exercised in this pass. |
| N06 / high | Loop signatures omit provider/server, use original names on success but exposed names for unresolved/errors, and are recorded in completion order. Same-named servers can collapse into one repeated interaction; alias errors may miss `base.py`'s original-name lookup. | Runner + detector + loop-state transition. Record concrete identity consistently, test separate servers, alias failures, repeated rounds, legitimate duplicate requests within a batch, changing outputs, and offloaded results. |
| N07 / high | Dictionary-envelope guessing remains lossy: a legitimate `{"data":"value","unit":"kg"}` result becomes just `"value"`. Native calls did not remove this old ambiguity. | Explicit result contract + local integrations + executor + guards/offload. Preserve arbitrary business dictionaries and explicitly declared success/partial/error outcomes; update callers together rather than silently changing all dict semantics. |
| N08 / high | Generic provider-specific continuation fields disappear. A synthetic field survives neither normalization nor the outgoing allowlist; tool-call metadata has only ID/name/arguments. This is evidence of loss, not proof of a particular remote provider failure. | Model turn/request records + LiteLLM adapter + stream assembly + history/context. Validate actual provider signed/reasoning records and application Responses continuation before claiming all-provider support. |
| N09 / high | Connections are opened in gathered tasks but their context stacks are later closed from another task; `_close_session` suppresses cancel-scope mismatch. Connection failure prevents this pass from establishing successful lifecycle cleanup. | MCP session ownership + add/remove/cleanup + cancellation. After N01–N04, test actual subprocess exit and HTTP session deletion on success, failure and cancellation; do not treat a swallowed error as clean closure. |
| N10 / constraint | Every call in a batch starts concurrently, with no concurrency limit or dependency/side-effect analysis. Independence is requested in the prompt. Sync functions run in threads; timing out the await cannot guarantee the thread/effect stops. | Scheduler/executor policy if stronger guarantees are required. Test conflicting writes, concurrency bounds and post-timeout effects before making stronger claims. Do not reintroduce removed workflow agents. |
| N11 / constraint | MCP non-text blocks are serialized inside JSON tool-result text. Preserving a base64 image block is not equivalent to providing a model-native image input. New MCP interactive/input-required result types have no explicit application handling. | Result/message interface and MCP feature support matrix. Verify supported multimodal/interactive behavior explicitly; ordinary text tools do not prove it. |

## Loop detection remains necessary

Native calling solves the syntax contract, not repeated non-progress. The current
detector hashes normalized arguments and guarded results before artifact references
change, intentionally excluding generated call IDs. When it detects repetition,
the agent enters `STUCK`, sends no tool schemas on the next request, and asks for an
answer from existing results. If a model nevertheless supplies calls, execution
halts with `tool_loop`. The existing offload repetition test still passes.

Keep this capability; repair N06. The diagnostic feeds the signature the runner
currently produces for same-named tools and confirms it repeats. The inference
that two server bindings collide is grounded in the call site; it is **not** a
successful two-server transport test. Pattern detection is per tool and records
completion order, so it also needs round/batch-aware behavioral tests.

## What is obsolete versus still required

Deleted in this review after definition/reference/export searches:

- `core/tools/tool_catalog.py`: unused old `find_mcp_tool`, `find_local_tool_name`
  and `MCPToolMatch`. Its error text incorrectly said server-qualified calls were
  unsupported. The native catalog is the execution resolver.
- `ContextInclusion`, `ToolCallRecord`, `LoopDetectorConfig` in `core/types.py`:
  no repository callers/exports/tests. The latter was not the configuration of
  the actual loop detector.
- `default_agent_config`: uncalled wrapper; configuration uses `AgentConfig` and
  normalization directly.

Further simplification requiring coordinated edits, **not deleted blindly**:

- The executor and governed runner still accept/write history through a callback;
  the native runner passes a no-op callback and owns the real post-guard/offload
  write. Consolidate persistence ownership and delete the redundant callback and
  redaction plumbing together. Preserve telemetry redaction and standalone runner
  tests; do not move raw results into persistent history.
- Replace result-envelope guessing (N07), and consolidate repeated JSON decoding
  used for validation, telemetry and redaction around one validated argument record.
  JSON decoding itself is required: native function arguments arrive as JSON text.
- MCP text can be checked once at the handler and again after normalization. Audit
  guard coverage for text/structured blocks before removing duplicated checks.
- `ToolCallResult` actually describes a resolved invocation; `ToolRequest` describes
  a provider request. The naming is confusing, but those identities/execution fields
  are used by governance, telemetry and offload. They are not dead types.

Keep native schema validation, deterministic aliases, discovery, governance,
execution/timeouts, result normalization, correlation/history grouping, context
selection, offloading, stream fragment assembly and loop protection. These are
application responsibilities even with native provider APIs. Provider schema
submission currently does not request `strict: true`; local validation is the
execution safeguard, so do not describe the model output as guaranteed-valid JSON.

Remaining XML in skill catalogs and organizational prompt markup is data/instruction
organization, with no action parser. Artifact XML extension detection and XML task
files remain legitimate capabilities. Removing them would not simplify native tool
calling. Retired RouterAgent/ParallelAgent/SequentialAgent paths and XML control tags
were searched again in current source, cookbook, docs and README; no active control
path was found. Historical discovery documents and literal-XML tests remain evidence.

## Verification and coverage limits

- **212 passed** in the focused regression run, including the new parallel
  rendezvous test, native runtime/catalog/protocol/stream, MCP mocked client,
  executor/governance, child runs, history/context, real-application smoke and imports.
- `engineering/validation/native_boundary_audit.py` ran without a model or external
  service. It used real installed MCP types and launched the synthetic stdio server
  in `fixtures/native_audit_mcp_server.py`. The committed JSON records **failures**;
  it is a diagnostic, not a green acceptance gate. (Re-run 2026-09-19 after the
  MCP v2 plan: 7 of 8 pass; `provider_specific_fields_retained` still fails, the
  provider continuation gap in the row above.) HTTP fails at API binding before
  any network request. A separate public `connect_to_servers()` probe returned with
  empty sessions/tools, demonstrating silent unavailability.
- The earlier 1,039-pass full suite at `ab7552c` remains historical evidence. It did
  not establish compatibility with actual MCP 2.2 objects/transports. This review
  does not rerun or claim live credentials for other providers or cloud stores.
- Searched symbols/imports across all **171 production Python modules** at baseline,
  and protocol/workflow references in tests, cookbook and current docs. Followed the
  facade, construction/config, prompts, native catalog/registry, request/stream loop,
  local/MCP/governed execution, results/offload, detector, history/context, children,
  serving/background boundaries and their existing tests. Static scanning every
  module is not a line-by-line proof that every public utility is dead or live.
- Generic exported exceptions, CLI command functions, cache utilities, supported
  context strategies and test-used helpers were not deleted just because a simple
  name-count scan found few callers. They are outside the proven obsolete control
  path. No claim is made that every remaining repository file has been proven useful.

## Order for the next implementation checkpoints

1. Repair installed MCP SDK boundaries N01–N04 and connection failure propagation;
   replace old-shaped mocks with real SDK objects. Gate with real stdio and HTTP
   local-server native calls, local+MCP mixed batches and correlated results.
2. Finish MCP pagination/lifecycle N05/N09; test add/remove, cancellation, process
   exit, real session teardown, configured/deep/background MCP use and OAuth/SSE.
3. Simplify the execution/result/persistence boundary (N07 and redundant callback),
   preserving authority and guarded history; test business payloads and all outcomes.
4. Repair loop identity and batch semantics N06; retain loop protection and test
   useful repetition separately from stuck execution. Establish N10 limits clearly.
5. Complete lossless provider/Responses contracts N08 and the supported multimodal
   matrix N11. Then run the live provider matrix and final integration suite.

Each checkpoint needs tests, a focused commit and an updated coverage record. The
present review/cleanup commit is not a claim that these repairs have been made.
