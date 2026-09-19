# Provider continuation plan

Branch `refactor/native-tool-runtime`, started at `194b01b` (2026-09-19), after
the [MCP v2 completion plan](mcp-v2-completion-plan.md). Earlier analysis:
[LiteLLM review](../validation/litellm-current-review.md). The remaining
failure of the [boundary audit](../validation/native-boundary-audit-results.json)
(`provider_specific_fields_retained`) is this problem.

## Problem

Some models return data with a turn that must be sent back unchanged on the
next request, or the next request fails or loses the model's reasoning:

| Provider (through LiteLLM 1.101.0) | Field | Where | Read back by LiteLLM from |
| --- | --- | --- | --- |
| Anthropic extended thinking | `thinking_blocks`: `thinking` (with `signature`) and `redacted_thinking` (`data`) | assistant message | the assistant message |
| Gemini thinking models | `thought_signature` | `provider_specific_fields` of each tool call (and its `function`) | the tool call; also encoded in the tool-call ID as `<id>__thought__<signature>` |
| OpenAI reasoning items | `reasoning_items` | assistant message | the assistant message |
| OpenRouter (Claude, Gemini, and others) | `reasoning_details` (signed for Claude, encrypted for others) | LiteLLM moves it into the message's `provider_specific_fields` | a top-level `reasoning_details` on the assistant message only; LiteLLM does not move it back (verified offline with `OpenrouterConfig.transform_request`) |
| Any provider | `provider_specific_fields` | assistant message | provider-dependent |

OmniCoreAgent keeps only `reasoning_content`. The other fields are dropped at
four points, each an allowlist of one field:

1. response normalization (`core/agents/llm_response.py`);
2. stream assembly (`core/model_stream.py`), which also drops per-tool-call fields;
3. the request builder (`LLMConnection.to_dict` in `core/llm.py`);
4. history reload (`core/agents/message_history.py`).

The expected effect is that a multi-step tool loop with Anthropic extended
thinking fails on the second model call, and Gemini thinking models lose their
signatures whenever the ID encoding is not used. That effect is inferred from
the code and LiteLLM's transformations; P1 must prove it before the fix.

## Goal

Whatever continuation data a provider returns with a turn reaches the next
request for that turn exactly as returned: within a run, across runs through
stored history, after context compression, and in streaming and non-streaming
mode. The trace records that it was present without storing opaque blobs under
the default capture policy.

## Decisions (2026-09-19)

1. **Proof without provider keys.** No Anthropic, Gemini, or OpenRouter key is
   available now. All three paths (`anthropic`, `gemini`, `openrouter`) are
   proven offline: local fake servers speaking each provider's real wire format
   and rejecting a follow-up whose continuation data was changed or dropped,
   reached through LiteLLM's real transformations for that provider. Direct
   keys (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, optionally
   `OPENROUTER_API_KEY`) are preferred for live runs and are added later.
2. **Tool arguments in history.** History stores the real tool arguments, so
   the model sees its own past calls in later runs; governance redacts them
   only in telemetry. (Today history stores `[REDACTED]`.)

Decided by default (consistent with earlier decisions): thinking text follows
the response capture policy (recorded only with `capture="full"`); signatures
and encrypted items are never recorded, only their presence, count, and digest.

## Definition of done

| # | Area | Must hold |
| --- | --- | --- |
| 1 | Response | `thinking_blocks`, `reasoning_items`, message `provider_specific_fields`, and tool-call and function `provider_specific_fields` survive normalization unchanged. |
| 2 | Stream | The same fields survive stream assembly, including thinking and signature deltas merged in order and per-tool-call fields. |
| 3 | Request | The fields are sent back for the turn they belong to; for OpenAI, forwarding them never produces an invalid request. |
| 4 | History | Stored history keeps the fields, reload restores them, every memory backend round-trips them, and the privacy filter never alters a signature, encrypted item, or signed tool-call ID. |
| 5 | Context | Compression and summarization never split a turn from its continuation data, and the most recent tool turn always keeps it; summaries never include opaque blobs. |
| 6 | Telemetry | Presence, type counts, and digests are recorded under every capture policy; thinking text only under full capture; signatures never; the portable schema types the new metadata. |
| 7 | Proof | Offline end-to-end runs against fake Anthropic, Gemini, and OpenRouter servers fail before the fix and pass after, streaming and not; the boundary audit passes 8 of 8; a live OpenAI regression run passes; live runs for the other providers once keys exist. |

