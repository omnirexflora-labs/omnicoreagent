# OmniServe Architecture

This is an internal architecture record under `engineering/architecture`, not
public product documentation. It is the source of truth for OmniServe serving
design.

OmniServe is the optional HTTP serving layer for an already-built
`OmniCoreAgent`. It must not become a second agent runtime, tracing system,
evaluation system, or workflow engine.

OmniServe live and replay streaming is telemetry-backed:
`TelemetryRecorder -> TelemetryStore -> TelemetryStream -> OmniServe SSE`.
New serving work should target telemetry and should not reintroduce a parallel
visibility stack.

## Purpose

OmniServe exists to expose an agent over HTTP:

- accept REST and SSE requests
- call the configured agent
- expose health, readiness, tools, session, event, and metrics endpoints
- expose background task control endpoints when background execution is enabled
- handle HTTP middleware concerns such as CORS, auth, request logging, rate
  limiting, timeout, and error responses
- serialize runtime objects into stable API responses
- manage agent startup and cleanup through FastAPI lifespan

## Non-Goals

OmniServe does not own:

- agent loop behavior
- tool execution
- MCP connection logic beyond startup/shutdown calls
- memory, workspace, artifacts, or subagent orchestration
- trace, evaluation, or feedback-loop design
- retry/circuit-breaker policy for agent internals

Those capabilities belong in the agent runtime or in a later dedicated
production observability design.

## Module Ownership

```text
serve/
  server.py              # OmniServe facade and uvicorn startup
  app_factory.py         # FastAPI app construction
  config.py              # OmniServeConfig and env overrides
  state.py               # typed access to app.state
  serialization.py       # runtime-object to API payload normalization
  sse.py                 # SSE event formatting and streaming helpers
  metrics.py             # per-app HTTP request metrics and /prometheus
  lifespan.py            # agent startup and cleanup
  routes/
    health.py            # /health, /ready
    runs.py              # /run, /run/sync
    sessions.py          # session history and event endpoints
    tools.py             # /tools
    metrics.py           # /metrics agent usage endpoint
    background.py        # /background durable task endpoints
  middleware/
    auth.py
    cors.py
    errors.py
    logging.py
    rate_limit.py
    timeout.py
```

## Serving Flow

1. User creates an `OmniCoreAgent`.
2. User passes it to `OmniServe(agent, config=...)`.
3. `server.py` delegates FastAPI construction to `app_factory.py`.
4. `app_factory.py` stores the agent/config in app state, installs middleware,
   installs per-app HTTP metrics, and mounts API routes.
5. Route handlers read the agent through `state.py`, call the runtime, and use
   `serialization.py` for HTTP-safe response payloads.
6. `lifespan.py` connects MCP servers on startup and calls agent cleanup on
   shutdown if those methods exist.

## Configuration Boundary

OmniServe configuration is owned by `OmniServeConfig`.

Defaults must support a zero-server-config local start. Production settings are
opt-in and must use explicit OmniCoreAgent-prefixed environment variables:

- `OMNICOREAGENT_SERVE_*` for HTTP serving behavior
- `OMNICOREAGENT_BACKGROUND_*` for background task serving behavior

Generic process variables such as `REDIS_URL` and `MONGODB_URI` are not part of
the OmniServe public contract. Background task stores may use Redis or MongoDB,
but the serving layer must read those locations through the
`OMNICOREAGENT_BACKGROUND_*` names so configuration stays explicit and docs stay
accurate.

Environment values should fail clearly when invalid. Silent ignore behavior is
bad production behavior because it makes a server appear correctly configured
while running with defaults.

## Runtime Lifecycle Boundary

OmniServe owns the HTTP lifespan around one served agent:

1. store agent/config/background manager in app state.
2. keep `omniserve_startup_complete` false while startup is in progress.
3. connect MCP servers when the served agent exposes `connect_mcp_servers`.
4. initialize the background manager when background execution is enabled.
5. register the served agent under `background_agent_id`.
6. start the background worker when configured.
7. mark startup complete only after dependencies are ready.
8. on shutdown, mark startup incomplete before stopping the background manager.
9. call agent cleanup when the served agent exposes `cleanup`.

Lifespan errors should fail startup or shutdown clearly. They should not leave a
half-started server that reports readiness while core dependencies failed.

Readiness is intentionally cheap. `/ready` combines the startup-complete flag,
agent initialization state, and configured MCP connection state. Agents without
configured MCP servers do not require an MCP client to be ready. Agents with
configured MCP servers must have connected MCP sessions. Readiness never
performs model calls, tool calls, background work, or network probes.

