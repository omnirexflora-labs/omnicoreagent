# Background Agents Architecture

This is an internal architecture record under `engineering/architecture`, not
public product documentation. It is the source of truth for the next
OmniCoreAgent background-agent design.

Keep public docs thin. Keep the full reasoning, ownership boundaries,
implementation decisions, failure modes, and coding-agent instructions here.

This document is not a list of nice-to-have ideas. When background-agent code is
rebuilt, implementation, tests, public docs, examples, and this architecture
record must stay aligned.

Read this before changing:

- `src/omnicoreagent/background`
- background-agent exports in `src/omnicoreagent/__init__.py`
- background events in `src/omnicoreagent/core/events`
- background cookbook examples
- OmniServe endpoints that will later manage background agents
- workspace conventions for scheduled or long-running runs

The design target is a durable background execution system for agents. It is not
just "run this function every N seconds."

---

## External Design Inputs

The design is informed by production systems that separate durable execution,
scheduling, lifecycle events, and runtime state.

| System | Design lesson |
|--------|---------------|
| APScheduler | Schedules, triggers, job stores, and executors are separate concepts. A scheduler should decide when work is due; it should not own all agent state. |
| Celery | Periodic scheduling, queued execution, retries, and task results are separate operational concerns. |
| Temporal | Long-running work needs durable run state, retry policies, terminal states, and crash recovery. Fallible I/O should be retryable and observable. |
| OpenAI background mode | Long-running model work needs stable run IDs, status polling, cancellation, and terminal run states. |
| Claude Code lifecycle hooks | Agent work has a lifecycle: session start, user prompt, tool calls, tool batches, subagents, compaction, completion, and stop events. Background agents should expose lifecycle events, not hide them. |
| Google ADK | Sessions, memory, artifacts, tools, and runners are separate services. A background agent should compose these pieces rather than blur them. |

These systems do not dictate OmniCoreAgent's API. They confirm the architecture
principle: durable background execution is a runtime layer around the agent
harness, not a second agent framework.

References reviewed:

- APScheduler user guide: `https://apscheduler.readthedocs.io/en/stable/userguide.html`
- Celery task guide: `https://docs.celeryq.dev/en/stable/userguide/tasks.html`
- Temporal durable execution docs: `https://docs.temporal.io/`
- OpenAI background mode: `https://platform.openai.com/docs/guides/background`
- OpenAI Codex cloud tasks/environments: `https://developers.openai.com/codex/cloud/environments`
- Claude Code hooks: `https://code.claude.com/docs/en/hooks`
- Google ADK sessions/memory: `https://adk.dev/sessions/memory/`

---

## Purpose

Background agents exist to run `OmniCoreAgent` under supervision.

They solve these harness problems:

| Problem | Background-agent answer |
|---------|-------------------------|
| Agents need to run without a human request in the foreground | Schedule or trigger tasks and execute them in the background |
| Long-running work must be inspectable | Persist each run, status, attempts, events, and workspace output |
| Scheduled work must survive process restarts | Store task definitions and run state in a durable task store |
| Agent runs can fail, time out, or loop | Apply retry, timeout, cancellation, and overlap policies outside the model loop |
| Operators need control | Support start, pause, resume, cancel, run-now, inspect, and delete operations |
| Background work must integrate with the harness | Use OmniCoreAgent memory, workspace, tools, guardrails, context management, subagents, and events |

The design rule is:

```text
Background agents supervise OmniCoreAgent runs.
They do not replace OmniCoreAgent and they do not fork the agent runtime.
```

---

## Current Problem

The current implementation is functional but too small and too tightly coupled.

Current shape:

```text
BackgroundAgentManager
  -> TaskRegistry
  -> APSchedulerBackend
  -> BackgroundOmniCoreAgent extends OmniCoreAgent
```

Main issues:

- `BackgroundOmniCoreAgent` subclasses `OmniCoreAgent`, making background
  execution feel like a different agent type instead of a supervised runtime
  mode.
- `TaskRegistry` is in-memory only, so schedules and run state are not durable.
- `create_agent()` mutates the input config by popping `task_config`.
- task definition, schedule, queue, worker, retry, status, and agent config are
  represented as mutable dictionaries.
