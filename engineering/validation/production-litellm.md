# Production LiteLLM routing — 2026-09-14

Baseline `8b86602`, branch `refactor/native-tool-runtime`.

OpenAI now uses LiteLLM 1.100.1 in the actual `LLMConnection` async, synchronous,
and streaming paths. Model names use the explicit `openai/` provider prefix.
The direct OpenAI SDK workaround is removed. The existing dedicated Cencori
endpoint remains a separate adapter; there is no automatic SDK fallback.

Every LiteLLM request receives its connection's API key explicitly, preventing
another connection's environment assignment from changing request credentials.
Unsupported parameters are not silently dropped. LiteLLM retries are disabled;
the runtime owns retry policy for complete requests and never replays a partial
stream. Production stream assembly and cleanup use the existing shared code.

The temporary validation routing shim was deleted. The replacement
[provider observer](provider_observation.py) counts actual LiteLLM requests and
forbids OmniCoreAgent's direct SDK factory during OpenAI validation. It does not
replace `LLMConnection` methods, model responses, stream assembly or execution.
New tests verify that observation leaves production methods intact and restores
its wrappers even after failure.

## Results

- **9/9 live application scenarios passed in one run** through production routing:
  native batches, history continuation, tool errors/timeouts, streamed tool calls,
  cancellation, configured and dynamic children, workspace output, real HTTP SSE,
  and background execution, plus literal XML as task content.
- Observed **9 complete and 13 streaming LiteLLM requests**, including children.
  No direct SDK bypass occurred.
- HTTP delivered 1,036 text deltas starting at **2.74 seconds**, before the
  **12.73-second** run finished. Public streamed tool continuation returned 69
  deltas and executed the tool exactly once.
- Initial provider/stream/native-runtime regression group: **49 passed**.
- Full final suite: **1,038 passed, 13 external-service skips, 2 deselected**,
  128.67 seconds. The isolated Redis/MongoDB services from the dependency refresh
  had been stopped; their earlier live results remain in that checkpoint. S3/R2
  credentials remain unavailable. The upstream Starlette/AnyIO deprecation warning
  is still visible.
- Source distribution and wheel builds, Ruff, and `git diff --check` passed.

Raw scenario metrics: [production-litellm-results.json](production-litellm-results.json).

```bash
uv run --no-sync python engineering/validation/live_native_runtime.py \
  --require-litellm --env-file .env --report /tmp/production-litellm.json
```

`--require-litellm` enables observation and bypass detection only. Ordinary
`OmniCoreAgent.run()` and `.stream()` already use the new production route without
that flag. The old `--adapter litellm` override no longer exists.

This routing checkpoint uses Chat Completions and the validated Luna setting
`reasoning_effort="none"`. It does not implement application Responses execution
or lossless reasoning-state persistence. The separate high-effort LiteLLM Responses
checks already passed; integrating that contract is the next distinct change.
