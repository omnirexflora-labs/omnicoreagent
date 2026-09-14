# Native tool calling and streaming implementation plan

## Scope and execution rules

This is the implementation sequence authorized after the [XML discovery](xml-control-discovery/README.md). Source baseline: main `60da57a6dacd3f7796fffd9d198c0a7420aa6aad`; discovery commit `fdaa60e`. Work continues in the isolated `omnicoreagent-xml-discovery` worktree on `refactor/native-tool-runtime`.

Retain normal OmniCoreAgent runs, deep capabilities (configured/dynamic subagents, skills, workspace, offload), and background execution. Retain useful context strategies, storage engines, governance and telemetry; rewrite their faulty boundaries. Delete retired workflow APIs and unused implementations/examples. No XML execution fallback or retired-workflow compatibility layer. XML documents, SVG, and XML discussion remain valid task data. Stored conversations must not be erased.

For **every step**: inspect its callers and consumers; implement production code and meaningful tests together; run the stated acceptance checks; review the diff and cleanup; record the exact result here; commit; move to the next step. A failed check prevents completion of that step. Smaller commits within a step are allowed when they leave a coherent, tested boundary. Do not claim a stub, mock-only test, or unexecuted provider integration is complete behavior. Do not silently move to another step. Record justified changes to the sequence in the change log below.

Commits stay on this implementation branch. No merge or release is part of this plan. Routine continuation does not require another confirmation. External-provider checks are optional credentialed verification and must not block offline implementation or be reported as verified without execution.

## Ordered steps and acceptance gates

### 01 — Remove retired workflows [complete]

Delete RouterAgent, ParallelAgent, SequentialAgent, package/root exports, dedicated examples, public docs/navigation and remaining pointers. No replacements or aliases. Preserve ordinary parallel tool batches and subagents.

Evidence: `e349d0b`; 254 selected offline tests passed; package/export absence probe and JSON/diff checks passed. Historical discovery links point to the source snapshot.

### 02 — Repair current history metadata boundary [complete, preliminary]

Keep tool, summary, delegation and custom metadata as general stored data. Restore matching call/result IDs and exclude orphan/duplicate results. This is prerequisite repair, **not** completion of the native history/context work in step 07.

Evidence: `18639e9`; 226 passed, 2 external-store tests skipped; real batch execution → MemoryRouter → fresh agent run regression; Ruff/diff checks passed. This step was executed before this plan was written.

### 03 — Define shared runtime contracts and normalize provider responses

Dependencies: 01–02. Discovery units M1/M2/M8.

Define complete model turn, tool request, result association, terminal outcomes and stream events. Retain content, tool IDs/names/raw JSON arguments, finish reason and usage without flattening native calls. Keep summary-only text extraction explicit. Model requests must serialize only provider fields; internal metadata stays internal. Malformed responses fail explicitly; no call IDs invented while decoding provider replies.

Acceptance: object/dict provider-shaped fixtures, tool-only null content, text plus calls, multiple calls, complete multi-block text, invalid/missing IDs, finish reasons, usage, request-field filtering and summary behavior. Record contract decisions in this document. Commit tested boundary before wiring execution.

### 04 — Build native tool catalogs and exact argument resolution

Dependencies: 03. M3/M6.

Generate model schemas from local, MCP, workspace, artifact, skill and advanced discovery registries. Resolve exposed names to concrete provider/server/tool identities. Handle collisions and provider name restrictions deterministically. Preserve MCP ambiguity protection and trusted workspace authority. Decode JSON objects exactly once; remove heuristic string/list/number conversion from native execution. Register configured delegation as explicit native tools; retain dynamic spawning with a typed array schema.

Acceptance: strings `001`, `false`, comma text, nulls, booleans, singleton arrays and literal XML reach real tools unchanged; MCP discovery/collisions; reserved names; advanced discovery availability on subsequent requests; exact governance authority target. No tool effects on resolution/validation failure.

### 05 — Switch the complete agent loop, tools and delegation together

Dependencies: 03–04. M1–M4/M6/M10. This is an atomic runtime cutover; do not enable half of the new contract.

Pass schemas into actual provider requests; consume structured turns. Text with calls is intermediate assistant content; calls run and the loop continues. Text without calls is the answer. A length-limited/refused/empty/failed response is not silent success. Execute only complete valid calls, retain provider IDs, and produce one correlated result per requested call including validation/execution failures. Use structured assistant/tool messages in active context and persistence. Adapt configured and dynamic child execution, parent continuation and cleanup. Replace all default/optional XML control prompts and repair messages at the same time. Remove XML action/final/delegation parsing and obsolete wrappers/tests.

