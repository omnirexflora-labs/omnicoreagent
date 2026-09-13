# C. Migration dependency map

This is a dependency map for later design review, **not authorization or implementation**. Snapshot: `main`, `60da57a6dacd3f7796fffd9d198c0a7420aa6aad`. IDs refer to [inventory](inventory.md). The three context strategies remain in place; their message representations and accounting need adaptation.

## Settled scope decision: remove the workflow classes completely

The retained product scope is normal OmniCoreAgent runs, its deep-agent capabilities, and background execution for long-horizon tasks. On this snapshot, deep-agent capabilities are implemented within OmniCoreAgent; there is no separate DeepAgent class.

**RouterAgent, ParallelAgent and SequentialAgent are to be deleted completely**, together with their package/root exports, workflow examples, documentation/navigation and any dedicated tests or references. No fallback implementation, compatibility alias, deprecation wrapper or replacement workflow framework is required. Their behavior is not a migration preservation requirement. Core parallel tool batches and configured/dynamic subagent capabilities remain part of the retained agent runtime.

References to these classes below describe the source snapshot and establish deletion boundaries only. M7 is a removal unit; X34–X35 are removal-only inventory entries. Historical conversation data remains data: retaining stored records does not justify retaining an executable router or workflow fallback. This update changes the discovery findings only; source/example deletion has not yet been performed.


## Components that must change together

```text
M1 provider result/message interface <-> M2 loop/action/final contract
                                         |          |
                                         v          v
M3 tool schema/resolution/arguments <-> M4 tool results/observations
              |                          |          |
              +--------------------------+-------> M5 history/context
                                         |
M6 configured delegation + dynamic spawn-+
M8 terminal result/public consumers <---- M2/M4/M6
M9 streaming interface/events/SSE <------ M1/M2/M4/M6/M8
M10 retained prompts/docs/test fixtures follows each corresponding contract

M7 DELETE workflow package + exports + examples/docs/references
   No dependency on native routing, fallback, or workflow compatibility
```

This diagram depicts required coordination, not a prescribed redesign or development order. An adapter can preserve an existing interface while a unit changes internally. XML removal is unsafe until every retained path has compatible producers and consumers. The retired workflow paths must be removed, not adapted.

## M1 — Provider requests, complete responses and model-facing messages

- **Existing contract:** `llm_call(messages, tools=None)` returns a complete provider object or `None`; runtime passes only messages. Extraction yields first-choice text plus usage. Message serialization passes through dict/dataclass fields. OpenRouter's stop string applies when tools are absent. [R20] [R21] [R22]
- **Callers/consumers:** loop LLM step; active-context summary callback; facade session-summary callback; possible external `llm_call_sync` callers. Inventory X17, X38. Router capability-summary calls disappear with M7.
- **Must survive:** supported providers/configuration, query/instruction content, usage limits, complete answer availability, explicit error behavior, tool identity and argument fidelity.
- **Interface change:** supply native definitions when appropriate and retain structured calls/IDs, content blocks, completion state and usage. Provider-request serialization must distinguish native request/result fields from internal metadata. Summary-only calls need their own output expectation.
- **Compatibility questions:** provider native-tool support and names, content-plus-call outputs, reasoning/multimodal content, empty content, parallel support, usage on final chunks, finish/error semantics, legacy OpenRouter stop behavior, Cencori compatibility.
- **Verification before removal:** offline provider-shaped objects for every supported adapter family; inspect exact request dictionaries; fake streams with split/interleaved arguments and cancellation; later credentialed compatibility tests. Existing `test_llm` establishes optional tools forwarding only, not runtime native-tool operation.

## M2 — Loop actions, completion and recovery

