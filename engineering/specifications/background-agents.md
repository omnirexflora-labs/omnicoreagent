# Background Agents Specification

This specification defines the behavior contract for OmniCoreAgent background
execution.

Read this with:

- `engineering/architecture/background-agents.md`
- `src/omnicoreagent/background`
- `src/omnicoreagent/core/runtime`
- `src/omnicoreagent/core/workspace`
- `src/omnicoreagent/core/events`
- `src/omnicoreagent/core/memory_store`

When this specification changes, implementation and tests must change with it.

---

## Scope

This specification covers:

- public background manager API
- typed specs and state records
- task-store configuration and behavior
- schedule behavior
- dispatch and queue behavior
- supervisor lifecycle behavior
- retry, timeout, cancellation, overlap, heartbeat, and recovery behavior
- workspace behavior
- event behavior
- error behavior
- required tests

This specification does not cover:

- OmniCoreAgent reasoning loop internals
- tool execution internals
- MCP client internals
- memory backend implementation
- workspace storage driver implementation
- trace/evaluation/feedback-loop design

---

## Public Exports

The background package exports:

```python
BackgroundAgentManager
BackgroundAgentSpec
BackgroundTaskSpec
BackgroundScheduleState
BackgroundRun
BackgroundAttempt
ScheduleSpec
RetryPolicy
OverlapPolicy
SessionPolicy
WorkspacePolicy
RunStatus
TaskStoreBackend
TaskStoreConfig
AbstractTaskStore
TaskStoreRouter
SchedulerBackend
InMemoryTaskStore
SqlTaskStore
RedisTaskStore
MongoDBTaskStore
BackgroundAgentError
AgentAlreadyRegisteredError
AgentNotFoundError
TaskAlreadyRegisteredError
TaskNotFoundError
RunNotFoundError
InvalidScheduleError
InvalidTaskStoreError
TaskStoreError
SchedulerError
RunLeaseError
RunCancelledError
RunTimeoutError
RunExecutionError
```

Concrete optional backend classes must be lazy exports. Importing
`omnicoreagent` or `omnicoreagent.background` must not import Redis, MongoDB,
SQLAlchemy, or scheduler packages until the matching backend is selected.
Scheduler implementations are internal adapters unless explicitly documented as
extension points.

---

## Backend Names

Task-store backend names:

```text
in_memory
sql
redis
mongodb
```

Rules:

- `in_memory` is ephemeral and intended for tests/development.
- `sql` covers SQLite and Postgres through one backend family.
- `redis` stores task state in Redis.
- `mongodb` stores task state in MongoDB.
- The manager uses one task store for its lifecycle.
- Switching task-store backend during active runs is not supported.
- Moving state between task-store backends requires explicit export/import or
  transfer tooling.

Redis requirements:

- task, schedule, run, and attempt records must not use TTL.
- Redis deployments used for durable background state must enable AOF or an
  equivalent managed durability setting with an explicit acknowledged-write loss
  bound. RDB-only persistence is not sufficient for strict durable task state.
- eviction policy must not evict task-store keys.
- atomic claim, transition, overlap, and schedule-advance operations must use
  transactions or Lua scripts.

SQL requirements:

- scheduled dispatch, overlap checks, `cancel_previous`, run claiming, lease
  refresh, and terminal transitions must run in database transactions.
- SQL backends must enforce uniqueness for `(task_id, occurrence_id)` when
  `occurrence_id` is not null.
- claim and transition queries must use row-level locking or an equivalent
  compare-and-set condition.
- Postgres must use an isolation level that prevents duplicate active runs for
  guarded overlap policies.

MongoDB requirements:

- task-store writes that must be atomic across schedule/run/attempt records must
  use MongoDB transactions on deployments that support them.
- deployments must use majority write concern for durable task state.
- MongoDB backends must create a unique index for `(task_id, occurrence_id)`
  when `occurrence_id` is not null.
- claim and transition updates must include expected status and lease token in
  the filter.

---

## Construction

