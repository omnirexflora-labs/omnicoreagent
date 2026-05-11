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
| Scheduler is not executor | The scheduler emits due work; it never runs the agent |
| Supervisor owns execution | Retry, timeout, cancellation, heartbeat, leases, and terminal status are handled outside the model loop |
| Workspace is mandatory | Every background run gets a workspace namespace for durable outputs |
| Memory is separate | Conversation/session memory stays in `MemoryRouter`; operational state stays in the task store |
| Events are visibility, not truth | Events describe lifecycle transitions; the task store is the source of truth |
| Storage is pluggable | `AbstractTaskStore` supports `in_memory`, `sql`, `redis`, and `mongodb` through one interface |
| Backend choice is explicit | A running manager uses one task store; backend transfer is explicit, not hot-swapped mid-run |
| Root import stays light | Optional scheduler/storage dependencies load only when background features need them |

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
        +--> SchedulerBackend
        +--> Dispatcher
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

The manager owns orchestration. The store owns operational truth. The scheduler
owns due-time calculation. The supervisor owns execution lifecycle.
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
- start and stop scheduler, dispatcher, queue, and supervisor
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

The router is a construction boundary only. Runtime code depends on
`AbstractTaskStore`, not on backend-specific classes.

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

### `SchedulerBackend`

Computes due work from schedule specs.

Responsibilities:

- register enabled scheduled tasks
- compute next run time
- handle interval, cron, and once schedules
- apply misfire policy
- notify dispatcher when work is due
- pause, resume, and remove scheduled triggers

Non-responsibilities:

- running agents
- retrying failed runs
- persisting result state
- writing workspace files
- deciding overlap behavior

The architecture depends on the `SchedulerBackend` contract, not on a specific
scheduler package. Any scheduler implementation must be replaceable without
changing the manager, task store, dispatcher, queue, or supervisor.

### `Dispatcher`

Converts due tasks into run records.

Responsibilities:

- load the task from the task store
- verify the task is enabled
- apply overlap policy
- create a run record
- persist the run as queued
- enqueue the run
- emit queued or skipped lifecycle events

### `RunQueue`

Provides the boundary between due work and execution.

Responsibilities:

- accept queued run IDs
- provide backpressure
- preserve deterministic ordering
- support graceful shutdown
- allow priority/concurrency policy without changing task storage

The initial queue is process-local. The queue contract allows a durable queue
implementation without changing the manager API.

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
| `in_memory` | Fast development and tests; state is lost on process exit |
| `sql` | Durable relational storage for SQLite locally and Postgres in production |
| `redis` | Durable operational state in Redis for deployments already using Redis |
| `mongodb` | Durable document storage for MongoDB deployments |

Do not expose separate public routers named after each SQL database. `sql` is
the relational backend family. Its connection URL decides whether the concrete
database is SQLite, Postgres, or another supported SQLAlchemy target.

Example:

```python
manager = BackgroundAgentManager(
    task_store={
        "backend": "sql",
        "url": "sqlite:///.omnicoreagent/background.db",
    }
)
```

Production Postgres uses the same backend:

```python
manager = BackgroundAgentManager(
    task_store={
        "backend": "sql",
        "url": "postgresql://user:pass@host:5432/omnicoreagent",
    }
)
```

### Backend Selection

The selected task store is part of manager construction.

```text
BackgroundAgentManager(task_store="in_memory")
BackgroundAgentManager(task_store="sql")
BackgroundAgentManager(task_store="redis")
BackgroundAgentManager(task_store="mongodb")
BackgroundAgentManager(task_store={...})
```

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

### Run

`BackgroundRun` is one concrete execution created from a task.

Fields:

- `run_id`
- `task_id`
- `agent_id`
- `status`
- `attempt`
- `max_attempts`
- `trigger`
- `session_id`
- `workspace_path`
- `lease_owner`
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
- `status`
- `started_at`
- `finished_at`
- `error`
- `retry_delay_seconds`
- `worker_id`

