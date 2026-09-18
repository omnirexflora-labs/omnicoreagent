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
| M2 | Complete | (this commit) | New real MCP 2 probe server (`tests/fixtures/mcp_probe_server.py`, `MCPServer`) serving stdio, streamable HTTP, or SSE; its tools report the headers, working directory, and environment they received. 6 new tests drive `MCPClient` against it: stdio connects, lists all tools, returns structured data and errors, and honours `cwd` and `env`; streamable HTTP and SSE connect on free local ports and the configured header reaches the server; the session gets a float read timeout and `client_info` (`omnicoreagent` and its version); stdio `args` default to `[]`; `cwd` survives agent config normalization (`MCPToolConfig.cwd` added). Streamable HTTP now passes headers, timeouts, and auth through `create_mcp_http_client` (`httpx2`); the reported server name comes from `session.server_info`. Old tests: the two mocked stdio/SSE connect tests and their v1 session fixture were deleted (replaced by the real-server tests); the governance connect tests now set `session.server_info`. Still open for M4: cleanup logs the cross-task "cancel scope" warning (4 in this file's run); no probe process was left running. Full suite 1,336 passed, 14 skipped; ruff clean. |
