# Background Agents Specification

This is the behavior contract for OmniCoreAgent background agents. The
architecture record explains why the system exists and how it is owned. This
specification defines what the implementation must do.

Read this with:

- `engineering/architecture/background-agents.md`
- `src/omnicoreagent/background`
- `src/omnicoreagent/core/runtime/omnicore_agent.py`
- `src/omnicoreagent/core/workspace`
- `src/omnicoreagent/core/events`
- `tests/test_background_agent.py`

When this specification changes, implementation and tests must change in the
same PR.

This specification describes the target rebuild. It is intentionally stricter
than the current implementation.

---

## Scope

This specification covers:

- public background-agent manager API
- typed specs and state records
- task store behavior
- schedule behavior
- dispatch and queue behavior
- supervisor lifecycle behavior
- retry, timeout, cancellation, and overlap behavior
- workspace behavior
- event behavior
- error behavior
- required tests

This specification does not cover:

- OmniCoreAgent reasoning loop internals
- tool execution internals
- MCP client internals
- conversation memory backend implementation
- workspace storage driver implementation
- OmniServe HTTP endpoints for background agents
- future trace/evaluation/feedback-loop design

---

## Public Exports

The public background-agent exports should be:

```python
BackgroundAgentManager
BackgroundAgentSpec
BackgroundTaskSpec
BackgroundRun
ScheduleSpec
RetryPolicy
OverlapPolicy
SessionPolicy
RunStatus
TaskStore
InMemoryTaskStore
SQLiteTaskStore
```

`APSchedulerBackend` may remain public only if it is documented as a scheduler
adapter, not the background-agent architecture.

`BackgroundOmniCoreAgent` should not remain part of the final public API. The
background system should compose `OmniCoreAgent`.

---

## Model Contracts

### `BackgroundAgentSpec`

Fields:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `agent_id` | `str` | yes | Stable identifier |
| `name` | `str | None` | no | Agent name; defaults to `agent_id` |
| `system_instruction` | `str | None` | when reconstructing | Agent system instruction |
| `model_config` | `dict | None` | when reconstructing | OmniCoreAgent model config |
| `agent_config` | `dict` | no | OmniCoreAgent runtime config |
| `mcp_tools` | `list[dict]` | no | MCP server tool configs |
| `workspace_config` | `dict | None` | no | Workspace config override |
| `metadata` | `dict` | no | User metadata |
| `created_at` | `datetime` | generated | Creation timestamp |
| `updated_at` | `datetime` | generated | Update timestamp |

Validation:

- `agent_id` must be non-empty.
- `agent_id` must be stable and URL/log safe: letters, numbers, `_`, `-`, `.`.
- `model_config` must pass OmniCoreAgent model config normalization when present.
- `agent_config.enable_workspace_files` must be true for background tasks unless
  the manager explicitly forces it on.

### `BackgroundTaskSpec`

Fields:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `task_id` | `str` | yes | Stable task identifier |
| `agent_id` | `str` | yes | Registered agent identifier |
| `query` | `str` | yes | Prompt passed to `OmniCoreAgent.run()` |
| `schedule` | `ScheduleSpec` | yes | Scheduling behavior |
| `enabled` | `bool` | no | Defaults to true |
| `timeout` | `int | None` | no | Run timeout in seconds |
| `retry_policy` | `RetryPolicy` | no | Retry behavior |
| `overlap_policy` | `OverlapPolicy` | no | Active-run behavior |
| `session_policy` | `SessionPolicy` | no | Session selection |
| `workspace_policy` | `WorkspacePolicy` | no | Run workspace layout |
| `metadata` | `dict` | no | User metadata |
| `created_at` | `datetime` | generated | Creation timestamp |
| `updated_at` | `datetime` | generated | Update timestamp |

Validation:

- `task_id` must be non-empty and URL/log safe.
- `query` must be non-empty.
- `timeout` must be positive when provided.
- `enabled` defaults to true.
- `retry_policy.max_retries` defaults to 0.
- `overlap_policy` defaults to `skip_if_running`.
- `session_policy` defaults to `task`.

### `ScheduleSpec`

Fields:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `type` | `interval | cron | once | manual` | yes | Schedule type |
| `seconds` | `int | None` | for interval | Interval in seconds |
| `expression` | `str | None` | for cron | Cron expression |
| `run_at` | `datetime | None` | for once | One-shot run time |
| `timezone` | `str` | no | Defaults to UTC |
| `start_at` | `datetime | None` | no | Earliest allowed run |
| `end_at` | `datetime | None` | no | Latest allowed run |
| `jitter_seconds` | `int | None` | no | Randomized schedule jitter |
| `misfire_policy` | `str` | no | Defaults to `skip_missed` |