Attempts make retry behavior inspectable. A multi-day task trajectory must
show every attempt and every transition that led to the final state.

---

## Run Lifecycle

```text
scheduled
  -> queued
  -> claimed
  -> running
  -> retrying
  -> running
  -> completed

running -> failed
running -> timeout
queued|claimed|running|retrying -> cancelled
scheduled|queued -> skipped
```

Terminal states:

```text
completed
failed
cancelled
timeout
skipped
```

Terminal states do not transition. A retry creates a new attempt inside the
same run before terminal status. A replay creates a new run.

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

Recovery policy must distinguish:

- run never started after queue claim
- run started but heartbeat expired
- run was retrying when heartbeat expired
- cancellation was requested before recovery
- terminal status was already written

The task store must make claim and terminal writes atomic so two supervisors do
not complete the same run.

---

## Trigger Model

Every run has a trigger record.

Trigger types:

- `manual`
- `interval`
- `cron`
- `once`
- `retry`
- `recovery`

Trigger metadata includes:

- `triggered_at`
- `due_at`
- `reason`
- `source`
- scheduler job ID when available

This gives operators the complete task trajectory: why a run exists, when it
became due, who claimed it, what happened during execution, and how it ended.

---

## Overlap Policy

Overlap policy is applied by the dispatcher before a run is queued.

Supported policies:

| Policy | Behavior |
|--------|----------|
| `skip_if_running` | Do not queue a new run while another active run for the same task exists |
| `queue_next` | Queue the new run behind active work |
| `cancel_previous` | Request cancellation for active runs and queue the new run |
| `allow_parallel` | Allow multiple active runs for the same task |

Default:

```text
skip_if_running
```

This protects long-running tasks from accidental runaway schedules.

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

The supervisor writes `run.json`. The agent writes durable task output through
workspace tools. Events can also be mirrored to `events.jsonl` for easy
inspection, while `EventRouter` remains the event service.

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
GET    /background/runs/{run_id}/events
GET    /background/runs/{run_id}/workspace
```

The HTTP layer calls the same manager API used by Python applications.

---

## Implementation Phases

### Phase 1: Models And Task Store

- `models.py`
- `store/base.py`
- `store/router.py`
- `store/in_memory.py`
- `store/sql.py`
- `store/redis.py`
- `store/mongodb.py`
- validation tests
- store contract tests shared by every backend

### Phase 2: Scheduler Boundary

- `scheduler.py`
- scheduler adapter implementation
- startup schedule reconstruction
- interval, cron, once, and manual tests
- misfire tests

### Phase 3: Dispatcher And Queue

- dispatcher service
- queue service
- overlap policy tests
- queued/skipped event tests

### Phase 4: Supervisor

- run claiming
- leases and heartbeat
- timeout
- retry
- cancellation
- terminal status handling
- recovery behavior
- fake-agent end-to-end tests

### Phase 5: Workspace And Events

- run workspace namespace creation
- `run.json`
- `output.md` guidance
- event stream integration
- local workspace integration tests

### Phase 6: Public API, Cookbook, OmniServe

- manager API cleanup
- cookbook examples
- public docs
- OmniServe background routes

---

## Acceptance Criteria

The background execution system is ready when:

- every task is persisted through `AbstractTaskStore`
- `in_memory`, `sql`, `redis`, and `mongodb` conform to the same store tests
- SQL backend supports SQLite locally and Postgres through one `sql` backend
- enabled schedules survive manager restart on durable stores
- every run has a durable `run_id`
- every attempt is inspectable
- long-running runs heartbeat while active
- expired leases can be recovered deterministically
- cancellation, timeout, retry, and overlap policies are tested
- every background run writes workspace output
- run status can be inspected without reading event streams
- lifecycle events can reconstruct the run trajectory
- optional dependencies do not load during root import
- public docs describe shipped behavior only
