# B. XML dependency inventory

## Settled scope decision: remove the workflow classes completely

The retained product scope is normal OmniCoreAgent runs, its deep-agent capabilities, and background execution for long-horizon tasks. On this snapshot, deep-agent capabilities are implemented within OmniCoreAgent; there is no separate DeepAgent class.

**RouterAgent, ParallelAgent and SequentialAgent are to be deleted completely**, together with their package/root exports, workflow examples, documentation/navigation and any dedicated tests or references. No fallback implementation, compatibility alias, deprecation wrapper or replacement workflow framework is required. Their behavior is not a migration preservation requirement. Core parallel tool batches and configured/dynamic subagent capabilities remain part of the retained agent runtime.

References to these classes below describe the source snapshot and establish deletion boundaries only. M7 is a removal unit; X34–X35 are removal-only inventory entries. Historical conversation data remains data: retaining stored records does not justify retaining an executable router or workflow fallback. This update changes the discovery findings only; source/example deletion has not yet been performed.



Snapshot: `main` at `60da57a6dacd3f7796fffd9d198c0a7420aa6aad`. Stable IDs `X01`–`X44` identify distinct contracts, not necessarily files. Source references link to definitions at this snapshot; related callers/consumers are explicit below. Test references identify evidence, not a claim that every edge is covered. [Coverage](coverage.md) records executed tests and limitations.

“Control” determines execution/continuation/completion; “prompt” teaches the contract; “representation” is a serialized interaction value; “storage” persists/reconstructs it; “indirect” consumes normalized values/state; “external” exposes assumptions; “task content” must remain supported. A row can have multiple categories. XML-shaped prose headings are prompt representation, not executable tags.

