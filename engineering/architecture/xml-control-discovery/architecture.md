# A. Architecture walkthrough: XML-controlled OmniCoreAgent

Discovery only. Source snapshot: local `main`, commit `60da57a6dacd3f7796fffd9d198c0a7420aa6aad`, investigated 2026-09-13 in the separate `omnicoreagent-xml-discovery` worktree. No remote fetch was performed; “main” means the local branch at that commit. Production code is unchanged. See [inventory](inventory.md), [migration map](migration-map.md), and [coverage and evidence](coverage.md).

## Settled scope decision: remove the workflow classes completely

The retained product scope is normal OmniCoreAgent runs, its deep-agent capabilities, and background execution for long-horizon tasks. On this snapshot, deep-agent capabilities are implemented within OmniCoreAgent; there is no separate DeepAgent class.

**RouterAgent, ParallelAgent and SequentialAgent are to be deleted completely**, together with their package/root exports, workflow examples, documentation/navigation and any dedicated tests or references. No fallback implementation, compatibility alias, deprecation wrapper or replacement workflow framework is required. Their behavior is not a migration preservation requirement. Core parallel tool batches and configured/dynamic subagent capabilities remain part of the retained agent runtime.

References to these classes below describe the source snapshot and establish deletion boundaries only. M7 is a removal unit; X34–X35 are removal-only inventory entries. Historical conversation data remains data: retaining stored records does not justify retaining an executable router or workflow fallback. This update changes the discovery findings only; source/example deletion has not yet been performed.


## How the runtime fits together

`OmniCoreAgent` is the public facade, lazily exported from the package root. `run(query, session_id=None, run_id=None)` creates identifiers and telemetry, initializes resources, screens the user query, builds the runtime prompt, and awaits `ReactAgent.run`. Initialization normalizes dict/dataclass configuration, chooses the memory router, creates the guardrail and optional governance engine, selects a direct `LLMConnection` or the connection owned by `MCPClient`, and wires runtime tools and dynamic subagents. `ReactAgent` passes configuration to `BaseReactAgent`, which constructs the loop, response parser boundary, tool resolver/batch/observation/failure handlers, configured-subagent runner, history loader, context manager, and offloader. These are collaborating services, not independent agents. [R01] [R02] [R03] [R04]

The public defaults matter: workspace files **on**, full input/tool guardrails **on**, max steps 15 (base loop imposes minimum 5), tool timeout 30 seconds, sliding-window session memory 10,000 messages, summarization **off**, active context management **off**, tool offload **off**, advanced retrieval **off**, dynamic subagents **off**, skills **off**, governance **off**. The standalone `OffloadConfig` defaults to enabled, and directly constructing `BaseReactAgent` without its public configuration can therefore behave differently. `mcp_enabled` does not control connection construction: nonempty `mcp_tools` does. A normal facade `run()` does not itself connect MCP; applications use `connect_mcp_servers()`, while serving/workflow/subagent entry points handle lifecycle around it. [R03] [R04] [R05]

The central interaction is:

```text
retained application / serving / background
  -> OmniCoreAgent.run
  -> prompt + runtime registry + reconstructed history + current user query
  -> BaseReactAgent loop
     -> optional context reduction -> awaited provider completion
     -> extract text and usage -> regex XML classification
        -> final answer -> store stripped answer -> return public result
        -> tool action -> JSON action list -> resolution -> generated IDs
           -> record assistant request -> authorize -> concurrent execution
           -> persist individual tool outputs -> normalize/offload/record signatures
           -> guard output -> XML user observation -> next model call
        -> configured agent action -> child runs -> special XML user observation
        -> parse error -> repair instructions as active user message -> next call
```

## Prompt construction and configuration contracts

`LazyDefaultPromptBuilder` instantiates `OmniCoreAgentPromptBuilder(REACT_AGENT_PROMPT)`. Its `build` wraps the application instruction in `<system_instruction>` and appends the base prompt. A custom `prompt_builder` can replace that surface, but the parser remains XML-only; replacing the instructions alone does not switch protocols. Runtime initialization then prepares the **same executable registry** for prompt rendering and execution. Initial message preparation concurrently renders tools and loads history, with a 20-second timeout and exception-to-default handling; it inserts the assembled system message at index zero. The current user query is stored without the active `[CURRENT_DATETIME: ...]` prefix. [R06] [R07] [R08]

