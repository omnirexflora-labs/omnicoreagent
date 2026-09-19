# MCP v2 completion plan

Branch `refactor/native-tool-runtime`, started at `e08a675` (2026-09-18), after
the [telemetry trajectory completion plan](telemetry-trajectory-completion-plan.md)
passed Phase C. The research behind this plan, including every confirmed
breakage against `mcp` 2.2.0, is in the
[MCP v2 migration research](mcp-v2-migration-plan.md).

## Goal

An agent configured with MCP servers connects over stdio, streamable HTTP, and
SSE with the installed `mcp` 2.x SDK, offers their tools to the model, calls
them, and reports success, tool errors, protocol errors, timeouts, and dropped
sessions truthfully. Every MCP call is as readable in the trajectory as a
local tool call: the same ten checklist items hold. Connections open and close
cleanly, with no cross-task errors and no leftover child processes.

## Decisions (2026-09-18)

| Question | Decision |
| --- | --- |
| Handshake | Classic `ClientSession.initialize()` (protocol 2025-11-25). 2026-07-28 negotiation may be added later as an opt-in. |
| Server identity | The configured name identifies a server in routing, governance (`mcp_server`), and telemetry. The server-reported name and version are kept as metadata. |
| Structured content | When a result carries structured content, it is the data returned to the model; text blocks are added only when they carry something else. The full result stays in the telemetry record. |
| Dropped session | Reconnect once and retry the call; if that fails, the call is an error with its MCP code and the server is reported unavailable. Every reconnect is recorded in the trace. |
| SSE | Still supported. |
| Version bound | `mcp>=2.2.0,<3`. |
| Global OpenTelemetry tracer | Not installed by OmniCoreAgent. |

## Definition of done

| # | Area | Must hold |
| --- | --- | --- |
| 1 | Connect | stdio, streamable HTTP, and SSE connect to real local servers; a server that fails to connect does not stop the others and is reported with its reason. |
| 2 | Catalog | MCP tools appear in the native catalog with their input schema; tool listing follows pagination. |
| 3 | Results | Text, structured, multi-block, and error results are normalized correctly; `is_error=True` is an error; content blocks keep their wire names. |
| 4 | Errors | A protocol error (`MCPError`) and a per-call timeout become error results carrying the code; nothing is reported as success by mistake. |
| 5 | Identity | The configured name is used everywhere; reported name and version are metadata; a server without a configured name gets a deterministic one. |
| 6 | Lifecycle | Each connection is opened and closed by one owner task; cleanup raises nothing and leaves no child process; a dropped session reconnects once. |
| 7 | OAuth | The callback returns `AuthorizationCodeResult` with `iss`, does not block the event loop, and uses `httpx2` types. |
| 8 | Configuration | Fields are validated per transport; fields that do nothing for a transport are rejected. |
| 9 | Telemetry | MCP calls satisfy the trajectory checklist; the run header lists each server's status, reported identity, protocol version, and tool count; reconnects are recorded; readiness reports partially connected servers. |
| 10 | Proof | The trajectory acceptance includes an MCP server; the boundary audit passes; a live LiteLLM run calls an MCP tool. |

## Working rules

The same as the telemetry plan: one unit at a time; failing test first; focused
tests, full suite, `ruff check`; log the result and commit hash below; commit
and push each unit. Tests use real `mcp.types` objects and real local servers,
never hand-built v1 mocks. Bugs found on the way are fixed in the current unit
and recorded in its log entry.

## Units

### M1. Result and catalog fields
- Read `input_schema`, `is_error`, `structured_content` in `native_catalog.py`,
  `harness_tools.py`, and `tool_executor.py`.
- Serialize content blocks with `model_dump(by_alias=True, mode="json",
  exclude_none=True)`.
- Structured content preferred as the data (decision above).
- Replace the v1-attribute mocks in `tests/test_client.py` and
  `tests/test_tool_executor.py` with real `mcp.types` objects.

### M2. Session and transports
- Float `read_timeout_seconds`; explicit `client_info`; the reported identity
  from `session.server_info`.
- Streamable HTTP through a configured `httpx2.AsyncClient` (headers, timeout,
  read timeout, auth); float SSE timeouts; stdio `args` default to `[]`, with
  optional `cwd`.
- Real local servers on stdio, streamable HTTP, and SSE (free local ports).