| Dependency ID | Location | Category | Producer | Consumer | Current purpose | Activation | Removal impact | Evidence | Migration implication |
|---|---|---|---|---|---|---|---|---|---|---|
| X01 | `OmniCoreAgentPromptBuilder.build`, lazy default builder | Prompt | Application instruction + base prompt | Prompt context builder, model | Wrap instruction in `<system_instruction>` and attach mandatory XML rules | Default builder on all runs; custom builder can differ | Model loses framing/contract if removed without replacement | [R06]/[R07]; `test_prompt_context` | Preserve application instructions and custom builder hooks while updating output instructions. |
| X02 | `REACT_AGENT_PROMPT` format/thought/final sections | Prompt/control | Constant | Model -> XML parser | Mandatory thought/call/final syntax, Markdown payload, no XML inside answer | Default prompts including child/router agents | Untagged answers currently retry; altered instruction alone breaks loop | [R09]/[R18]; `test_base` | Coordinate final/text/tool semantics; remove obsolete restrictions on task XML. |
| X03 | Base single/batch/argument examples | Prompt/representation | Tool schema prose + XML examples | Model -> `_parse_calls/_parse_args` -> normalizer | Independent batch preference, scalar text and JSON values in element parameters | Default prompt | Calls/types/batching guidance becomes inconsistent | [R09]/[R18]/[R25]; base batch test | Preserve independent execution and exact schemas; change examples with actual invocation contract. |
| X04 | Base observation/memory/extension sections | Prompt | Base constant plus optional sections | Model interprets runtime observations/catalogs | `<observations>`, display identity, memory-use and extension rules | Default prompt | Model cannot reliably interpret changed result representation | [R09]/[R40] | Align return representation and prompts; memory organizational headings are not actions. |
| X05 | `ToolPromptRenderer.render`, `available_tool_names` | Prompt/indirect | Local/MCP schemas rendered as `name:` lines | Model and regex feature detection | Teaches parameter names/types; detects whether optional tools are visible | Every initial prompt | Replacing text schemas can accidentally disable optional extensions | [R07]/[R11]; prompt renderer/context tests | Preserve feature availability checks when definitions become native schemas. |
| X06 | Advanced discovery extension and retrieval tool | Prompt/indirect | Flag, always-visible names, BM25 catalog | Model -> retriever tool -> model -> resolver | XML search examples and discovery-before-inability; hidden schemas discovered as ordinary results | Advanced flag + visible retriever | Hidden tools may become unreachable or model instructed to use nonexistent surface | [R10]/[R12]/[R45]; prompt/smoke tests | Preserve filtering, retriever schema/result interpretation, unrestricted resolution of available hidden tools. |
| X07 | Dynamic spawn extension/instruction | Prompt | `build_subagents_additional_prompt`, factory instruction/custom hook | Model -> ordinary spawn tool; child model -> workspace write | XML `subagents_json` example and read-back requirement | Dynamic flag + visible spawn; child factory | Delegation and output read-back can disappear silently | [R10]/[R44]; `test_subagents` | Update wrapper schema, child prompts, output paths and parent interpretation together. |
| X08 | Configured delegation extension/registry | Prompt/control | Application list, reflection of `run` | Model -> `agent_call(s)` parser | Separate invocation from tool calls | Truthy configured `sub_agents`, independent of dynamic flag | Configured workers become uncallable if only native tools are migrated | [R07]/[R10]/[R42]; `test_prompt_context` | Provide an explicit replacement for this separate action kind and registry. |
| X09 | Workspace extension | Prompt | Enabled workspace + visible tools | Model -> ordinary parser/resolver | XML create/append examples, filesystem/artifact distinction | Workspace defaults on; forced for dynamic workers | Stale invocation examples; file/state preservation guidance lost | [R10]/[R29]; runtime registry tests | Keep path semantics and durable-state guidance; update examples only. |
| X10 | Artifact extension/context message | Prompt/representation | Offloader preview marker and extension | Model -> artifact retrieval tool | XML retrieval example, `[TOOL RESPONSE OFFLOADED]`, ID/path/tool and preview | Offload enabled + read_artifact available | Model loses access to full result or recursively offloads reads | [R10]/[R05]/[R38]; offload tests | Preserve reference/read contract, inline provider policy and preview information. |
| X11 | Skills catalog and extension | Prompt/representation | Discovered SKILL.md metadata -> `<available_skills><skill><name>/<description>/<location>` | Model through context builder | Tool discovery/activation and XML read/script examples | Skills enabled, nonempty catalog, skill tool names visible | Skill knowledge becomes invisible; schema text inconsistent | [R13]/[R10]/[R30]; skills/prompt tests | Catalog can be independently re-rendered; actual read/script calls must match common tool protocol. |
| X12 | `_extract_call_blocks`, `_parse_calls` | Control | Complete provider response string | `parse_action_or_answer` -> ParsedResponse | Single/collection selection; tool/agent name and args aliases | Every response | No invocation extraction; malformed input behavior changes | [R18]; diagnostics aliases/two_unwrapped/empty_collection | Preserve accepted behaviors intentionally; define mixed/conflicting/malformed semantics before deleting regex path. |
| X13 | `_parse_args` | Representation/control | XML parameters or JSON object in params | JSON action list -> tool normalizer or configured child kwargs | Embedded array/object decode; scalar strings; duplicate overwrite | Any parsed call | Arguments can change type/value, including XML files | [R18]; diagnostic entities/parameter_text | Native typed args need lossless transport; obsolete XML coercion must not corrupt new inputs. |
| X14 | `parse_action_or_answer` classification | Control | Parsed tool/agent blocks and final regex | Loop step handler | Tool > agent > answer precedence across entire string | Every response | Completion/action ordering changes; tag examples execute today | [R18]/[R23]; mixed-response diagnostics | Explicit output/action model; task XML must never trigger execution merely by appearing in content. |
| X15 | `_xml_shape_error`, `_missing_xml_error` | Prompt/control | Failed classification + response prefix | User repair message -> next LLM call | Teach XML retries; thought-only misleadingly presented as valid | Parse failure | Repair loop teaches removed syntax or no longer recovers | [R18]/[R23]; `test_base`, five-error diagnostic | Replace recovery with format-appropriate errors; keep bounded attempts and useful failure outcome. |
| X16 | `extract_thought` | Control-adjacent/representation | `<thought>` in response | **No in-repo caller**; public module helper possible external import | Extract optional thought text | Direct helper calls only | No traced runtime loss; external import compatibility unknown | [R18]; caller search | Do not invent reasoning-stream dependency; determine external compatibility separately. |
| X17 | Provider adapter + response extractor | Indirect/interface | LiteLLM/Cencori full response object | LLM step -> string parser; summaries/router direct calls | First choice content + usage; native call fields discarded | Every model step | Native calls unrecognized even if XML parser removed | [R20]/[R21]/[R22]; LLM tests and native-only probe | Adapter, extractor, message serialization, call IDs and completion accounting must change as one interface unit. |
| X18 | `ParsedResponse`, `parse_tool_actions` | Representation/indirect | XML parser JSON string | Resolver -> executable ToolCallResults | Flags choose tool/delegation; second JSON decode validates shape | Ordinary actions | Dispatch and resolver inputs break | [R24]/[R62]; resolver/action tests | Replace serialized intermediate with equivalent explicit action data; preserve validation and errors. |
| X19 | `normalize_tool_args` | Indirect | Parsed scalar strings/nested values | Local/MCP executors and governance | Heuristic typing/repair, singleton-list flattening | Ordinary tools including dynamic spawn; not configured agents | Removing changes accepted arguments; retaining damages native typed strings/arrays | [R25]; `test_tool_arguments`, single-spawn probe | Audit schema-aware boundary compatibility; do not blindly reuse XML-era coercion. |
| X20 | `ToolCallResolver.resolve/resolve_single_action` | Indirect/control | ToolAction names/params | Executor, failure handler | MCP-first/local fallback, retriever exception, all-or-nothing resolution | Ordinary tool/batch | Wrong tools, authority identities or validation semantics | [R12]/[R31]; resolver tests | Keep resolution and concrete provider/server identities independent from invocation representation. |
| X21 | `build_sub_agent_tool_error` | Prompt/representation | Configured agent name in tool action | Failure observation -> model | Explicit corrective `<agent_call>` instruction | Name exactly matches configured worker | Stale recovery loses delegation path | [R12]/[R39]; resolver tests | Update wrong-surface error with configured-delegation replacement. |
| X22 | `ToolBatchRunner.start`, history helpers | Storage/representation | Raw response + resolved normalized calls | Memory, active messages, replay loader | Generate UUIDs/native-looking metadata; store XML or governed redacted text | Resolved ordinary batch | IDs/history and replay associations disappear | [R19]/[R33]/[R26]; ID/batch/redaction tests | Coordinate IDs, assistant request metadata, active messages and persistent representation. |
| X23 | `ToolExecutor.execute/_normalize_result` | Indirect/storage | Local/MCP return/exception | Tool history, batch observation parser, telemetry | Normalize status/data/message; persist result with UUID and args | Every ordinary execution | Result/failure interpretation and association break | [R35]/[R36]; executor tests, MCP probe | Preserve recoverable result behavior; explicitly handle MCP structured/multipart/error information later. |
| X24 | Batch timeout/error paths | Indirect/control/storage | Gather/observation exception or timeout | Error tool records -> XML observation -> next loop | Per-request error results, loop signatures, timeout telemetry | Tool/pipeline timeout | Recovery or call-result completeness breaks | [R33]; timeout tests | Keep cancellation, partial completion and error/result association through new representation. |
| X25 | `parse_tool_observation` | Representation/indirect | Current normalized batch, JSON/legacy success-error envelopes | Formatter/append path | Data JSON coercion and global partial status; **not an XML parser** | Ordinary results | Legacy/result data assumptions lost | [R37]; observation tests | May adapt independently behind stable normalized result shape. |
| X26 | `build_results_observation` | Representation/indirect | Normalized result list + resolved call order | Debug display, telemetry, signatures, mutable results | Plain summary, offload and per-tool deduped signature records | Ordinary result pipeline | Changes signatures, preview mutation and telemetry meaning | [R38]; observation tests | Preserve identity/order/output policy separately from XML serialization. |
| X27 | `build_xml_observations_block` | Representation/prompt | Scrubbed normalized results | Model; history string filter | Escaped body, tool_name counter, grouped results (without UUID/status/args) | Ordinary tool response/validation failure | Model return contract and transient-history detection break | [R40]/[R39]/[R26]; observation tests, false-value probe | New return message must keep call association/error semantics; preserve arbitrary XML data. |
| X28 | `append_observations` | Storage/control | Results -> XML builder | Active messages, MemoryRouter, next model | User-role feedback; state OBSERVING | Ordinary tool pipeline | Roles/history/state change with tool-result messages | [R39]/[R23]; observation/action tests | Change active result role, persistent metadata and reader together. |
| X29 | Guardrail boundaries | Indirect/task content | User text; MCP concatenated text; normalized result fields | Guard engine -> blocked/suspicious/error status | Injection screening; delimiter patterns include XML-like role tags | Full default; input_only/off options | Tag deletion is not a replacement for content screening | [R36]/[R39]/[R63]; guardrail tests | Preserve screening of untrusted data; update boundary inputs/order deliberately, not by deleting XML-related patterns. |
| X30 | Loop detector and failure handler | Indirect/prompt/control | Tool name, str(args), formatted/offloaded data or error | Signature/pattern detector -> replaced prompt + forced XML final guidance | Detect repetition, ask model to stop or change approach | Repeated ordinary calls/errors | XML-specific recovery remains; changing coercion/offload changes signatures | [R41]/[R14]; loop/failure tests | Keep normalized behavior/signatures while changing recovery syntax and eventual completion semantics. |
| X31 | Configured runner/kwargs | Control/indirect/storage | `agent_calls` JSON list | Child `.run`, parent observation/usage/history | Separate reflection dispatch, forced session, concurrent results/cleanup | Configured XML agent action | Delegation bypasses normal tool migration | [R42]/[R43]; configured runner tests | Adapt call action, session/args contract, error/cancel, output and parent resume together. |
| X32 | `build_sub_agents_observation_xml` | Representation/storage | Child status/output/name | Parent model and history sentinel filter | `<agent_name>/<status>/<o>/<e>` with start/end sentinels | Configured delegation | Status/identity and historical filtering disappear | [R43]/[R42]/[R26]; escaping test | Preserve child result identity/status and support old sentinel records. |
| X33 | Dynamic factory/tool wrapper | Indirect/external | Spawn args after common normalizer | New children, workspace outputs, aggregate tool result, parent | New sessions, inherited policy/config, 500-char summaries, lexical failure inference | Dynamic spawn ordinary tool | Capability can silently break while direct wrapper tests pass | [R44]/[R45]; subagent tests + singleton diagnostic | Verify through actual model-action boundary, not wrapper-only tests. |
| X34 | **DELETE:** Router decision/registry/retry | Control/prompt/storage | Inner agent final-answer XML; capability registry | Outer regex picks child and query | `<routing>` prompt, `<agent>/<task>` actual parse, retries | Router workflow (retired) | Router cannot choose child after inner final format changes | [R17]; source trace | Delete producer, parser, class, exports and dedicated examples/docs through M7. No native replacement, fallback or compatibility requirement. |
| X35 | **DELETE:** Sequential/parallel workflows | Indirect/external | Completed child public result | Next child query / aggregate mapping | `response`, `agent_name`, shared session, retries/shield | Workflow public APIs | Generator/delta replacement breaks composition | [R46]/[R47]; cookbook workflows | Delete both classes, exports and dedicated examples/docs through M7. Do not preserve their APIs, wrappers or cancellation behavior. |
| X36 | Message types/history loader | Storage/control/task content | Backend records after metadata renaming | Active provider messages, model | Validate metadata, skip observation strings, match call IDs | Continued sessions | Historical sessions fail or control/data misclassified | [R26]/[R62]; history tests + real round-trip probe | Versioned compatibility for XML/native/redacted/malformed metadata; stop using task content as sole message-type discriminator. |
| X37 | Memory backends and router | Storage/indirect | Assistant/tool/user writes | Shared summarizer and history loader/API | Raw string + normalized metadata with roles/status/summary IDs | in_memory/sql/redis/mongodb | New metadata must survive every backend and API | [R48]/[R50]/[R51]/[R52]; memory tests | Preserve retention/backends; representation migration must not erase records. |
| X38 | Active and session summarization/truncation | Indirect/storage/prompt | Content-only history, arbitrary message suffixes | Summary model, active context and later replay | Preserve recent/system messages; plain summaries | Context enabled / memory overflow | Native requests without content lose task details; pairs can split | [R49]/[R53]/[R15]/[R16]; context/summarizer tests, orphan diagnostic | Adapt representation/group accounting without replacing strategy; preserve call/result meaning in summary input. |
| X39 | Summary memory constructor export | Prompt/external | XML-organized constant requiring JSON output | No in-repo runtime consumer; exported constant | Legacy narrative/retrieval format | External import only | Runtime removal unnecessary; external consumers unknown | [R15] | Separate prompt markup from control; assess public export compatibility. |
| X40 | Public final-result construction | Indirect/external | Extracted final or loop/error result | Apps, workflows, serve, dynamic/configured children, background | answer->response; metric/session/run/trace | All public runs | Returning chunks or raw protocol changes every consumer | [R01]/[R27]/[R54]/[R55]/[R56] | Preserve terminal result contract alongside any future streaming API. |
| X41 | Telemetry recorder/store/stream/SSE | External/indirect/representation | Full model response, normalized tools, results, errors | Stored traces, exporters, live/replay SSE and examples | Lifecycle visibility; optional raw XML; final complete event | Telemetry default, serve optional | Event consumers/visibility policies/cursors may break | [R60]/[R61]/[R57]/[R58]/[R59]; telemetry/SSE tests | Specify text/tool delta events and terminal aggregation, payload policies, replay and cancellation separately. |
| X42 | Background specs/supervisor/results | External/indirect/storage | Registered/reconstructed agent run | Durable attempt/run state, 1000-char preview, workspace events | Completion/error/timeout/retry/session/workspace lifecycle | Background manager/direct or served | Changed return/error/session contracts alter operational state | [R56]/[R64]; background tests | Preserve facade result semantics and durable task separation; old specs/custom prompts need compatibility review. |
| X43 | XML documents, SVG/MDX, artifact extension and repr | Task content / non-control | Tool/user file content, docs assets and repr | Workspace/artifact readers, user/model, docs renderer | Legitimate XML support, display/layout | XML task/file/docs/CI | Global XML removal breaks valid work | [R05]/[R29]; `test_offload_detects_xml_extension`; docs assets, CI | Keep `.xml` detection/readback/escaping and task processing; ignore MDX/JSX markup as control dependencies. |
| X44 | Public docs/tests/cookbook examples | External/prompt | Base contract, known result shapes and stale documented call_sub_agent tool | Users, fake models, smoke tests, cookbook events/workflows | Documents XML custom parser; fixtures emit tags; apps read response/events | Documentation and integration tests | Tests may pass on stale mocks while real provider path breaks | README architecture table; docs agent-harness/architecture/events; base/smoke/production tests | Delete retired workflow examples/docs/references via M7; adapt retained examples and distinguish tag snapshots from behavioral assertions. |


