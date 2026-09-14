# GPT-5.6 Luna live validation — 2026-09-14

Branch: `refactor/native-tool-runtime`, starting from `6820e50`. The accompanying
commit adds the changes below and this report. These are actual model requests,
not provider mocks. Credentials were read from the user's local `.env` into the
validation process; no credential file was copied or committed.

Configuration: OpenAI `gpt-5.6-luna`, Chat Completions,
`reasoning_effort="none"`, `max_tokens=2500` (mapped to
`max_completion_tokens` for OpenAI). Installed OpenAI SDK: 2.24.0;
LiteLLM: 1.83.14; HTTPX: 0.28.1.

## Findings and corrections

- The credential's model listing included `gpt-5.6-luna` and `gpt-5.6-terra`.
  Luna was selected; Terra has not been run through these scenarios.
- Injected sampling defaults and redundant `tool_choice="auto"` failed the
  installed LiteLLM parameter checks. Sampling now defaults to unspecified;
  explicit values are retained. Tool selection uses the endpoint's default auto.
- The provider rejected Luna function tools with reasoning enabled on Chat
  Completions. Its error required `reasoning_effort="none"` or the Responses API.
  Reasoning effort is now an explicit forwarded setting. Cookbook defaults select
  `none`; explicit overrides survive and unsupported combinations fail.
- A direct LiteLLM streaming diagnostic returned a `tool_calls` finish marker and
  usage without any call fragments. Agent runs therefore exhausted their step
  limit with empty turns. The equivalent direct OpenAI SDK request returned the
  call ID, name, and incremental JSON argument fragments correctly.
- OpenAI async, sync, and streaming requests now use the OpenAI SDK. Other
  providers retain their existing adapter. SDK retries are disabled so retry
  policy remains at the runtime boundary; partial streams are never replayed.
  Clients and upstream streams close on completion/error/cancellation.
- A stream ending with `tool_calls` but no calls now fails explicitly instead of
  entering empty-answer recovery. This guards other adapters against the observed
  loss. No XML fallback or text-to-tool conversion was added.
- The initial HTTP checks observed text only after provider completion through
  the old path, including an 800-word response. With the SDK path, the HTTP client
  received 987 deltas, starting at 6.16 seconds, before completion at 16.67 seconds.
  This establishes live delivery for this tested path, not every proxy/deployment.

## Passing live scenarios

| Scenario | Evidence |
| --- | --- |
| XML as task content | Literal XML answer preserved, no tool calls |
| Native batch and continued session | Two calls in one turn; `"001"`/`"002"` remain strings, quantities integers; two correlated stored results; later run recalls receipts without tool reuse |
| Failure and timeout | Synthetic exception and a 3-second tool under a 2-second timeout produce correlated recoverable errors |
| Streamed native tool continuation | One tool effect, two provider turns, 60 text deltas delivered before provider closure |
| Cancellation | Closing after the first delta closes the provider stream before its terminal turn |
| Configured child | Two parent turns, one child turn, child's text delivered under its own actor identity |
| HTTP SSE | Real localhost Uvicorn + HTTPX socket client; early text and exactly one successful complete event |
| Dynamic deep agent | Native `spawn_subagents`; child streams, writes `worker.md`; parent reads it and returns `DEEP_READY` |
| Background run | Manager executes native tool exactly once, records completed state, retains result preview and four background events |

Machine-readable timings: [luna-live-results.json](luna-live-results.json).
The nine scenarios passed across two sequential invocations, seven then two.
The earlier failures are documented above, not counted as passes.

Reproduce from a source checkout with its dependencies installed:

```bash
PYTHONPATH=src python engineering/validation/live_native_runtime.py --env-file .env --report /tmp/luna-live.json
```

These are paid, opt-in calls with synthetic data. Deep/background files are kept
in temporary directories. `--scenario NAME` can select individual checks.

## Limits

This does not validate reasoning-enabled Responses API execution, Terra, other
providers, remote MCP servers, remote stores, multimodal streams, proxy buffering,
or actual hours-long execution/restart behavior. Background coverage is a short
supervised run. Model refusals and adversarial/malformed arguments remain covered
by offline tests, not claimed as live observations here. Initial network attempts
encountered DNS/timeouts; one diagnostic subprocess exited 139 without a usable
trace. Subsequent sequential runs passed; that isolated process failure has not
been diagnosed. No general provider-compatibility claim follows from this run.

## Offline regression checks

Full suite: **1,036 passed, 13 skipped, 2 deselected** (113.12 seconds):

```bash
PYTHONPATH=src python -m pytest -q -ra -m 'not requires_network and not requires_api_key and not OpenAIIntegration'
```

After the final SDK-shaped tool-chunk test and background cookbook update:
**17 passed** in `test_model_stream.py` and `test_background_cookbook.py`.
The skips are unavailable external storage integrations. Changed-file Ruff and
`git diff --check` pass. Tests assert forwarded parameters, exact native calls,
provider/client closure, and fail-fast handling of missing streamed call data.

Reference: [OpenAI Luna model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
and [function-calling documentation](https://developers.openai.com/api/docs/guides/function-calling).
Endpoint restrictions above were observed directly in this account's API response.