```python
manager = BackgroundAgentManager(
    task_store=None,
    scheduler_backend=None,
    memory_router=None,
    event_router=None,
    workspace=None,
    worker_id=None,
)
```

Defaults:

- `task_store`: `{"backend": "sql", "url": "sqlite:///.omnicoreagent/background.db"}`
- `scheduler_backend`: default scheduler adapter implementing `SchedulerBackend`
- `memory_router`: normal OmniCoreAgent default memory router
- `event_router`: normal OmniCoreAgent default event router
- `workspace`: normal OmniCoreAgent default workspace
- `worker_id`: generated stable process identifier

Accepted `task_store` forms:

```python
"in_memory"
"sql"
{"backend": "sql", "url": "sqlite:///.omnicoreagent/background.db"}
{"backend": "redis", "url": "redis://localhost:6379/0", "prefix": "oca:bg"}
{"backend": "mongodb", "uri": "mongodb://localhost:27017", "database": "omnicoreagent"}
AbstractTaskStore(...)
```

If an `AbstractTaskStore` instance is passed, the manager uses it directly.

### `TaskStoreBackend`

Allowed values:

```text
in_memory
sql
redis
mongodb
```

### `TaskStoreConfig`

Common fields:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `backend` | `TaskStoreBackend` | yes | Store backend |
| `prefix` | `str | None` | no | Key/table/collection prefix |
| `connect_timeout` | `float | None` | no | Backend connection timeout |

Backend-specific fields:

| Backend | Fields |
|---------|--------|
| `in_memory` | no required fields |
| `sql` | `url`; accepts SQLite and Postgres URLs; defaults to `sqlite:///.omnicoreagent/background.db` when omitted |
| `redis` | `url`; Redis must use persistence and no eviction for task records |
| `mongodb` | `uri`, `database`; optional `collection_prefix` |

Unknown fields raise `InvalidTaskStoreError`. `uri` is only accepted for
MongoDB. `url` is used for SQL and Redis.

Bare string construction is accepted only for `in_memory` and `sql`. Redis and
MongoDB require explicit config dictionaries so connection and durability choices
are visible at construction time.

---

## Model Contracts

### `BackgroundAgentSpec`

Fields:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `agent_id` | `str` | yes | Stable agent identifier |
| `name` | `str | None` | no | Display/runtime name |
| `system_instruction` | `str | None` | no | Instruction used when reconstructing an agent |
| `model_config` | `dict | None` | no | OmniCoreAgent model config |
| `agent_config` | `dict` | no | OmniCoreAgent runtime config |
| `mcp_tools` | `list[dict]` | no | MCP tool server configs |
| `local_tools_ref` | `str | None` | no | Application-owned local tool set reference |
| `workspace_config` | `dict | None` | no | Workspace override |
| `metadata` | `dict` | no | User metadata |
| `created_at` | `datetime` | generated | Creation timestamp |
| `updated_at` | `datetime` | generated | Update timestamp |

Validation:

- `agent_id` is required.
- `agent_id` contains only letters, numbers, `_`, `-`, and `.`.
- `model_config` is validated by the same normalization path used by
  `OmniCoreAgent`.
- `metadata` defaults to `{}`.

### `BackgroundTaskSpec`

Fields:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `task_id` | `str` | yes | Stable task identifier |
| `agent_id` | `str` | yes | Registered agent identifier |
| `query` | `str` | yes | Prompt passed to `OmniCoreAgent.run()` |
| `schedule` | `ScheduleSpec` | yes | Scheduling behavior |
| `enabled` | `bool` | no | Defaults to true |
| `timeout_seconds` | `int | None` | no | Run timeout |
| `retry_policy` | `RetryPolicy` | no | Retry behavior |
| `overlap_policy` | `OverlapPolicy` | no | Active-run behavior |
| `session_policy` | `SessionPolicy` | no | Memory session behavior |
| `workspace_policy` | `WorkspacePolicy` | no | Workspace namespace behavior |
| `metadata` | `dict` | no | User metadata |
| `created_at` | `datetime` | generated | Creation timestamp |
| `updated_at` | `datetime` | generated | Update timestamp |