- **Existing contract:** regex tool > agent > final; JSON string plus flags in ParsedResponse; malformed text gets user repair; one step per model attempt; STUCK prompts are advisory. X12–18, X30, X40. [R18] [R23] [R04]
- **Callers/consumers:** BaseReactAgent extraction wrapper -> loop handler -> tool/configured-child runners/outcome handler. Compatibility wrappers `act`, `execute_sub_agent_calls`, `resolve_tool_call_request` delegate to the same services.
- **Must survive:** bounded attempts, correct action dispatch, final answers, recoverable invocation errors, usage/resource outcomes and deterministic termination. Existing accidental precedence and null-on-parse-exhaustion are findings to decide, not requirements to copy blindly.
- **Interface change:** action/text/final interpretation and error/repair contract must match M1; update the forced-final loop prompt at the same time. Native action IDs must survive into execution and history.
- **Compatibility questions:** text and actions in one reply; mixed delegation/tool actions; empty final; unexpected finish reasons; deprecated BaseReactAgent helpers/ParsedResponse use outside repository; whether retry consumes a step.
- **Verification:** scenario tests through actual loop (not injected ParsedResponse only), malformed/empty/provider-error paths, final allowed step, repeated call halt, literal XML example in answer that must not execute, plain XML task input/output.

## M3 — Tool definitions, discovery, argument fidelity and authority inputs

- **Existing contract:** textual schema registry plus XML examples; heuristic scalar/list conversion; all-or-nothing batch resolution; MCP-first bare-name lookup; provider/server preserved only after resolution. X03, X05–06, X19–21. [R11] [R12] [R25] [R31]
- **Callers/consumers:** runtime registry and MCP discovery -> prompt/model; parsed actions -> ToolRegistry/MCP handler; resolved values -> governance, telemetry, offload policy.
- **Must survive:** built-in/local/MCP/skill tools; advanced hidden-tool discovery; reserved names; case-insensitive names; explicit MCP ambiguity error; concrete authority target and trusted internal provider markers; required Python parameters/defaults.
- **Interface change:** native tool schemas and argument decoding need to replace the text/XML typing assumptions; tool-name availability detection currently parses rendered prose and must remain accurate. Heuristic normalizer cannot blindly process already-typed native values.
- **Compatibility questions:** old numeric/boolean-looking string arguments; comma-bearing text; one-element arrays; custom registry integrations; tools needing genuine XML strings; JSON-array-string spawn schema; MCP names that native providers reject.
- **Verification:** end-to-end tool executes with exact arguments, including strings `001`, `false`, `hello, world`, arrays of one object and XML documents; advanced/MCP discovery then invocation; both workspace move targets authorized; governance denial before effects. Keep policy logic independently testable against normalized authority requests.

## M4 — Tool result normalization, observation return and safeguards

- **Existing contract:** Python/MCP output -> normalized result -> tool-role persistent write -> JSON observation normalizer -> offload/signatures/plain telemetry -> guard -> escaped XML user message. UUIDs and name counters differ. X23–30. [R33] [R35] [R37] [R38] [R39] [R40]
- **Callers/consumers:** tool executor/batch runner -> result normalizer -> formatter/guard -> next model/history; display/telemetry and loop detector also consume results.
- **Must survive:** one result per call, tool errors/timeouts, association in repeated-name batches, mixed success, path-based authority, guardrail decisions, inline workspace/artifact reads and retrievable offloaded data.
- **Interface change:** replace model-facing XML wrapper/user role with the selected result representation, retaining status/content/identity. Keep normalized internal result shape or adapt its consumers together. Output type metadata must not be inferred from XML syntax.
- **Compatibility questions:** MCP isError/structured/multiple content blocks; legitimate falsy data; timeout after partial completion and duplicate stored results; `partial` dynamic aggregate envelope; guarding before/after offload/persistence; tool-name escaping; typed payload size limits.
- **Verification:** successful/empty/falsy/business-envelope results, error recovery, timeout and pipeline timeout separately, guard-blocked content, offload/read-back, repeated tool names, no leaking action syntax from tool data. Current deficiencies are listed in coverage; do not silently codify them as target behavior.

## M5 — Active context, stored sessions, reconstruction and summaries

