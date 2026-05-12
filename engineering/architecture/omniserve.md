# OmniServe Architecture

This is an internal architecture record under `engineering/architecture`, not
public product documentation. It is the source of truth for OmniServe serving
design.

OmniServe is the optional HTTP serving layer for an already-built
`OmniCoreAgent`. It must not become a second agent runtime, tracing system,
evaluation system, or workflow engine.

## Purpose

OmniServe exists to expose an agent over HTTP:

- accept REST and SSE requests
- call the configured agent
- expose health, readiness, tools, session, event, and metrics endpoints
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

## Metrics Boundary

OmniServe request metrics are per FastAPI app instance. They are exposed as
Prometheus text at `/prometheus`.

This is not full observability. It is request counting, active requests, and
request duration only. Agent-level usage remains available through
`agent.get_metrics()` and the `/metrics` endpoint.

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
- `api_prefix` must apply consistently to API routes.
- Background routes must use the same `api_prefix`, auth, rate-limit, logging,
  and timeout middleware as other protected API routes.
- OmniServe may own HTTP access to background execution, but
  `BackgroundAgentManager` remains the runtime owner for task stores, schedules,
  leases, retries, events, and workspace files.
- Public endpoints that intentionally bypass auth must be explicit and tested.
- `request_timeout` must be enforced for agent run endpoints.
- Docs and cookbooks must describe only shipped behavior.
