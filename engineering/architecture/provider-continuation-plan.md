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
the code and LiteLLM's transformations; P7 must prove it before the fix.

## Goal

Whatever continuation data a provider returns with a turn reaches the next
request for that turn exactly as returned: within a run, across runs through
stored history, after context compression, and in streaming and non-streaming
mode. The trace records that it was present without storing opaque blobs under
the default capture policy.

## Decisions to confirm

1. **Live proof.** Only an OpenAI key is available. Anthropic and Gemini are
   proven offline: local fake provider servers speaking each provider's real
   wire format, driven through LiteLLM's own request and response
   transformations. A live run needs an Anthropic and a Gemini key.
2. **Governance-redacted arguments in history.** Under governance, stored
   history replaces tool arguments with `[REDACTED]`, so the next run sends the
   model its own past calls with redacted arguments. Keep that (privacy first)
   or store the real arguments in history and redact only in telemetry?

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
| 7 | Proof | Offline end-to-end runs against fake Anthropic and Gemini servers fail before the fix and pass after; the boundary audit passes 8 of 8; a live OpenAI regression run passes; live Anthropic and Gemini runs if keys are provided. |

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
- Decision 2 applied.

### P6. Context management and telemetry
- Compression and summarization keep each turn with its continuation data;
  summaries exclude opaque blobs.
- Model-call telemetry records continuation presence, counts, and digests;
  schema and trajectory reader updated.

### P7. Proof and documentation
- The P1 runs pass; boundary audit 8 of 8; live OpenAI regression; live
  Anthropic and Gemini runs when keys are available.
- Docs: models guide (reasoning and thinking models), observability guide.

## Out of scope

OpenAI Responses API integration (a separate transport change), provider
prompt-cache controls (`cache_control`), and choosing reasoning settings for
the user.

## Execution log

| Unit | Status | Commit | Evidence |
| --- | --- | --- | --- |