- **Existing contract:** raw XML/redacted assistant text + native-looking metadata; individual tool rows + transient user XML; loader validates narrow ToolCallMetadata then filters by strings and groups IDs. Two independent content-only context managers. X22, X28, X32, X36–39. [R19] [R26] [R48] [R49] [R53]
- **Callers/consumers:** all history writers (ordinary/configured/final), four backends/MemoryRouter, public session APIs, initial preparer, summary callbacks, provider serialization.
- **Must survive:** existing history retained, session/agent scope, tool associations, strategy configuration, all-system/recent-message preservation, summaries and retention policy, artifact references.
- **Interface change:** message schemas/roles/metadata and reconstruction must be coordinated with writers and M1/M4. Summary rendering and token accounting must include structured call meaning. Message selection needs an explicit representation-validity decision when a window splits related records; that is adaptation of the existing strategy, not replacement of it.
- **Compatibility questions:** version markers or translation of XML-era rows; malformed current metadata; redacted arguments; child rows lacking agent_name; old transient strings that might be real task content; raw full outputs replaying despite offload; persisted summaries with mixed formats; current async summary persistence races.
- **Verification:** real MemoryRouter round trips for text, batch, configured/dynamic child, offload and summary; all backend serializers; truncated/orphan/duplicate result groups; version-mixed sessions; closing/reopening artifacts; no action from historical XML alone. Current real-tool reload fails despite unit pairing tests passing, so compatibility work must use actual writer output.

## M6 — Both delegation surfaces

- **Existing contract:** configured `agent_calls` bypass normal tools, pass parent session and build XML child observations; dynamic spawn is a normal tool with new child sessions and prompted file outputs. X07–08, X21, X31–33. [R42] [R43] [R44] [R45]
- **Callers/consumers:** parser/loop -> configured runner -> existing child objects -> parent model; normal tool path -> factory -> new child facade -> workspace paths/summaries -> parent.
- **Must survive:** both configurable workers and ad hoc workers; concurrent independent work; role/task/kwargs fidelity; memory/session scope; cleanup/cancellation; narrowed dynamic policies; parent resume/read-back; child usage/results.
- **Interface change:** configured action request and return format must change together; dynamic spawn argument schema/normalizer/result aggregation must remain compatible with M3/M4. Custom child prompt hooks must retain their role/task/output parameters.
- **Compatibility questions:** configured parent governance coverage differs from dynamic; child traces are not automatically merged; response-text failure inference; recursive spawn restriction; whether writing output is guaranteed or merely requested; independently cancelled child aggregate.
- **Verification:** separate end-to-end cases for one/multiple configured and dynamic children, failures, timeout/cancel and cleanup, custom prompt hook, inherited tools/config/policy, persisted child/parent records. Direct wrapper tests do not establish singleton spawn through the parser/normalizer.

## M7 — Complete removal of RouterAgent, ParallelAgent and SequentialAgent

- **Existing contract (deletion evidence only):** sequence forwards completed responses; parallel gathers completed child runs; router uses capability-summary calls and a second XML decision parser. X34–X35. [R17] [R46] [R47]
- **Callers/consumers to clean:** package exports, public workflow docs/navigation, dedicated cookbook directory, README indexes and a cross-link from the retained subagent example. No core runtime or background supervisor dependency on these classes was found. See the concrete manifest below.
- **Behavior that must survive:** none of these classes' public APIs, routing decisions, retry/shield behavior or workflow-specific result formats. Normal runs, deep-agent capabilities, core parallel tool batches, configured/dynamic subagents, serving and background execution must continue working.
- **Required change:** delete implementations and workflow package; remove both root export declarations and lazy import mappings; delete all dedicated examples/bootstrap/package files and the public workflow page; remove navigation, imports, references and any dedicated tests. Do not port router XML to native routing.
- **Compatibility decision:** settled—no fallback, aliases, deprecated wrappers, adapters for these APIs or replacement workflow classes. Existing application imports will intentionally cease to work. Historical text records are not executable compatibility surfaces and must not be erased.
- **Verification:** no remaining executable imports, exports, docs links or active examples for the three classes; no orphan navigation/bootstrap files; package imports plus normal/deep/background tests still pass. Recheck references at cleanup time. Snapshot evidence in this discovery package and historical changelog entries may mention removed classes without preserving behavior.

### Concrete removal manifest at the investigated snapshot