Validation:

- `task_id` is required.
- `task_id` contains only letters, numbers, `_`, `-`, and `.`.
- `query` is required and non-empty.
- `timeout_seconds` is positive when provided.
- `enabled` defaults to true.
- `retry_policy.max_retries` defaults to 0.
- `overlap_policy` defaults to `skip_if_running`.
- `session_policy` defaults to `task`.
- `workspace_policy` always creates a run workspace.

### `ScheduleSpec`

Fields:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `type` | `manual | interval | cron | once` | yes | Schedule type |
| `seconds` | `int | None` | for interval | Interval duration |
| `expression` | `str | None` | for cron | Cron expression |
| `run_at` | `datetime | None` | for once | One-shot run time |
| `timezone` | `str` | no | Defaults to UTC |
| `start_at` | `datetime | None` | no | Earliest due time |
| `end_at` | `datetime | None` | no | Latest due time |
| `jitter_seconds` | `int | None` | no | Random schedule jitter |
| `misfire_policy` | `skip_missed | run_once | queue_all` | no | Missed trigger behavior |

Validation:

- `interval` requires `seconds > 0`.
- `cron` requires a valid cron expression.
- `once` requires `run_at`.
- `manual` does not require scheduler fields.
- `end_at` must be after `start_at` when both exist.
- `jitter_seconds` must be non-negative when provided.

### `RetryPolicy`

Fields:

| Field | Type | Default |
|-------|------|---------|
| `max_retries` | `int` | `0` |
| `initial_delay_seconds` | `int` | `30` |
| `max_delay_seconds` | `int` | `300` |
| `backoff` | `fixed | exponential` | `fixed` |
| `retry_on` | `list[str]` | `["exception", "timeout"]` |

Validation:

- `max_retries >= 0`
- `initial_delay_seconds >= 0`
- `max_delay_seconds >= initial_delay_seconds` unless
  `initial_delay_seconds == 0`

Timeout semantics:

- an attempt timeout records the attempt as `timeout`.
- if timeout is retryable and attempts remain, the run transitions to
  `retrying`, waits according to policy, then transitions to `queued`.
- the run transitions to terminal `timeout` only when no retry remains or
  timeout is not retryable.

### `OverlapPolicy`

Allowed values:

```text
skip_if_running
queue_next
cancel_previous
allow_parallel
```

### `SessionPolicy`

Fields:

| Field | Type | Default |
|-------|------|---------|
| `mode` | `task | run | fixed` | `task` |
| `session_id` | `str | None` | required for `fixed` |

Session ID rules:

- `task`: `background:{agent_id}:{task_id}`
- `run`: `background:{run_id}`
- `fixed`: exact supplied `session_id`

### `WorkspacePolicy`

Fields:

| Field | Type | Default |
|-------|------|---------|
| `namespace_template` | `str` | `background/{agent_id}/{task_id}/{run_id}` |
| `write_run_json` | `bool` | `true` |
| `write_events_jsonl` | `bool` | `true` |

Background runs always require workspace output. If no workspace is configured,
the manager uses the default local workspace.

### `BackgroundScheduleState`

Fields:

| Field | Type | Meaning |
|-------|------|---------|
| `task_id` | `str` | Source task |
| `next_due_at` | `datetime | None` | Next due occurrence |
| `last_due_at` | `datetime | None` | Last due occurrence |
| `last_dispatched_at` | `datetime | None` | Last successfully dispatched occurrence |
| `paused` | `bool` | Whether scheduling is paused |
| `schedule_revision` | `int` | Monotonic revision for compare-and-set updates |
| `misfire_cursor` | `str | None` | Backend-specific cursor for missed occurrences |
| `updated_at` | `datetime` | Last schedule-state update |

Each due occurrence has a stable `occurrence_id`. The store uses
`occurrence_id` to prevent duplicate run creation for the same scheduled
occurrence.

Occurrence IDs are unique per task. The portable uniqueness key is:

```text
(task_id, occurrence_id)
```

`occurrence_id` is derived from the schedule type, schedule revision, and due
timestamp:

```text
{schedule_type}:{schedule_revision}:{due_at_iso}
```

Manual runs do not use occurrence IDs.

### `BackgroundRun`

Fields:

| Field | Type | Meaning |
|-------|------|---------|
| `run_id` | `str` | Stable run identifier |
| `task_id` | `str` | Source task |
| `agent_id` | `str` | Executing agent |
| `status` | `RunStatus` | Current state |
| `attempt` | `int` | Current attempt number |
| `max_attempts` | `int` | First attempt plus retries |
| `query_snapshot` | `str` | Run input captured at dispatch time |
| `trigger_type` | `str` | manual, interval, cron, once |
| `triggered_at` | `datetime` | Trigger timestamp |
| `due_at` | `datetime | None` | Schedule due timestamp |
| `occurrence_id` | `str | None` | Stable schedule occurrence id |
| `trigger_metadata` | `dict` | Trigger reason/source metadata |
| `session_id` | `str` | Memory session id |
| `workspace_path` | `str` | Run workspace namespace |
| `lease_owner` | `str | None` | Worker holding the run |
| `lease_token` | `str | None` | Fencing token for the current lease |
| `lease_generation` | `int` | Monotonic lease generation |
| `lease_expires_at` | `datetime | None` | Lease expiry |
| `heartbeat_at` | `datetime | None` | Last heartbeat |
| `queued_at` | `datetime | None` | Queue timestamp |
| `claimed_at` | `datetime | None` | Claim timestamp |
| `started_at` | `datetime | None` | Execution start |
| `finished_at` | `datetime | None` | Terminal timestamp |
| `cancel_requested_at` | `datetime | None` | Cancellation request |
| `error` | `str | None` | Final error |
| `result_preview` | `str | None` | Small response preview |
| `metadata` | `dict` | User/runtime metadata |

### `BackgroundAttempt`

Fields:

| Field | Type | Meaning |
|-------|------|---------|
| `attempt_id` | `str` | Stable attempt identifier |
| `run_id` | `str` | Parent run |
| `attempt_number` | `int` | Attempt index starting at 1 |
| `reason` | `str` | initial, retry, recovery, lease_expired |
| `status` | `str` | running, completed, failed, timeout, cancelled |
| `worker_id` | `str` | Worker executing attempt |
| `lease_token` | `str` | Lease token held by the worker |
| `started_at` | `datetime` | Attempt start |
| `finished_at` | `datetime | None` | Attempt end |
| `error` | `str | None` | Attempt error |
| `retry_delay_seconds` | `int | None` | Delay before next attempt |

---

## Run Status

Allowed statuses:

```text
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

Allowed transitions:

- `queued -> claimed`
- `claimed -> running`
- `running -> retrying`
- `retrying -> queued`
- `queued -> claimed`
- `claimed -> running`
- `running -> completed`
- `running -> failed`
- `running -> timeout`
- `queued -> cancelled`
- `claimed -> cancelled`
- `running -> cancelled`
- `retrying -> cancelled`
- `queued -> skipped`

Schedule state tracks due work before a run exists. A run starts at `queued`
after the dispatcher creates it. Terminal runs cannot be mutated except for
non-state metadata explicitly allowed by the store.

---

## Manager API

### Agent Operations

```python
await manager.register_agent(agent_id: str, agent: OmniCoreAgent, replace: bool = False)
await manager.register_agent_spec(spec: BackgroundAgentSpec | dict, replace: bool = False)
await manager.unregister_agent(agent_id: str, force: bool = False)
await manager.get_agent(agent_id: str)
await manager.list_agents()
```

Rules:

- duplicate agent IDs raise unless `replace=True`.
- unregistering an agent with active runs raises unless `force=True`.
- direct agent objects are process-local.
- serializable agent specs are persisted through the task store.

### Task Operations

```python
await manager.register_task(
    spec: BackgroundTaskSpec | dict | None = None,
    *,
    task_id: str | None = None,
    agent_id: str | None = None,
    query: str | None = None,
    schedule: ScheduleSpec | dict | None = None,
    timeout_seconds: int | None = None,
    retry_policy: RetryPolicy | dict | None = None,
    overlap_policy: OverlapPolicy | str | None = None,
    session_policy: SessionPolicy | dict | None = None,
    workspace_policy: WorkspacePolicy | dict | None = None,
    metadata: dict | None = None,
    replace: bool = False,
)
await manager.update_task(task_id: str, patch: dict)
await manager.delete_task(task_id: str, delete_runs: bool = False)
await manager.get_task(task_id: str)
await manager.list_tasks(agent_id: str | None = None)
```

Rules:

- registering a task for an unknown `agent_id` raises.
- duplicate task IDs raise unless `replace=True`.
- callers pass either `spec` or keyword fields, not both.
- updating schedule state reschedules from task-store truth.
- deleting a task removes scheduler triggers.
- historical runs are preserved unless `delete_runs=True`.

### Runtime Operations

```python
await manager.start()
await manager.shutdown()
await manager.pause_task(task_id: str)
await manager.resume_task(task_id: str)
await manager.run_now(task_id: str, query: str | None = None)
await manager.cancel_run(run_id: str)
await manager.recover_expired_runs()
```

Rules:

- `start()` initializes store, scheduler, queue, dispatcher, and supervisor.
- `start()` rebuilds enabled schedules from the task store.
- `shutdown()` stops accepting new runs and then stops workers by shutdown
  policy.
- `run_now()` creates a run for any registered enabled task, including manual
  tasks.
- `cancel_run()` is idempotent.
- `recover_expired_runs()` claims expired leases according to recovery policy.

### Inspection Operations

```python
await manager.get_run(run_id: str)
await manager.list_runs(task_id: str | None = None, status: str | None = None)
await manager.list_attempts(run_id: str)
await manager.get_task_status(task_id: str)
await manager.get_manager_status()
await manager.get_run_events(run_id: str)
```

Status payloads must be serializable dictionaries.

---

## `AbstractTaskStore` Protocol

Required methods:

```python
async def initialize() -> None: ...
async def close() -> None: ...

async def save_agent(spec: BackgroundAgentSpec) -> None: ...
async def get_agent(agent_id: str) -> BackgroundAgentSpec | None: ...
async def delete_agent(agent_id: str) -> None: ...
async def list_agents() -> list[BackgroundAgentSpec]: ...

async def save_task(spec: BackgroundTaskSpec) -> None: ...
async def get_task(task_id: str) -> BackgroundTaskSpec | None: ...
async def delete_task(task_id: str) -> None: ...
async def list_tasks(agent_id: str | None = None, enabled: bool | None = None) -> list[BackgroundTaskSpec]: ...

async def save_schedule_state(state: BackgroundScheduleState) -> None: ...
async def get_schedule_state(task_id: str) -> BackgroundScheduleState | None: ...
async def get_due_schedules(now: datetime, limit: int) -> list[tuple[BackgroundTaskSpec, BackgroundScheduleState, str]]: ...
async def advance_schedule(task_id: str, expected_revision: int, occurrence_id: str, next_due_at: datetime | None) -> BackgroundScheduleState: ...
async def dispatch_scheduled_run(run: BackgroundRun, overlap_policy: OverlapPolicy, expected_schedule_revision: int, next_due_at: datetime | None) -> BackgroundRun: ...
async def create_run_with_overlap_guard(run: BackgroundRun, overlap_policy: OverlapPolicy) -> BackgroundRun: ...
async def get_run(run_id: str) -> BackgroundRun | None: ...
async def update_run_metadata(run_id: str, patch: dict, worker_id: str | None = None, lease_token: str | None = None) -> BackgroundRun: ...
async def transition_run(run_id: str, expected: set[RunStatus], next_status: RunStatus, patch: dict | None = None, worker_id: str | None = None, lease_token: str | None = None) -> BackgroundRun: ...
async def list_runs(task_id: str | None = None, status: RunStatus | None = None) -> list[BackgroundRun]: ...
async def list_active_runs(task_id: str | None = None) -> list[BackgroundRun]: ...
async def list_claimable_runs(limit: int) -> list[BackgroundRun]: ...
async def claim_next_run(worker_id: str, lease_seconds: int) -> BackgroundRun | None: ...