| Fragment | Definition / inclusion | Model requirement and runtime consumer |
|---|---|---|
| Application instruction | `OmniCoreAgentPromptBuilder.build`; every default prompt | `<system_instruction>` is prompt organization only, read by the model; no runtime tag parser consumes it. |
| Base contract, all examples and final rules | `REACT_AGENT_PROMPT`; default builder always | `<thought>`, single `<tool_call>` or batch `<tool_calls>`, nested `<parameters>`, `<final_answer>`; model interprets `<observations>` and `tool_name="name#n"`. Parser consumes calls/finals; thought extraction helper has no runtime caller. |
| Tool schemas | `ToolPromptRenderer.render`; every initial prompt | Plain `name: description` plus types/required fields/examples teaches XML argument names and embedded JSON arrays/objects. Schema text is not native provider `tools`. |
| Advanced discovery | `tools_retriever_additional_prompt`; advanced flag **and** rendered `tools_retriever` name | Single/batch XML discovery examples, search-before-inability guidance, then read returned tool descriptions. Registry hides MCP and non-always-visible local schemas, but resolver still permits them. Actual config is `enable_advanced_tool_use`; `<activation_flag>use_tools_retriever</activation_flag>` is not a separately read config field. |
| Configured subagents | `build_subagents_additional_prompt` and signature registry; truthy `sub_agents` regardless of dynamic flag | `<agent_call><agent_name>...` or `<agent_calls>`; runtime reflection lists `agent.run` parameters. Separate configured runner consumes parsed agent lists. |
| Dynamic subagents | Same builder, dynamic branch; `enable_subagents` **and** visible `spawn_subagents` | Normal XML tool call with `subagents_json`; read output paths with `read_file`. Child gets focused role/task/output instructions, default base contract, inherited config, workspace on and recursive spawn off. |
| Workspace | `workspace_files_additional_prompt`; workspace flag (also forced by dynamic subagents) and visible `ls`, `read_file`, `write_file` | XML write examples; files/artifacts distinction, path and read-before-edit guidance. Ordinary local tool execution consumes arguments. |
| Artifacts | `artifact_tool_additional_prompt`; offload enabled and visible `read_artifact` | Interpret `[TOOL RESPONSE OFFLOADED]`, use artifact tools via XML, keep returned artifact data inline. `<activation_flag>tool_offload_enabled</activation_flag>` is descriptive. |
| Skills | `agent_skills_additional_prompt`, `SkillManager.get_skills_context_xml`; enabled, discovered nonempty skills context and both skill tools visible | Read `<available_skills><skill><name>/<description>/<location>` as a catalog. XML examples for reading `SKILL.md` and running scripts. Runtime does not parse the catalog XML back. |
| Parse recovery | `xml_parser._xml_shape_error/_missing_xml_error`; unrecognized response | XML examples plus first 200 response characters. User-role active message; not persisted. Missing-name/parameters and invalid-JSON errors use shorter messages. |
| Wrong invocation | `ToolCallResolver.build_sub_agent_tool_error`; a configured agent is named as a tool | Teaches `<agent_call>` instead of `<tool_call>`. Goes through tool error observation escaping and back to model. |
| Loop recovery | `ToolFailureHandler.handle_loop_state`; signature detector fires | Replaces system prompt with stuck guidance and appends user instruction explicitly requiring `<final_answer>`. No hard immediate termination. |
| Routing workflow — DELETE, snapshot evidence only | `RouterAgent._build_router_system_instruction`, retry prompt | Requires `<routing><agent>/<task>` **inside** `<final_answer>`; outer base parser then router regex consumer. |
| Summaries | `FAST_CONVERSATION_SUMMARY_PROMPT` via loop/facade callbacks | Complete plain-text summary, not XML action. `SUMMARIZER_MEMORY_CONSTRUCTOR_PROMPT` has XML organizational tags but requires JSON; exported, no in-repository runtime caller. |