### M3. Identity
- Sessions and tools keyed by the configured name; reported name and version
  stored as metadata; governance `mcp_server` and telemetry use the configured
  name; deterministic default name (no random suffix).

### M4. Lifecycle and errors
- One owner task per server that enters and exits the transport and session.
- A connect timeout covering transport, initialize, and paged `list_tools`.
- A per-call timeout below the agent's tool timeout.
- `MCPError` and timeouts converted to error results with their code.
- Reconnect once on a closed or terminated session; otherwise report.
- Cleanup with no swallowed cross-task errors and no leftover processes.

### M5. OAuth
- `AuthorizationCodeResult(code, state, iss)`; an async callback wait instead
  of the blocking `time.sleep` loop; no rewriting of the server URL; `httpx2`
  types. Tested with an in-process authorization server stub.

### M6. Configuration
- `MCPToolConfig` validated per transport; inapplicable fields rejected with a
  clear message; `mcp>=2.2.0,<3`.

### M7. Telemetry
- MCP server status in the run header; reconnect and connection-failure
  events; readiness reports partial connection.
- An MCP stdio server added to the trajectory acceptance scenario; all ten
  checklist items hold for MCP calls (success, tool error, protocol error,
  timeout, malformed arguments).

### M8. Proof and documentation
- Re-run `native_boundary_audit.py` and update its results.
- A live LiteLLM run that calls an MCP tool and reads end to end.
- MCP guide and configuration docs updated; the research document marked
  resolved.

## Out of scope

The 2026-07-28 protocol negotiation, server sampling/elicitation/roots
support, MCP resources and prompts, Windows stdio, and a global OpenTelemetry
tracer.

## Execution log

