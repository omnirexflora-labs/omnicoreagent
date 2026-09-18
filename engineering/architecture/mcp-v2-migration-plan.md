# MCP v2 SDK migration plan

Research record, 2026-09-18, branch `refactor/native-tool-runtime`. No MCP
code has changed yet. MCP work starts only after the telemetry trajectory plan
([telemetry-trajectory-completion-plan.md](telemetry-trajectory-completion-plan.md))
passes Phase C.

## Versions

- Installed: `mcp` 2.2.0 and `mcp-types` 2.2.0. It depends on `httpx2`, not `httpx`.
- Pinned: `pyproject.toml` `"mcp[cli]>=2.2.0"`; `uv.lock` locks 2.2.0. There is
  no upper bound.

## Where OmniCoreAgent uses the SDK

Direct SDK imports are confined to `src/omnicoreagent/mcp_clients_connection/`.

| Location | Use |
| --- | --- |
| `transports.py:8-11` | `StdioServerParameters`, `sse_client`, `stdio_client`, `streamable_http_client` |
| `transports.py:100`, `:128`, `:144` | `sse_client(**kw)`, `streamable_http_client(**kw)` (expects 3 return values), `stdio_client(params)` |
| `client.py:6`, `:128-133` | `ClientSession(read, write, read_timeout_seconds=timedelta(seconds=300))` |
| `client.py:134-135` | `session.initialize()`, `init_result.serverInfo.name` |
| `client.py:217-218` | `session.list_tools()`, first page only |
| `oauth.py:9-10`, `:140-146`, `:160-166` | `OAuthClientProvider`, `TokenStorage`, OAuth models; callback returns a tuple |

Code that reads MCP result objects:

| Location | Reads |
| --- | --- |
| `core/tools/mcp_tool_handler.py:35` | `session.call_tool(name, args)`, no per-call timeout |
| `core/tools/tool_executor.py:71`, `:78`, `:88-89` | `block.model_dump(exclude_none=True)`, `structuredContent`, `isError` |
| `core/tools/native_catalog.py:100` | `tool.inputSchema` |
| `core/runtime/harness_tools.py:72` | `tool.inputSchema` |

Identity consumers: `core/agents/native_tools.py`, `core/tools/governed_tool_runner.py`,
`governance/capabilities.py`, `serve/readiness.py`. Configuration:
`core/runtime/config.py` (`MCPToolConfig` and its normalization).

## Confirmed breakages against 2.2.0

Evidence: `engineering/validation/native_boundary_audit.py` re-run (matches
`native-boundary-audit-results.json`) and scratch scripts against real local
servers.

1. **stdio cannot connect.** `ClientSession` receives a `timedelta`; v2 expects
   float seconds. Error: `unsupported operand type(s) for +: 'float' and
   'datetime.timedelta'`, raised on the first request.
2. **Streamable HTTP cannot connect.** v2 is
   `streamable_http_client(url, *, http_client=None, terminate_on_close=True)`
   and returns two values. Headers, timeouts, and auth move to an
   `httpx2.AsyncClient`. Error: `unexpected keyword argument 'headers'`.
3. **MCP tools make the tool catalog raise.** v2 types use snake_case
   (`input_schema`, `output_schema`, `is_error`, `structured_content`,
   `server_info`, `next_cursor`, `mime_type`). Error: `'Tool' object has no
   attribute 'inputSchema'`.
4. **Tool errors are reported as success.** `getattr(result, "isError")` and
   `getattr(result, "structuredContent")` now always return `None`, so an
   `is_error=True` result becomes `status: "success"` and structured data is
   dropped. This is the most serious defect.
5. **Server name read fails.** `init_result.serverInfo` raises; use
   `session.server_info`. On 2026-07-28 connections the name can be `None`.
6. **OAuth callback shape.** v2 reads `AuthorizationCodeResult(code, state,
   iss)`; the adapter returns `(code, state)` and never captures `iss`. Found by
   reading the code; the flow was not run.
7. **Content block serialization changes.** `model_dump(exclude_none=True)` now
   emits `mime_type`/`meta` instead of `mimeType`/`_meta`; use
   `model_dump(by_alias=True, mode="json", exclude_none=True)`.
8. **stdio without `args` fails** (older than v2). Normalization drops `None`
   values and `transports.py` reads `server["args"]`.
9. **Cleanup crosses tasks.** Connections are opened inside `asyncio.gather`
   tasks and closed from another task; each close raises "Attempted to exit
   cancel scope in a different task" and the error is swallowed.
