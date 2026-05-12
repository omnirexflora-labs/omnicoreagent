# Background Agents Architecture

This is an internal architecture record for the OmniCoreAgent background
execution system. It is not public product documentation.

The document defines the system we are building: durable, inspectable,
scheduled, triggerable, long-running agent execution on top of `OmniCoreAgent`.
It is the source of truth for implementation, tests, cookbook examples, and
OmniServe integration.

Public docs stay concise. This record keeps the full engineering design.

Read this before changing:

- `src/omnicoreagent/background`
- `src/omnicoreagent/core/runtime`
- `src/omnicoreagent/core/workspace`
- `src/omnicoreagent/core/events`
- `src/omnicoreagent/core/memory_store`
- `engineering/specifications/background-agents.md`

---

## Design Goal

Background agents turn `OmniCoreAgent` into a durable task runner.

The system must support:

- manual background runs
- interval schedules
- cron schedules
- one-shot schedules
- long-running jobs that run for minutes, hours, or days
- cancellation
- retry
- timeout
- overlap control
- restart recovery
- run inspection
- workspace output
- lifecycle events
- OmniServe control APIs

The core design rule:

```text
Background execution supervises OmniCoreAgent.
OmniCoreAgent remains the only reasoning and tool-execution engine.
```

Background agents are not a second agent framework. They are a durable
execution layer around the agent harness.

---

## Architectural Principles

| Principle | Requirement |
|-----------|-------------|
| Composition over subclassing | Background execution wraps an `OmniCoreAgent` run instead of creating a separate agent type |
| Durable state first | Task, schedule, run, attempt, cancellation, and lease state live in a task store |
| Schedule dispatch is store-driven | Due work is read from task-store schedule state; no external scheduler owns execution |
| Supervisor owns execution | Retry, timeout, cancellation, heartbeat, leases, and terminal status are handled outside the model loop |
| Workspace is mandatory | Every background run gets a workspace namespace for durable outputs |
| Memory is separate | Conversation/session memory stays in `MemoryRouter`; operational state stays in the task store |
| Events are visibility, not truth | Events describe lifecycle transitions; the task store is the source of truth |
| Storage is pluggable | `AbstractTaskStore` supports the shipped `sql` and `in_memory` stores and leaves Redis/MongoDB behind the same interface |
| Backend choice is explicit | A running manager uses one task store; backend transfer is explicit, not hot-swapped mid-run |
| Root import stays light | Background imports do not pull Redis, MongoDB, or heavyweight scheduler packages |

---

## System Context

```text
User / OmniServe / Application
        |
        v
BackgroundAgentManager
        |
        +--> AgentRegistry
        +--> TaskStoreRouter
        |       |
        |       v
        |   AbstractTaskStore
        |       |
        |       +--> in_memory
        |       +--> sql
        |       +--> redis
        |       +--> mongodb
        |
        +--> ScheduleDispatcher
        +--> RunQueue
        +--> BackgroundSupervisor
                |
                v
          OmniCoreAgent.run()
                |
                +--> MemoryRouter
                +--> Workspace
                +--> EventRouter
                +--> Tools / MCP / Subagents / Guardrails
```

The manager owns orchestration. The store owns operational truth. Schedule
helpers compute due times from task specs. The supervisor owns execution lifecycle.
`OmniCoreAgent` owns reasoning, tool calls, workspace tools, memory, context
management, guardrails, and final response generation.

---

## Main Components

### `BackgroundAgentManager`

The public facade for registering agents, defining tasks, starting scheduling,
running tasks manually, inspecting runs, cancelling runs, and shutting down the
background runtime.

It coordinates the internal services but does not directly execute agent logic.

Responsibilities:

- validate public input
- register agent specs and task specs
- initialize the selected task store
- start and stop schedule dispatch, queue claiming, and supervisor work
- expose control operations
- expose inspection operations
- enforce background runtime defaults

### `AgentRegistry`