| Location | Required cleanup |
|---|---|
| `src/omnicoreagent/workflows/` | Delete `__init__.py`, `router_agent.py`, `parallel_agent.py`, `sequential_agent.py` and the package directory. |
| `src/omnicoreagent/__init__.py` | Remove the three names from `__all__` and all three lazy export mappings. |
| `cookbook/workflows/` | Delete `README.mdx`, `__init__.py`, `_bootstrap.py`, `parallel_agent.py`, `router_agent.py`, `sequential_workflow.py`; remove the whole directory. |
| `docs/core-concepts/workflows.mdx` | Delete the dedicated public workflow page, rather than correcting its obsolete examples. |
| `docs.json` | Remove `docs/core-concepts/workflows` and `cookbook/workflows/README` navigation entries. |
| `README.md` | Remove the workflow cookbook link in the getting-started table and the Sequential/Parallel/Router cookbook listing. |
| `cookbook/README.mdx` | Remove the retired workflow section/link and directory-tree entry. Preserve unrelated multi-step application examples. |
| `cookbook/getting_started/agent_with_sub_agents.py` | Remove the final pointer to retired workflow patterns; retain the actual configured-subagent example. |
| Tests and other references | No direct class-name/workflow-package references were found in tracked tests. Remove any dedicated tests/references discovered at implementation time; retain core subagent, parallel tool-batch and background tests. Do not delete `.github/workflows`, which is CI, or unrelated files merely named “workflow”. |

The [saved removal-reference search](evidence/workflow-removal-references.txt) records the current locations. This is a deletion checklist, not evidence that cleanup has already happened.

## M8 — Terminal results, serving and background consumers

- **Existing contract:** complete facade dict with response/metric/IDs, telemetry final_answer, serving fixed-field normalization, background success-by-return and response preview. X40–42. [R01] [R54] [R55] [R56]
- **Callers/consumers:** retained cookbook apps, configured/dynamic children, POST /run and /run/sync, background supervisor and durable records, events/exporters/session APIs.
- **Must survive:** complete answer, stable correlation, error/cancellation visibility, session continuity, operational state separated from conversation state.
- **Interface change:** a future stream must still yield/produce a terminal result these callers can consume; decide how typed internal failures map to existing public outcomes.
- **Compatibility questions:** current None/model-error/step-limit may be “completed”; guardrail details dropped by serve normalizer; background stores preview rather than full returned object; reconstructed durable specs omit process-local tools/custom prompt builders/configured children.
- **Verification:** sync/SSE terminal payload equality; no duplicate terminal event; background normal/error/timeout/cancel/retry behavior; serialized specs across restart; keep context/history intact.

## M9 — Streaming beyond XML removal

- **Existing contract:** completion-oriented provider and loop, lifecycle telemetry, bounded SSE fanout/replay, child/batch gather, blocking sync tool execution. X17, X24, X33, X41. Retired workflow aggregation/shielding is excluded. [R20] [R21] [R58] [R60]
- **Callers/consumers:** M1 adapter through M2 loop, execution and child tasks, recorder/store, facade streaming helpers, serve event pumps, clients.
- **Must survive:** ordering/correlation, no execution of incomplete arguments, backpressure, exact terminal result, partial work and cancellation accounting, payload privacy configuration, bounded cleanup.
- **Interface change:** explicit stream/delta/completion contract at provider boundary, event publication and aggregation; compatibility with complete-response summaries and existing run API. Model text visibility must not rely on enabling raw model-response telemetry.
- **Compatibility questions:** tool/text delta interleaving; chunk-level usage; retry after partial output; SSE replay of chunks and event-ID cardinality; payload truncation/ref envelopes; queue overflow; HTTP disconnect; retained subagent cancellation; sync subprocess captures; polling store behavior. Removing XML addresses none of these automatically.
- **Verification:** deterministic fake-stream and disconnect tests before any live model tests; replay/live deduplication and queue overflow; cancellation mid-text/mid-arguments/mid-tool; batch/child completion; token totals; tool/body XML preserved as data. Later provider-specific live verification is required, but not for this discovery package.

## M10 — Prompt and public contract cleanup, tied to each unit

