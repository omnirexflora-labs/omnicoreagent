# Durable runs plan

Status: decisions confirmed by the maintainer 2026-09-19 (all four as recommended).

## Why

Three things a production agent needs share one missing foundation:

- **Approval that waits for a person.** Governance can decide `ask`, but an
  ask only works through a resolver function that answers while the run
  waits. Without one, the call fails (`ApprovalRequiredError`) and the run
  carries on without it. A run cannot pause for someone in a web UI or chat
  and continue later.
- **Recovery after a crash.** Background runs have durable storage, leases,
  and recovery, but recovery restarts the whole attempt: every model call and
  tool call runs again, and side effects (a write, a sent message) can happen
  twice. An interactive run that dies is simply lost.
- **Steering.** Once a run starts, the caller can only wait or cancel.

All three need a **durable run state saved at step boundaries**. Built once,
each becomes a small layer on top.

## What already exists (and is reused)

- The conversation is already saved step by step: the user message, each
  assistant turn with its tool calls, and each tool result go to the session
  history in the memory store (in memory, Redis, MongoDB, or SQL) as they
  happen (`add_message_to_history` in `core/agents/base.py`,
  `native_tools.py`). The run state does not copy messages; it records what
  history lacks.
- The loop has clear step boundaries (`agent.step` spans) and one tool
  dispatch point (`GovernedToolRunner`).
- Governance produces an `ApprovalRequest` (capability, target, risk, reason,
  expiry) for every `ask`.
- Background runs have leases, heartbeats, and recovery of expired runs.
- The trace already links runs (`run_id`, parent and child traces).

## Design

### Run state

A `RunRecord` per run, saved to a `RunStateStore`:

- identity: `run_id`, `session_id`, agent name and version, owner (lease);
- progress: status, current step, usage so far, trace segments;
- tool calls of the current step: each with its arguments digest and a state
  (`pending`, `awaiting_approval`, `started`, `completed`), written **before**
  a tool starts and after it ends (write-ahead);
- approvals: request, digest, decision, approver, note, expiry, used or not;
- inbox: steering messages not yet delivered;
- a version number for optimistic concurrency (two workers cannot both
  advance a run).

Statuses: `running`, `awaiting_approval`, `interrupted`, `completed`,
`failed`, `cancelled`.

The run state lives in the memory store the application already chose
(in memory, SQL, Redis, or MongoDB), beside the session history it points
into; there is no second store to configure. Crash recovery therefore works
with any durable memory store. The in-memory store is for development: pause
and resume work within one process, and nothing survives a restart.

### Approval: suspend and resume

```python
result = await agent.run("clean up the old reports", session_id="s1")
# result["status"] == "awaiting_approval"
# result["approvals"] == [{"approval_id", "tool", "capability", "arguments" (redacted),
#                          "reason", "expires_at"}]

await agent.resolve_approval(run_id, approval_id, decision="approve", approver="alice")
await agent.resolve_approval(run_id, other_id, decision="deny", note="use the staging bucket")
result = await agent.resume(run_id)
```

- When a call needs approval and no resolver answers, the other calls in the
  same step finish, the run state is saved as `awaiting_approval`, and `run`
  returns. Nothing unapproved has run.
- A denial's note reaches the model as the tool result ("denied by alice: use
  the staging bucket"), so it can change course.
- Approve with edits: the approver may change the arguments; the edited call
  is authorized again as a new request.
- An approval is bound to a digest of the exact request (capability, target,
  and arguments), is used once, and expires; approving `delete a.txt` cannot
  authorize `delete b.txt`.
- Every decision is recorded in the trace with the approver and note.
- OmniServe: `GET /runs/{run_id}`, `POST /runs/{run_id}/approvals/{approval_id}`,
  `POST /runs/{run_id}/resume`, and an SSE event when a run starts waiting.

### Crash recovery

- `agent.resume(run_id)` also continues a run whose owner died (lease
  expired). Background recovery resumes from the saved step instead of
  restarting the attempt.
- Completed tool calls are never run again; their results are in history.
- A call that was `started` but never finished has an unknown outcome:
  - it runs again only if it is **idempotent**;
  - otherwise the model is told "this call was interrupted; its outcome is
    unknown" and decides what to do (under governance, running it again is a
    new, authorized call).
- Idempotent by default: workspace reads, skill file reads, tool discovery,
  and MCP tools that declare `readOnlyHint` or `idempotentHint`. Application
  tools declare it: `register_tool(..., idempotent=True)`.
- A model call in progress is simply made again (cost, no side effects).

### Steering and interrupt

- `agent.steer(run_id, "also check the Q3 numbers")` (and
  `POST /runs/{run_id}/steer`) adds a message to the run's inbox. It is
  delivered at the next step boundary, never during a model or tool call,
  recorded as a user message and in the trace.
- A steering message is user input: it passes the injection guardrail, and in
  Serve only the run's caller may send it. Tool output can never steer.
- `agent.interrupt(run_id)` stops at the next boundary and saves the run as
  `interrupted` (resumable); `cancel` still discards it.

### Sandbox continuity

- A waiting or crashed run does not keep its container. The session is
  closed; workspace files are already safe (the bridge copies them back after
  every command). On resume the model is told the sandbox was reset and the
  workspace is intact.
- Keeping a container alive or snapshotting it is later work, per provider.

### One story in the trace

- Each resume is a new trace segment with the same `run_id`, linked to the
  previous one, with a visible `run_suspended` / `run_resumed` /
  `run_recovered` / `run_steered` event.
- `agent.get_run_trajectory(run_id)` joins the segments into one trajectory.

## Units

Each unit: failing tests first, full suite, commit and push, log below.

- **D1. Run state.** `RunRecord`; run-state methods on the memory store,
  implemented for all four built-in backends (in memory, SQL, Redis, MongoDB;
  Redis and MongoDB tested against real servers in throwaway containers);
  saved at each step boundary and write-ahead around tool calls; optimistic
  versioning; `agent.get_run(run_id)`. A custom memory store without the
  methods keeps working; its runs are simply not durable.
- **D2. Approval suspend and resume.** Suspend on an unanswered ask; request
  digests and single-use, expiring approvals; `resolve_approval`, `resume`
  (in the same process); deny-with-note and approve-with-edits; trace events;
  OmniServe routes and SSE event.
- **D3. Crash recovery.** Resume a run whose owner died; leases; idempotency
  declarations (application tools, built-ins, MCP hints); unknown-outcome
  handling; background recovery resumes instead of restarting. Proof: a
  subprocess is killed mid-run and another process finishes the run with no
  duplicated side effect.
- **D4. Steering and interrupt.** Inbox, delivery at step boundaries,
  guardrail, `steer` / `interrupt` in Python and OmniServe.
- **D5. Sandbox continuity.** Close on suspend, reset notice on resume,
  workspace intact; tested with Docker.
- **D6. Trajectory, docs, and proof.** Joined trajectory across segments; a
  "Durable runs" docs page; end-to-end tests across every feature together.

## Decisions (confirmed 2026-09-19)

| # | Question | Decision |
| --- | --- | --- |
| 1 | With governance on and no resolver, should an `ask` suspend the run (new) or fail the call (today)? | Suspend by default; `approval_mode="fail"` keeps today's behaviour. |
| 2 | Where is run state stored? | In the memory store the application chose, all four backends in D1 (the maintainer's revision of "memory and SQL first": one store, nothing extra to configure; in-memory is for development, so no start-up notice). |
| 3 | A non-idempotent call interrupted by a crash: tell the model the outcome is unknown, or require approval to run it again? | Tell the model; a retry is a new call, authorized like any other. |
| 4 | A waiting run's sandbox: close it (workspace survives) or keep it alive? | Close it; the model is told on resume. |

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |
| D1 | Complete | (this commit) | New `core/runs.py`: `RunTracker` saves each run's record (status, step, usage, trace IDs, and each tool call with an arguments digest, never the arguments) through the memory store the application chose; `current_run()` is the running tracker. Run-state methods (`save_run_state` with compare-and-swap versions, `get_run_state`, `list_run_states`) on the memory store base (not abstract: a store or router without async run-state methods keeps working, not durable) and implemented for in memory, SQL (a `run_states` table, created on existing databases too), Redis (one hash per run, a session index, and a Lua compare-and-swap), and MongoDB (`<collection>_run_states`, updates matched on the version). The agent records the start, every step, each tool call as `started` before it runs (write-ahead; if that save fails the call does not run) and `completed` or `interrupted` (cancelled or timed out, effect unknown) after, and the end: `completed`, `failed` (including a provider error that ends the run with an error response), `blocked` (guardrail), or `cancelled`. Saves are serialized per run, so parallel tool calls cannot race on versions. `agent.get_run(run_id)` and `agent.list_runs(session_id=, status=)`. Choosing SQL, Redis, or MongoDB without its URL now logs a warning instead of info (the store silently falls back to in memory). Found and fixed during D1: the SQL and Redis connection managers were process-wide singletons, so a second store with a different database URL silently used the first one's database (reproduced for both); SQL engines are now per URL and each Redis store has its own client; the tests that reset the old singletons no longer need to. Redis and MongoDB were tested against real servers in throwaway containers (`redis:7-alpine`, `mongo:7`), which also ran the existing live Redis and MongoDB memory and background task store tests for the first time here. 19 new tests, including one per durable backend where a separate agent with a fresh connection reads a finished run. Full suite with the live backends 1,582 passed, 2 skipped (S3 and R2, which need cloud credentials); ruff clean. |
