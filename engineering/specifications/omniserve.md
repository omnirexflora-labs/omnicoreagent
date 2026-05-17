# OmniServe Specification

This is an internal specification. It defines the behavior OmniServe must
implement and test. Public docs live under `docs/` and `cookbook/`.

## Purpose

OmniServe turns one existing `OmniCoreAgent` instance into a REST/SSE API.

It owns the HTTP serving contract:

- configuration
- FastAPI app construction
- route mounting
- lifespan startup/shutdown
- middleware
- serialization
- SSE transport
- request metrics
- background task HTTP control

It does not own the agent reasoning loop, tool execution, memory, workspace,
MCP protocol internals, background scheduling internals, trace, evals, policy,
or sandboxing.

## Public Exports

The serving package exports:

```python
from omnicoreagent import OmniServe, OmniServeConfig
```

No additional serving internals are public API unless deliberately documented in
a later specification.

## Configuration Contract

`OmniServeConfig` is the source of truth for serving configuration.

Defaults:

| Field | Default | Meaning |
|-------|---------|---------|
| `host` | `0.0.0.0` | Bind address |
| `port` | `8000` | Bind port |
| `workers` | `1` | Uvicorn worker processes |
| `api_prefix` | `""` | Prefix for agent API routes |
| `enable_docs` | `true` | Enable `/docs` |
| `enable_redoc` | `true` | Enable `/redoc` |
| `cors_enabled` | `true` | Enable CORS middleware |
| `cors_origins` | `["*"]` | Allowed origins |
| `cors_methods` | `["*"]` | Allowed methods |
| `cors_headers` | `["*"]` | Allowed headers |
| `cors_credentials` | `true` | Allow credentials |
| `auth_enabled` | `false` | Require bearer auth on protected routes |
| `auth_token` | `None` | Bearer token |
| `request_logging` | `true` | Log requests |
| `log_level` | `INFO` | Uvicorn log level |
| `request_timeout` | `300` | HTTP request timeout seconds |
| `rate_limit_enabled` | `false` | Enable in-process rate limiting |
| `rate_limit_requests` | `100` | Requests per window |
| `rate_limit_window` | `60` | Rate limit window seconds |
| `background_enabled` | `true` | Expose background routes |
| `background_agent_id` | `default` | Served-agent id in background manager |
| `background_task_store` | `in_memory` | Background task store backend |
| `background_start_worker` | `true` | Start background scheduler/worker |

Environment variables override code values:

| Variable | Field |
|----------|-------|
| `OMNICOREAGENT_SERVE_HOST` | `host` |
| `OMNICOREAGENT_SERVE_PORT` | `port` |
| `OMNICOREAGENT_SERVE_WORKERS` | `workers` |
| `OMNICOREAGENT_SERVE_API_PREFIX` | `api_prefix` |
| `OMNICOREAGENT_SERVE_ENABLE_DOCS` | `enable_docs` |
| `OMNICOREAGENT_SERVE_ENABLE_REDOC` | `enable_redoc` |
| `OMNICOREAGENT_SERVE_CORS_ENABLED` | `cors_enabled` |
| `OMNICOREAGENT_SERVE_CORS_ORIGINS` | `cors_origins` |
| `OMNICOREAGENT_SERVE_CORS_METHODS` | `cors_methods` |
| `OMNICOREAGENT_SERVE_CORS_HEADERS` | `cors_headers` |
| `OMNICOREAGENT_SERVE_CORS_CREDENTIALS` | `cors_credentials` |
| `OMNICOREAGENT_SERVE_AUTH_ENABLED` | `auth_enabled` |
| `OMNICOREAGENT_SERVE_AUTH_TOKEN` | `auth_token` |
| `OMNICOREAGENT_SERVE_REQUEST_LOGGING` | `request_logging` |
| `OMNICOREAGENT_SERVE_LOG_LEVEL` | `log_level` |
| `OMNICOREAGENT_SERVE_REQUEST_TIMEOUT` | `request_timeout` |
| `OMNICOREAGENT_SERVE_RATE_LIMIT_ENABLED` | `rate_limit_enabled` |
| `OMNICOREAGENT_SERVE_RATE_LIMIT_REQUESTS` | `rate_limit_requests` |
| `OMNICOREAGENT_SERVE_RATE_LIMIT_WINDOW` | `rate_limit_window` |
| `OMNICOREAGENT_BACKGROUND_ENABLED` | `background_enabled` |
| `OMNICOREAGENT_BACKGROUND_AGENT_ID` | `background_agent_id` |
| `OMNICOREAGENT_BACKGROUND_TASK_STORE` | `background_task_store` |
| `OMNICOREAGENT_BACKGROUND_TASK_STORE_URL` | SQL or Redis task-store URL |
| `OMNICOREAGENT_BACKGROUND_TASK_STORE_URI` | MongoDB task-store URI |
| `OMNICOREAGENT_BACKGROUND_TASK_STORE_DATABASE` | MongoDB database |
| `OMNICOREAGENT_BACKGROUND_TASK_STORE_PREFIX` | Redis key prefix |
| `OMNICOREAGENT_BACKGROUND_TASK_STORE_COLLECTION_PREFIX` | MongoDB collection prefix |
| `OMNICOREAGENT_BACKGROUND_TASK_STORE_CONNECT_TIMEOUT` | Backend connect timeout seconds |
| `OMNICOREAGENT_BACKGROUND_START_WORKER` | `background_start_worker` |