Validation:

- `interval` requires `seconds > 0`.
- `cron` requires a valid cron expression.
- `once` requires `run_at`.
- `manual` must not require scheduler fields.
- `end_at` must be after `start_at` when both exist.

### `RetryPolicy`

Fields:

| Field | Type | Default |
|-------|------|---------|
| `max_retries` | `int` | `0` |
| `initial_delay` | `int` | `30` |
| `max_delay` | `int` | `300` |
| `backoff` | `fixed | exponential` | `fixed` |
| `retry_on` | `list[str]` | `["exception", "timeout"]` |

Validation:

- `max_retries >= 0`
- `initial_delay >= 0`
- `max_delay >= initial_delay` unless `initial_delay` is 0

### `OverlapPolicy`

Allowed values:

```text
skip_if_running
queue_next
cancel_previous
allow_parallel
```

Default:

```text
skip_if_running
```

### `SessionPolicy`

Allowed values:

```text
task
run
fixed
```

Default:

```text
task
```

Rules:

- `task` session id: `background:{agent_id}:{task_id}`
- `run` session id: `background:{run_id}`
- `fixed` requires explicit `session_id`

### `BackgroundRun`

Fields:

| Field | Type | Meaning |
|-------|------|---------|
| `run_id` | `str` | Stable run identifier |
| `task_id` | `str` | Source task |
| `agent_id` | `str` | Executing agent |
| `status` | `RunStatus` | Current run state |
| `attempt` | `int` | Current attempt number |
| `max_attempts` | `int` | Retry limit plus first attempt |
| `session_id` | `str` | Memory session id |
| `workspace_path` | `str` | Run workspace namespace |
| `queued_at` | `datetime | None` | Queue timestamp |
| `claimed_at` | `datetime | None` | Worker claim timestamp |
| `started_at` | `datetime | None` | Execution start timestamp |
| `finished_at` | `datetime | None` | Terminal timestamp |
| `cancel_requested_at` | `datetime | None` | Cancellation request timestamp |
| `error` | `str | None` | Final error |
| `result_preview` | `str | None` | Small response preview |
| `metadata` | `dict` | User/runtime metadata |

Allowed statuses:

```text
scheduled
queued
claimed
running
retrying
completed
failed
cancelled
timeout
skipped
```

Terminal statuses:

```text
completed
failed
cancelled
timeout
skipped
```

Transition rules:

- `scheduled -> queued`
- `queued -> claimed`
- `claimed -> running`
- `running -> retrying`
- `retrying -> running`
- `running -> completed`
- `running -> failed`
- `running -> timeout`
- `queued|claimed|running|retrying -> cancelled`
- `scheduled|queued -> skipped`
- terminal states do not transition

---

## Manager Contract

### Construction

```python
manager = BackgroundAgentManager(
    task_store=None,
    scheduler_backend=None,
    memory_router=None,
    event_router=None,
)
```

Defaults:

- `task_store`: `SQLiteTaskStore` after the rebuild, `InMemoryTaskStore` only
  when explicitly requested or during tests
- `scheduler_backend`: APScheduler adapter
- `memory_router`: `MemoryRouter("in_memory")` unless passed
- `event_router`: `EventRouter("in_memory")` unless passed

### Agent Registration

```python
await manager.register_agent(agent_id: str, agent: OmniCoreAgent)
await manager.register_agent_spec(spec: BackgroundAgentSpec | dict)
await manager.unregister_agent(agent_id: str)
await manager.get_agent(agent_id: str)
await manager.list_agents()
```

Rules:

- registering the same `agent_id` twice raises unless `replace=True` is passed
- unregistering an agent with active runs raises unless `force=True` is passed
- direct agent objects are stored in process; serializable specs are stored in
  `TaskStore`

### Task Registration

```python
await manager.register_task(spec: BackgroundTaskSpec | dict)
await manager.update_task(task_id: str, patch: dict)
await manager.delete_task(task_id: str)
await manager.get_task(task_id: str)
await manager.list_tasks(agent_id: str | None = None)
```

Rules:

- registering a task for an unknown `agent_id` raises
- deleting a task disables schedule and preserves historical runs unless
  `delete_runs=True`
- updating schedule must reschedule the task from the task store source of truth