Sources: [R06] [R07] [R09] [R10] [R11] [R12] [R13] [R14] [R15] [R16] [R17]. An exhaustive lexical prompt-tag appendix is in [evidence/tag-catalog.md](evidence/tag-catalog.md): organizational tags are model-facing prompt syntax, not additional runtime actions.

Prompt disagreements are material. Base final-answer rules ban XML inside answers, but routing requires it. Base claims thoughts are not stored, yet successful ungoverned tool requests persist the **whole response**, including thoughts. Parse repair describes thought-only as a valid intent, but thought-only cannot continue successfully as its own action. Prompts prohibit markdown fences, while regex matching accepts a fenced call. Base forbids XML inside parameter values and answers, unnecessarily constraining legitimate XML task content. These statements describe current prompts, not requirements to preserve. [R09] [R10] [R18] [R19]

## Provider response to state transition

`LLMConnection._completion_params` serializes messages and sends model/temperature/max_tokens/top_p. It can accept native `tools` and set `tool_choice="auto"`, but **none of the repository runtime LLM callers supplies that argument**. `AgentLlmStepRunner` calls `llm_call(messages)`, as do its summary callback, the facade summary callback, and router capability summarization. Normal requests use LiteLLM `acompletion`; Cencori uses OpenAI-compatible `AsyncOpenAI.chat.completions.create`. There is a sync adapter too, without an in-repository runtime caller. No path asks for `stream=True`. OpenRouter without tools gets the legacy stop string `\n\nObservation:`. [R20] [R21]

The LLM step checks usage limits and context before calling, awaits the full object, records telemetry/usage, then calls `extract_response_content`. Only first-choice `message.content` survives; direct `message`, `text`, `content`, dict and string shapes are also accepted. Non-string content is `str(...)`, not a preserved multimodal structure. Native tool-call IDs, tool-call arguments, finish reasons, other choices, and reasoning fields are not forwarded to control logic. Usage preserves request/completion/total token counts. A native-tool-only object with `content=None` fails extraction. Adapter exceptions are logged and returned as `None`; exceptions that escape the LLM step become a generic answer, while usage-limit failures carry a resource-halt trace status. The adapter's catch prevents most provider errors from reaching its retry decorator. [R20] [R21] [R22]

`parse_action_or_answer` uses case-sensitive, exact-tag, DOTALL regexes, not an XML library. Ordering is **tool calls → agent calls → final answer → error** across the entire text. It does not respect textual order or nesting. Thus a tool block inside a final-answer example, or after an answer, takes precedence. A malformed recognized tool block returns an error before considering a valid final answer. `AgentLoopStepHandler` checks `answer` first, but the parser never sets both; the parser's ordering establishes actual mixed-response precedence. [R18] [R23]

For each call type, a complete collection selects only its first collection body and all matching item blocks inside. Without a complete collection, only the first standalone item is selected; two unwrapped items do not form a batch. Empty complete collections can hide standalone items outside them. Unclosed collections may fall back to a complete inner standalone item. Self-closing/attributed/uppercase tags are not canonical matches. The parser accepts aliases `<name>` for tool/agent name and `<args>` for parameters, preferring canonical names when both appear. A missing name or parameters block is an error; empty parameters are allowed. [R18]

Arguments that start/end with `{}` go through `json.loads`; invalid JSON here is fatal. Otherwise regex collects `<word>value</word>` pairs, last duplicate key wins, and embedded arrays/objects are JSON-decoded when valid. Scalar values initially stay strings. Plain parameter text silently becomes `{}`. There is no entity unescape, XML namespace support, recursive element model, XML repair library, or schema validation at this stage. Tool actions are serialized to a JSON string in `ParsedResponse.data`, deserialized again by `parse_tool_actions`, checked for nonempty string name and dict parameters, then deeply normalized. Normalization converts boolean/null/numeric strings, JSON/Python literals, comma-separated strings, and singleton dict lists without consulting the schema. Configured agent arguments do **not** pass through this second normalization. [R18] [R24] [R25]

