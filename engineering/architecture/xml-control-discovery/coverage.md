# D. Coverage, evidence and unresolved questions

## Settled scope decision: remove the workflow classes completely

The retained product scope is normal OmniCoreAgent runs, its deep-agent capabilities, and background execution for long-horizon tasks. On this snapshot, deep-agent capabilities are implemented within OmniCoreAgent; there is no separate DeepAgent class.

**RouterAgent, ParallelAgent and SequentialAgent are to be deleted completely**, together with their package/root exports, workflow examples, documentation/navigation and any dedicated tests or references. No fallback implementation, compatibility alias, deprecation wrapper or replacement workflow framework is required. Their behavior is not a migration preservation requirement. Core parallel tool batches and configured/dynamic subagent capabilities remain part of the retained agent runtime.

References to these classes below describe the source snapshot and establish deletion boundaries only. M7 is a removal unit; X34–X35 are removal-only inventory entries. Historical conversation data remains data: retaining stored records does not justify retaining an executable router or workflow fallback. This update changes the discovery findings only; source/example deletion has not yet been performed.


## Snapshot and method

- Investigated **local `main`**, commit **`60da57a6dacd3f7796fffd9d198c0a7420aa6aad`**, on 2026-09-13. Separate worktree: `/home/abiorh/ai/omnirexflora-labs_dir/omnicoreagent-xml-discovery`.
- Original checkout remains on `sandbox-execution-service` at `65fc7ce779bc50ec5e74e44a6de11662dcd9cfb3` with its pre-existing untracked `.codex_production_harness_qa.md` untouched. Early reads there were rechecked against main; the diff has seven files, notably absence of `sandbox/execution.py` on main. This package does not attribute that service to main.
- `git worktree add ../omnicoreagent-xml-discovery main` created the new worktree. No fetch/pull was performed, so this report does not claim alignment with a later remote tip.
- Applicable instruction search found no repository or ancestor `AGENTS.md`. Read `CONTRIBUTING.md`, `engineering/README.md`, project configuration and CI guidance. Internal discovery records belong under engineering; no production implementation was changed.
- Broad searches covered tags, f-string tag builders, XML/parser libraries, regexes, response extraction, action/argument/observation consumers, model-call sites, history roles/metadata, subagent/workflow invocation, and serving/background result consumers. Tracked-source enumeration included `core/workspace`, which generic `rg` can omit because `workspace/` is ignored. Saved source/tag/caller indexes are supporting navigation, not proof by themselves.
- Source establishes behavior. Architecture/specification documents were used for ownership/intended contracts; relevant sections on runtime, tools, context, workspace, telemetry streaming, serving, background lifecycle and governance were compared against implementation. Long engineering records include future goals; this was not a line-by-line conformance audit of every unrelated specification section.

## Coverage ledger

| Area | Components and paths followed | Configurations / what is established | Limits |
|---|---|---|---|
| Public/runtime | root lazy exports; runtime normalization/config/construction/builder/imports/facade/execution/harness_tools/summaries | Default initialization, custom builder, direct/connected LLM, guardrail full/input_only/off, governance opt-in, run IDs/results | External custom builders/registries unknown |
| Agent loop | Base/React, LLM step/extractor, XML parser, loop handler/outcome/state, initial messages, action wrappers, history loader | Default/empty/tool/agent/error/final/stuck paths, one step per attempt | Live model adherence not tested |
| Prompts | Complete base and optional extension families, prompt context builder, schema renderer, skills catalog, router and error/loop prompts, summary constants | Workspace, offload, advanced retrieval, configured/dynamic subagents and skills inclusion gates | No deployed application-specific prompts available |
| Tools | registry/local/MCP handlers, catalog/resolver/actions/normalizer, executor/batch/events/failure, all observation stages | Single/batch, missing/duplicate names, MCP-first resolution, result envelopes, timeout, trusted providers | External MCP servers not connected |
| Advanced tools | retriever registration, BM25 catalog loading/result path, always-visible renderer set | Hidden tools still executable, retrieval returns stringified tool descriptions | Ranking quality/cross-agent global catalog behavior not audited |
| Workspace/artifacts | config/manager/files/tools/path/offload policy, artifacts offload/preview/read/tail/search, local/cloud driver boundary | XML task data retained; provider inline policy; workspace/scoped authority; local and fake S3 tests | No live S3/R2 storage; remote driver durability not re-audited |
| Skills | manager/models/discovery, XML catalog, read_skill_file/run_skill_script | Local tools, path checks, subprocess capture, guard/offload return path | No third-party skill corpus; no arbitrary scripts run by discovery |
| Delegation | configured runner/helpers; dynamic factory/harness registration; sequential/parallel/router workflows | Separate protocols, session/context/config inheritance, aggregation, cleanup, response/status consumers | Parent/child live trace topology and all cancellation races need follow-up |
| Memory/context | active SessionState; history loader/router; in-memory/SQL/Redis/Mongo record writers/readers; shared summarizer and callbacks; offloader | Raw/parsed storage, filtering/association, real in-memory failure, active truncation and summary input | Redis/Mongo live tests skipped; no production historical sessions available |
| Governance/guardrails | batch authorization/capability targets, MCP connect, dynamic spawn narrowing; result/input guard; delimiter patterns | Policy depends on resolved values, not XML; configured delegate bypass distinct; main sandbox contracts only | Not an audit of whole security model or approval-policy effectiveness |
| Telemetry | recorder/config/redaction/context/store/stream/normalizer/export boundary | Complete model event policy, tool results, IDs, live/replay, payload limits | No live OTLP/vendor exporter calls; high-rate streaming not tested |
| Serving | server/app factory/CLI/lifespan; run/session/event/telemetry/background route wiring, SSE/serialization/models/timeouts | Existing telemetry SSE, terminal complete result, replay/cancel/error | Real proxy buffering and browsers not exercised |
| Background | manager/spec reconstruction/run helpers/supervisor/transitions/events/workspace IO/store interfaces | Same facade run, preview-based completion, session policies, timeout/cancel/retry and durable lifecycle separation | No Redis/Mongo multiworker recovery deployment; not an exhaustive scheduler audit |
| Docs/examples/tests | README, public architecture/harness/context/memory/subagent/workflow/skills/workspace/MCP/events/serve guidance and config; related engineering records; workflow/real-app/getting-started/background/serve cookbook and tests | External result and event assumptions; stale doc statements separated below | `omnistudio` is absent from this tracked main snapshot; other untracked UI checkout content is out of scope |

The walkthrough's scenario table covers all twelve requested scenarios and separately covers dynamic delegation and workflows. No native-tool-controlled loop or token-streaming runtime was found. The adapter's optional `tools` argument is present but unused by traced runtime callers. Configured agent calls, ordinary calls, and routing decisions are all traced to the next parent/model/public output; they are not assumed to share one dispatch route.

## Executed verification

Tests used the existing sibling checkout's virtualenv **only as interpreter/dependencies**, with `PYTHONPATH="$PWD/src"` selecting the new main worktree source. No install/sync or source edits were made. [Environment](evidence/environment.txt) records interpreter, package versions, import locations and snapshot. No live model calls were made; selected LLM tests mock provider adapters. Tests use temporary files/in-memory or test SQLite state and fake remote tools where appropriate.

1. **Core discovery suite: 312 passed in 43.23s.** [Exact command](evidence/test-commands.sh), [output](evidence/core-tests.txt). Covers base/LLM/extraction/loop/action/arguments/resolver/executor/batch/observation/failure/history/prompts/registry/delegation/governance/context/summaries/offload/smoke/SSE/outcomes/state/telemetry.
2. **Boundary suite: 454 passed, 2 skipped in 61.71s.** Same command file and [output](evidence/boundary-tests.txt). Covers MCP client/state, registry/skills/guards/workspace/runtime/telemetry/background/serve/memory. The two live memory contracts require Redis/MongoDB environment URLs; they were not configured. No claim of live remote-backend verification is made.
3. **Offline diagnostics:** [script](evidence/diagnose.py), [results](evidence/diagnostics.jsonl). Executed once against main. They invoke parser/normalizer/loader/core loop with fake LLM responses and real in-memory memory, never a provider. The saved JSON includes generated timestamps from that run, not stable test snapshots. These are discovery probes outside production/tests, not migration implementation.

Combined existing tests: **766 passed, 2 skipped** across disjoint file lists. No full-suite, lint, pre-commit, live-model, live-MCP or remote-backend result is claimed. Existing tests are evidence for their exercised shapes, not proof that all combined paths work.

## What the tests establish—and miss

| Evidence | Useful behavior to preserve | Representation-specific or missing coverage |
|---|---|---|
| `test_base` local/batch tests | Actual local function executes, normalized input, one UUID per tool, internal registry shared between prompt and execution | Some action tests inject ParsedResponse and supply incomplete XML; they do not verify parsing that text |
| `test_tool_call_resolver`, `test_tool_arguments` | MCP/local naming, ambiguity errors, shape rejection, current coercion | Heuristic conversion assertions may need retirement for typed arguments; no provider-schema validation implied |
| `test_tool_batch_runner` | Per-call execution/results, governance denies before side effects, batch/pipeline timeout, telemetry and argument redaction | Injected normalized calls do not prove model produces valid invocations; timeout partial completion duplicate-history combination not established |
| `test_tool_observation`, guardrail tests | Recoverable result handling, inline provider policy, guard blocking/escaping and offload | Tag/counter string assertions are representation; common guard follows offload/raw persistence, so tests of final XML do not prove earlier stores are scrubbed |
| `test_message_history` | Accepted metadata pairing, incomplete-batch discard, transient filtering | Tests use narrow metadata, unlike actual executor/child/summary writes. Full writer-to-loader mismatch diagnosed separately |
| `test_subagent_runner` | Configured child cleanup, successful/error result assembly, escaped output | Stub history writers hide real metadata failures; no common tool governance/timeout is established |
| `test_subagents`, `test_subagent_governance` | Config inheritance, no recursive spawn, policy narrowing, wrapper list/string handling, failures | Direct wrapper list works; parser/normalizer collapses singleton list before wrapper. Claimed file write is not verified by factory |
| `test_context_manager`, `test_summarizer` | Strategy thresholds, preserved system/recent messages, summary fallback | No call-group integrity or native argument-only summary input guarantee; token counts omit tool metadata |
| `test_llm`, `test_llm_response` | Forward optional tools to mocked adapter, accepted complete text/usage shapes | No real native tool response path, multimodal retention or streamed chunk consumption |
| `test_omniserve_sse`, runtime telemetry tests | Lifecycle event forwarding, replay filtering/deduplication, terminal result, timeout/cancel handling | Tests demonstrate event streaming, not token streaming or every child trace in parent stream |
| `test_real_application_smoke` | Scripted XML model drives local/MCP/workspace/offload/readback through loop | Fake model returns compliant full responses; not evidence of live-provider adherence |
| `test_tool_response_offloader::...xml_extension` | Genuine XML data saved as .xml | **Keep this behavior**; it is not obsolete control syntax |
| CI comments and old negative prompt tests | Identify historical XML assertions | CI commentary references removed test/classes; absent `observation_marker`/`tool_call_1` assertions do not identify active protocol tags |

## Diagnostic results that change migration planning

| Probe | Observed result | Consequence |
|---|---|---|
| `answer_then_tool`, `tool_inside_answer`, `all_three` | Tool action wins; final/agent is not executed | Do not infer final-first behavior from loop-handler order. Literal code examples can be classified as control today |
| `malformed_tool_and_answer` | Missing parameters error wins over valid final | Recovery precedence must be explicitly decided |
| `two_unwrapped_calls`, `empty_collection` | First standalone only; empty first collection masks outside call | Batch syntax semantics are not full XML validation |
| `fenced_call`, `aliases`, `entities`, `parameter_text` | Fenced call accepted; aliases work; entities remain escaped; plain args become empty dict | Current prompt and parser differ; do not carry accidental string mutation into native values |
| `native_only_response` | ValueError for content=None despite tool_calls present | Provider/extractor adaptation required beyond deleting XML parser |
| `mcp_multi_content` | First text only, status success despite isError=True | MCP response contract needs explicit later verification |
| `normalization`, `single_spawn_normalized` | Comma text becomes list; `001` becomes 1; singleton array becomes dict; spawn wrapper raises TypeError | Direct spawn tests are insufficient; typed native inputs should not inherit all XML coercion |
| `load_tool_metadata`, `load_subagent_metadata`, `load_summary_metadata` | Unexpected-key TypeError in ToolCallMetadata | Compatibility/schema gap exists in present code |
| Real single-tool round trip / `continued_tool_session_context` | First run stores five records; next model receives only new system/query because history load failed | Reported history pairing behavior is conditional, not the actual ordinary stored-history path today |
| `five_parse_errors` | Five requests; null answer; state restored to idle | Bounded loop does not guarantee meaningful max-step outcome |
| `observation_false_and_error_status` | Error-status result with false data becomes `No output`, no status tag | Normalized semantics are lost in current XML projection |
| `context_splits_native_batch` | Remaining roles: system, tool, tool, tool, tool | Preserve strategies but adapt message validity/group handling for native protocol |

## Documentation/source disagreements

1. Public architecture says **per-tool timeout** and diagrams **guard then offload**. Batch runner uses one gather timeout and a separate pipeline timeout; common scrub follows offload and raw tool history writes. README also places offload differently in its schematic. [R33]/[R38]/[R39] establish actual order.
2. Base prompt says thoughts are not stored; ungoverned successful tool and configured-agent replies store complete model response. Default model telemetry suppresses content, which does not prevent raw XML/thoughts in conversation history. [R09]/[R19]/[R42]/[R61].
3. Retired-code finding only: base final answer prohibits XML; RouterAgent explicitly requires nested routing XML. [R09]/[R17]. Parser accepts nested XML, so the contradiction is in instructions rather than an enforced ban.
4. Repair prompt presents thought-only as valid; parser returns error for it. Fenced output prohibited by instructions but accepted by regex. [R18] and diagnostics.
5. `BaseReactAgent.run` docstring says JSON communication; active control is XML and only the intermediate action list is JSON. `AgentMessageHistoryLoader` describes clean paired replay, but actual stored metadata fails validation first. [R04]/[R26]/[R62].
6. Subagent public typing says dict in facade/run kwargs, while registry/reflection/resolution iterate actual agent objects in a list. Cookbook uses lists. Configured runner has no parent `subagent.spawn` authorization/narrowing, unlike the generalized governance subagent policy and the dynamic factory. [R01]/[R07]/[R42]/[R44]; engineering governance spec Subagent Policy.
7. Dynamic factory reports output “saved” based on response heuristics; it does not check workspace. Public/general subagent workspace claims describe a prompt expectation, and do not describe the configured runner's guarantees. [R44].
8. Standalone OffloadConfig default true versus public AgentConfig default false is an API-layer difference, not a blanket claim that default public runs offload. [R03]/[R05].
9. Governance spec describes a batch-level authority request in addition to per-tool requests; ToolBatchRunner authorizes per resolved tool (including multiple workspace targets), without a distinct batch authority request. This is separate from protocol migration. [R33]/[R34].
10. Public `docs/core-concepts/sub-agents.mdx` advertises `call_sub_agent`, but no such tool is registered in source: configured delegation uses special XML agent calls. Public workflow examples pass unsupported `model_config` to sequential/parallel constructors and `task` to their run methods, omit required router `agent_config`, and omit explicit initialization. Current cookbook workflows use the actual lifecycle/signatures. The advertised call_sub_agent remains a retained-subagent documentation defect. The workflow page/examples are slated for deletion, not repair or compatibility work.
11. CI's commented broken-test catalog mentions old classes/methods absent from current tests. Executed current tests passed; the comments are not current failure evidence.

## Unresolved compatibility questions and specific next investigations

| ID | Question / remaining uncertainty | Next investigation before implementation or removal |
|---|---|---|
| U01 | Which XML-era storage shapes exist in deployed sessions? No real historical database was available | Obtain sanitized representative records from each backend and custom integration, including summaries/child calls/redacted calls; run loader/translator diagnostics without deleting them |
| U02 | How will native provider responses differ across ten public providers/Cencori, and which support simultaneous text/tools/stream usage? | Build offline fixtures from documented SDK objects, then a credentialed provider matrix after design approval; inspect name/schema limits, finish reasons and chunk sequences |
| U03 | Which external callers use BaseReactAgent compatibility methods, ParsedResponse, prompt exports, custom builders, or llm_call_sync? | Search downstream usage of retained runtime helpers/builders/history providers. This does not reopen compatibility for the three retired workflow classes: their exports and aliases are to be absent |
| U04 | Should parser quirks and text-based child failure classification remain compatible? | Decide expected behavior for mixed text/actions, empty answer, malformed args, final-step errors, singleton arrays and dynamic partial status; turn approved semantics into boundary tests |
| U05 | How should historical and native call groups survive existing context strategies? | Exercise actual persisted multi-tool groups and argument-only assistant calls under each memory/context strategy; define validity/summary input adaptation and token accounting |
| U06 | Which child events should a parent run/session stream expose? Current children create separate traces/stores and dynamic sessions | Deterministic integrated configured/dynamic trace test using shared versus default stores; inspect parent trace/run IDs and SSE filters; then define aggregation/correlation contract |
| U07 | Configured delegation lacks dynamic parent's governance/timeout path | Test configured parent policy plus child independent policy and cancellation. Decide adaptation explicitly; do not assume ordinary native tool conversion preserves equivalent authority |
| U08 | Partial timeout may produce both success and timeout tool records; dynamic independent child cancellation handling is incomplete | Controlled two-tool/child delay tests with cancellation at each boundary, inspect stored IDs, background completion and parent state |
| U09 | Native result privacy/offload ordering may expose different content | Trace large untrusted local/MCP/child outputs through raw memory, artifacts, telemetry, guard and reconstruction with sentinel payloads; decide ordering separately from representation |
| U10 | Current telemetry store drops a full subscriber silently, while SSE has explicit overflow failure | Stress fake high-rate events to verify replay/catch-up/stall behavior before using token-sized events; inspect Jsonl store fanout in a multi-process deployment |
| U11 | Background reconstructed agents include MCP config but manager/supervisor/spec reconstruction do not explicitly connect MCP or restore process-local tools/custom builders/configured children | Offline restart test with a reconstructed MCP agent, explicit lifecycle setup, and registered local-tool references; document supported registration modes before changing model interface |
| U12 | Legitimate task XML can collide with history sentinel or action examples | End-to-end XML read/write/discussion fixtures, including `<observations>` user content and literal tool-tag examples; distinguish message type using an approved explicit representation |

## Existing defects kept separate from implementation scope

**Migration-critical evidence gaps / defects to account for:** actual history metadata mismatch; singleton dynamic-spawn normalization; content-only native response rejection; call-group splitting/content-only summary input; configured delegation bypass. The router second parser is excluded from migration requirements because it will be deleted under M7. These directly constrain safe replacement. They were investigated and documented, not fixed.

**Adjacent defects, not authorization for broad repair:** falsy observation loss; MCP isError/multipart loss; partial dynamic status treated as business data by ToolExecutor; tool-name attribute escaping; skill catalog descriptions interpolated without escaping; offload-before-common-guard and replay of pre-guard raw tool data; all-parse-errors null result; current provider exception swallowing/decorator bypass; event-loop-blocking sync tools/subprocesses; dynamic cancellation edge; configured metadata missing parent agent identity; silent telemetry subscriber removal on overflow; background MCP reconstruction lifecycle gap. Some will need decisions to define a new contract, but this package makes no fixes.

**Defects in retired code—delete, do not repair:** parallel failure aggregation missing agent_name, workflow shielding/cancellation behavior, router prompt conflicts and invalid public workflow examples. These no longer create migration blockers or follow-up design requirements.

Two additional source observations are not migration blockers: skill path containment uses string-prefix comparisons (potential sibling-prefix weakness; no exploit attempted), and context token-budget guarantees are soft when preserved recent/system messages or an oversized summary exceed the budget. These deserve separate issue triage, not incidental changes here.

## Coverage conclusion

The identified executable tag families have traced producers and consumers in the core parser, configured-subagent path, observation/history return path and router workflow. The inventory also covers optional prompt contracts, normalized indirect consumers, storage/context/offload and public/background/event interfaces. XML task data is explicitly separated from control. Tests and diagnostics establish concrete behavior at this snapshot, including gaps hidden by isolated fixtures.

The decision to delete all three workflow classes and their examples with no fallback is settled; it is not an unresolved compatibility question. Remaining retained-runtime uncertainty is bounded in U01–U12. This is a repository discovery package suitable for migration design review, not a claim of complete downstream compatibility, provider verification, production-backend testing or streaming readiness. Stop here for review; no XML replacement, native control implementation or streaming was added.

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