## HTTP API Boundary

OmniServe exposes one agent through a stable API surface:

- `/health` and `/ready`
- `/run` for SSE execution
- `/run/sync` for JSON execution
- `/events/{session_id}` for telemetry event replay/follow streams
- `/events/{session_id}/list` for stored telemetry events
- `/events/{session_id}/trace` for the current compact telemetry trace summary
  exposed by the agent runtime
- `/sessions/{session_id}/history`
- `/sessions/{session_id}` delete
- `/tools`
- `/metrics`
- `/prometheus`
- `/background/*` when background execution is enabled

When background execution is disabled, the background router is not mounted.
Disabled background execution should not expose dead `/background/*` endpoints
or stale OpenAPI paths.

`api_prefix` applies to API routes mounted by the agent router. Global FastAPI
documentation and Prometheus paths remain global unless a deliberate design
changes that.

## SSE Boundary

SSE is a serving transport over telemetry events.

`/run` must:

- create a fresh `run_id` for the request.
- attach that `run_id` to telemetry event emission.
- stream only events matching that `run_id`.
- include the route `session_id` in every event payload.
- stream final `complete` with the same `run_id`.
- emit a terminal session-ended chunk even after timeout or handled errors.
- avoid waiting forever on catch-up replay after the agent run has completed.

`/events/{session_id}` must:

- replay stored events for that session.
- follow live events for that session.
- deduplicate events by `event_id` when available.
- never mix events from another session.

This is not the future trace system. It is the reliable live/replay event
transport OmniServe owns today.

## Metrics Boundary

OmniServe request metrics are per FastAPI app instance. They are exposed as
Prometheus text at `/prometheus`.

This is not full observability. It is request counting, active requests, and
request duration only. Agent-level usage remains available through
`agent.get_metrics()` and the `/metrics` endpoint.

Rate-limit state is also per FastAPI process. It is a serving guardrail for
simple deployments, not a distributed quota system.

## Middleware Boundary

Middleware must be predictable and testable:

- public auth bypass paths are explicit.
- protected routes require bearer auth when auth is enabled.
- rate limiting applies to protected routes when enabled, including protected
  requests rejected by auth.
- request timeout is configured by `request_timeout`.
- CORS behavior follows `OmniServeConfig`.
- request logging adds process-time headers when enabled.
- unhandled serving errors become stable JSON responses.

Do not hide route-specific `HTTPException` responses behind generic `500`
payloads.

## Background Serving Boundary

OmniServe may expose HTTP control for background execution, but
`BackgroundAgentManager` remains the owner of task stores, scheduling, leases,
retries, cancellation, run events, and workspace output.

The serving layer is responsible for:

- constructing or accepting a background manager.
- registering the served agent.
- mapping HTTP requests to manager calls.
- preserving typed error/status responses.
- applying the same middleware and `api_prefix` rules as the rest of the API.

Background `wait=true` uses a serving wait budget derived from
`request_timeout` so OmniServe can return a structured `504` before the outer
HTTP timeout cancels response generation.

## Public Surface

The package-level serving exports should stay small:

- `OmniServe`
- `OmniServeConfig`

The serving layer should not publicly export retry/circuit-breaker helpers or
global metrics handles unless those features are deliberately designed as part
of the production agent harness.

## Invariants

- `import omnicoreagent` stays light and does not import FastAPI or provider
  clients.
- `omnicoreagent[serve]` owns FastAPI and Uvicorn dependencies.
- Multiple `OmniServe` app instances must not share request metrics state.
- Direct `OmniServe` serves an in-process agent object and therefore runs with
  one Uvicorn worker. Horizontal scaling is done by running multiple OmniServe
  processes behind a process manager or load balancer.
- `api_prefix` must apply consistently to API routes.
- Background routes must use the same `api_prefix`, auth, rate-limit, logging,
  and timeout middleware as other protected API routes.
- OmniServe may own HTTP access to background execution, but
  `BackgroundAgentManager` remains the runtime owner for task stores, schedules,
  leases, retries, events, and workspace files.
- Public endpoints that intentionally bypass auth must be explicit and tested.
- `request_timeout` must be enforced for agent run endpoints.
- Docs and cookbooks must describe only shipped behavior.
- OmniServe docs must use the single public model credential variable:
  `LLM_API_KEY`.
- OmniServe task-store docs must use `OMNICOREAGENT_BACKGROUND_*` variables.
- CLI examples must not ask users to export mutually exclusive backends in one
  copy-paste block.
- CLI/cookbook output must use the OmniCoreAgent package version when a version
  is shown. OmniServe does not have a separate version.
