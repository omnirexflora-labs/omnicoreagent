# Durable runs plan

Status: proposed 2026-09-19, awaiting the maintainer's approval.

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

Messages come from the session history, so **crash recovery needs a durable
memory store and a durable run store**. With the in-memory defaults, pause
and resume works within one process, and the agent says (at start) that runs
cannot survive a restart.

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

- **D1. Run state and store.** `RunRecord`, `RunStateStore` with in-memory and
  SQL (SQLite or PostgreSQL through SQLAlchemy, which the optional `postgres`
  extra already installs) backends;
  saved at each step boundary and write-ahead around tool calls; optimistic
  versioning; `agent.get_run(run_id)`. Startup notice when stores are not
  durable.
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
- **D6. Trajectory, docs, and proof.** Joined trajectory across segments;
  Redis and MongoDB run stores; a "Durable runs" docs page; end-to-end tests
  across every feature together.

## Decisions to confirm

| # | Question | Recommendation |
| --- | --- | --- |
| 1 | With governance on and no resolver, should an `ask` suspend the run (new) or fail the call (today)? | Suspend by default; `approval_mode="fail"` keeps today's behaviour. Nothing unapproved runs either way; suspend is what makes `ask` usable. |
| 2 | Which durable run store backends first? | In-memory and SQL in D1; Redis and MongoDB in D6. |
| 3 | A non-idempotent call interrupted by a crash: tell the model the outcome is unknown, or require approval to run it again? | Tell the model; under governance a retry is authorized like any call. |
| 4 | A waiting run's sandbox: close it (workspace survives) or keep it alive? | Close it. |

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |
