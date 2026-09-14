# LiteLLM review — 2026-09-14

Reviewed OmniCoreAgent branch `refactor/native-tool-runtime` at `ff27635`.
This is a review and isolated diagnostic exercise; production adapters and the
project dependency lock were not changed.

## Recommendation correction

Retain LiteLLM as the intended provider integration layer. Current evidence does
not justify replacing it with separate OpenAI, Anthropic, and Gemini SDK adapters.
The previous recommendation was premature: it followed a failure on the old pinned
version without first testing current LiteLLM or its Responses interface.

The OpenAI SDK change in `ff27635` remains in the branch. Replacing that workaround
with a tested LiteLLM integration is subsequent implementation, not completed by
this review. The earlier nine passing application scenarios remain valid for the
SDK path they tested; they are not results for the new LiteLLM path.

## Versions and setup

- Project lock and existing environment: LiteLLM **1.83.14**, OpenAI SDK **2.24.0**.
- Latest stable PyPI release checked directly: LiteLLM **1.100.1**, published
  **2026-09-10T01:42:29Z**. GitHub also lists newer prereleases; they were not chosen.
- Installed 1.100.1 into `/tmp/omni-litellm-review` using an isolated virtual
  environment. Its initial resolution installed OpenAI SDK 2.54.0.
- Repeated the important checks after pinning that temporary environment back to
  OpenAI SDK **2.24.0**. Both still passed. A newer OpenAI SDK is therefore not
  required for the observed success. Other resolved dependencies also differ;
  this is not a bisect identifying one upstream fix.
- All calls used the supplied local credential, synthetic `receipt(code: str)`
  tasks and `gpt-5.6-luna`. No credential was committed. Local model cost maps were
  enabled in both environments, matching our application configuration.

## Executed comparisons

| Environment / interface | Observation | Conclusion |
| --- | --- | --- |
| 1.83.14 Chat, usage enabled | 2 chunks, 0 tool fragments | Reproduced the missing-call failure |
| 1.83.14 Chat, usage disabled | 1 chunk, 0 tool fragments | Removing `include_usage` alone does not fix this case |
| 1.100.1 Chat, usage enabled | 8 chunks, 6 tool fragments, exact `{"code":"001"}` | Streamed native call preserved |
| 1.100.1 Chat, usage disabled | 7 chunks, 6 tool fragments, exact arguments | Streamed native call preserved |
| 1.100.1 Responses, low effort | 5 argument deltas; tool-result continuation succeeded; 0 reasoning tokens | Tool round trip passed; insufficient to claim exercised reasoning |
| 1.100.1 Responses, high effort | 13 events, 5 argument deltas, 1 reasoning item, encrypted state, 15 reasoning tokens; continuation succeeded | Reasoning plus streamed tools and stateless continuation exercised |
| 1.100.1 + OpenAI SDK 2.24.0, Chat | 8 chunks, 6 tool fragments, exact arguments | Success also with the project's OpenAI SDK version |
| 1.100.1 + OpenAI SDK 2.24.0, Responses high | Same 13-event / 5-delta / 15-reasoning-token result; continuation succeeded | Success does not require upgrading the OpenAI SDK |

An additional old-version Responses check completed a tool round trip but emitted
only one event with no argument deltas and no reasoning items. This does not prove
live incremental streaming or reasoning on that old path.

The initial diagnostic labeled completed Chat requests as “passed” even when they
contained no call fragments. The table above classifies the actual observed
fragment counts, not that overly weak transport-success label.

The high-effort checks observed outbound request paths and selected JSON fields
through an isolated HTTPX send wrapper: both requests used `/v1/responses` with
`reasoning={"effort":"high"}`. No request headers or credential values were logged.
The first response's complete output items, including encrypted reasoning state,
were submitted with the correlated `function_call_output` in the next request.
`store=False` was used; this did not rely on provider-hosted conversation history.
The second response contained the synthetic receipt ID.

## Current capabilities versus our integration

| Concern | LiteLLM evidence | OmniCoreAgent responsibility |
| --- | --- | --- |
| OpenAI Responses and streaming | Documented `responses`/`aresponses`; exercised live above | Add request, event, output-item and continuation handling; current adapter only calls Chat Completions |
| Native tool fragments | Current-version live Chat and Responses checks preserve them | Execute only complete validated calls; preserve IDs and arguments |
| Reasoning with tools | High-effort Responses check passed | Forward the selected reasoning setting and retain continuation state |
| Anthropic thinking | Documented `thinking_blocks` round trip; current transformation source retains signed thinking/redacted-thinking blocks | Preserve blocks in normalization, history, context transforms and subsequent requests |
| Gemini signatures | Documentation and current transformation source preserve signatures in tool/provider fields and support an ID-encoding compatibility path | Preserve returned state; test actual selected Gemini model and multi-call continuation |
| Cancellation, usage, errors | Interfaces exist, but this review did not rerun the full application suite through current LiteLLM | Revalidate early public/SSE delivery, upstream closure, error semantics and usage accounting |

A synthetic diagnostic of our current normalizer showed that assistant
`thinking_blocks` and per-tool `provider_specific_fields` are discarded.
`LLMConnection.to_dict()` also filters out `thinking_blocks`. Source:
[llm_response.py](../../src/omnicoreagent/core/agents/llm_response.py),
[model_protocol.py](../../src/omnicoreagent/core/model_protocol.py),
[llm.py](../../src/omnicoreagent/core/llm.py), and
[model_stream.py](../../src/omnicoreagent/core/model_stream.py).
Gemini signatures encoded in call IDs may survive our existing ID preservation;
field loss alone does not establish that every Gemini invocation fails.

LiteLLM documents a `modify_params=True` workaround that can drop Anthropic
thinking when the application loses thinking blocks. That would mask our data-loss
bug by reducing capability. Preserve the blocks instead; do not use that workaround
as proof of reasoning-enabled correctness.

## Required follow-up, not implemented here

1. Update and pin LiteLLM deliberately; run dependency and application regressions.
2. Define lossless continuation records, preserving provider items and reasoning
   metadata through active context, persisted history, reconstruction and offload.
3. Integrate LiteLLM Responses for the OpenAI reasoning/tool path; retain the shared
   agent loop, tool execution, governance and background runtime.
4. Run the nine application scenarios through that integration, including true SSE
   latency, cancellation and deep/background execution. Add reasoning continuation.
5. Validate Anthropic and Gemini separately when their credentials are available.
   Documentation/source support is not a substitute for those live tests.
6. Remove the direct OpenAI SDK workaround once the replacement passes its gates.

Outstanding: the precise upstream change responsible for the old missing fragments
was not bisected. The old bundled map lacks Luna; the current map contains it, but
that alone does not prove the map was the sole cause. Responses serialization also
emitted a Pydantic usage-shape warning during these successful calls. Verify usage
normalization rather than suppressing or ignoring it in production. No new claim
is made about every model, transport, multimodal feature, or external service.

## Primary references

- [PyPI 1.100.1](https://pypi.org/project/litellm/1.100.1/)
- [LiteLLM releases](https://github.com/BerriAI/litellm/releases)
- [Responses API](https://docs.litellm.ai/docs/response_api)
- [Reasoning and thinking-block preservation](https://docs.litellm.ai/docs/reasoning_content)
- [Anthropic](https://docs.litellm.ai/docs/providers/anthropic)
- [Gemini and thought signatures](https://docs.litellm.ai/docs/providers/gemini)

Source inspection used the installed distribution at 1.100.1:
`litellm/responses/main.py`, `llms/anthropic/chat/transformation.py`,
`llms/vertex_ai/gemini/transformation.py`, and the bundled model map.
