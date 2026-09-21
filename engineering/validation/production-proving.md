# Proving the runtime in production: what the steward broke, and what was fixed

Status: in progress (2026-09-21). P1, P2, P4, P5 and P6 of the
[production proving plan](../architecture/production-proving-plan.md) are
done; P3 waits on a token permission; P7 (a week unattended) is running.
This page is the write-up the plan promised: what broke, what was fixed,
what it cost, with the traces. It will be finished when P7 ends.

## The application

A **repository steward** for `omnirexflora-labs/omnicoreagent` itself — a
background agent on this runtime, deployed on a Hetzner server as a compose
project (OmniServe, Postgres, Redis; E2B sandboxes; the hosted GitHub MCP
server), governed by a strict policy of 35 allow, 10 ask and 9 deny rules,
budgeted in dollars ($5.00 a day, $1.00 a piece of work), with every run
recorded in full. Its code is `apps/steward/`. It finds work — a failing
test, an open issue, a failed run of its own — reproduces it in a sandbox,
fixes it behind a person's approval, and opens a pull request that links its
own trace. Nothing in it is a demo helper: every scenario asserts what a
person would check by hand, against the real server, the real model
(`gpt-5.6-terra`), the real repository.

## Why this counts as proof

Nobody trusts a runtime because of its feature list. The steward was built
to hurt the runtime from the outside — kill the server mid-run, take the
sandbox away, run out of money, put two workers on one budget, flood it with
duplicates — and every unit ends with a scripted scenario that either passes
on the server or names what broke. Between 2026-09-20 and 2026-09-21 it
found **seventeen runtime defects** and one missing capability, every one
of them fixed with a test that fails without the fix; the runtime's test
suite went from 1,756 to 1,792 tests. None of these were visible to the
suite before, because the suite's models are scripted and its stores are in
memory. The steward's are not.

## What broke, in the order it was found

Each line names the commit; the plan's execution log has the detail.

### Deploying (P1)

1. Neither Dockerfile could build: the package version comes from git tags,
   and an image has no git. (`UV_DYNAMIC_VERSIONING_BYPASS`)
2. Under a strict policy, connecting to an MCP server, a background task's
   lifecycle, and starting and cancelling its runs are capabilities of their
   own — found one deploy at a time.
3. A task bound to an old policy snapshot could not be deleted either: an
   orphan with no way out. Pausing and deleting under a newer policy are now
   allowed; running stays refused. (`5facdfd`)
4. The injection guardrail blocked every governed background run: the run id
   in the runtime's own workspace preamble read as an encoded payload.
   (`a5dea95`)
5. A real MCP schema crashed the run-configuration digest: a JSON-schema
   `"type": ["string", "null"]` is unhashable. (`6bdf3b7`)
6. The first real model call was refused — `temperature` on a reasoning
   model — and the run died a provider error. The runtime now retries once
   without the parameter the provider names, and records it. (`5993d32`)
7. **A killed run was failed, not resumed.** With the server killed at step
   3 of a run whose durable record was resumable, expired-lease recovery
   treated the lost attempt as a failed one and, with no retry left, ended
   the run. Recovery now reads the agent's checkpoint first: a resumable run
   is requeued as `interrupted`, and the next attempt continues it.
   (`c425cfe`) — proved: killed at step 3, completed at step 8, two trace
   segments in one run.

### Reproducing in a sandbox (P2)

8. No application could say what its sandbox is: the docs promised "no
   network unless your policy allows it", and nothing could ask.
   `governance_config.sandbox_manifest` now can. (`d9f5bc2`)
9. **An ask raised inside a tool call did not pause the run.** The sandbox's
   network approval was recorded against no tool call; the tool errored, the
   run went on to "success", a pending approval was left on its record.
   The governed tool runner now marks the call it executes; the run pauses
   and continues the call after a decision. (`d9f5bc2`)
10. **A killed sandbox was a slow command.** E2B reports a sandbox that died
    mid-command as a timeout; every later command "timed out" the same way,
    and the run never learned its sandbox was gone. The adapter asks the
    sandbox whether it still runs; the scope opens a fresh one; the model is
    told; the trace records `lost: true`. (`614cf71`) — proved: sandbox
    killed at the provider mid-run, the run finished in a fresh one.