async def create_attempt(attempt: BackgroundAttempt) -> None: ...
async def update_attempt(attempt_id: str, patch: dict, worker_id: str, lease_token: str) -> BackgroundAttempt: ...
async def list_attempts(run_id: str) -> list[BackgroundAttempt]: ...

async def claim_run(run_id: str, worker_id: str, lease_seconds: int) -> BackgroundRun: ...
async def steal_expired_run(run_id: str, worker_id: str, lease_seconds: int) -> BackgroundRun: ...
async def refresh_lease(run_id: str, worker_id: str, lease_token: str, lease_seconds: int) -> None: ...
async def release_lease(run_id: str, worker_id: str, lease_token: str) -> None: ...
async def list_expired_leases(now: datetime) -> list[BackgroundRun]: ...

async def request_cancel(run_id: str) -> None: ...
async def is_cancel_requested(run_id: str) -> bool: ...
```

Store requirements:

- writes are atomic at record level.
- IDs are unique.
- state transitions are validated.
- terminal status is written exactly once.
- claim operations are atomic.
- scheduled dispatch creates the run, applies overlap, handles
  `cancel_previous`, deduplicates `occurrence_id`, and advances schedule state
  atomically.
- manual run creation and overlap guard are atomic.
- `claim_next_run` enforces `queue_next` per-task serialization.
- execution-state transitions require `worker_id` and `lease_token` after claim.
- run metadata writes from an active worker require `worker_id` and
  `lease_token`; operator metadata writes outside execution must not change
  execution state.
- lease writes compare `worker_id` and `lease_token`.
- expired-lease stealing creates a new `lease_token` and increments
  `lease_generation` atomically.
- list methods are deterministic.
- durable backends persist across process restart.
- every backend passes the shared task-store contract tests.

---

## Scheduler Contract

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

- manual tasks are not scheduled.
- disabled tasks are not scheduled.
- schedule callbacks are advisory wakeups that tell the dispatcher to read due
  schedules with `get_due_schedules(now, limit)`.
- schedule callbacks do not call `OmniCoreAgent.run()`.
- startup schedules are rebuilt from task-store records.
- invalid schedule specs fail validation before scheduler registration.
- misfire behavior follows `ScheduleSpec.misfire_policy`.
- mutable schedule progress is written through `BackgroundScheduleState`.

---

## Dispatcher Contract

Dispatcher input:

```python
task_id: str
due_at: datetime | None
trigger_type: str
trigger_metadata: dict
occurrence_id: str | None
```

Behavior:

Scheduled behavior:

1. A scheduler wakeup or startup scan calls `get_due_schedules(now, limit)`.
2. For each due occurrence, build a `BackgroundRun` with `query_snapshot`
   copied from the task.
3. Persist the run with `dispatch_scheduled_run()`, which atomically applies
   overlap policy, deduplicates `occurrence_id`, handles `cancel_previous`,
   and advances schedule state.
4. Emit `background_run_queued` or `background_run_skipped`.

Manual behavior:

1. Load task from task store.
2. If missing, raise `TaskNotFoundError`.
3. If disabled, raise `TaskNotFoundError`.
4. Build a `BackgroundRun` with `query_snapshot` copied from the task or manual
   override.
5. Persist the run through `create_run_with_overlap_guard()`.
6. Do not advance schedule state.
7. Emit `background_run_queued` or `background_run_skipped`.

Overlap behavior:

- `skip_if_running`: create a terminal `skipped` run for a valid scheduled or
  manual request.
- `queue_next`: queue the new run; `claim_next_run` holds it until earlier
  active runs for the same task are terminal.
- `cancel_previous`: request cancel on active runs and create the successor run
  in the same store transaction.
- `allow_parallel`: queue the new run.

The dispatcher does not implement overlap with a separate list-then-create
sequence. The task store performs the overlap decision atomically.

---

## Supervisor Contract

Supervisor loop:

```text
claim next run with lease
resolve agent
resolve task
resolve session id
resolve workspace namespace
mark running
execute attempt
heartbeat while active
handle retry/timeout/cancel/error
write workspace metadata
mark terminal
emit events
```

Execution call:

```python
result = await agent.run(
    query=run.query_snapshot,
    session_id=run.session_id,
)
```

Before execution, the supervisor binds the run workspace and injects the
background run context into the request. The context contains `run_id`,
`task_id`, `workspace_path`, and output-file guidance. The mechanism can be a
runtime context object or request augmentation, but it must be explicit in the
implementation and covered by tests.

Requirements:

- do not call agent when cancellation is already requested.
- set `started_at` before agent execution.
- enforce timeout with async timeout control.
- refresh heartbeat while active.
- every heartbeat, attempt update, and terminal write verifies `lease_token`.
- preserve partial workspace output on failure.
- create an attempt record before each attempt.
- update attempt status after each attempt.
- apply retry policy outside scheduler.
- write `result_preview` from final response.
- emit terminal event exactly once.
- mark terminal state even when event emission fails.
- release lease after terminal state.

---

## Recovery Contract

Recovery scans expired leases.

For each expired run:

- if terminal, ignore.
- steal the expired lease with `steal_expired_run()` to get a new
  `lease_token`.
- close the abandoned running attempt as `failed` with reason
  `lease_expired`.
- if cancellation requested, mark cancelled with the new lease token.
- if attempts remain and retry policy allows recovery, mark retrying and then
  queued for the normal claim path with the new lease token.
- if attempts are exhausted, mark failed with the new lease token.
- emit `background_run_recovered` when recovery changes state.

Recovery must use atomic store transitions so multiple supervisors do not
recover the same run.

---

## Workspace Contract

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
scratchpad/
```