The loop increments one step per model attempt, not per tool or child. Valid final answers, including empty strings, store the extracted content and finish before the max-step check. Valid actions execute even on the final allowed step, then can return the max-step marker. Parse errors append active user repair instructions and return early from the handler, bypassing its max-step result; five malformed replies at the minimum limit exit the outer loop with `answer=None`. `STUCK` is advisory: loop condition excludes only `FINISHED`, so recovery gets another model attempt if budget remains. `TOOL_CALLING` exists in the enum but is not assigned in this tool execution path. `OBSERVING` is assigned after ordinary observations; configured delegation does not assign it. The session context manager restores its previous state in `finally`, even after setting finished/error. [R04] [R23] [R26] [R27]

## Tools and observations: the complete round trip

Local `ToolRegistry` stores functions/schema/descriptions by lowercase name; decorators infer a simple schema when absent. `Tool.execute` matches Python signature parameters, fills defaults, rejects missing required parameters, ignores extra supplied keys, and does not validate JSON Schema types. Async functions are awaited; sync functions run directly on the event loop. Workspace/artifact tools are registered in the same registry, with runtime-owned provider markers. Skills are local tools with path checks and subprocess handling. Reserved workspace names are checked before registration. [R11] [R28] [R29] [R30]

MCP lifecycle connects configured stdio/SSE/Streamable HTTP transports and calls `session.list_tools()`, retaining `available_tools[server]` under the MCP-reported `serverInfo.name`; a changed reported identity is reauthorized before registration. Resolution first rejects exact configured-subagent names used as tools; then MCP lookup (case-insensitive), then local lookup. `tools_retriever` specially bypasses MCP. Duplicate MCP tool names across servers yield an error: server-qualified invocation is explicitly unsupported. The registry prompt omits server identity, but the resolved `ToolCallResult` carries `tool_provider="mcp"` and `tool_server`; governance and telemetry need both. Any unresolved member causes the resolver to reject the **whole batch before execution**. [R12] [R31] [R32]

`ToolBatchRunner.start` assigns a UUID per call, constructs OpenAI-style metadata (`has_tool_calls`, `tool_calls[].id/function.name/function.arguments`, first `tool_call_id`, `agent_name`), persists assistant text, and appends assistant text **without native call metadata** to active messages. Without governance that text is the raw XML reply; with governance it is a redacted textual description and redacted argument metadata. `asyncio.gather` executes the resolved batch and returns outputs in input order. It is under one batch-wide timeout, followed by a separate observation-pipeline timeout. Individual completions may already have persisted results/emitted telemetry while another tool is running. [R19] [R33]

Before each execution, optional governance derives authority requests from normalized arguments, resolved provider/server and tool identity. Workspace/artifact capabilities add scoped resource requests; skill tools currently use the generic local-tool authority target, not a separate skill-script resource request; MCP connection has a separate authorization boundary. Denial returns a recoverable error with redacted arguments, never executes the tool. No XML parser is inside governance. The policy engine and configured sandbox contracts must still receive equivalent structured authority inputs after migration; main does not contain the other branch's `sandbox/execution.py` service. [R33] [R34] [R32]

`ToolExecutor` converts Python results to `{tool_name,args,status,data,message}` and persists one tool-role record with UUID/name/args/agent metadata before observation offload/scrubbing. Plain dicts generally remain business data; only recognized result envelopes are unwrapped. Exceptions become error results. Falsy scalar output becomes error, while structured-envelope success with no data gets an explanatory message. MCP response handling is lossy: for objects with `.content`, only `content[0].text` is used; `isError`, later content blocks and structured content are not interpreted. MCP guardrail checks concatenated text **before** that flattening. [R35] [R36]

`parse_tool_observation` is misleadingly named for XML migration: it parses JSON/dicts, current `tools_results`, legacy `successes/errors`, or a single result. It decodes JSON data strings and computes success/partial/error. It does **not** parse the model-facing XML. Next, `ToolObservationFormatter` offloads large `data` values (unless resolved workspace/artifact provider), mutates `data` to a preview reference, creates a plain-text display/telemetry observation and records loop signatures. Only the first result for each tool name in a batch is recorded in loop detection. Then `ToolObservationHandler.append_observations` scrubs result `data/message`, builds XML, appends it as a user message to active context and persistent history, and sets `OBSERVING`. [R37] [R38] [R39]