- scheduler state and task registry state are separate but not reconciled.
- task updates do not cleanly reschedule from a single source of truth.
- task runs do not have a durable run record.
- run output is returned as a result but not standardized into workspace files.
- lifecycle events exist but are incomplete for operations such as queued,
  retrying, timeout, cancelled, skipped, paused, resumed, and deleted.
- tests cover `TaskRegistry` and `APSchedulerBackend`, but not the full durable
  lifecycle.

The rebuild must move from "background wrapper" to "durable task runtime for
agents."

---

## Target Architecture

Target shape:

```text
BackgroundAgentManager
  -> BackgroundAgentRegistry
  -> BackgroundTaskStore
  -> SchedulerBackend
  -> Dispatcher
  -> RunQueue
  -> BackgroundSupervisor
  -> OmniCoreAgent.run()
  -> Workspace + Memory + Events
```

High-level flow:

```text
register agent spec
register task spec
persist task
scheduler marks task due
dispatcher creates run
queue accepts run
supervisor claims run
agent executes with session and workspace namespace
events stream throughout lifecycle
workspace captures files/artifacts/output
task store records terminal run state
scheduler computes next due time
```

The scheduler only decides when a task is due. It does not own execution.

The supervisor owns execution lifecycle. It does not own model reasoning.

`OmniCoreAgent` owns reasoning, tools, memory, workspace tools, subagents,
guardrails, context management, observations, and final response.

---

## Terminology

Use these names consistently.

| Term | Meaning |
|------|---------|
| Background agent | A registered `OmniCoreAgent` plus supervision metadata |
| Agent spec | Static definition needed to construct or reference the agent |
| Task spec | Durable definition of scheduled or manual work |
| Schedule spec | Interval, cron, once, or manual trigger configuration |
| Run | One concrete execution attempt created from a task spec |
| Attempt | One try within a run under the retry policy |
| Task store | Operational persistence for agents, tasks, schedules, runs, and attempts |
| Scheduler backend | Adapter that computes due work from schedule specs |
| Dispatcher | Converts due tasks into queued runs |
| Run queue | Backpressure and ordering boundary before execution |
| Supervisor | Claims runs, executes them, handles retries/timeouts/cancel, and records state |
| Workspace namespace | Durable file area for a task or run |

Do not call the task store a memory store. Memory stores conversation history.
The task store stores operational state.

Do not call workspace storage a task store. Workspace stores files and artifacts.
The task store stores metadata, status, and scheduling state.

---

## Architecture Invariants

These invariants are mandatory.

| Invariant | Reason |
|-----------|--------|
| `OmniCoreAgent` remains the execution engine | Avoid creating two agent runtimes |
| Background execution uses composition, not agent subclassing | Keep scheduling/supervision decoupled from reasoning |
| Task definitions are durable | Scheduled agents must survive restarts |
| Every run has a durable `run_id` and terminal state | Operators need status, debugging, and reliable automation |
| Scheduler does not execute agent logic directly | Scheduling and execution have different failure modes |
| Supervisor owns retries, timeout, cancellation, and overlap policy | These are operational controls, not model reasoning controls |
| Workspace is the file boundary for run output | Every background run must leave inspectable output |
| Memory remains conversation/session history | Do not hide scheduled task state in MemoryRouter |
| Events describe lifecycle transitions | Observability must be reconstructable from events |
| Public APIs accept typed specs or validated dictionaries | Avoid unvalidated mutable config blobs |
| In-memory implementations are development/test only | Production behavior needs durable task state |

---

## Component Ownership

Target module layout:

```text
background/
  __init__.py
  manager.py              # public facade
  models.py               # typed specs, run records, enums
  store.py                # TaskStore protocol and in-memory implementation
  sqlite_store.py         # local durable task store
  scheduler.py            # SchedulerBackend protocol
  apscheduler_backend.py  # APScheduler adapter
  dispatcher.py           # due task -> queued run
  queue.py                # run queue and backpressure policy
  supervisor.py           # run claiming, execution, retries, timeout, cancel
  runner.py               # thin OmniCoreAgent.run adapter
  workspace.py            # workspace namespace/path helpers
  events.py               # background event builders
  errors.py               # typed background errors
```

The existing files can be migrated into this shape. Do not keep the old names
only for compatibility. This code has not been widely exposed enough to justify
carrying a confusing architecture forward.