Acceptance: fake-provider → real run → real local execution → next request → final response; multiple same-name calls, mixed text/calls, malformed JSON, unknown tool, last step, tool-only response, configured/dynamic single/multiple children, failures and cancellation. Literal control-looking XML in answers or tool data must never execute. Run loop, prompt, registry, subagent and runtime suites. No executable XML fallback remains.

### 06 — Normalize results, safeguards and execution failure semantics

Dependencies: 05. M4/M6/M8.

Unify result status/data/error/ID before formatting or persistence. Preserve false/zero/empty values; honor MCP error flags and all supported content/structured blocks. Replace batch JSON/XML round trips with normalized records. Enforce governance before effects, guard output before model exposure, and persist the same approved/offloaded representation used in active context. Associate timeout/cancellation with pending calls exactly once, retain completed siblings, and prevent event-loop blocking by synchronous tools where supported. Keep loop detection based on normalized tool/argument/result values. Report child failures structurally, not by searching text.

Acceptance: business dictionaries, falsy outputs, MCP errors/multiple blocks, per-call mixed failures, partial batch timeout, cancellation, governance denial, prompt-injection rejection, offload/read-back and duplicate prevention. Verify tool result consumers and telemetry adapters together.

### 07 — Complete native history and context adaptation

Dependencies: 05–06. M5.

Make active and persisted interaction records reconstruct the same provider-valid exchanges. Version new interaction metadata; read old sessions as historical data without executing or reparsing XML. Use explicit metadata rather than content-prefix deletion. Keep complete call/result groups through all context strategies; summaries and token accounting include structured calls/results. Preserve offload references, agent/session boundaries and real task XML. Do not erase legacy rows or add a legacy execution path.

Acceptance: fresh-run/restart round trips with real writers; mixed-era sessions; literal XML user input; summaries and all truncation/token strategies cutting through batches; orphan/duplicate/cancelled rows; large offloaded output stays offloaded on reload. Exercise in-memory/SQLite and mocked Redis/Mongo serializers; label live-store skips.

### 08 — Make terminal outcomes consistent across public callers

Dependencies: 05–07. M8.

Keep existing useful public result fields and correlation IDs; add explicit success/error/cancelled/resource/step-limit termination. Map outcomes through facade, configured/dynamic children, serving serialization and durable background records. A returned error or exhausted loop must not become a successful background run. Preserve retry/session/workspace policy and reconnect configured MCP tools during reconstructed background runs when required.

Acceptance: complete success, provider failure, exhaustion, denial, timeout/cancellation; normal run and serving result agreement; background retry/state/restart tests; no duplicate terminal event or misleading success preview.

### 09 — Implement provider streaming and incremental assembly

Dependencies: 03, 05, 08. M1/M9.

Add provider stream interface for both adapter families. Assemble split/interleaved tool arguments by index/ID, retaining text deltas, usage and finish reason. Publish text before completion but never execute partial calls. Define failures before/after first visible output; do not silently retry and duplicate visible output. Close upstream streams on completion/error/cancel. Complete `run()` remains supported through the same normalized turn contract.

Acceptance: deterministic async fake streams; first text delivered while provider is blocked; split/interleaved calls; usage-only final chunks; malformed/truncated calls; refusal; cancellation/close; no duplicated output or premature tool execution. Verify request parameters against installed SDK interfaces; later live provider compatibility is separately recorded.

### 10 — Carry streaming through agent runs, telemetry and SSE

Dependencies: 06–09. M9/M8.

Expose a public agent stream with ordered deltas and one terminal result. Connect model deltas through the loop to serving SSE independently of raw debug trace payloads. Preserve run/session/trace/call IDs and distinguish child actors. Specify bounded buffering/backpressure, replay/deduplication and disconnect cancellation. Background consumers can observe events while durable completion remains based on the terminal outcome.

Acceptance: public stream yields before model completion; complete run/stream terminal parity; tool and subagent continuation; SSE live/replay order, queue pressure and disconnect; cancellation during text/arguments/tool/child; privacy and output truncation; background event and terminal state tests.

### 11 — Remove obsolete code and align all documentation/examples

Dependencies: 05–10. M10 and inventory X01–X44.

Audit each discovery dependency against its replacement/deletion. Delete unused parsers, converters, observation wrappers, dead aliases, obsolete tests and examples; do not retain files for nostalgia. Update normal/deep/background examples and public API docs for native tools, streaming and deliberate breaking changes. Preserve legitimate XML task support and historical discovery evidence, pinning removed source links to the baseline.