Invalid env values must fail clearly during configuration creation. They must
not be silently ignored.

Generic env fallbacks such as `REDIS_URL`, `MONGODB_URI`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or `GROQ_API_KEY` are not part of the
OmniServe public configuration contract.

## Background Task Store Normalization

`OmniServeConfig.background_task_store_config()` returns the value passed to
`BackgroundAgentManager`.

Rules:

- if `background_task_store` is a dict, return it unchanged.
- if `OMNICOREAGENT_BACKGROUND_TASK_STORE_URI` or
  `background_task_store_uri` is set, return a MongoDB config.
- if a task-store URI is set with any backend other than unset, `in_memory`, or
  `mongodb`, fail clearly.
- if `OMNICOREAGENT_BACKGROUND_TASK_STORE_URL` or
  `background_task_store_url` is set and `background_task_store` is unset or
  `in_memory`, return a SQL config. URL-only means SQL.
- if a task-store URL is set and `background_task_store == "redis"`, return a
  Redis config.
- if a task-store URL is set with any backend other than unset, `in_memory`,
  `sql`, or `redis`, fail clearly.
- if `background_task_store == "redis"` and no task-store URL is configured,
  fail clearly before manager initialization. OmniServe must not read
  `REDIS_URL`.
- if `background_task_store == "mongodb"` and no task-store URI is configured,
  fail clearly before manager initialization. OmniServe must not read
  `MONGODB_URI`.
- otherwise return the configured backend string.

The serving layer must not read `REDIS_URL` or `MONGODB_URI` to fill missing
task-store locations.

## Route Contract

The agent router exposes:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness |
| `GET` | `/ready` | Readiness |
| `POST` | `/run` | Agent run with SSE stream |
| `POST` | `/run/sync` | Agent run with JSON response |
| `GET` | `/events/{session_id}` | Replay/follow session events over SSE |
| `GET` | `/events/{session_id}/list` | List stored session events |
| `GET` | `/events/{session_id}/trace` | Agent-provided compact event summary |
| `GET` | `/sessions/{session_id}/history` | Session messages |
| `DELETE` | `/sessions/{session_id}` | Clear session messages |
| `GET` | `/tools` | List available tools |
| `GET` | `/metrics` | Agent usage metrics |

When `background_enabled` is true, background routes expose:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/background/status` | Manager status |
| `POST` | `/background/agents` | Register served agent or agent spec |
| `GET` | `/background/agents` | List background agents |
| `GET` | `/background/agents/{agent_id}` | Inspect agent spec |
| `DELETE` | `/background/agents/{agent_id}` | Delete agent spec |
| `POST` | `/background/tasks` | Create task |
| `GET` | `/background/tasks` | List tasks |
| `GET` | `/background/tasks/{task_id}` | Inspect task |
| `GET` | `/background/tasks/{task_id}/status` | Task status |
| `PATCH` | `/background/tasks/{task_id}` | Patch task |
| `POST` | `/background/tasks/{task_id}/run` | Queue/manual run |
| `POST` | `/background/tasks/{task_id}/pause` | Pause scheduled dispatch |
| `POST` | `/background/tasks/{task_id}/resume` | Resume scheduled dispatch |
| `DELETE` | `/background/tasks/{task_id}` | Delete task |
| `POST` | `/background/runs/{run_id}/cancel` | Cancel run |
| `GET` | `/background/runs` | List runs |
| `GET` | `/background/runs/{run_id}` | Inspect run |
| `GET` | `/background/runs/{run_id}/attempts` | List run attempts |
| `GET` | `/background/runs/{run_id}/events` | Replay run lifecycle events |
| `GET` | `/background/runs/{run_id}/workspace` | Inspect run workspace files |

`api_prefix` applies to these agent router paths.

Global FastAPI routes are not mounted through the agent router:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/docs` | Swagger UI when enabled |
| `GET` | `/redoc` | ReDoc UI when enabled |
| `GET` | `/openapi.json` | OpenAPI schema |
| `GET` | `/prometheus` | OmniServe HTTP metrics |

