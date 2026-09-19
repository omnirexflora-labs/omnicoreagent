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
  `native_tools.py`). (Revised in D2a: the run record also keeps its own
  working context; see "Run state".)
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

A run record belongs to one request: it is keyed by `run_id`, and
`session_id` is only stored on it for listing. Concurrent requests in one
session keep separate records (tested). The session's message history,
though, is shared by every request in the session, and messages are not
tagged with their run. A resumed run must not read another request's
messages that arrived while it waited, so from D2 every history message
carries its `run_id`, and a resumed run rebuilds its context from the
history as it was when the run started plus its own messages only.

Revised in D2a: session history cannot be the source for resuming even with
tags, because reads are windowed and other requests can summarize (mark
inactive, or delete under the `delete` retention policy) messages while a run
waits, including the run's own tool-call message. So the run record keeps its
own working context: the history exactly as the run loaded it, and the
messages the run added, stored as the history stores them (same privacy
redaction, so nothing new is exposed). Nothing another request does to the
session changes it. This replaces "the run state does not copy messages".

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
- **D2. Approval suspend and resume.** History messages tagged with their
  `run_id`, and a resumed run's context limited to the history before it
  started plus its own messages (tested with another request running in the
  same session while it waits). Suspend on an unanswered ask; request
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
| D1 | Complete | `5669a0b` | New `core/runs.py`: `RunTracker` saves each run's record (status, step, usage, trace IDs, and each tool call with an arguments digest, never the arguments) through the memory store the application chose; `current_run()` is the running tracker. Run-state methods (`save_run_state` with compare-and-swap versions, `get_run_state`, `list_run_states`) on the memory store base (not abstract: a store or router without async run-state methods keeps working, not durable) and implemented for in memory, SQL (a `run_states` table, created on existing databases too), Redis (one hash per run, a session index, and a Lua compare-and-swap), and MongoDB (`<collection>_run_states`, updates matched on the version). The agent records the start, every step, each tool call as `started` before it runs (write-ahead; if that save fails the call does not run) and `completed` or `interrupted` (cancelled or timed out, effect unknown) after, and the end: `completed`, `failed` (including a provider error that ends the run with an error response), `blocked` (guardrail), or `cancelled`. Saves are serialized per run, so parallel tool calls cannot race on versions. `agent.get_run(run_id)` and `agent.list_runs(session_id=, status=)`. Choosing SQL, Redis, or MongoDB without its URL now logs a warning instead of info (the store silently falls back to in memory). Found and fixed during D1: the SQL and Redis connection managers were process-wide singletons, so a second store with a different database URL silently used the first one's database (reproduced for both); SQL engines are now per URL and each Redis store has its own client; the tests that reset the old singletons no longer need to. Redis and MongoDB were tested against real servers in throwaway containers (`redis:7-alpine`, `mongo:7`), which also ran the existing live Redis and MongoDB memory and background task store tests for the first time here. 19 new tests, including one per durable backend where a separate agent with a fresh connection reads a finished run. Full suite with the live backends 1,582 passed, 2 skipped (S3 and R2, which need cloud credentials); ruff clean. |
| D2a | Complete | `9f48f5f` | A run is bounded to its own request. Every message a run stores in the session history carries its `run_id`. The run record keeps its own working context: the history exactly as the run loaded it (first load only) and the messages it added, stored exactly as the history stores them (same privacy redaction). Other requests in the same session, the memory window, and summarization cannot change it (tested: six later runs with a two-message sliding window leave an earlier run's context byte-for-byte unchanged). The tool call entries still hold only an arguments digest; the context holds the conversation as the history does. The design changed from "no copied messages" because a windowed or summarized session history cannot be resumed from safely. 5 new tests. Full suite with live Redis and MongoDB 1,587 passed, 2 skipped (S3 and R2); ruff clean. |
| D2b | Complete | `0ea919c` | New `core/run_approvals.py`. `RunApprovalResolver` plugs into governance's existing resolver interface, so expiry checks, telemetry, and the high-risk rules still apply: an ask with no decision yet is recorded on the run as a pending approval (capability, tool, target, risk, reason, request digest, expiry; 24 hours when the policy sets none) and refused for now; a decided approval for the same request is returned once and marked used. `request_digest` binds capability, actor, target, provider, method, host, MCP server, tool, and an arguments digest (now on every tool authority request); the execution surface is left out because it depends on whether the run has a sandbox, not on what was approved. `agent.resolve_approval(run_id, approval_id, decision="approve"/"deny", approver=, note=, arguments=)` records a decision on a pending, unexpired approval (versioned save); a denial's note becomes the refusal reason; `arguments` approves the edited call and only that one. The arguments digest moved to `governance.hashing`. Found during D2b: the resolver marked an approval used before reading its decision from the same record, so every approval read as a denial; caught by the tests before commit. The resolver is not yet the agent's default (D2c). 7 new tests. Full suite with live Redis and MongoDB 1,594 passed, 2 skipped; ruff clean. |
| D2c | Complete | `7394fe4` | With governance on and no approval resolver, an `ask` now pauses the run (`governance_config.approval_mode="suspend"`, the default; `"fail"` keeps the old refusal). In the step that asked, the other calls finish and are saved; the waiting call's result is not saved (it has not happened), its record state is `awaiting_approval`, and no further model call is made. `run()` returns `status="awaiting_approval"` with each approval's ID, tool, capability, target, risk, reason, expiry, and the arguments as the model made them (from the run's context, redacted as history is). The trace segment ends with the new `suspended` status and a `run_suspended` event; the step span ends OK, marked suspended. `agent.resume(run_id)` refuses a run that is not waiting or still has pending approvals, then continues in a new trace segment (`run_resumed` event with the earlier trace IDs and the decisions): the conversation is rebuilt from the run's own context (never the session history), the waiting calls run first (governance authorizes each again, and the run's resolver returns the recorded decision once; approved-with-edits calls run with the edited arguments; a denial reaches the model as the call's result with the approver and note), then the loop continues from the saved step. Usage accumulates across segments. Found while building D2c: approve-with-edits rebuilt the edited request with the default actor, but real tool calls are authorized with the agent's name, so an edited approval would never have matched for a named agent (the D2b test used the default actor); pending approvals now keep their actor. 8 new tests: a pause runs nothing unapproved and makes no further model call; approve, resume, finish, with the result delivered once; a denial's note reaches the model; an edited call runs instead of the original (named agent); another request in the same session while a run waits is never seen by the resumed run; resume refused while waiting or after completion; `approval_mode="fail"`; config validation. Full suite with live Redis and MongoDB 1,602 passed, 2 skipped; ruff clean. |
| D2d | Complete | `e67eb93` | OmniServe: `/run/sync` and the SSE `complete` event return `awaiting_approval` with the approvals (`RunResponse.response` is optional and gains `approvals`); the request trace of a paused run ends `suspended`, not `failed`; the agent's `run_suspended` event streams live. New routes: `GET /runs/{run_id}` (the record without its saved conversation, behind the public privacy boundary), `POST /runs/{run_id}/approvals/{approval_id}` (`decision` approve or deny, `approver`, optional `note` and edited `arguments`; 404 unknown, 409 already decided or expired, 422 invalid decision), `POST /runs/{run_id}/resume` (404 unknown, 409 not waiting or still pending, 504 on timeout). Docs: a new "Durable Runs" page (records, pause and resume, `approval_mode`) and the OmniServe route table. Found during D2d: the sync route finished its request trace and then, if building the response failed, its error handler finished it again, raising "No active telemetry context" and hiding the real error (here, the response model rejecting a paused run's empty response); the route now forgets a finished trace, as the SSE route already did. One existing test compares the sync response exactly and now includes `approvals: null`. 3 new tests. Full suite with live Redis and MongoDB: 1,604 passed, 2 skipped, and the exact-shape test failed; after updating it, its file and the durable-runs files passed (150); ruff clean. D2 is complete. |
| D3 | Complete | `18e585e` | Crash recovery. A live run's record carries an owner and a heartbeat, refreshed on every save and by a keep-alive task during long model or tool calls (`run_lease_seconds`, default 60). `agent.resume(run_id)` also continues a run whose heartbeat is older than the lease, or one marked interrupted; a run with a current heartbeat is refused as running elsewhere, and versioned saves let only one process take a run over. `agent.run(query, run_id=...)` with a known run ID does the same (background recovery calls it that way), and for a finished or failed run starts a new attempt on the same record, keeping a summary of earlier attempts (`attempt`, `previous_attempts`). This fixed a D1 regression: a background retry reuses the run ID, and since D1 its second attempt would have failed trying to create a record that already existed. On recovery, calls with a result never run again, calls that never started run, and a call that started but never finished runs again only if its tool is idempotent; otherwise it does not run, the model gets "its outcome is unknown", and the record says `outcome: unknown`. Idempotency: `register_tool(..., idempotent=True)`; built-in workspace, artifact, and skill-file reads and tool discovery are idempotent; MCP tools use the spec's `readOnlyHint` or `idempotentHint`; the flag is on each catalog binding. Proof: a subprocess using a SQLite memory store is killed with SIGKILL while a tool runs, and a separate agent in the test process finishes the run with the completed charge not repeated and the interrupted report reported as unknown. A crash is simulated in-process with a BaseException subclass no handler catches (`SystemExit` cannot be used: asyncio lets it escape the event loop). Found during D3: checking for a known run ID before the trace started initialized the agent there, so an initialization failure was no longer recorded in a trace; the check now reads the memory router directly. Docs: recovery and idempotency on the Durable Runs page. Not done: a background run that pauses for approval is not yet mapped to a background status (D6). 8 new tests. Full suite with live Redis and MongoDB 1,613 passed, 2 skipped; ruff clean. |
| D4 | Complete | `b3a3d66` | Steering and interrupt. `agent.steer(run_id, message, sender=)` queues a message on the run's record (running, waiting for approval, or interrupted runs); the injection guardrail checks it first, and a blocked message is never queued (OmniServe answers 422). At each step boundary (the top of the loop, before the step counter moves, so a resumed run does not skip a number) the run reads its record once: undelivered messages become user messages, stored in history with `kind: "steering"` and recorded as `run_steered`; once delivered, the inbox keeps only a digest of the text. `agent.interrupt(run_id)` asks a running run to stop at its next boundary: it returns `status="interrupted"`, its record says `interrupted`, the trace segment ends `suspended` with a `run_interrupted` event, and `resume` continues it (with any messages sent meanwhile). The record is now written by the running process and by outside callers at once, so the tracker merges on a version conflict: while the record still belongs to it (its owner, or the owner it loaded on resume), it takes the inbox and interrupt flag others wrote, keeps its own delivered copies, and saves again; a different owner means the run was taken over and the save fails. Outside writers retry against the fresh record (`update_from_outside`). OmniServe: `POST /runs/{run_id}/steer` and `POST /runs/{run_id}/interrupt`. Docs on the Durable Runs page and the route table. 6 new tests (a message mid-tool arrives after the tool result; a blocked message never reaches the model; interrupt then resume; a message sent while stopped arrives on resume; errors; steering a paused run over HTTP). Full suite with live Redis and MongoDB 1,619 passed, 2 skipped; ruff clean. |