Acceptance: repository searches followed by caller checks; no control tags in active prompt contracts; all imports/exports and docs navigation resolve; retained examples smoke-test without live calls; no retired workflow references outside historical evidence/changelog.

### 12 — Final integration and review package

Dependencies: all previous steps.

Run the offline suite and build/lint checks appropriate to changed packages. Re-run the discovery scenario matrix against the new implementation: text, local batch, MCP, workspace/skill, both delegations, malformed turn, failures/timeouts, mixed text/calls, context pressure, continued session, serving/background. Include streaming visibility/cancellation and XML-as-data checks. Distinguish real behavioral tests from representation assertions. Resolve migration failures; separately record unrelated defects and genuinely unavailable integrations.

Acceptance: every retained scenario passes or has a specific external-verification limitation; every inventory row has a disposition; clean worktree; commits and exact validation results listed here. Summarize breaking changes and remaining provider-specific verification for review. Do not declare success merely because searches are empty or unit mocks pass.

## Contract decisions to implement

- Native tool calls control execution. Text is always content, including XML-looking text.
- A complete model turn retains assistant text, calls, finish reason and usage. Structured calls are never reduced to text for execution.
- Provider-assigned call IDs remain stable through execution, observations, history and events. Tool names may be mapped, but authority always uses the resolved concrete identity.
- Arguments are JSON objects. No guessing types from strings; malformed arguments become correlated call errors without tool effects.
- A call batch and its results form an interaction group for context selection. Operational metadata is not sent to providers.
- Mixed text/calls continues execution; final text is emitted only from a completed answer turn. Streaming intermediate text is labelled as such.
- Historical XML is readable data, never a second execution protocol. Existing storage is retained; retired workflow APIs are not.
- Streaming and complete-response APIs share execution and terminal outcomes, not separate agent loops.

## Execution log

| Step | Status | Commit / validation |
|---|---|---|
| 01 | Complete | `e349d0b`; 254 passed |
| 02 | Complete prerequisite | `18639e9`; 226 passed, 2 skipped |
| Plan | Recorded | Markdown/diff validation; see commit history |
| 03 | Complete | `2814590`; 47 passed (model protocol, response extraction, LLM adapter/step, import startup); Ruff/diff passed. Complete-turn decoder and provider-field serializer committed with this update. |
| 04 | Complete catalog boundary | `05fa347`; 50 passed (native catalog/protocol, resolver, registry/runtime registry); schema validation, exact execution arguments, collision mapping and per-catalog BM25 discovery covered. `uv lock` resolved 151 packages; offline attempt lacked cached build dependencies. |
| 05 | Complete initial cutover | `764f99c`; 93 passed (native runtime, base, LLM step, prompts, subagents, history, telemetry, imports). XML parser and dispatcher seams deleted. One import subprocess SIGSEGV occurred during overlapping test invocations; the sequential rerun passed all 93. Result/cancellation hardening follows in 06; broad fixture/docs cleanup remains in 11–12. |
| 06 | Complete result boundary | `2cfc7b0`; 153 passed (executor, registry, native runs, telemetry, output guardrails, subagent governance/factory, offloader). Falsy/MCP/partial results, timeout siblings, cancellation records and approved history covered. |
| 07 | Complete representation adaptation | `4114782`; 135 passed, 2 live-store skips. Real native batch round trips on in-memory and SQLite; context/group selection, summary/token accounting and generic storage tests. SQLite agent-name filtering fixed after the new round trip exposed TEXT/JSON containment behavior. |
| 08 | Complete outcome propagation | `95fa022`; 271 passed (runtime/outcomes, background, serve, LLM step and native runs). Returned errors fail background attempts; serving retains status/reason; missing provider usage still counts a request. |
| 09 | Complete provider stream | `41f13b2`; 45 passed (stream assembly, both adapters, complete turns and LLM step); Ruff/diff passed. Indexed fragments, usage-only tail, early text, upstream close and async retry covered. |
| 10 | Complete public delivery | `434a182`; 189 passed (public/provider streaming, native loop, SSE, facade telemetry, serve/background APIs), plus 15 passed after hiding runtime-only child parameters. Bounded producer, early deltas, child identity, cancellation during provider/tool, terminal parity and failure covered. |
| 11 | Complete cleanup | `5e097df`; 102 passed (native runs/catalog, prompts, governance, offloading, configured children, real applications, cookbook and import smoke). Changed-file Ruff and diff checks passed. Earlier broad run identified stale fixtures, subsequently migrated; full final rerun is step 12. |
| 12 | Complete offline integration | Full suite: 1,027 passed, 13 external-service skips, 2 network/key tests deselected. Final cancellation/persistence change: 26 passed (native runs and streaming). Build, wheel manifest, changed-source Ruff/diff, 31-page documentation navigation and 44-ID review-link audit passed. |