These global paths are not prefixed by `api_prefix`.

## Auth And Public Paths

When auth is disabled, all routes are accessible.

When auth is enabled:

- `/health` and `/ready` bypass auth.
- prefixed health/readiness paths bypass auth when `api_prefix` is set.
- `/docs`, `/redoc`, `/openapi.json`, and `/prometheus` bypass auth.
- every other route requires `Authorization: Bearer <token>`.
- missing or invalid auth returns `401` JSON.

Public bypass paths must stay explicit and tested.

## Rate Limiting

Rate limiting is in-process per FastAPI app instance.

When enabled:

- public auth-bypass paths are exempt.
- protected routes are keyed by client IP.
- `X-Forwarded-For` is used when present.
- `X-Real-IP` is used when present and `X-Forwarded-For` is absent.
- otherwise the ASGI client host is used.
- denied requests return `429` JSON and rate-limit headers.
- allowed requests include rate-limit headers.

Distributed rate limiting is not part of this phase.

## Timeout Contract

`request_timeout <= 0` disables OmniServe request timeout behavior.

`/run/sync`:

- wraps `agent.run()` with `asyncio.wait_for` when timeout is positive.
- returns `504` when the run exceeds the timeout.

`/run` SSE:

- passes `request_timeout` to the SSE run helper.
- emits an `error` SSE chunk when the run exceeds the timeout.
- emits a final session-ended SSE chunk after timeout handling.

Background `wait=true`:

- uses a wait budget derived from `request_timeout`.
- returns the terminal run if it finishes inside the wait budget.
- returns structured `504` if the run remains non-terminal after the wait
  budget.
- the `504` detail includes `message`, `run_id`, `task_id`, `status`,
  `wait_timeout_seconds`, and `request_timeout_seconds`.
- when `request_timeout <= 0`, OmniServe does not create its own HTTP wait
  budget. The background manager decides whether the run completes inline or
  returns the latest non-terminal state according to its `run_now(wait=True)`
  contract.

## SSE Contract

`/run` SSE sequence:

1. session-started event.
2. zero or more runtime events for the current `run_id`.
3. `complete` event with normalized run result and same `run_id`.
4. session-ended event.

On handled timeout/error:

1. session-started event.
2. `error` event with `session_id` and `run_id`.
3. session-ended event.

Every runtime event emitted through `/run` includes:

- `session_id`
- the request `run_id`
- normalized event fields from the runtime event object

Concurrent `/run` streams using the same `session_id` must not stream each
other's runtime events.

`/events/{session_id}`:

- starts with a session-streaming event.
- replays stored events for that session.
- follows live events for that session.
- deduplicates by `event_id` when available.
- ends with a session-ended event when the client disconnects or the generator
  closes.

## Serialization Contract

`agent.run()` responses normalize to:

```python
{
    "response": str,
    "agent_name": str,
    "metric": dict | None,
}
```

If the runtime returns a dict, OmniServe reads `response`, `agent_name`, and
`metric`. If the runtime returns any other value, OmniServe stringifies it as
`response`.

Runtime events normalize to JSON-ready dictionaries using:

- mappings
- dataclasses
- `to_dict()`
- Pydantic `model_dump()`
- legacy `dict()`
- public `__dict__`
- string fallback

## Lifespan Contract

Startup:

1. set `app.state.start_time`.
2. call `agent.connect_mcp_servers()` when present.
3. initialize background manager when enabled.
4. register the served agent under `background_agent_id`.
5. start background worker when `background_start_worker` is true.