Holds process-local agent objects and serializable agent specs.

Two registration modes exist:

- direct object registration for applications that construct `OmniCoreAgent`
  themselves
- serializable spec registration for durable agents that can be reconstructed
  by workers

### `TaskStoreRouter`

Builds the selected `AbstractTaskStore` implementation from a backend name and
configuration.

Supported backend names:

```text
in_memory
sql
redis
mongodb
```

The shipped implementations are `sql` and `in_memory`. Redis and MongoDB use
the same contract when implemented. The router is a construction boundary only.
Runtime code depends on `AbstractTaskStore`, not on backend-specific classes.

### `AbstractTaskStore`

The persistence interface for background execution.

It stores:

- agent specs
- task specs
- schedule state
- run records
- attempt records
- run leases
- cancellation flags
- pause/enabled flags
- lifecycle checkpoints

It does not store:

- conversation history
- workspace files
- tool definitions
- MCP clients
- raw event streams

Conversation memory belongs to `MemoryRouter`. Files and artifacts belong to
workspace storage. Events belong to `EventRouter`. The task store owns
operational state.

### `ScheduleDispatcher`

Converts due tasks into run records.

Responsibilities:

- read due schedule occurrences from the task store
- compute the next due time from the task schedule
- apply overlap policy
- create a run record
- persist the run as queued
- advance schedule state atomically with run creation
- make the queued run claimable through the task store
- emit queued or skipped lifecycle events

### `RunQueue`

Provides the boundary between due work and execution.

Responsibilities:

- expose claimable queued run IDs from the task store
- provide backpressure
- preserve deterministic ordering
- support graceful shutdown
- allow priority/concurrency policy without changing task storage

The run queue is store-backed. A queued run is durable before any worker sees
it, and queued runs remain claimable after process restart. A process-local
queue can exist only as an optimization over the store-backed queue; it must not
be the source of truth.

### `BackgroundSupervisor`

Executes queued runs under operational control.

Responsibilities:

- claim runs with a lease
- refresh heartbeats for long-running runs
- resolve agent, task, session, and workspace namespace
- execute `OmniCoreAgent.run()`
- enforce timeout
- honor cancellation
- apply retry policy
- preserve partial output
- write run metadata to workspace
- mark terminal status exactly once
- emit lifecycle events

The supervisor design supports workers running in a separate process without
redesigning the data model.

---

## Task Store Architecture

### Backend Names

The public task-store backend names are:

| Backend | Purpose |
|---------|---------|
| `in_memory` | Default zero-config store for first-run UX; state is lost on process exit |
| `sql` | Durable local SQLite storage when restart persistence is required |
| `redis` | Reserved backend name for future Redis task-store support |
| `mongodb` | Reserved backend name for future MongoDB task-store support |

Do not expose separate public routers named after each SQL database. The
current `sql` implementation is SQLite-backed and uses the Python standard
library.

Example:

```python
manager = BackgroundAgentManager()  # defaults to in_memory

durable_manager = BackgroundAgentManager(
    task_store={
        "backend": "sql",
        "url": "sqlite:///.omnicoreagent/background.db",
    }
)
```

### Backend Selection

The selected task store is part of manager construction.

```text
BackgroundAgentManager()
BackgroundAgentManager(task_store="in_memory")
BackgroundAgentManager(task_store="sql")
BackgroundAgentManager(task_store={...})
```

Omitting `task_store` means `in_memory`. This keeps the first-run developer
experience lightweight. Production deployments that need restart persistence
must choose `sql` explicitly.

Bare string construction is limited to `in_memory` and `sql`.

The manager must not hot-swap task stores while runs are active. Operational
state is stateful by design. Moving from one backend to another requires an
explicit export/import or transfer utility so task definitions, schedule state,
run records, attempts, leases, and cancellation flags move together.

### Source Of Truth

The task store is the source of truth for:

- which tasks exist
- whether a task is enabled
- when a task last ran
- when a task is next due
- which runs exist
- which runs are active
- which worker claimed a run
- whether cancellation was requested
- final run status

The scheduler can keep its own runtime jobs, but those jobs are rebuildable from
task-store state. On startup, the manager reads enabled tasks from the task
store and reconstructs scheduler triggers.

Scheduler wakeups are not the source of truth. The task store records schedule
state, exposes due occurrences, and atomically dispatches each scheduled run
while advancing schedule state.

---

## Data Model

### Agent Spec

`BackgroundAgentSpec` identifies an agent the background system can run.

Fields:

- `agent_id`
- `name`
- `system_instruction`
- `model_config`
- `agent_config`
- `mcp_tools`
- `local_tools_ref`
- `workspace_config`
- `metadata`
- `created_at`
- `updated_at`

Direct Python tool objects are process-local. Durable agent specs can reference
tool sets by name, but they do not serialize live Python callables.

### Task Spec

`BackgroundTaskSpec` defines reusable background work.

Fields:

- `task_id`
- `agent_id`
- `query`
- `schedule`
- `enabled`
- `timeout_seconds`
- `retry_policy`
- `overlap_policy`
- `session_policy`
- `workspace_policy`
- `metadata`
- `created_at`
- `updated_at`

The task spec is stable configuration. It is not a run result.

### Schedule Spec

`ScheduleSpec` defines when work becomes due.

Supported schedule types:

- `manual`
- `interval`
- `cron`
- `once`

Schedule state tracks:

- next due time
- last due time
- last dispatch time
- misfire handling
- timezone
- jitter
- enabled/paused state

### Schedule State

`BackgroundScheduleState` records mutable scheduling progress for one task.

Fields:

- `task_id`
- `next_due_at`
- `last_due_at`
- `last_dispatched_at`
- `paused`
- `schedule_revision`
- `misfire_cursor`
- `updated_at`

The schedule spec defines the rule. Schedule state records the runtime progress
of that rule. The task store owns schedule state so interval, cron, once,
misfire, and restart behavior remain deterministic.

Each due occurrence gets a stable `occurrence_id`. Dispatch uses that
occurrence ID when creating a run so the same schedule occurrence cannot create
duplicate runs after restart or competing dispatchers.

### Run

`BackgroundRun` is one concrete execution created from a task.

Fields:

- `run_id`
- `task_id`
- `agent_id`
- `status`
- `attempt`
- `max_attempts`
- `query_snapshot`
- `trigger_type`
- `triggered_at`
- `due_at`
- `occurrence_id`
- `trigger_metadata`
- `session_id`
- `workspace_path`
- `lease_owner`
- `lease_token`
- `lease_generation`
- `lease_expires_at`
- `heartbeat_at`
- `queued_at`
- `claimed_at`
- `started_at`
- `finished_at`
- `cancel_requested_at`
- `error`
- `result_preview`
- `metadata`

### Attempt

`BackgroundAttempt` records each try inside a run.

Fields:

- `attempt_id`
- `run_id`
- `attempt_number`
- `reason`
- `status`
- `started_at`
- `finished_at`
- `error`
- `retry_delay_seconds`
- `worker_id`
- `lease_token`

Attempts make retry behavior inspectable. A multi-day task trajectory must
show every attempt and every transition that led to the final state.

---

## Run Lifecycle

```text
queued
  -> claimed
  -> running
  -> retrying
  -> queued
  -> claimed
  -> running
  -> completed

running -> failed
running -> timeout
queued|claimed|running|retrying -> cancelled
queued -> skipped
```

Terminal states:

```text
completed
failed
cancelled
timeout
skipped
```

Terminal states do not transition. Schedule state tracks due work before a run
exists. A retry creates a new attempt inside the same run. A replay creates a
new run.

---

## Long-Running Execution

Long-running tasks require leases and heartbeats.

When a supervisor claims a run, it writes:

- `lease_owner`
- `lease_expires_at`
- `heartbeat_at`