`run.json` fields:

- `run_id`
- `task_id`
- `agent_id`
- `status`
- `attempt`
- `query_snapshot`
- `trigger_type`
- `occurrence_id`
- `session_id`
- `workspace_path`
- `started_at`
- `finished_at`
- `error`
- `result_preview`

Run context guidance must include:

```text
This is a background run. Write durable outputs under:
/workspace/background/{agent_id}/{task_id}/{run_id}/
Use output.md for the final durable result.
Use scratchpad files for progress, notes, todos, and resumable work.
```

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
background_run_claimed
background_run_started
background_run_heartbeat
background_run_retrying
background_run_completed
background_run_failed
background_run_timeout
background_run_cancelled
background_run_skipped
background_run_recovered
```

Common payload fields:

| Field | Required | Meaning |
|-------|----------|---------|
| `agent_id` | yes | Agent identifier |
| `task_id` | when task-related | Task identifier |
| `run_id` | when run-related | Run identifier |
| `session_id` | when run-related | Memory session id |
| `status` | when status-related | Current status |
| `attempt` | when run-related | Attempt number |
| `timestamp` | yes | ISO timestamp |
| `sequence` | run events | Monotonic event sequence within the run |
| `error` | on failures | Error string |
| `workspace_path` | on run events | Run workspace namespace |
| `worker_id` | on worker events | Supervisor worker id |

Events must be emitted through `EventRouter`.

Every background event includes `run_id` for run-scoped events. Event backends
that store task-level streams must filter by `run_id` when returning a run
trace.

`get_run_events(run_id)` reads events from `EventRouter` when the event backend
supports replay and run filtering. If the event backend cannot replay, the
manager reads the run workspace `events.jsonl` mirror. Returned events are
ordered by `sequence`, then `timestamp`. Missing event history returns an empty
list; it does not change run state.

---

## Error Contract

Typed errors:

```text
BackgroundAgentError
AgentAlreadyRegisteredError
AgentNotFoundError
TaskAlreadyRegisteredError
TaskNotFoundError
RunNotFoundError
InvalidScheduleError
InvalidTaskStoreError
TaskStoreError
SchedulerError
RunLeaseError
RunCancelledError
RunTimeoutError
RunExecutionError
```

Public manager methods raise typed errors for programming mistakes and return
serializable state for normal status queries.

---

## Required Tests

### Model Tests

- validates agent/task/run IDs
- validates schedule types
- validates schedule-state defaults and revisions
- validates retry policy
- validates timeout
- validates workspace policy
- validates default policies
- rejects invalid status transitions

### Task Store Contract Tests

Run the same contract tests against every backend:

- saves and retrieves agent specs
- saves and retrieves task specs
- creates and reads run records through overlap-guarded creation
- creates and reads attempt records
- creates and advances schedule state
- dispatches scheduled run and advances schedule atomically
- updates run records
- rejects invalid transitions
- rejects terminal state mutation
- claims runs atomically
- creates manual runs with atomic overlap guard
- handles `cancel_previous` atomically with successor run creation
- enforces `queue_next` claim ordering
- refreshes leases
- rejects stale lease-token writes
- lists expired leases
- stores cancellation flags
- lists active runs
- returns deterministic lists
- persists across reconnect for durable stores

### Scheduler Tests

- schedules interval tasks
- schedules cron tasks
- schedules once tasks
- ignores manual tasks
- ignores disabled tasks
- unschedules deleted tasks
- rebuilds jobs from task store on startup
- reports next run time
- applies misfire policy

### Dispatcher Tests

- due task creates queued run
- manual missing task raises `TaskNotFoundError`
- manual disabled task raises `TaskNotFoundError`
- every overlap policy works
- duplicate occurrence IDs do not create duplicate runs
- manual dispatch does not advance schedule state

### Supervisor Tests

- claims queued run
- executes agent runner once
- records completed run
- records failed run
- records timeout
- records cancellation before start
- records cancellation during retry wait
- applies fixed retry
- applies exponential retry
- refreshes heartbeat
- rejects stale lease-token updates
- recovers expired lease
- steals expired lease with a new lease token
- closes abandoned attempt on recovery
- requeues recovered runs through the normal claim path
- executes `query_snapshot`, not mutable task query
- binds workspace and injects run context before execution
- writes run metadata to workspace
- emits lifecycle events
- mirrors run events to `events.jsonl` when enabled

### Manager Tests

- registers agent
- registers task
- starts schedules for enabled tasks
- pauses task
- resumes task
- runs task immediately
- cancels run
- lists runs
- lists attempts
- reads run events from EventRouter or workspace mirror
- shuts down scheduler and workers
- does not import optional scheduler/storage dependencies during root import
- does not import optional scheduler/storage dependencies during
  `import omnicoreagent.background`
- lazy optional backend exports fail with clear optional-extra errors when the
  dependency is absent

### Integration Tests

- in-memory end-to-end background run with fake agent
- SQL SQLite end-to-end restart recovery
- workspace output persists for local workspace
- event trace can be built for a background run
- cookbook background example runs with a short schedule and clean shutdown

---

## Acceptance Criteria

The implementation satisfies this specification when:

- `BackgroundAgentManager` uses `AbstractTaskStore` for all operational state.
- `TaskStoreRouter` supports `in_memory`, `sql`, `redis`, and `mongodb`.
- `sql` supports SQLite and Postgres through one backend interface.
- durable stores survive manager restart.
- every run has a durable `run_id`.
- every run has a durable `query_snapshot`.
- every scheduled occurrence has a stable `occurrence_id`.
- every retry creates an attempt record.
- active runs have leases and heartbeats.
- lease tokens fence stale workers.
- expired leases are recoverable.
- queued runs remain claimable after restart.
- run status is inspectable without event replay.
- workspace output is written for every background run.
- cancellation, timeout, retry, overlap, and recovery behavior are tested.
- root and background-package imports remain lightweight.
- public docs and cookbook examples match the implemented API.