---

## Public API Shape

The public API should feel like scheduling a normal `OmniCoreAgent`, not
creating a different kind of agent.

```python
from omnicoreagent import BackgroundAgentManager, OmniCoreAgent

agent = OmniCoreAgent(
    name="system_monitor",
    system_instruction="Monitor system health and write reports to workspace.",
    model_config={"provider": "openai", "model": "gpt-4o-mini"},
)

manager = BackgroundAgentManager(task_store="sqlite")

await manager.register_agent(
    agent_id="system_monitor",
    agent=agent,
)

await manager.register_task(
    task_id="hourly_health_report",
    agent_id="system_monitor",
    query="Check system status and save a report.",
    schedule={"type": "interval", "seconds": 3600},
    timeout=300,
    retry_policy={"max_retries": 2, "initial_delay": 30},
)

await manager.start()
```

Manual execution:

```python
run = await manager.run_now("hourly_health_report")
```

Status and operations:

```python
await manager.get_task("hourly_health_report")
await manager.get_run(run.run_id)
await manager.list_runs(task_id="hourly_health_report")
await manager.cancel_run(run.run_id)
await manager.pause_task("hourly_health_report")
await manager.resume_task("hourly_health_report")
await manager.delete_task("hourly_health_report")
```

The manager may also support one-call registration for convenience:

```python
await manager.register(
    agent_id="system_monitor",
    agent=agent,
    task_id="hourly_health_report",
    query="Check system status and save a report.",
    schedule={"type": "interval", "seconds": 3600},
)
```

Convenience must call the same typed core path. It must not create a second
configuration path.

---

## Agent Spec

`BackgroundAgentSpec` defines what agent is available for background execution.

It supports two registration forms:

1. pass an already-created `OmniCoreAgent`
2. pass a serializable construction spec

In-process direct registration:

```python
await manager.register_agent(agent_id="monitor", agent=agent)
```

Serializable registration:

```python
await manager.register_agent_spec(
    {
        "agent_id": "monitor",
        "name": "system_monitor",
        "system_instruction": "Monitor health and report problems.",
        "model_config": {"provider": "openai", "model": "gpt-4o-mini"},
        "agent_config": {
            "context_management": {"enabled": True},
            "tool_offload": {"enabled": True},
            "enable_workspace_files": True,
        },
        "mcp_tools": [],
    }
)
```

Direct `local_tools` may be in-process only because Python callables are not
portable across processes. A durable task store may record that an agent has
non-serializable local tools, but it cannot reconstruct those tools in a new
process unless the user provides an import path or factory.

This is a key boundary: durable tasks are possible only when the agent can be
reconstructed or when the process keeps the agent object registered.

---

## Task Spec

`BackgroundTaskSpec` defines the scheduled work.

Required fields:

- `task_id`
- `agent_id`
- `query`
- `schedule`

Important optional fields:

- `enabled`
- `timeout`
- `retry_policy`
- `overlap_policy`
- `session_policy`
- `workspace_policy`
- `metadata`

The task spec is durable. It is the source of truth. Scheduler jobs are derived
from task specs and can be rebuilt.

---

## Schedule Spec

Supported schedule types:

| Type | Meaning |
|------|---------|
| `interval` | Run every N seconds |
| `cron` | Run from a cron expression |
| `once` | Run once at `run_at` |
| `manual` | Never scheduled automatically; runs only through `run_now()` |

Schedule spec examples:

```python
{"type": "interval", "seconds": 300}
{"type": "cron", "expression": "0 * * * *", "timezone": "UTC"}
{"type": "once", "run_at": "2026-05-09T00:00:00Z"}
{"type": "manual"}
```

Scheduling options:

- `timezone`
- `start_at`
- `end_at`
- `jitter_seconds`
- `misfire_policy`

The default misfire policy should be conservative:

```text
skip_missed
```

Do not surprise users by running a backlog of missed jobs after downtime unless
they explicitly choose that behavior.

---

## Run Model

Every execution creates a `BackgroundRun`.

The run is the operational unit that can be inspected, cancelled, retried, and
linked to workspace output.

```text
run_id
task_id
agent_id
status
attempt
queued_at
started_at
finished_at
cancel_requested_at
timeout_seconds
error
result_preview
workspace_path
session_id
metadata
```