### Runtime Control

```python
await manager.start()
await manager.shutdown()
await manager.pause_task(task_id: str)
await manager.resume_task(task_id: str)
await manager.run_now(task_id: str, query: str | None = None)
await manager.cancel_run(run_id: str)
```

Rules:

- `start()` loads enabled tasks from task store and schedules them
- `shutdown()` stops accepting new runs, asks workers to finish or cancel based
  on shutdown policy, then closes scheduler
- `run_now()` creates a run even for manual tasks
- `cancel_run()` is idempotent

### Inspection

```python
await manager.get_run(run_id: str)
await manager.list_runs(task_id: str | None = None, status: str | None = None)
await manager.get_task_status(task_id: str)
await manager.get_manager_status()
await manager.get_run_events(run_id: str)
```

Status payloads must be serializable dictionaries.

---

## Task Store Contract

`TaskStore` is a protocol.

Required methods:

```python
async def save_agent(spec: BackgroundAgentSpec) -> None: ...
async def get_agent(agent_id: str) -> BackgroundAgentSpec | None: ...
async def delete_agent(agent_id: str) -> None: ...
async def list_agents() -> list[BackgroundAgentSpec]: ...

async def save_task(spec: BackgroundTaskSpec) -> None: ...
async def get_task(task_id: str) -> BackgroundTaskSpec | None: ...
async def delete_task(task_id: str) -> None: ...
async def list_tasks(agent_id: str | None = None) -> list[BackgroundTaskSpec]: ...

async def create_run(run: BackgroundRun) -> None: ...
async def get_run(run_id: str) -> BackgroundRun | None: ...
async def update_run(run_id: str, patch: dict) -> BackgroundRun: ...
async def list_runs(task_id: str | None = None, status: str | None = None) -> list[BackgroundRun]: ...

async def request_cancel(run_id: str) -> None: ...
async def is_cancel_requested(run_id: str) -> bool: ...
```

Store requirements:

- all writes are atomic at the record level
- IDs are unique
- updates to terminal runs are rejected unless explicitly allowed for metadata
- list methods return deterministic order by creation time unless specified
- SQLite store persists across process restart

---

## Scheduler Contract

`SchedulerBackend` is a protocol.

Required methods:

```python
async def start() -> None: ...
async def shutdown() -> None: ...
async def schedule(task: BackgroundTaskSpec, callback: Callable) -> None: ...
async def unschedule(task_id: str) -> None: ...
async def pause(task_id: str) -> None: ...
async def resume(task_id: str) -> None: ...
async def next_run_time(task_id: str) -> datetime | None: ...
async def is_scheduled(task_id: str) -> bool: ...
```

Scheduler requirements:

- `manual` tasks are not scheduled
- disabled tasks are not scheduled
- startup rebuilds schedule from task store
- schedule callbacks do not execute the agent; they notify the dispatcher
- invalid schedule specs raise validation errors before reaching scheduler

---

## Dispatcher Contract

Dispatcher input:

```python
task_id
due_at
trigger_reason
```

Dispatcher behavior:

1. Load task from task store.
2. If missing, emit skipped and return.
3. If disabled, emit skipped and return.
4. Apply overlap policy.
5. Create `BackgroundRun`.
6. Persist run as `queued`.
7. Enqueue run.
8. Emit `background_run_queued`.

Overlap behavior:

- `skip_if_running`: if active run exists for task, create skipped run or emit
  skipped event without creating a normal run.
- `queue_next`: create queued run.
- `cancel_previous`: request cancel for active runs, then queue new run.
- `allow_parallel`: create queued run without checking active runs.

---

## Supervisor Contract

Supervisor loop:

```text
dequeue run
claim run
resolve agent
resolve task
resolve session id
resolve workspace namespace
mark running
execute agent
handle retry/timeout/cancel/error
mark terminal
emit events
```

Execution call:

```python
result = await agent.run(query=task.query, session_id=run.session_id)
```

Supervisor requirements:

- do not call agent if cancel is already requested
- set `started_at` before calling agent
- enforce timeout with `asyncio.wait_for`
- preserve partial workspace output on failure
- update `attempt` before each attempt
- apply retry policy outside scheduler
- write `result_preview` from final response
- emit terminal event exactly once
- mark run terminal even when event emission fails

---

## Workspace Contract

Background runs require workspace files.

Default namespace:

```text
background/{agent_id}/{task_id}/{run_id}
```

Standard files:

```text
output.md
run.json
events.jsonl
logs/
artifacts/
subagents/
```

Supervisor must write `run.json` with:

- `run_id`
- `task_id`
- `agent_id`
- `status`
- `session_id`
- `started_at`
- `finished_at`
- `error`
- `result_preview`

The agent prompt/run context should include:

```text
Write durable outputs for this background run under:
/workspace/background/{agent_id}/{task_id}/{run_id}/
Use output.md for the final durable result.
Use scratchpad/progress files when the task is long.
```

The final response should still be returned and stored as preview, but
workspace output is the durable artifact of the background run.

---

## Event Contract

Required event names:

```text
background_agent_registered
background_task_registered
background_task_scheduled
background_task_paused
background_task_resumed
background_task_deleted
background_run_queued
background_run_started
background_run_retrying
background_run_completed
background_run_failed
background_run_timeout
background_run_cancelled
background_run_skipped
```

Common payload fields:

| Field | Required | Meaning |
|-------|----------|---------|
| `agent_id` | yes | Agent identifier |
| `task_id` | when task-related | Task identifier |
| `run_id` | when run-related | Run identifier |
| `session_id` | when run-related | Memory/event session id |
| `status` | when status-related | Current status |
| `attempt` | when run-related | Attempt number |
| `timestamp` | yes | ISO timestamp |
| `error` | on failures | Error string |
| `workspace_path` | on run events | Run workspace namespace |

Events must be emitted through `EventRouter`.

Run state must still be persisted in `TaskStore`; event emission is not the
source of truth.

---

## Error Contract

Use typed background errors:

```text
BackgroundAgentError
AgentAlreadyRegisteredError
AgentNotFoundError
TaskAlreadyRegisteredError
TaskNotFoundError
RunNotFoundError
InvalidScheduleError
TaskStoreError
SchedulerError
RunCancelledError
RunTimeoutError
RunExecutionError
```

Public manager methods should raise typed errors for programming mistakes and
return serializable state for normal status queries.

Do not hide scheduler/store/execution failures behind `False` returns.

---

## Required Tests

### Model Tests

- validates agent/task/run IDs
- validates schedule types
- validates retry policy
- validates timeout
- validates default policies
- rejects unknown status transitions

### Task Store Tests

- stores and retrieves agent specs
- stores and retrieves task specs
- stores and updates run records
- rejects terminal run mutation
- supports cancellation flag
- SQLite store persists across reconnect
- list methods are deterministic

### Scheduler Tests

- schedules interval tasks
- schedules cron tasks
- ignores manual tasks
- ignores disabled tasks
- unschedules deleted tasks
- rebuilds jobs from task store on startup
- reports next run time

### Dispatcher Tests

- due task creates queued run
- disabled task is skipped
- missing task is skipped
- overlap policies work
- enqueue failure marks run failed or skipped according to policy

### Supervisor Tests

- executes agent runner once
- records completed run
- records failed run
- enforces timeout
- handles cancellation before start
- handles cancellation during retry wait
- applies fixed retry
- applies exponential retry
- writes run metadata to workspace
- emits lifecycle events

### Manager Tests

- register agent
- register task
- start schedules enabled tasks
- pause/resume task
- run now
- cancel run
- list runs
- shutdown stops scheduler and workers
- no direct import of APScheduler on root package import unless background extra is used

### Integration Tests

- in-memory end-to-end background run with fake agent
- SQLite-backed end-to-end restart recovery
- workspace output persists for local workspace
- event trace can be built for a background run
- cookbook background example runs with a short schedule and clean shutdown

---

## Migration Contract

The legacy implementation can be removed rather than preserved.

Migration rules:

- replace `BackgroundOmniCoreAgent` subclassing with supervisor composition
- replace `TaskRegistry` with `TaskStore`
- replace mutable dict config flow with typed validation
- keep APScheduler only as an adapter
- update public docs only after behavior is implemented
- update cookbook examples in the same PR as public API changes

Temporary compatibility wrappers are allowed only inside tests or migration
helpers and must not appear in public docs.

---

## Acceptance Criteria

The background-agent rebuild is acceptable when:

- background runs have durable `run_id` records
- tasks survive process restart with SQLite task store
- run status can be inspected without reading event streams
- scheduled runs, manual runs, cancellation, timeout, and retry are tested
- background run output is written under workspace
- lifecycle events are emitted and traceable
- root import remains lightweight
- public docs describe only shipped behavior
- old subclass-centered design is removed