- **Existing contract:** all default/optional examples teach XML, while docs describe a custom tool-call parser and tests frequently bypass it. X01–11, X15, X21, X30, X39, X44.
- **Callers/consumers:** builders/config gates, model, fake providers, readers of docs/cookbook/examples and exported constants.
- **Must survive:** truthful capability discovery, batch independence, subagent selection, workspace/offload/skill workflows, literal task XML and complete answers.
- **Interface change:** update every retained prompt and recovery message when its runtime consumer changes; delete retired workflow prompts/examples through M7; replace representational assertions without weakening behavioral tests.
- **Compatibility questions:** third-party prompt builders/exported summary constants; catalog XML that may remain harmless organizational markup; user-supplied prompt examples; tests referring to obsolete tags like absent `tool_call_1`/`observation_marker`.
- **Verification:** configuration matrix for default/no-tools/advanced/MCP/skills/workspace/offload/configured/dynamic combinations; actual prompt-to-execution tests, not only tag presence tests. Keep XML data fixtures, SVG and CI report support.

## Independent adaptation boundaries

Policy evaluation, workspace storage drivers/path normalization, memory backend storage engines, signature detector algorithm, BM25 ranking, background durable task stores and telemetry persistence can remain independently maintained **if** their normalized inputs and identifiers remain stable. Their *adapters* are coupled to the units above. The skill catalog's organizational markup and unused summary-constructor prompt can be evaluated separately; neither is an executable XML action parser.

Review gates before deleting the old path: ordinary tools and retained configured/dynamic delegation covered; retired workflow implementations/exports/examples removed through M7; all optional prompts aligned; historical records and summary inputs verified with actual writers; task XML remains data; public complete-result compatibility established; streaming tested as a separate interface change. The [coverage report](coverage.md) identifies unresolved decisions and existing defects rather than expanding this phase into repairs.