Shutdown:

1. shut down background manager when present.
2. call `agent.cleanup()` when present.

Startup failures must not report readiness as healthy.

## Readiness Contract

`/health` reports process liveness and uptime.

`/ready` reports:

- `ready`
- `agent_name`
- `initialized`
- `mcp_connected`

Readiness must remain cheap. It should not execute model calls, tool calls, or
background work.

## Error Contract

Route-specific `HTTPException` responses must keep their intended status and
detail payload.

Unhandled serving errors return stable JSON:

```json
{
  "error": "InternalServerError",
  "message": "An internal server error occurred",
  "detail": "..."
}
```

Future hardening may hide `detail` behind a debug flag. Until then, docs must
not promise sanitized production error bodies.

## Metrics Contract

`/prometheus` exposes per-app OmniServe HTTP metrics:

- `omniserve_requests_total`
- `omniserve_requests_success`
- `omniserve_requests_error`
- `omniserve_active_requests`
- request duration summary fields
- path-specific request counters

Metrics are per process and per FastAPI app instance.

`/metrics` exposes agent runtime usage metrics returned by `agent.get_metrics()`.

## CLI Contract

`omniserve run`:

- loads a Python file containing `agent` or `create_agent()`.
- builds `OmniServeConfig`.
- starts `OmniServe`.

`omniserve quickstart`:

- creates a simple `OmniCoreAgent`.
- starts `OmniServe`.

`omniserve config --env-example`:

- prints `LLM_API_KEY`.
- prints optional `OMNICOREAGENT_SERVE_*` settings.
- prints background durable task-store examples as mutually exclusive choices.
- must not print stale `OMNISERVE_*`, generic provider key names, `REDIS_URL`,
  or `MONGODB_URI`.

CLI version/output uses the installed OmniCoreAgent package version. OmniServe
does not have a separate version. Hard-coded stale `v0.0.1` strings are not
acceptable.

## Docker Generator Contract

The Docker generator creates a Dockerfile for a provided agent file.

It may set non-sensitive defaults such as:

- `AGENT_PATH`
- local workspace backend and directory

It must not bake credentials into the image.

Generated usage examples should use the same image tag consistently and should
not introduce stale names that conflict with OmniServe or OmniCoreAgent docs.

## Documentation Contract

Public OmniServe docs and cookbook examples must:

- use `LLM_API_KEY` as the model credential variable.
- use `OMNICOREAGENT_SERVE_*` for serving config.
- use `OMNICOREAGENT_BACKGROUND_*` for background serving config.
- document in-memory as the zero-config default.
- document SQL, Redis, and MongoDB as durable task-store choices.
- say to choose one durable backend per deployment.
- use `$RUN_ID` for copy-pasteable curl examples.
- not claim full trace/eval/observability features in this phase.

## Test Requirements

The OmniServe hardening phase must maintain or add tests for:

- import laziness for `omnicoreagent` and optional serve dependencies.
- default config values.
- env overrides.
- invalid env values failing clearly.
- task-store config normalization for in-memory, SQL, Redis, and MongoDB.
- absence of generic `REDIS_URL`/`MONGODB_URI` dependency in OmniServe config.
- CLI env example correctness.
- auth middleware with and without `api_prefix`.
- public bypass paths.
- rate limiting allowed and denied paths.
- request timeout for `/run/sync`.
- SSE timeout/error sequence for `/run`.
- concurrent same-session `/run` isolation by `run_id`.
- `/events/{session_id}` replay/follow isolation by `session_id`.
- lifecycle startup/shutdown order.
- background API auth, prefix, disabled state, wait=true, timeout, event replay,
  workspace inspection, and OpenAPI response shapes.
- request metrics are per app instance.
- docs tests asserting public docs use current env names and endpoint examples.

## Acceptance Criteria

OmniServe is production-hardened for this phase when:

- configuration behavior is explicit, prefixed, documented, and tested.
- serving lifecycle is deterministic and tested.
- REST responses and SSE streams have stable contracts.
- background task HTTP control remains aligned with `BackgroundAgentManager`.
- middleware behavior is predictable with `api_prefix`.
- docs and cookbook examples match the code.
- full test suite passes.
- reviewer/evaluator agents complete without blocking findings.