The next model call receives the prior raw/redacted assistant text and a user message shaped as:

```xml
<observations>
  <observation tool_name="ping#1">{"x":1}</observation>
</observations>
```

Observation body text is escaped and dict/list content is compact JSON. `name#n` is a per-block display counter, **not** the UUID. Status, arguments, provider/server and UUID are omitted from the XML; tool identity/grouping are carried by `tool_name` and the wrapper. Errors rely on body text rather than an explicit error attribute. `data or message or "No output"` loses false/zero/empty data. Tool-name attributes are not escaped. XML is therefore more than decorative packaging, but is also a lossy projection of a richer result. [R40]

Loop detection hashes strings of tool name, normalized arguments, and formatted output; it does not hash the XML wrapper. Offloaded output includes time-bearing artifact IDs, so representation can affect signatures indirectly. Governance redaction also changes argument strings. Common output guardrails inspect content values, not the wrapper. They execute **after** raw tool persistence, offload and observation telemetry; MCP additionally checks at its boundary. Configured child observations bypass the common guard/offload/signature path entirely. Migration must preserve these boundaries deliberately, and review the existing order rather than assuming the diagrams describe it. [R38] [R39] [R41]

## Retained delegation and retired workflow contracts

Configured delegation uses parser-produced JSON agent lists directly in `SubAgentCallRunner`, bypassing tool resolution, UUID assignment, ordinary batch timeout, common observation handling and parent tool governance. It persists the raw assistant XML with `agent_calls` metadata (without `agent_name`), matches child names exactly, forces the parent's session ID, filters arguments through the child `run` signature, connects MCP when needed, awaits child `run`, and cleans up MCP. Children are existing application objects with their own instructions/configuration/memory; parent messages are not copied. Results are gathered in request order; errors become observations, cancellation is re-raised. A dict result contributes `response`, else `output`, else its string; `metric: Usage` is aggregated. Error-sounding response text is still success unless the child raised. [R42] [R43]

Its user observation has the sentinel `OBSERVATION RESULT FROM SUB-AGENTS`, `<observations><observation><agent_name>...<status>...<o>output</o>` or `<e>error</e>`, then `END OF OBSERVATIONS`. Values are escaped. This exact sentinel also controls history filtering. Parent telemetry records spawn/result/error spans; children create their own traces/stores unless the application has shared them. No automatic child workspace write is enforced on this configured path. [R42] [R43]

Dynamic delegation is the **ordinary tool** `spawn_subagents`. The factory inherits model, MCP, local tools excluding spawn, memory router, and config; caps child steps at 15, disables recursive spawning, forces workspace on, and derives narrower policy under governance. Child `run(str(task))` gets a new session ID, not the parent session. Output writing is a prompt instruction; runtime does not verify the file exists. The wrapper returns a 500-character summary/path and counts, using error phrases/response length to classify child failures. Multiple specs use gather; the aggregate can say `partial`, which the general ToolExecutor does not recognize as a standard success/error envelope. Parent continues through normal tool observation and is instructed to read output paths. A single-spec array is currently broken through XML normalization (diagnostic), despite wrapper tests passing for direct list/string calls. [R44] [R45]

**The following two paragraphs document code to delete, not behavior to preserve.** Sequential/parallel workflows are application orchestration, not parser actions. `SequentialAgent` initializes children, uses one session ID, awaits each public run and forwards its `response` as the next query; retries exceptions, not error-text results. `ParallelAgent` schedules one public run per child and returns a mapping by `agent_name`, after all finish. It shields child runs from outer cancellation. Its exception fallback lacks the `agent_name` later required for aggregation, a separate existing defect. Neither adds workspace records or a workflow trace itself; child runs do. [R46] [R47]

`RouterAgent.initialize` directly asks each child LLM for a plain capability summary, builds a registry and an internal `OmniCoreAgent`. Its internal run extracts outer `<final_answer>` and persists the routing XML as answer content. The outer workflow regex searches that result for `<agent>` and optional `<task>`; it does not validate `<routing>` or exactly-one semantics. Invalid decisions retry with an XML agent-name list. A chosen child's public run is shielded and retried on exceptions. No choice returns an error dict plus the original task. This entire routing path will be deleted under M7, including its second XML parser, capability-summary calls and retry prompt. It imposes no native-routing requirement on the retained final-answer contract. [R17]

## Persistence and context requirements, answered explicitly

| Question | Current behavior and implication |
|---|---|
| Raw XML or parsed values? | Both for successful ordinary tool calls: raw assistant XML (redacted text under governance), parsed/normalized call metadata, individual tool results, and formatted XML observation. Finals store stripped answers. Parse repair is active-only. Configured delegation stores raw XML/agent list and XML result, using different metadata. |
| Distinguishable requests/results? | Roles and `has_tool_calls/tool_calls/tool_call_id` distinguish ordinary exchanges; UUID associates them. XML display `name#n` is separate. Configured calls/results have `agent_calls`/`sub_agent_results`, no UUID pairing. |
| Reconstruction dependencies? | MemoryRouter changes backend `msg_metadata` to `metadata`. Loader validates every record first, then drops user content starting with `<observations>` or the child sentinel; it reconstructs ordinary assistant/tool protocol dictionaries only when expected UUIDs are present. Normal user/assistant content becomes `Message` with metadata removed. |
| What actually reloads? | Text-only history reloads. Real tool metadata contains `tool` and `args` rejected by `ToolCallMetadata`; subagent-result and summary keys are also rejected. Because validation builds the whole list before applying any record, the initial-preparation gather catches the error and loads no history. Existing pairing tests use simplified accepted metadata. This is verified with a real in-memory tool round trip. |
| Can truncation split related records? | Yes. Session sliding windows/token budgets select individual messages; summaries replace arbitrary older messages. Active context preserves all system messages and last N others, with no call-group awareness. A diagnostic leaves four orphan tool messages after truncating one native-style five-call batch. |
| Summary representation? | Both summary callbacks render `role: content` only, omitting call metadata/IDs. Session summaries are user messages with `[CONVERSATION SUMMARY]` and history-summary metadata; active summaries are user dicts with `[CONTEXT SUMMARY]` and `msg_metadata`. Neither is an action parser input. Native argument-only calls would lose meaning in these content-only renderers unless adapted. |
| Persistence retention? | In-memory, SQL, Redis and Mongo store role/content/metadata/lifecycle fields and invoke shared summarization logic on retrieval. Configured `keep` marks summarized records inactive; `delete` removes them. Summaries are persisted asynchronously (task/thread), so returned working history and durable updates have different timing. No discovery operation deleted history. |
| Offload contents? | Full tool **data**, serialized by `str` for non-strings, is saved before XML body escaping, with argument/session metadata. The preview retains artifact ID/path/tool identity and read instructions. Original tool-role history still contains full output, so a working reconstructed session could reintroduce it. Artifacts themselves do not encode executable control. `.xml` extension detection/readback is legitimate data support and must remain. |
| Can old control syntax execute again? | Stored text is not directly re-parsed as a new action. It can influence a new model response; any echoed control tag can then match, even inside an answer or code fence. User task XML beginning `<observations>` is falsely treated as transient by the current loader. |
| Compatibility decisions? | Decide versioning/translation for mixed XML/text/native records, redacted arguments, incomplete/duplicate tool results, summary content and metadata, subagent records missing agent identity, and artifact references across sessions/processes. Do not assume all existing records satisfy today's loader. Preserve context strategies while adapting their representation inputs/outputs. |

Sources: [R19] [R26] [R35] [R42] [R48] [R49] [R50] [R51] [R52] [R53]. See the diagnostics and unresolved decisions in coverage.

## Public results, events, cancellation and streaming constraints