## Working rules

The same as the telemetry and MCP plans: one unit at a time; failing test
first; focused tests, full suite, `ruff check`; log the result and commit hash
below; commit and push each unit. Tests use LiteLLM's real response, stream,
and request transformation types, never hand-shaped dictionaries that only
resemble them.

## Units

### P1. Prove the failure
- Local fake Anthropic Messages and Gemini `generateContent` servers that
  reject a follow-up request whose thinking block or signature differs from the
  one they issued (as the real APIs do), reached through LiteLLM with
  `api_base`.
- A scripted two-step tool run against each, non-streaming and streaming; they
  must fail today. These become the acceptance tests for P2 to P6.
- The same for OpenRouter's chat format (`reasoning_details`).

### P2. Carry the fields in a model turn
- `ModelTurn` carries message-level continuation fields; `ToolRequest` carries
  its own `provider_specific_fields`; `assistant_message()` and
  `ToolRequest.as_dict()` return them unchanged.
- Normalization reads them from LiteLLM `ModelResponse` objects and mappings.

### P3. Streaming
- Stream assembly merges `thinking_blocks` deltas (text and signature, by
  index), `reasoning_items`, message fields, and per-tool-call fields.

### P4. Request path
- `LLMConnection.to_dict` forwards the continuation fields.
- OpenRouter: `reasoning_details` is sent back as a top-level field of the
  assistant message (LiteLLM leaves it inside `provider_specific_fields`).
- `provider_specific_fields` is sent raw by LiteLLM's OpenAI-compatible
  transformations; it is forwarded only where the target accepts it, so no
  provider receives a field it rejects.
- Proven per provider through LiteLLM's request transformation (offline): the
  Anthropic request contains the signed thinking block before the tool use;
  the Gemini request contains the signature on the function call part; the
  OpenAI request is valid (checked against a local fake OpenAI server and one
  live call).

### P5. History and privacy
- The stored `model_message` keeps the fields; reload restores them.
- The privacy filter and key redaction exempt signatures, encrypted items, and
  signed tool-call IDs (the C1 UUID bug is the precedent).
- Round trip through the in-memory, SQL, Redis, and MongoDB stores that the
  test environment supports.
- Decision 2 applied: history keeps real tool arguments; governance redaction
  stays in telemetry only (tests for both).

### P6. Context management and telemetry
- Compression and summarization keep each turn with its continuation data;
  summaries exclude opaque blobs.
- Model-call telemetry records continuation presence, counts, and digests;
  schema and trajectory reader updated.

### P7. Proof and documentation
- The P1 runs pass; boundary audit 8 of 8; live OpenAI regression; live
  Anthropic, Gemini, and OpenRouter runs once keys exist.
- Docs: models guide (reasoning and thinking models), observability guide.

## Out of scope

OpenAI Responses API integration (a separate transport change), provider
prompt-cache controls (`cache_control`), and choosing reasoning settings for
the user.

## Execution log