10. **Tests mask the breakage.** `tests/test_client.py` and
    `tests/test_tool_executor.py` mock v1 attribute names.

SSE still connects. The v2 SDK itself works end to end on stdio, streamable
HTTP, and SSE against real local servers.

## v2 client changes that matter

Sources: the SDK migration guide
(<https://py.sdk.modelcontextprotocol.io/migration/>,
<https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md>)
cross-checked against the installed source.

- **Transports.** Streamable HTTP configuration moves to the HTTP client; the
  default client uses 30 s timeouts and a 300 s read timeout (a bare
  `httpx2.AsyncClient()` uses 5 s, too short for the stream). `sse_client`
  keeps its signature but `auth` must be an `httpx2.Auth`. `stdio_client`
  merges `env` over a default environment.
- **Session.** Timeouts are float seconds and raise `MCPError` with code
  `-32001`; `McpError` became `MCPError`. `list_tools` pages with
  `PaginatedRequestParams(cursor=...)`. New accessors: `server_info`,
  `server_capabilities`, `protocol_version`, `instructions`.
  `ClientSession.initialize()` performs the pre-2026 handshake; `discover()` or
  `Client(mode="auto")` negotiates 2026-07-28.
- **Errors.** A server handler exception arrives as a JSON-RPC `MCPError`, not
  `is_error=True`. Over streamable HTTP, non-2xx responses become per-request
  errors and a dropped session is `MCPError(-32600, "Session terminated")`.
- **Wire and tracing.** Every request carries `_meta`; the SDK creates
  OpenTelemetry client spans and injects `traceparent` only when a global tracer
  provider exists. OmniCoreAgent does not install one today.
- **Server side.** `FastMCP` became `MCPServer` (default name `"mcp-server"`).

## Fix plan

Each unit is small and independently testable.

1. **Result fields.** Read snake_case fields in `native_catalog.py`,
   `harness_tools.py`, and `tool_executor.py`; serialize content with
   `by_alias=True, mode="json"`. Keep the public `structuredContent` key. Test
   with real `mcp.types` objects.
2. **Session construction.** Float `read_timeout_seconds`, explicit
   `client_info`, `session.server_info` for the reported name.
3. **Transports.** Streamable HTTP through a configured `httpx2.AsyncClient`
   (headers, timeout, read timeout, auth); float SSE timeouts; stdio
   `args` default to `[]` plus optional `cwd`.
4. **OAuth.** Return `AuthorizationCodeResult` with `iss`; replace the blocking
   `time.sleep` callback loop; stop rewriting the server URL; use `httpx2` types.
5. **Identity.** Key sessions and tools by the configured server name; store the
   reported name/version as metadata. Governance `mcp_server` and the telemetry
   actor use the configured name. Make the default configured name
   deterministic.
6. **Lifecycle.** One owner task per server (enter and exit in the same task);
   connect timeout covering transport, initialize, and paged `list_tools`;
   per-call timeout below the agent tool timeout; `MCPError` converted to an
   error result with its code; explicit partial-server status in readiness and
   telemetry; lazy reconnect on a closed or terminated session; no swallowed
   cleanup errors.
7. **Configuration.** Validate fields per transport and reject those that do not
   apply.
8. **Tests.** Real `mcp.types` unit tests; transport argument tests; OAuth
   callback test; stdio smoke through `MCPClient` with an improved fixture
   (unknown-tool rejection, error, structured, and paginated tools);
   streamable HTTP and SSE smoke on a free local port; cleanup test with no
   leftover child processes; an agent tool call asserting telemetry actor,
   server identity, and governance `mcp_server`; re-run
   `native_boundary_audit.py`.

## Open questions for the maintainer

1. Handshake: keep `ClientSession.initialize()` (2025-11-25), or negotiate
   2026-07-28 with `discover()` / `Client(mode="auto")`? The newer protocol
   refuses server sampling, elicitation, and roots requests and may omit the
   server name.
2. Identity: configured name or server-reported name? Configured is
   recommended, but it changes existing governance policy and telemetry values.
3. Structured content: v2 servers always attach it, so every typed tool would
   return `{"content": [...], "structuredContent": {...}}`. Accept that, or keep
   it only for errors and multi-block results?
4. SSE: keep supporting it or deprecate it?
5. Reconnect: automatic retry or report only?
6. Version bound: add `mcp<3`?
7. Tracing: should OmniCoreAgent ever install a global OpenTelemetry tracer
   provider (which enables SDK spans and `traceparent` injection)?

Not verified: the OAuth flow end to end, Windows stdio, third-party
2026-only servers, and real streamable HTTP reconnect behaviour.