While the run is active, the supervisor refreshes the heartbeat. If the process
dies, the lease expires and another supervisor can recover the run according to
the recovery policy.

Every claim creates a `lease_token` and increments `lease_generation`. Heartbeat,
attempt update, terminal status, and workspace metadata writes must carry the
current lease token. A worker that loses its lease must stop writing and fail
closed. This fencing rule reduces duplicate side effects during multi-day runs.

Expired-lease recovery steals the lease with a new token before changing run or
attempt state. The abandoned attempt is closed as `failed` with reason
`lease_expired` before the recovered run is requeued or marked terminal.

Recovery policy must distinguish:

- run never started after queue claim
- run started but heartbeat expired
- run was retrying when heartbeat expired
- cancellation was requested before recovery
- terminal status was already written

The task store must make claim and terminal writes atomic so two supervisors do
not complete the same run.

Recovery requeues eligible expired runs through the same claim path used by
normal execution. It does not create a new run. It records recovery as an
attempt reason and emits `background_run_recovered`.

Generic metadata updates never change execution state. Any execution-state
transition after claim must include the worker ID and lease token so stale
workers cannot mutate a recovered run.

---

## Trigger Model

Every run has a trigger record.

Run trigger types:

- `manual`
- `interval`
- `cron`
- `once`

Trigger metadata includes:

- `triggered_at`
- `due_at`
- `reason`
- `source`
- `occurrence_id`
- scheduler job ID when available

This gives operators the complete task trajectory: why a run exists, when it
became due, who claimed it, what happened during execution, and how it ended.

Retry and recovery are attempt reasons and lifecycle events, not initial run
trigger types. They do not create a new run unless an operator explicitly starts
a replay.

---

## Overlap Policy

Overlap policy is applied by the dispatcher before a run is queued.

Supported policies:

| Policy | Behavior |
|--------|----------|
| `skip_if_running` | Persist a terminal `skipped` run instead of queueing a new active run while another active run for the same task exists |
| `queue_next` | Queue the new run behind active work |
| `cancel_previous` | Request cancellation for active runs and create the successor run in the same store transaction |
| `allow_parallel` | Allow multiple active runs for the same task |

Default:

```text
skip_if_running
```

This protects long-running tasks from accidental runaway schedules.

Overlap policy must be enforced atomically by the task store when a run is
created. The dispatcher does not perform a separate "list active runs, then
create run" sequence because that races under multiple managers. For
`queue_next`, the run can be persisted as `queued`, but `claim_next_run` must not
claim it while an earlier active run for the same task is still active.

---

## Retry Policy

Retry policy belongs to the supervisor.

Fields:

- `max_retries`
- `initial_delay_seconds`
- `max_delay_seconds`
- `backoff`
- `retry_on`

Retries are attempts inside a run. The scheduler is not involved in retrying a
failed attempt.

When an attempt times out and retries remain, the run transitions to
`retrying`, records the failed attempt as `timeout`, waits according to the
retry policy, then returns to `queued` for the next attempt. The run reaches
terminal `timeout` only after retry policy is exhausted or timeout is not
retryable.

---

## Cancellation

Cancellation is a durable flag on the run.

Cancellation can happen:

- before execution starts
- while waiting in queue
- while running
- during retry delay

The supervisor checks cancellation:

- before claiming
- before starting the agent
- between attempts
- during retry sleep
- after timeout or exception before scheduling another retry

Agent-level hard interruption depends on what the active model/tool call can
support. The background runtime still records cancellation intent immediately
and marks the run terminal when execution is safely stopped.

---

## Session Policy

Background tasks use normal OmniCoreAgent memory through `MemoryRouter`.

Supported session policies:

| Policy | Session ID |
|--------|------------|
| `task` | `background:{agent_id}:{task_id}` |
| `run` | `background:{run_id}` |
| `fixed` | User-provided session ID |

Default:

```text
task
```