Run statuses:

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

Once a run reaches a terminal status, it must not transition again except
through an explicit new retry run or replay run.

---

## Task Store

The task store is operational persistence.

It stores:

- agent specs that are serializable
- task specs
- schedule state
- run records
- attempt records
- cancellation flags
- pause/enabled flags

It does not store:

- conversation memory
- workspace files
- tool definitions
- MCP connection objects
- event stream payloads unless an implementation chooses to denormalize

Initial implementations:

```text
InMemoryTaskStore
SQLiteTaskStore
```

Later implementations:

```text
RedisTaskStore
PostgresTaskStore
```

SQLite should become the local durable default for background agents because a
background scheduler that forgets all task state on restart is not a production
background system.

---

## Scheduler Backend

The scheduler backend is replaceable.

Initial backend:

```text
APSchedulerBackend
```

Responsibilities:

- accept enabled task specs
- compute due times
- trigger dispatcher when work is due
- expose next run time
- pause/resume/delete scheduler jobs
- rebuild scheduler jobs from task store on manager startup

Non-responsibilities:

- run the agent
- retry failed work
- store result state
- write workspace files
- emit all lifecycle events

The scheduler should emit "due task" signals, not call `OmniCoreAgent.run()`
directly.

---

## Dispatcher And Queue

The dispatcher converts due tasks into run records.

Responsibilities:

- load task spec
- check `enabled`
- evaluate overlap policy
- create a durable `BackgroundRun`
- enqueue the run
- emit queued/skipped events

The run queue provides backpressure.

Queue policies:

- max queue size
- enqueue timeout
- priority later if needed
- per-task or global concurrency later if needed

The queue can start in memory. The task store keeps run state durable, so a
future process can recover queued/running runs.

---

## Supervisor

The supervisor is the execution heart.

Responsibilities:

- claim queued runs
- mark runs `running`
- execute `OmniCoreAgent.run()`
- enforce timeout
- handle cancellation
- apply retry policy
- record attempts
- write run result metadata
- emit lifecycle events
- mark terminal status
- release resources

The supervisor should be written so it can later run in a separate process or
worker. It must not depend on global process state that prevents that future.

---

## Retry Policy

Retry belongs to the supervisor, not to the scheduler.

Default:

```text
max_retries = 0
```

Users opt into retries. Retrying an agent can be expensive and can repeat side
effects.

Supported fields:

- `max_retries`
- `initial_delay`
- `max_delay`
- `backoff`
- `retry_on`

Backoff values:

```text
fixed
exponential
```

Retry must create attempt records. A retry is not invisible.

---

## Timeout And Cancellation

Timeout and cancellation are first-class.

Timeout behavior:

- supervisor starts timer when run becomes `running`
- if timeout is exceeded, cancel the execution task
- mark run `timeout`
- emit timeout event
- preserve workspace files already written

Cancellation behavior:

- `cancel_run(run_id)` sets a cancellation flag in the task store
- supervisor checks cancellation before starting, between retries, and during
  long-running execution where possible
- cancellation marks run `cancelled`
- cancellation must be idempotent

Hard cancellation of an in-flight Python coroutine is best-effort. The
architecture must represent that honestly.

---

## Overlap Policy

Overlap policy controls what happens when a task is due while a previous run is
still active.

Supported values:

| Policy | Behavior |
|--------|----------|
| `skip_if_running` | Do not enqueue a new run if active run exists |
| `queue_next` | Queue one or more future runs |
| `cancel_previous` | Request cancellation of active run before queuing new one |
| `allow_parallel` | Allow multiple active runs for the same task |

Default:

```text
skip_if_running
```

This default prevents runaway schedules and duplicate side effects.

---

## Session Policy

Background agents need clear memory behavior.

Supported session policies:

| Policy | Behavior |
|--------|----------|
| `task` | Reuse one session per task |
| `run` | Use one new session per run |
| `fixed` | Use provided `session_id` |

Default:

```text
task
```

For recurring monitoring/reporting, task-level session continuity is useful.
For independent batch jobs, run-level sessions avoid old context leaking into
new work.

---

## Workspace Policy

Every run must have a workspace namespace.

Default layout:

```text
/background/{agent_id}/{task_id}/{run_id}/
  output.md
  scratchpad.md
  events.jsonl
  artifacts/
  subagents/
  logs/
```

Workspace policy fields:

- `namespace`
- `persist_outputs`
- `output_file`
- `event_log_file`
- `artifact_policy`

The supervisor should inject workspace guidance into the run context so the
agent knows where to write durable output.

The runtime should never rely only on `result["response"]` for background work.
A background run must leave inspectable workspace output.

---

## Events

Background events must describe lifecycle transitions.

Required event types:

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

Existing event types can be migrated or mapped:

```text
background_task_started      -> background_run_started
background_task_completed    -> background_run_completed
background_task_error        -> background_run_failed
background_agent_status      -> keep as aggregate status event if useful
```

Events go through `EventRouter`. Run state goes through `TaskStore`.

Do not rely on events as the only source of run state. Event streams are for
visibility. Task store is the operational source of truth.

---

## Integration With OmniCoreAgent Harness

Background execution should enable or encourage harness features that matter for
long-running work:

- workspace files
- tool offloading
- context management
- event emission
- memory session continuity
- subagents where enabled by agent config

The background system should not silently change core agent behavior except
where required for durability. For example, if `enable_workspace_files` is false,
the manager can reject the task or force it on by documented policy. The
recommended policy is:

```text
background runs require workspace files
```

Reason: background work must leave durable inspectable output.

---

## OmniServe Integration Later

OmniServe should later expose background-agent operations through a dedicated
router.

Possible endpoints:

```text
POST   /background/agents
GET    /background/agents
GET    /background/agents/{agent_id}
DELETE /background/agents/{agent_id}

POST   /background/tasks
GET    /background/tasks
GET    /background/tasks/{task_id}
POST   /background/tasks/{task_id}/pause
POST   /background/tasks/{task_id}/resume
POST   /background/tasks/{task_id}/run
DELETE /background/tasks/{task_id}

GET    /background/runs
GET    /background/runs/{run_id}
POST   /background/runs/{run_id}/cancel
GET    /background/runs/{run_id}/events
```

This should happen after the internal runtime is clean. Do not build HTTP API
first.

---

## Migration Strategy

The rebuild should happen in stages.

### Stage 1: Typed Models And Store

- add `models.py`
- add `TaskStore` protocol
- add `InMemoryTaskStore`
- add `SQLiteTaskStore`
- add tests for model validation and store behavior

### Stage 2: Scheduler Boundary

- replace direct scheduler ownership with scheduler adapter protocol
- make APScheduler emit due-task callbacks
- rebuild schedules from task store at startup
- test interval, cron, manual, and disabled tasks

### Stage 3: Dispatcher, Queue, Supervisor

- create durable run records
- enqueue runs
- supervisor claims and executes runs
- implement retry, timeout, cancellation, and overlap policies
- test full lifecycle without real LLM by using fake agent runner

### Stage 4: Workspace And Events

- assign run workspace namespace
- inject workspace guidance
- write standard run output files
- emit complete lifecycle events
- test workspace output and event trace

### Stage 5: Public API And Docs

- replace cookbook examples
- update public background-agent docs
- add OmniServe design only after runtime stabilizes

---

## Open Design Decisions

These decisions must be finalized before implementation.

| Decision | Recommendation |
|----------|----------------|
| Default task store | SQLite for durable local behavior; in-memory only when explicitly requested |
| Default overlap policy | `skip_if_running` |
| Default retry policy | no retries unless configured |
| Default session policy | `task` |
| Workspace required? | yes, background runs require workspace files |
| Keep `BackgroundOmniCoreAgent`? | no, migrate to composition around `OmniCoreAgent` |
| Keep old dict API? | accept dicts only through typed validation; do not preserve old shape for compatibility |
| First HTTP API? | no, internal runtime first |

---

## Coding-Agent Instructions

Before editing background-agent code:

1. Read this file.
2. Read `engineering/specifications/background-agents.md`.
3. Read current files under `src/omnicoreagent/background`.
4. Do not preserve confusing legacy shapes for compatibility.
5. Keep `OmniCoreAgent` as the execution engine.
6. Add tests before or with each behavior change.
7. Keep public docs honest: if a feature is not implemented, do not describe it
   as shipped.