## Tag-to-consumer closure

| Identified tag family / syntax | Producer | Actual runtime consumer | Inventory IDs |
|---|---|---|---|
| `<thought>` | Base/router prompts -> model | Only `extract_thought` helper, no runtime caller; may remain in raw stored tool text | X02, X16, X22, X34 |
| `<final_answer>` | Base/router/recovery prompts -> model | `parse_action_or_answer` -> answer handling -> public result; router then parses nested content | X02, X14–15, X30, X34, X40 |
| `<tool_calls>/<tool_call>` | Base/extension prompts -> model | `_extract_call_blocks` -> tool branch | X03, X06–10, X12, X14 |
| `<agent_calls>/<agent_call>` | Configured extension/repair -> model | Same extraction helpers -> separate subagent runner | X08, X12, X21, X31 |
| `<tool_name>/<agent_name>/<name>` | Model following examples/registry | `_first_tag_match`; aliases chosen by priority | X12–14 |
| `<parameters>/<args>/<arbitrary_word_parameter>` | Model | `_parse_args`, then ordinary-tool normalization; child signature binding separately | X13, X19, X31 |
| `<observations>/<observation tool_name="name#n">` | Ordinary observation serializer | Model; history loader checks leading wrapper only | X04, X27–28, X36 |
| Child `<observation>/<agent_name>/<status>/<o>/<e>` + sentinels | Child result formatter | Model; loader checks prefix sentinel, no child XML result parser | X31–32, X36 |
| `<routing>/<agent>/<task>` | Router instruction -> inner final-answer payload | Router regex reads agent/task only; routing wrapper not validated; entire path to be deleted | X34 (remove) |
| Router registry `<agent>/<name>/<capabilities>` | Application registry | Model only | X34 |
| `<available_skills>/<skill>/<name>/<description>/<location>` | Skill manager metadata | Model only via system context | X11 |
| `<system_instruction>`, `<extension ...>`, all organizational prompt tags | Static prompt builders | Model only; runtime gates inclusion with flags/tool-name regex, not tag parsing | X01–11, X39; full tag catalog |
| XML file tags, XML declaration, HTML/SVG/MDX, escaped path placeholders | Ordinary data/docs/files | File tools, artifact extension detector/readback, renderer/user | X43 |

No XML parsing-library dependency was found in the traced runtime. This conclusion rests on the caller paths above and the saved tracked-source searches, not on a zero-match search alone. New provider calls, third-party prompt builders and external users of exported helpers remain outside repository closure.

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