Use task-level sessions for recurring jobs that carry memory across previous runs.
Use run-level sessions for isolated batch jobs.

---

## Workspace Contract

Workspace is mandatory for background runs.

Default run namespace:

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

The supervisor binds the run workspace before calling the agent and injects a
short run-context instruction into the request. The agent writes durable task
output through workspace tools. Events can also be mirrored to `events.jsonl`
for easy inspection, while `EventRouter` remains the event service.

The final response returned by `OmniCoreAgent.run()` is stored as
`result_preview`. The durable result of background work is the workspace output.

Workspace storage can be local, S3, or R2 through the existing workspace
architecture. Background execution does not create a separate file-storage
system.

---

## Event Model

Background lifecycle events go through `EventRouter`.

Required events:

- `background_agent_registered`
- `background_task_registered`
- `background_task_scheduled`
- `background_task_paused`
- `background_task_resumed`
- `background_task_deleted`
- `background_run_queued`
- `background_run_claimed`
- `background_run_started`
- `background_run_heartbeat`
- `background_run_retrying`
- `background_run_completed`
- `background_run_failed`
- `background_run_timeout`
- `background_run_cancelled`
- `background_run_skipped`
- `background_run_recovered`

Events make the run observable. They do not replace the task store.

---

## Public API Shape

```python
from omnicoreagent import BackgroundAgentManager, OmniCoreAgent

agent = OmniCoreAgent(
    name="system_monitor",
    system_instruction="Monitor system health and write reports to workspace.",
    model_config={"provider": "openai", "model": "gpt-4o-mini"},
)

manager = BackgroundAgentManager(
    task_store={
        "backend": "sql",
        "url": "sqlite:///.omnicoreagent/background.db",
    }
)

await manager.register_agent(agent_id="system_monitor", agent=agent)

await manager.register_task(
    task_id="hourly_health_report",
    agent_id="system_monitor",
    query="Check system status and save a report.",
    schedule={"type": "interval", "seconds": 3600},
    timeout_seconds=300,
    retry_policy={"max_retries": 2, "initial_delay_seconds": 30},
)

await manager.start()

run = await manager.run_now("hourly_health_report")
status = await manager.get_run(run.run_id)
await manager.cancel_run(run.run_id)
```

---

## OmniServe Integration

OmniServe exposes background execution through the runtime contract.

API shape:

```text
POST   /background/agents
GET    /background/agents
GET    /background/agents/{agent_id}
DELETE /background/agents/{agent_id}

POST   /background/tasks
GET    /background/tasks
GET    /background/tasks/{task_id}
PATCH  /background/tasks/{task_id}
POST   /background/tasks/{task_id}/pause
POST   /background/tasks/{task_id}/resume
POST   /background/tasks/{task_id}/run
DELETE /background/tasks/{task_id}

GET    /background/runs
GET    /background/runs/{run_id}
POST   /background/runs/{run_id}/cancel
GET    /background/runs/{run_id}/attempts
GET    /background/runs/{run_id}/events
GET    /background/runs/{run_id}/workspace
```

The HTTP layer calls the same manager API used by Python applications.

---

## Acceptance Criteria

The background execution system is ready when:

- every task is persisted through `AbstractTaskStore`
- `in_memory` and `sql` conform to the same store tests
- SQL backend supports local SQLite durability
- enabled schedules survive manager restart on durable stores
- schedule state stores next due, last due, dispatch cursor, and occurrence IDs
- every run has a durable `run_id`
- every run stores the exact input snapshot used for execution
- every attempt is inspectable
- long-running runs heartbeat while active
- lease tokens fence stale workers from writing after recovery
- expired leases can be recovered deterministically
- queued runs remain claimable after restart
- cancellation, timeout, retry, and overlap policies are tested
- every background run writes workspace output
- run status can be inspected without reading event streams
- lifecycle events can reconstruct the run trajectory
- optional dependencies do not load during root or background-package import
- public docs describe shipped behavior only