| Unit | Status | Commit | Evidence |
| --- | --- | --- | --- |
| M1 | Complete | `d637b49` | New `core/tools/mcp_results.py` is the one place that reads SDK tools and call results; the catalog, public tool listing, executor, and MCP guardrail use it. 10 new tests on real `mcp.types` objects: catalog and listing read `input_schema`; `is_error=True` is an error (was reported as success); structured content is the data when the text only repeats it; blocks that add something are kept beside it; wire names (`mimeType`) kept; errors keep their structured details. Checked against a real v2 stdio server (`MCPServer`): typed returns give the structured dict, plain `dict` returns arrive as JSON text only and pass through, raised exceptions are errors. Found during M1: (1) the guardrail scanned only text blocks, so an injection in structured content, now the data the model receives, would have gone unchecked; it now scans both; (2) SDK servers wrap scalar returns as `{"result": value}`; that wrapper is removed so the model gets the typed value. Old tests: the v1-mock executor test was deleted (replaced), and the catalog, listing, and guardrail tests now use real SDK objects instead of `SimpleNamespace`/`MagicMock` stand-ins. Also fixed a flaky background test (`test_cancel_after_failed_attempt_blocks_retry_requeue`, 1 failure in the first full run): under load the 1 s lease emitted a legitimate heartbeat between start and cancellation, and the test required an exact event list; it now checks the lifecycle order and that heartbeats fall only while the run is active (0 of 20 under CPU load, previously failing within 3). Full suite 1,332 passed, 14 skipped; acceptance checks passed; ruff clean. |
| M2 | Complete | `895a18d` | New real MCP 2 probe server (`tests/fixtures/mcp_probe_server.py`, `MCPServer`) serving stdio, streamable HTTP, or SSE; its tools report the headers, working directory, and environment they received. 6 new tests drive `MCPClient` against it: stdio connects, lists all tools, returns structured data and errors, and honours `cwd` and `env`; streamable HTTP and SSE connect on free local ports and the configured header reaches the server; the session gets a float read timeout and `client_info` (`omnicoreagent` and its version); stdio `args` default to `[]`; `cwd` survives agent config normalization (`MCPToolConfig.cwd` added). Streamable HTTP now passes headers, timeouts, and auth through `create_mcp_http_client` (`httpx2`); the reported server name comes from `session.server_info`. Old tests: the two mocked stdio/SSE connect tests and their v1 session fixture were deleted (replaced by the real-server tests); the governance connect tests now set `session.server_info`. Still open for M4: cleanup logs the cross-task "cancel scope" warning (4 in this file's run); no probe process was left running. Full suite 1,336 passed, 14 skipped; ruff clean. |
| M3 | Complete | `0c64390` | Servers are stored, routed, governed, and recorded under their configured name; the reported name, version, and protocol version are connection metadata (`server_info`, `protocol_version`). Governance authorizes the configured name once, before any process starts or network connection opens; the re-authorization by the server-reported name is removed (the server controls that value). Unnamed servers get a stable name from their command or host plus a hash of transport, command, args, and URL, instead of a random suffix. Policy decision events now carry `mcp_server` in their always-kept metadata. 6 new tests against the probe server: configured-name keys and metadata; two servers reporting the same name both connect (the second was rejected before); a strict policy that knows only the configured name allows the connection (it was denied before); an agent run routes, governs (`matched_rule_ids`, `mcp_server`), and records (`mcp.tool.call` actor) by configured name; stable default names; readiness by configured name. New `engineering/validation/mcp_interop.py`: the official reference server `@modelcontextprotocol/server-everything` 2026.8.31 (TypeScript SDK 1.30) over stdio and streamable HTTP, 6 checks each (text, sum, structured content, image with `mimeType`, invalid arguments `-32602`, unknown tool), all passed. Old tests: the requested-versus-reported alias tests were rewritten for the configured-name contract and the reported-name re-authorization test was deleted. Found during M3: at load average 10 to 12 (other processes) the acceptance scenario's delegation exceeded its 3 s tool limit (4 of 6 runs); the limit is now 10 s. One failure where the failing tool's error was missing could not be reproduced in 18 runs; the check now reports the call and whether the trace lost a write, so a recurrence explains itself. Full suite 1,340 passed, 14 skipped; ruff clean. |
| Dependencies | Complete | `c3442d9` | Requested during M3. Lock upgraded (25 packages, including litellm 1.101.0 and httpx2 2.13.0); direct minimums raised to the tested versions; mcp 2.2.0 already latest; openai, importlib-metadata, filelock, and pydantic-core held only by their upstream dependents. Full suite, ruff, MCP interop, and a live LiteLLM run passed. |
| M4 | Complete | `82a851e` | New `mcp_clients_connection/connection.py`: each server connection is opened and closed by its own owner task, so the SDK's cancel scopes are entered and exited in one task. A connect timeout (`connect_timeout`, default 30 s) covers the transport, handshake, and every tool page; a connection that never becomes usable is cancelled at once. Tool listing follows pagination. Protocol errors (`MCPError`) become error results carrying `mcp_error` code, message, and data; an optional per-call `call_timeout` gives `-32001`. A dropped session (`-32000` connection closed, `-32600` session terminated or not found, closed streams) is reconnected once and the call retried; the entry is updated in place (`reconnects`, `last_error`); a failed reconnect returns an error naming both causes and marks the server unavailable. Failed connections are recorded in `state.failures` without stopping other servers. Cleanup closes all connections concurrently and returns and logs close errors. 12 new tests on two real servers (the probe server and a new low-level fixture that pages tools, raises protocol errors, is slow, and reports its PID): cleanup leaves no process and no warning; pagination; protocol-error code; per-call timeout, then a normal call; a killed stdio server reconnects (new PID) and the call succeeds; a failed reconnect; a silent server times out in 2 s while another connects; removing one server keeps the other; a restarted HTTP server reconnects; only a gone session triggers a reconnect; config carries the timeouts. The cross-task "cancel scope" warning is gone (4 before). Found during M4: (1) before the change, a server that never answers blocked `connect_to_servers()` indefinitely; (2) a restarted HTTP server answers with "Session not found", not the "Session terminated" the SDK source suggested, found only by the restart test; (3) the handler's module-level SDK import made agents without MCP servers load pydantic at startup (caught by the startup test), now lazy; (4) a stale lost-notice from a replaced connection is ignored. Old tests: the mocked cleanup, remove, and governance-allow connect tests were deleted (real tests replace them); the full-stack smoke test's v1-style fake session now answers with real SDK types. Reference-server interop passed on both transports. Full suite 1,347 passed, 14 skipped; ruff clean. |
| M5 | Complete | `bd9ddb4` | OAuth rewritten on the v2 SDK: the callback returns `AuthorizationCodeResult(code, state, iss)`; the redirect is received by a loopback callback server whose result reaches the event loop through a future (the old `time.sleep` polling loop blocked it); the callback server starts only when a login is needed and stops off the loop; the browser is opened off the loop; the server URL is used as configured (the old code stripped `/mcp` and `/sse`, breaking discovery on other paths); the redirect uses `127.0.0.1` on `auth.callback_port` or a free port (was `3000 + n`); `auth.callback_timeout` is configurable. New fixture `tests/fixtures/mcp_oauth_server.py`: an MCP server that is its own authorization server (auto-approve, dynamic registration, RFC 9207 `iss` in the redirect). 7 new tests; only the browser is replaced: the full flow (discovery, registration, PKCE, callback, token exchange, authorized tool call) passes over streamable HTTP and SSE while a heartbeat shows the loop never stalls over 0.5 s; a redirect claiming another issuer is rejected; the configured URL is kept; configured and free callback ports; the callback returns code, state, and issuer; an authorization error is reported. Found during M5: (1) `HTTPServer.shutdown()` blocked the loop for about 0.5 s (caught by the heartbeat), now run in a thread; (2) the SDK does not require `iss`, it validates it when present, so passing it through is what makes a mix-up (another issuer) fail, verified with a wrong issuer; (3) connection failures were recorded as "unhandled errors in a TaskGroup"; failures and reconnect errors now record the first real cause (for example "Authorization response iss mismatch: https://attacker.example != http://127.0.0.1:…"). Full suite 1,354 passed, 14 skipped; ruff clean. |
| M6 | Complete | `0615f7a` | MCP server settings are validated per transport, for agent config and for `MCPClient` built directly: `command`, `args`, `cwd`, `env` apply to stdio (`command` required); `url`, `headers`, `timeout`, `sse_read_timeout`, `auth` apply to sse and streamable_http (`url` required, http or https); `name`, `transport_type`, `connect_timeout`, `call_timeout` apply to all. A setting that does nothing for the transport, an unknown setting (was a bare `TypeError`), wrong types, non-positive timeouts, and invalid `auth` (method must be `oauth`; `callback_port` 1 to 65535; positive `callback_timeout`; no other keys) are rejected with a message naming the server. `streamable-http` (the SDK's spelling) is accepted. HTTP timeouts are no longer added to stdio servers (the dataclass defaulted them to 60 and 120; the transports apply those defaults). `mcp[cli]>=2.2.0,<3`; the lock records only the new constraint (refreshed offline during a DNS outage; no versions changed). 29 new tests, including a pin check; all 11 MCP configs in the docs and examples still validate. Full suite 1,383 passed, 14 skipped; ruff clean. |
| M7 | Complete | (this commit) | The run header lists each configured MCP server (`mcp_servers`: name, transport, status connected / disconnected / failed / not_connected, reported name and version, protocol version, tool count, reconnects, error) from `MCPClient.server_status()`, which never includes commands, arguments, environment, URLs, or headers. A reconnect is recorded as an `mcp_reconnect` event on the tool call it happened in (outcome reconnected or failed, the dropped-session reason, the reconnect error). Trajectory tool calls now show `server` and `reconnects`. `/ready` reports `mcp_servers` per server, so a partial failure is visible (additive; `mcp_connected` unchanged). The trajectory acceptance now includes a real MCP stdio server (`engineering/validation/fixtures/acceptance_mcp_server.py`) and a sixth step with one parallel MCP batch: structured success, tool error (`is_error`), protocol error (`-32602`), per-call timeout (`-32001`), and malformed arguments (rejected); all ten checklist items pass directly (full and default capture) and through OmniServe, the MCP observations reach the final turn exactly, and the fixture was regenerated (no local paths or commands in it). Direct runs connect on their own loop; the served run connects through the OmniServe lifespan, which the acceptance now runs (`with TestClient`). 5 new tests on real servers: the header with a connected and a failed server and no secrets; a call naming its server; a reconnect and a failed reconnect recorded on the call; readiness per server. An MCP run with a reconnect validates against the portable evidence schema. Old tests: three readiness tests compared the full response and now include the new `mcp_servers` field. Found during M7: the served acceptance run never ran the app lifespan, so an MCP server would never have connected there. The session crashed (segmentation fault in the tool host) while the full suite ran; all work was on disk and the suite was rerun. Reference-server interop passed. Full suite 1,388 passed, 14 skipped; ruff clean. |