The normal inner result is `{answer, usage}`; the facade maps it to `{response, session_id, agent_name, metric}` and adds `trace_id/run_id`. Blocked input returns safety text and `guardrail_result` without normal metric. Facade emits `final_answer` only after the loop returns. Returned generic model-error text, parse exhaustion (`None`) and step-limit markers can still be marked completed unless a special trace status was supplied. Serving normalization keeps a fixed subset of fields, dropping extra fields such as guardrail details. Background success is driven by whether the awaited run raised; `result_preview` is the first 1,000 characters of `response`, not a parse of inner status. [R01] [R54] [R55] [R56]

OmniServe `POST /run` is already SSE, but streams **telemetry events**, not tokens. `POST /run/sync` awaits a whole result. `run_agent_stream` starts an event pump and agent task concurrently, filters by run ID, catches up from the starting cursor, deduplicates event IDs, and emits `complete` after the final result. `/events/{session_id}` replays then follows telemetry; background routes expose lifecycle/run inspection. The SSE queue holds 1,000 events; the in-memory telemetry subscriber also has a 1,000-event queue and is silently unregistered if full; overflow is a stream error. Replay has a 10-second bound, cancellation draining 2 seconds, and response headers include `X-Accel-Buffering: no`. [R57] [R58] [R59]

Before completion of a model call, clients can see session/user/memory/context/step/model-call lifecycle events (payload visibility depends on telemetry configuration). They cannot see model text or decisions until the adapter returns a full response. Individual tool results can appear while other batch tools run, but the model waits for the combined batch. Configured and dynamic delegation similarly gather results; child result/public-summary interpretation requires completed child runs. [R21] [R33] [R42] [R44]

Telemetry records are typed JSON, not parsed XML. Raw XML appears in `model_response.content` only when `record_model_responses=True` (default false); raw assistant tool XML is stored in conversation memory by default. Tool and observation telemetry is on by default, carrying normalized results/plain display observations; tool content or router results may themselves contain XML. Error repair text may appear in tool-error events. Redaction is keyed structured-data redaction, not an XML-content scrubber. `max_payload_bytes` can replace payloads with truncation/reference envelopes; telemetry's reference generation is distinct from tool artifact offloading. [R60] [R61]

Removing XML leaves these additional streaming constraints: no adapter iterator/interface or stream parameter; content-only first-choice extraction; completion-only usage handling; one fully classified response per step; complete-response history writes; no call-delta assembly/IDs; batch and child aggregation; content-only summaries; telemetry payload suppression/size/ordering/cursor semantics; SSE queue/catch-up behavior; public final-result consumers; blocking sync tools and captured subprocess output; cancellation ownership. These are retained-runtime migration concerns, not implementations proposed here. Retired sequential/parallel/router wrappers and their aggregation/shield behavior are excluded; their removal is M7.

Cancellation reaches facade as `CancelledError`, records cancelled final state and re-raises. Tool batch cancellation is not caught by `except Exception`; async timeout translates cancellation into timeout error results, potentially duplicating already-completed tool records. Configured children explicitly clean up/re-raise cancellation. Dynamic cleanup is in `finally`, but its aggregate checks `Exception` rather than `BaseException`, leaving independently-cancelled child results a question. The retired parallel/router shield can keep children running after caller cancellation; delete that path rather than adapt its cancellation behavior. SSE finally cancels run/pump tasks; background supervises agent tasks with leases, heartbeat, retry, timeout and cancel flags. Sync Python tools/subprocess calls can block timely cancellation. [R01] [R33] [R42] [R44] [R47] [R58] [R56]

## Representative end-to-end scenarios

