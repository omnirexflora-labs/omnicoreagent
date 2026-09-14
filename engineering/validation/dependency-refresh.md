# Dependency refresh — 2026-09-14

Branch `refactor/native-tool-runtime`, starting from `575b63e`.
The worktree now has its own `.venv`, built with Python **3.12.13**. The original
checkout and its environment were not modified. This checkpoint updates the
installed baseline; it does not replace the current OpenAI adapter or implement
the pending lossless LiteLLM Responses integration.

## What changed

Queried PyPI for the latest stable versions of all **39 distinct direct dependency
names**, including runtime, optional extras, development and build requirements.
Updated the declarations and regenerated the entire lock with upgrade resolution:
**93 locked packages changed version**, with **149 packages** in the resulting
cross-platform lock. The Linux environment contains **145 installed packages**.
All project extras and dependency groups are installed.

| Dependency | New baseline |
| --- | --- |
| LiteLLM | 1.100.1 |
| MCP | 2.2.0 |
| OpenAI | 2.54.0 |
| Pydantic | 2.13.5 |
| FastAPI / Starlette | 0.141.1 / 1.6.0 |
| Redis client | 8.1.0 |
| PyMongo / Motor | 4.18.1 / 3.7.1 |
| NumPy | 2.5.3 |
| pytest / pytest-asyncio | 9.1.1 / 1.4.0 |
| Ruff | 0.16.7 |

The one direct runtime/development exception to absolute latest is **OpenAI**:
PyPI latest was **3.13.0**, but stable LiteLLM 1.100.1 explicitly requires
`openai>=2.20.0,<3.0.0`. The project declares `openai>=2.54.0,<3.0.0` and locks
2.54.0, the newest compatible release. No dependency constraint was bypassed and
no prerelease was selected. Build dependencies are resolved in isolated build
environments and are not all represented as runtime lock entries.

Latest NumPy requires Python 3.12, and LiteLLM requires Python below 3.15.
The project now declares **Python >=3.12,<3.15**; classifiers and installation
guidance match. This intentionally ends advertised Python 3.10/3.11 support.
Local validation and CI use 3.12; this checkpoint does not claim separate 3.13/3.14
execution results.

CI now installs every project extra and development group from the lockfile,
keys its environment cache by the lockfile, and uses `uv run --no-sync` during
tests to keep that environment intact. Its cache health check no longer assumes
uv-created environments contain pip. Removed obsolete CI comments about deleted
XML/workflow tests and corrected the live-model test exclusion. Development
instructions now use the actual dependency group, fixing the nonexistent `[dev]`
extra instruction.

Ruff 0.16.7 changed its default enabled rules, producing 813 additional findings
under an implicit policy. The previous release's check had zero findings.
The existing `E4`, `E7`, `E9`, `F` policy is now explicit and passes on the new Ruff.
This preserves the established gate; it is not a claim that all newly defaulted
style, modernization, exception-handling and other rules have been satisfied.
Adopting those additional rules should be a deliberate code-quality checkpoint.

## Executed verification

- `uv sync --all-extras --all-groups --locked`: complete, including editable install.
- `uv lock --check`: passed.
- `uv pip check`: all 145 installed packages compatible.
- Full test suite: **1,048 passed, 2 skipped, 2 deselected**, 122.17 seconds.
  Redis memory/background cases used an isolated local Redis **7.0.15** process;
  MongoDB cases used an isolated Docker MongoDB **8.3.9** instance. Both were
  stopped afterward. The skipped cases require S3/R2 credentials. Network/API-key
  marked tests were deselected and covered separately where credentials existed.
- Live agent validation: **9 passed, 0 failed**. Covered XML task content, native
  batches, session continuation, tool errors/timeouts, streamed tool continuation,
  cancellation, configured children, real HTTP SSE, dynamic deep-agent workspace
  output, and background execution. HTTP delivered 1,051 text deltas beginning at
  2.53 seconds, before the 13.21-second run finished.
- Separate live LiteLLM 1.100.1 Responses check: sent high effort to
  `/v1/responses`; received 13 events, 5 argument deltas, 1 encrypted reasoning
  item and 15 reasoning tokens; preserved that state through successful tool-result
  continuation. This tests the library directly, not a completed application
  Responses integration.
- Source distribution and wheel builds passed. Ruff and `git diff --check` pass.

The nine application scenarios still exercise the SDK-backed Chat Completions
path with reasoning disabled, as documented in the original live report. Their
success must not be misrepresented as nine application tests of LiteLLM Responses.

Two upstream warnings remain visible: Starlette's deprecated AnyIO BlockingPortal
alias during tests, and a Pydantic usage-shape serialization warning in the direct
LiteLLM Responses diagnostic. Neither failed execution. Do not suppress the latter
as a substitute for validating usage accounting during Responses integration.

Reproduce the environment:

```bash
uv sync --all-extras --all-groups --locked
uv run --no-sync pytest -q -ra -m 'not requires_network and not requires_api_key and not OpenAIIntegration'
uv run --no-sync python engineering/validation/live_native_runtime.py --env-file .env
```

Configure isolated Redis/MongoDB test URLs to reproduce backend coverage; without
those services, the corresponding external-service tests may skip. API validation
uses a local credential and makes paid synthetic requests. Credentials are not
part of this commit.

See [dependency-refresh-results.json](dependency-refresh-results.json) for direct
versions, lock changes and machine-readable live scenario results. The remaining
application work is in the [LiteLLM review](litellm-current-review.md).
