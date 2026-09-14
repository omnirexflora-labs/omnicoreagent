# Application retest through LiteLLM — 2026-09-14

Baseline: `ae16375`, branch `refactor/native-tool-runtime`.
Environment: the migration worktree's upgraded `.venv`, LiteLLM 1.100.1,
OpenAI SDK 2.54.0, Python 3.12.13. Model: `gpt-5.6-luna`.

## Routing and scope

Added `--adapter litellm` to the opt-in validation runner. A process-scoped
test adapter (removed after production routing was switched) routes `LLMConnection` complete/synchronous/
streaming requests through real `litellm.completion`/`acompletion`. It supplies the
actual credential, native schemas, model messages and reasoning setting, and uses
our existing complete-turn normalization and stream assembler. No model output is
mocked. The patch includes dynamically constructed child connections and restores
all methods on exit. OmniCoreAgent's direct SDK factory is blocked during the run;
LiteLLM can still use the SDK internally as part of its own implementation.

The agent loop, executor, history, context, governance, workspace, child creation,
background manager and HTTP delivery are the actual application paths. This is a
test adapter, not a permanent change to production routing. It disables LiteLLM
retries explicitly and does not exercise the production complete-call retry
wrapper. No automatic provider fallback is introduced.

## Observed application results

**All nine scenarios have passing live results**, across an initial nine-case run
and one corrected streaming retest:

| Scenario | Evidence |
| --- | --- |
| XML task content | Literal XML answer preserved, no tool execution |
| Native batch and continued session | Two correlated calls with exact typed arguments; two stored results; later run recalls receipts without calling tools again |
| Tool failure/timeout | Two recoverable correlated errors, including actual timeout |
| Streamed local tool continuation | Exactly one tool effect, two provider turns, 84 deltas; first text at 7.19s before completion at 8.10s |
| Stream cancellation | Provider stream closed before a completed model turn |
| Configured child | Two parent turns, one child turn; child text has its own actor identity |
| Real HTTP SSE | 1,114 text deltas; first text at 3.12s before completion at 13.15s; one successful terminal event |
| Dynamic deep agent | Child streams, writes workspace output; parent reads and reports it |
| Background native tool run | Completed with exactly one tool effect, result preview, and five background events |

Counters across both application invocations: **9 complete requests, 14 streaming
requests, 14 streams closed**. No direct-SDK bypass was detected.

The initial application run reported eight passes and one failure. Its Python
streaming assertion incorrectly required *every* queued delta to arrive before
the provider closed. A valid live stream can have trailing queued text after
provider completion. The correction requires the **first** text before provider
close, while retaining exact tool-effect, terminal-success and final-answer
checks. That scenario then passed. The original failure remains in the
[machine-readable results](litellm-agent-results.json); it is not relabeled as a
passing initial run. Other successful cases were not rerun unnecessarily.

Application cases use Chat Completions with `reasoning_effort="none"`, matching the
current runtime contract. They do not establish a completed application Responses
integration.

## Separate reasoning-enabled Responses check

Repeated the direct LiteLLM Responses round trip in the same environment with
`reasoning={"effort":"high"}`, `store=False`, and encrypted reasoning included.
Observed both outbound requests using `/v1/responses` with high effort. Result:
13 events, five argument deltas, one reasoning item, **19 reasoning tokens**, and
successful continuation after returning the encrypted state and correlated tool
result. This is distinct from the nine application scenarios above.

The existing Pydantic usage-shape warning was still visible in this separate
check. No credential or encrypted reasoning payload is included in the saved
report; the report records presence and counts only.

## Verification and reproduction

The command below now uses production routing with observation only. The temporary
`--adapter litellm` option and routing shim were removed after this historical run.

**14 offline tests passed** in `test_live_validation_adapter.py` and
`test_model_stream.py`. New checks verify real-adapter request construction via a
mock transport, direct-adapter bypass detection, restoration after failure, early
text delivery and upstream closure. These tests are separate from the live model
results. Ruff and `git diff --check` pass. Production source was not changed.

```bash
uv run --no-sync python engineering/validation/live_native_runtime.py \
  --require-litellm --env-file .env --report /tmp/litellm-agents.json
```

To select the corrected streaming case, append
`--scenario public_stream_and_tool_continuation`. These are paid synthetic model
requests. This result supports keeping LiteLLM; lossless continuation storage and
application Responses integration remain the next implementation checkpoint.