| Scenario | Verified source path and alternate branches | Evidence |
|---|---|---|
| Text-only answer | User -> provider full text -> `<final_answer>` regex -> extracted answer active+stored -> FINISHED -> facade response/final event. Untagged prose is **not** accepted; empty tagged answer is accepted. | `test_base`, `test_run_outcome`, diagnostic `plain`; [R18]/[R27]/[R54]. |
| Single local tool | Registry schema in prompt -> XML call -> JSON list -> normalized args -> local resolution -> UUID/assistant write -> function -> tool write -> normalized result -> optional offload -> guard -> user XML -> second model call. | `test_base`, `test_real_application_smoke`; diagnostic `single_tool_active_context` and stored shapes. |
| Multiple tools | First `<tool_calls>` -> all contained calls -> resolve all or reject all -> UUID each -> gather in input order -> result records as tasks finish -> one XML block. Same-tool display counters reset each batch; UUID persists. | `test_base::test_act_records_one_tool_call_id_per_parallel_tool`, `test_tool_batch_runner`. |
| MCP tool | Connect/list server tools -> bare name prompt -> case-insensitive MCP-first lookup -> server identity/governance -> session call -> MCP guard -> first content text -> common return. Duplicate names error; qualified names unsupported. | `test_client`, `test_tool_call_resolver`, `test_mcp_response_guardrail`, smoke fake MCP. |
| Workspace / skill | Config -> internal registry/provider or skills catalog -> normal XML tool -> authority requests -> file/subprocess wrapper -> common observation. Workspace/artifact output stays inline; skills may offload. | `test_tool_runtime_registry`, `test_workspace_files_backend`, `test_skills`, `test_tool_batch_runner`. |
| Configured subagent | `<agent_call(s)>` -> separate runner -> reflected args + forced parent session -> connect/run/cleanup -> escaped status/o/e observation -> parent model. Missing agent/required argument becomes child error observation. | `test_subagent_runner`; source [R42]/[R43]; persistence defect diagnosed. |
| Dynamic subagent | XML `spawn_subagents` -> ordinary resolution/normalization -> factory authorization -> new-session children -> prompted workspace writes -> paths/summaries -> tool observation -> parent reads outputs. One-spec normalization currently fails. | `test_subagents`, `test_subagent_governance`, diagnostic `single_spawn_normalized`. |
| Malformed response | Error -> user repair only -> consumes step -> retry. Missing blocks versus invalid JSON differ. All parse errors can exhaust with null answer. | `test_loop_step`, diagnostics `five_parse_errors`, `parameter_text`, `malformed_tool_and_answer`. |
| Tool failure / timeout | Exception -> normalized error and tool history -> observation -> continuation. Whole-batch timeout makes one error per requested tool; pipeline timeout preserves earlier tool history and substitutes errors for active context. | `test_tool_executor`, `test_tool_batch_runner` execution/pipeline timeout cases. |
| Mixed text/actions | Tool > agent > final, independent of order; raw mixed text retained on successful tool history, outside text discarded for final-only response. Fenced/nested call text can be acted upon. | Diagnostics `answer_then_tool`, `all_three`, `tool_inside_answer`, `fenced_call`. |
| Context during run | Pre-call threshold -> preserve all system + last N messages, optionally plain summary -> provider. Does not mutate persistent records. Offload happens before active XML append; may be followed by context truncation. | `test_context_manager`, `test_llm_step`, diagnostic orphan batch. |
| Continued session | Reset active state -> memory retrieval policy -> record validation -> transient filter/pairing -> prompt insertion -> new query. Simple text works; real tool/summary metadata fails wholesale history loading today. | `test_message_history` accepted shapes; real in-memory diagnostic demonstrates actual failure. |
| Serving/background | Serve lifecycle initializes/connects; sync/SSE await same facade, normalize results. Background manager/store/scheduler/supervisor reconstruct or reuse agent, prefix query/session/workspace/run IDs, await same run, save preview/lifecycle. | `test_omniserve_sse`, `test_omniserve_full`, `test_background_agent`; [R56]–[R59]. |
| Sequential/parallel/routing — DELETE | Snapshot behavior only; no preservation or fallback required. Application calls workflow; sequence forwards response, parallel aggregates complete child results, router uses a second XML parser on inner final. No native routing or token-stream workflow path exists; these workflow classes are now removal-only. | Source [R17]/[R46]/[R47]; cookbook/workflow examples; provider behavior not live-tested. |

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
[R17]: ../../../src/omnicoreagent/workflows/router_agent.py#L9
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
[R46]: ../../../src/omnicoreagent/workflows/sequential_agent.py#L7
[R47]: ../../../src/omnicoreagent/workflows/parallel_agent.py#L8
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