| Unit | Status | Commit | Evidence |
| --- | --- | --- | --- |
| P1 | Complete | `644ed5c` | New `tests/fixtures/continuation_providers.py`: one local server speaking Anthropic `/v1/messages`, Gemini `/models/<model>:generateContent` and `:streamGenerateContent?alt=sse`, and OpenRouter `/api/v1/chat/completions`, streaming and not, reached through LiteLLM's real provider code via `ANTHROPIC_API_BASE`, `GEMINI_API_BASE`, and `OPENROUTER_API_BASE`. Each issues continuation data on the first turn (a signed `thinking` and a `redacted_thinking` block; a `thoughtSignature` on the function call; `reasoning_details` with a signature) and rejects a follow-up without it using the provider's own error. The fakes were validated with LiteLLM alone: a correctly built follow-up is accepted and a stripped one rejected for every provider. New `tests/test_provider_continuation.py` runs a real `OmniCoreAgent` two-step tool loop per provider with `run()` and `stream()`. Result today: Anthropic and OpenRouter fail in both modes ("Expected `thinking` or `redacted_thinking`, but found `tool_use`"), marked strict expected failures; Gemini passes only because LiteLLM also encodes the signature in the tool-call ID. Found during P1: (1) LiteLLM's `stream_chunk_builder` doubles Anthropic thinking text (it concatenates the thinking deltas and the complete block LiteLLM emits with the signature) and drops OpenRouter `reasoning_details`; OmniCoreAgent assembles streams itself, so P3 must merge these correctly; (2) LiteLLM sends Gemini parts as `function_call` / `function_response`, which the fake first missed (fixed). Full suite 1,390 passed, 14 skipped, 4 expected failures; ruff clean. |
| P2 | Complete | `f59533e` | Normalization keeps `reasoning_content`, `thinking_blocks`, `reasoning_items`, and message `provider_specific_fields` (non-empty values only), and on each tool call its own and its function's `provider_specific_fields` (`ToolRequest.provider_fields`, `compare=False`); values are converted from LiteLLM objects to plain data and deep-copied, and `assistant_message()` / `ToolRequest.as_dict()` return copies, so no caller can alter a signature. 6 new tests on real LiteLLM responses from the P1 fakes (Anthropic thinking and redacted thinking, the Gemini signature on the tool call, OpenRouter reasoning details inside `provider_specific_fields`), a mapping response with every field, turns without continuation data unchanged, and copies not references. The boundary audit passes 8 of 8 (results re-saved). The P1 end-to-end cases stay expected failures until the request path forwards the data (P4). Noted for P6: with `capture="full"` the recorded model response and request messages can now contain signatures; the default capture records neither. Full suite 1,396 passed, 14 skipped, 4 expected failures; ruff clean. |
| P3 | Complete | `1552009` | `ModelStreamAssembler` assembles the same continuation data as a non-streamed turn: Anthropic thinking pieces append to the open block and the signature closes it (LiteLLM repeats the whole text with the signature; appending it would double the text, as LiteLLM's own `stream_chunk_builder` does), redacted blocks are appended whole, and an unsigned block still open at the end is kept; OpenRouter `reasoning_details` pieces are merged per index (text appended, signature and format set) and kept under `provider_specific_fields` as in the non-streamed path; LiteLLM's duplicate thinking pieces inside `provider_specific_fields` are not stored twice; per-call and per-function `provider_specific_fields` (Gemini's signature) are merged into their call. The shared converter is now public (`plain_copy`). 5 new tests: real LiteLLM streams from the three fakes match the non-streamed turn; two thinking blocks in one message stay separate (raw Anthropic events through LiteLLM's own stream parser); OpenRouter details streamed in pieces merge per index. Full suite 1,401 passed, 14 skipped, 4 expected failures; ruff clean. |
| P4 | Complete | `1753e93` | The request builder (`LLMConnection.to_dict`) is provider-aware: `anthropic` receives `thinking_blocks`; `gemini` receives the message's and each call's and function's `provider_specific_fields`; `openrouter` receives `reasoning_details` lifted to the top level (LiteLLM keeps it inside `provider_specific_fields` but sends only a top-level field), without the raw `provider_specific_fields`; every other provider receives exactly what it did before (role, content, reasoning content, plain tool calls). Values are deep-copied, so building a request never changes the stored message. Evidence first: recorded requests showed LiteLLM converts Anthropic thinking blocks into content blocks and sends Gemini signatures on the function-call part, but passes every unknown field raw to OpenAI-compatible providers (OpenAI accepted them in a live check; stricter providers may not, and the data is useless to them). 9 new tests on request bodies per provider, including five providers that must be unchanged and a no-mutation check. Found during P4: since P2, per-call fields rode inside `tool_calls`, so the old builder would have sent Gemini's signature field to every provider; closed here. The P1 end-to-end loops now pass (Anthropic and OpenRouter, run and stream); their strict expected-failure markers fired and were removed. Live OpenAI regression through the acceptance `--live` run: 2 steps, local and MCP tools succeeded, evidence complete. Full suite 1,414 passed, 14 skipped; ruff clean. |
| P5 | Complete | `0ff31bf` | History reload restores every continuation field and each call's and function's `provider_specific_fields` (the `ToolCall` record is validated without them, then they are re-attached). The privacy filter never pattern-scans opaque provider values: `id` joins the identifier keys (Gemini encodes its signature in the tool-call ID), `signature`, `thought_signature`, `thought_signatures`, and `encrypted_content` values are kept, and `redacted_thinking` and `reasoning.encrypted` blocks are kept whole; free text is still redacted. Memory redaction is on by default and a provider signs its thinking text, so when redaction must change continuation data the stored copy leaves it out and records `continuation_dropped` (never a corrupted signature; the live run keeps its own copy, and Anthropic requires thinking only on the tool turn in progress). Decision 2 applied: history stores the real tool arguments; governance redaction stays in telemetry and in the tool result the model receives. 9 new tests: a second run in the same session per provider and per memory backend (in-memory and SQLite) sends the stored data back and is accepted; opaque values survive privacy (base64 signatures with embedded card, phone, and date digit runs); thinking containing an email is not stored and is recorded as dropped while clean thinking is stored exactly; under governance history keeps the real arguments while the trace redacts them. Found during P5: (1) since P2, reloading a Gemini history crashed (`ToolCall.__init__() got an unexpected keyword argument 'provider_specific_fields'`), fixed before release; (2) the fake Anthropic server requires thinking on every earlier tool turn, stricter than the real API, so the cross-run Anthropic and OpenRouter results prove lossless storage rather than a real-API rejection. Old test: `test_governed_native_history_is_written_once_after_redaction` asserted the secret appears nowhere in history; it now asserts it appears only in the assistant's own stored call. Full suite 1,423 passed, 14 skipped; ruff clean. |
| P6 | Complete | `931abe2` | New `core/continuation.py`: `mask_opaque` replaces every opaque value (signature, thought signature, encrypted content, redacted and encrypted blocks, and the signature part of a Gemini signed tool-call ID wherever it appears in text) with a stable marker `[opaque sha256:<12 hex> len=<n>]`; `continuation_summary` gives counts per kind and a 16-hex digest. The recorder masks before the privacy filter in its payload, error-text, and digest paths, so no signature is stored under any capture policy, while masked IDs still link events and digests stay stable. Each model call's facts carry `continuation` (every capture policy); the model response record carries the continuation data under full capture only, masked. The summarizer's input no longer includes per-call provider fields or signed IDs. The portable schema types `modelCall.continuation` (both copies). 7 new tests: the summarizer never receives opaque values; compression keeps a recent turn and its continuation data unchanged; full-capture and default traces of Anthropic and Gemini runs contain no signature, record the counts and digest, keep the trajectory linked, keep thinking text only under full capture; a continuation run exports valid portable evidence. Found during P6: under default capture, Gemini's signature reached the trace through signed tool-call IDs in metadata. Observed once, not reproduced: in the full-suite run that was in progress when a session ended, two MCP tests had their second stdio server close during the handshake ("Connection closed"); 3 of 3 isolated runs and the next full suite passed. Full suite 1,430 passed, 14 skipped; ruff clean; fixture check passed. |
| P7 | Complete | (this commit) | Proof: the P1 end-to-end loops pass for Anthropic, Gemini, and OpenRouter in run and stream mode, including a second run in the same session from in-memory and SQLite history; the boundary audit passes 8 of 8 (results re-saved); live OpenAI regression through the acceptance `--live` run (`gpt-5.4-mini`: 2 steps, local and MCP tools succeeded, evidence complete, key absent). New `engineering/validation/continuation_live.py` runs the same two-run tool loop with a thinking model (`reasoning_effort="low"`) per provider in both modes and checks the trace (runs completed, continuation recorded, no key stored); live mode runs each provider whose key is set and skips the others (all three skipped today: no Anthropic, Gemini, or OpenRouter key); `--fake` runs it against the local fakes and passes for all six cases. Found during P7: LiteLLM attaches `provider_specific_fields={"citations": None}` to every Anthropic message, so final text turns reported an all-zero continuation summary; empty placeholder entries are no longer continuation data (1 new test). Docs: the models guide gains "Thinking and Reasoning Models" (what is kept per provider, streaming and history, privacy behaviour, telemetry); the observability guide describes `model_call.continuation` and the opaque markers; the evidence coverage map gains a continuation row. The validation script's per-provider key names stay out of the user docs (the docs rule allows only `LLM_API_KEY`; caught by `test_docs_claims.py`). Full suite 1,431 passed, 14 skipped; ruff clean; fixture check passed. Plan complete; live runs for Anthropic, Gemini, and OpenRouter remain pending until keys exist. |