11. The guardrail again, three ways: leetspeak folding turned a hex run id
    into letter runs (padding, ×7), the digits it cannot fold made the same
    token "letters with digits" (×7), and `_reasoning_override` matched a
    pattern meant for spaced-out words. (`02946ae`)
12. A worker could not be built under budgets: it inherited the parent's
    budgets twice, through the config and the policy. (`8051bc3`)
13. A risk word was a substring: `uv sync` printed "pydantic" five times and
    the counter found "dan" in each — "very dense attack keywords", tool
    output blocked. (`2a90d2a`)
14. **A lock left by a dead process bricked the deployment.** The container
    was recreated while the worker held the Redis task-store lock (lease five
    minutes, acquisition gave up after thirty seconds); every restart
    crash-looped until the lease lapsed. Thirty-second lease, acquisition
    outlasts it, a live holder is named. (`50f9342`)

### Fixing behind an approval (P3, in progress)

15. An approver over HTTP could not see what they approved: the run view
    listed an approval's state and tool but not the call's arguments.
    (`0b39944`)
16. **A delegation was one tool call.** The parent's `tool_call_timeout`
    cancelled `spawn_subagents` and killed a worker mid-fix, twice; the
    steward then, correctly, refused to push anything unverified. A worker
    is now bounded by its own limits, or by `subagent_timeout`. (`2b3f635`)
17. **A resumed run answered a call the provider could not see.** After an
    approval, the resumed run's first model call was rejected — OpenAI: "No
    tool call found for function call output". The history loader had
    discarded the paused assistant turn as incomplete; scripted models had
    never minded. A resumed run keeps that turn. Reproduced and verified live.
    (`94f0900`)

### Budgets in dollars (P4)

18. A background run that ran out of budget was recorded as *completed*
    ("Waiting for budget …" as its answer); it now parks in
    `awaiting_budget` like an approval pause. (`5397c97`)
19. Nobody could read what a budget had spent. `GET /runs/{run_id}/budget`.
    (`628f5b7`)
20. **A charge that lost the race was given up on.** Two processes on one
    Postgres key: the compare-and-swap tried eight times, five milliseconds
    apart, then raised, and one process's charges were lost. It now waits out
    the burst. (`11d7c51`) — proved: 600 charges from two processes, every
    one landed once.

### Its own failures as work (P5)

21. **A worker that had lost its lease kept running.** A blocking tool
    stalled the event loop, heartbeats stopped, the lease expired, the
    attempt was recorded as failed — and the agent ran on, unfenced, to
    completion. The heartbeat that finds the lease gone now stops the agent.
    (`4e331de`)

### The page (P6)

22. An application had nowhere to put a page. An agent file may now define
    `router` and `public_paths`; OmniServe mounts them beside the API.
    (`161861e`)

## What it cost

A read-the-repository run costs about two to eight cents on `gpt-5.6-terra`
(56k tokens, $0.0196; the first P1 run $0.0843); a reproduction with a worker
a few cents more; the whole of P1–P6, with every failed attempt, $1.42 on the
application's day counter. The budget's worst-case hold — `max_tokens` at
the output price, about five cents a call — is larger than most calls
actually cost, so a small budget is governed by the hold, not the spend; that
is the never-overspend rule working as written, and a question for the design
rather than a defect.

## What is still open

- P3's push waits on the steward's GitHub token: a fine-grained token whose
  *Contents* permission is read-only cannot create a branch (403). Not a
  runtime finding.
- A worker's own `ask` is reported to its lead as an error; the worker's run
  stays parked with nobody to resume it. The steward keeps every GitHub write
  with the lead. A worker's approval should pause the lead's run.
- The sandbox bridge copies the whole agent workspace — every earlier run's
  files — into every sandbox and hashes all of it after each command. P7
  measures it.
- `runtime_error` events carry no traceback, even at `capture="full"`.
- The SQL task store is SQLite-only; the steward's task store is Redis.
- With `guardrail_mode: full`, a *suspicious* tool output is blocked by
  default; for an agent working on a code repository that default blocks
  ordinary work. The steward sets `suspicious_output_action: flag`.

## The traces

Every run named above is on the server: `GET /telemetry/runs/{run_id}/trace`
behind the tunnel, and on the steward's page at `/steward/`. The run ids are
in the plan's execution log.