<!-- source-reference-definitions -->
[R01]: ../../../src/omnicoreagent/core/runtime/omnicore_agent.py#L37
[R02]: ../../../src/omnicoreagent/core/runtime/builder.py#L20
[R03]: ../../../src/omnicoreagent/core/runtime/config.py#L119
[R04]: ../../../src/omnicoreagent/core/agents/base.py#L53
[R05]: ../../../src/omnicoreagent/core/workspace/artifacts.py#L89
[R06]: ../../../src/omnicoreagent/core/runtime/imports.py#L66
[R07]: ../../../src/omnicoreagent/core/system_prompts/builder.py#L36
[R08]: ../../../src/omnicoreagent/core/agents/initial_messages.py#L11
[R09]: ../../../src/omnicoreagent/core/system_prompts/base.py#L3
[R10]: ../../../src/omnicoreagent/core/system_prompts/extensions.py#L4
[R11]: ../../../src/omnicoreagent/core/tools/tool_prompt_renderer.py#L58
[R12]: ../../../src/omnicoreagent/core/tools/tool_call_resolver.py#L17
[R13]: ../../../src/omnicoreagent/core/skills/manager.py#L18
[R14]: ../../../src/omnicoreagent/core/tools/tool_failure_handler.py#L52
[R15]: ../../../src/omnicoreagent/core/system_prompts/summaries.py#L3
[R16]: ../../../src/omnicoreagent/core/runtime/summaries.py#L8
[R17]: https://github.com/omnirexflora-labs/omnicoreagent/blob/60da57a6dacd3f7796fffd9d198c0a7420aa6aad/src/omnicoreagent/workflows/router_agent.py#L9
[R18]: ../../../src/omnicoreagent/core/agents/xml_parser.py#L13
[R19]: ../../../src/omnicoreagent/core/tools/tool_batch_events.py#L15
[R20]: ../../../src/omnicoreagent/core/llm.py#L130
[R21]: ../../../src/omnicoreagent/core/agents/llm_step.py#L29
[R22]: ../../../src/omnicoreagent/core/agents/llm_response.py#L8
[R23]: ../../../src/omnicoreagent/core/agents/loop_step.py#L22
[R24]: ../../../src/omnicoreagent/core/tools/tool_action.py#L25
[R25]: ../../../src/omnicoreagent/core/tools/arguments.py#L6
[R26]: ../../../src/omnicoreagent/core/agents/message_history.py#L8
[R27]: ../../../src/omnicoreagent/core/agents/run_outcome.py#L11
[R28]: ../../../src/omnicoreagent/core/tools/local_tools_registry.py#L55
[R29]: ../../../src/omnicoreagent/core/workspace/tools.py#L141
[R30]: ../../../src/omnicoreagent/core/skills/tools.py#L21
[R31]: ../../../src/omnicoreagent/core/tools/tool_catalog.py#L17
[R32]: ../../../src/omnicoreagent/mcp_clients_connection/client.py#L23
[R33]: ../../../src/omnicoreagent/core/tools/tool_batch_runner.py#L30
[R34]: ../../../src/omnicoreagent/governance/capabilities.py#L78
[R35]: ../../../src/omnicoreagent/core/tools/tool_executor.py#L12
[R36]: ../../../src/omnicoreagent/core/tools/mcp_tool_handler.py#L14
[R37]: ../../../src/omnicoreagent/core/tools/tool_observation_parser.py#L9
[R38]: ../../../src/omnicoreagent/core/tools/tool_observation_formatter.py#L11
[R39]: ../../../src/omnicoreagent/core/tools/tool_observation.py#L20
[R40]: ../../../src/omnicoreagent/core/tools/observations.py#L6
[R41]: ../../../src/omnicoreagent/core/agents/loop_detection.py#L12
[R42]: ../../../src/omnicoreagent/core/agents/subagent_runner.py#L20
[R43]: ../../../src/omnicoreagent/core/agents/subagent_helpers.py#L32
[R44]: ../../../src/omnicoreagent/core/subagents.py#L21
[R45]: ../../../src/omnicoreagent/core/tools/advance_tools_use.py#L5
[R46]: https://github.com/omnirexflora-labs/omnicoreagent/blob/60da57a6dacd3f7796fffd9d198c0a7420aa6aad/src/omnicoreagent/workflows/sequential_agent.py#L7
[R47]: https://github.com/omnirexflora-labs/omnicoreagent/blob/60da57a6dacd3f7796fffd9d198c0a7420aa6aad/src/omnicoreagent/workflows/parallel_agent.py#L8
[R48]: ../../../src/omnicoreagent/core/memory_store/memory_router.py#L12
[R49]: ../../../src/omnicoreagent/core/context_manager.py#L60
[R50]: ../../../src/omnicoreagent/core/memory_store/sql_db_memory.py#L175
[R51]: ../../../src/omnicoreagent/core/memory_store/redis_memory.py#L97
[R52]: ../../../src/omnicoreagent/core/memory_store/mongodb.py#L17
[R53]: ../../../src/omnicoreagent/core/summarizer/summarizer_engine.py#L232
[R54]: ../../../src/omnicoreagent/core/runtime/execution.py#L62
[R55]: ../../../src/omnicoreagent/serve/serialization.py#L35
[R56]: ../../../src/omnicoreagent/background/supervisor.py#L57
[R57]: ../../../src/omnicoreagent/serve/routes/runs.py#L19
[R58]: ../../../src/omnicoreagent/serve/sse.py#L208
[R59]: ../../../src/omnicoreagent/core/telemetry/stream.py#L9
[R60]: ../../../src/omnicoreagent/core/telemetry/recorder.py#L45
[R61]: ../../../src/omnicoreagent/core/telemetry/redaction.py#L13
[R62]: ../../../src/omnicoreagent/core/types.py#L101
[R63]: ../../../src/omnicoreagent/core/guardrails/patterns.py#L1
[R64]: ../../../src/omnicoreagent/background/agent_specs.py#L1
[R65]: ../../../src/omnicoreagent/core/tools/tool_observation_guardrail.py#L11
[R66]: ../../../src/omnicoreagent/core/telemetry/store.py#L78
[R67]: ../../../src/omnicoreagent/background/manager.py#L55
[R68]: ../../../src/omnicoreagent/core/tools/advance_tools/advanced_tools_use.py#L210
[R69]: ../../../src/omnicoreagent/core/memory_store/in_memory.py#L15
[R70]: ../../../src/omnicoreagent/background/models.py#L1
[R71]: ../../../src/omnicoreagent/background/run_helpers.py#L44
[R72]: ../../../src/omnicoreagent/core/workspace/files.py#L1
[R73]: ../../../src/omnicoreagent/core/guardrails/engine.py#L1
[R74]: ../../../src/omnicoreagent/serve/lifespan.py#L26
[R75]: ../../../src/omnicoreagent/core/agents/session_state.py#L10