## Plan changes and limitations

- Initial workflow deletion and history repair predate this explicit sequence. History repair does not authorize skipping step 07.
- Supported provider names do not prove each deployed model supports tools or streaming. Offline adapter fixtures establish our contract; model/provider capability verification must be reported separately.

- Step 03: malformed argument JSON remains attached to its identified call until execution validation; missing/duplicate provider IDs fail at response decoding. Text-only helpers reject native calls rather than discarding them. Terminal and streaming semantics are specified above and wired in steps 08–10.

- Step 04 sequencing clarification: configured-child schemas are represented as native catalog bindings now; executor wiring and the dynamic-spawn typed-array change stay in step 05 with the prompt cutover. No second execution path is enabled in this step. Colliding names use deterministic aliases with concrete provider/server bindings rather than ambiguous bare-name routing.

- Step 06 limitation: synchronous in-process Python tools run in worker threads to avoid blocking the event loop. Cancellation stops awaiting and records cancellation but cannot forcibly terminate arbitrary Python thread side effects; process isolation is outside this protocol migration.

- Step 07 policy: active `preserve_recent` expands to retain a complete recent interaction; stored sliding windows and token budgets drop whole groups that do not fit. Very small summary budgets use existing truncation behavior without marking source rows summarized. Unmarked XML-era observations remain historical data; only explicit transient metadata is omitted. PostgreSQL JSON filter expression is implemented but not live-verified.

- Step 09 source check: installed OpenAI chunk models expose indexed tool deltas and `AsyncStream.close()` closes the response. Checked [official function-calling streaming documentation](https://developers.openai.com/api/docs/guides/function-calling#streaming) and [Chat Completions reference](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create). Adapter tests remain offline; no model calls were made.

- Step 09 limitation: the stream contract accepts text deltas and function calls; multimodal content-block deltas fail explicitly. Streaming requests never retry automatically after partial output. Provider capability failures are surfaced instead of silently dropping tool parameters.

- Step 10 delivery policy: text deltas are live-only, carry root run ID, actor run/trace/session IDs and monotonic sequence, and are labelled intermediate until the completed answer. Public queue is bounded at 256; SSE queue at 1000. Lifecycle event replay retains event-ID deduplication; replay returns persisted final answers, not historical token fragments. Background runs retain lifecycle event streams and durable terminal outcomes; they do not record token fragments. Explicitly close a public iterator with `aclosing` when stopping early.

- Step 11 cleanup corrections: prompt capability activation uses native catalog identities, and history load failures surface instead of silently losing context. Discovery schemas belong to each run; calls are resolved against the turn-start catalog. BM25 splits snake_case/camelCase identifiers. Loop signatures use normalized guarded outputs before artifact references and argument redaction. Offload telemetry is emitted at the actual native boundary. Existing governance/authority, telemetry and offload tests were moved onto retained primitives; XML representation tests were deleted.

- Step 12 corrections: public delivery observes producer cancellation instead of waiting forever; cancelled tool spans close; returned child errors mark delegation failure. LiteLLM parameter policy is now per request, not a global mutation. Final caller audit removed the unused summary-memory-constructor prompt/export. A cancellation arriving between result-history writes finishes the active write and skips completed IDs during reconciliation; storage remains nontransactional across process death.
- Review package: [native-tool-migration-review.md](native-tool-migration-review.md) maps all X01–X44 entries, retained scenarios, breaking contracts and explicit external verification limits. The implementation checkpoints are complete; live provider/model and unavailable remote-storage verification remains a deployment gate, not a claimed offline result.

### Live validation follow-up — 2026-09-14

User supplied an OpenAI key and authorized Luna/Terra testing. Selected Luna and
completed nine live scenarios. Provider checks exposed dropped tool-stream
fragments in the installed LiteLLM path; OpenAI now uses its SDK for complete,
synchronous and streaming requests. Added explicit reasoning effort, optional
sampling defaults, SDK dependency, and fail-fast validation for missing streamed
calls. The shared cookbook and live background example use Luna. Explicit caller
model/reasoning choices remain intact.

Verification: 1,036 offline tests passed (13 external-service skips, 2 deselected),
then 17 focused tests passed after the final regression/example edits. The
[live report](../validation/luna-live-validation.md) records actual provider
results and the remaining Responses/reasoning, provider, and remote-service gates.
