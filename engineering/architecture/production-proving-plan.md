# Production proving plan: the repository steward

Status: proposed 2026-09-20. Chosen by the maintainer: "steward, Hetzner".
OmniCoreAgent is built for real applications in production, not demos, and
nobody trusts a runtime because of its feature list — they trust it because
they have seen it fail correctly, in public, on something that mattered. This
plan builds one real, difficult application on the runtime, runs it on a
server the way a production application runs, hurts it from the outside, and
fixes the runtime wherever it gives.

## What it is

A **repository steward** for `omnirexflora-labs/omnicoreagent` itself: a
background agent that, on a schedule, finds work — a failing test, an open
issue, a failed run in the runtime's own telemetry — reproduces it in a
sandbox against the real test suite, writes a fix, and opens a pull request
for a person to review. It cannot push, comment publicly, or merge without an
approval. Every PR it opens carries its own trace: the issue, the
reproduction, what it spent, and who approved the push.

This is the same shape as LangSmith Engine's *detect → fix → prevent* —
"run automatically in the background", "cluster related traces into
issues", "write prompt and code changes based on production failures", "open
GitHub PRs for review" — with the differences that are the point:
self-hosted on our own server, governed by a policy you can read, budgeted
in dollars, durable across restarts, reproducing in a sandbox rather than
reasoning about a trace, and every step of it recorded in a trace anyone can
read. Where Engine is a managed service billed in compute units, this is the
open runtime doing the work on its own repository, in public.

## Why this application

| It must have | Which exercises |
| --- | --- |
| A schedule and no operator watching it | background runs, leases, recovery after restarts |
| A real test suite run in isolation | Docker locally, E2B or Modal hosted; the workspace bridge |
| A side effect a person must approve (push a branch, open a PR) | `ask` governance, durable suspend, `resolve_approval`, resume |
| Many steps per piece of work | run state, checkpoints, the write-ahead tool record |
| A daily cap on the whole steward and a cap per issue | application and request budgets; pause and top up |
| Three kinds of work (triage, reproduce, fix) | sub-agents, delegation charged to the parent |
| GitHub through MCP, not hand-written glue | MCP identity, structured results, reconnects |
| A page showing what it is doing now | OmniServe, SSE streaming, trace routes |
| A call that must never run twice (open a PR) | the non-idempotent rule of crash recovery |
| Its own failures as input | the runtime's telemetry store as a source of work |

## Where it runs

The maintainer's Hetzner server (12 cores, 22 GB, Docker 29, uv; already
hosting other tenants' compose projects, which this plan does not touch).
The steward is its own compose project — OmniServe, Postgres, Redis, the
GitHub MCP server — bound to loopback and reached over an SSH tunnel until
auth is on; then behind a reverse proxy with a token. The host's Python is
3.14, so the steward runs from the repository's Docker image, not host
Python. Secrets (LLM key, GitHub token) live in a `.env` on the server, never
in the image, never in a sandbox.

The chaos is scripted from the maintainer's machine against that deployment,
because the point is to hurt it from outside: kill the worker mid-PR, restart
the container, exhaust the budget, deny an approval, take the sandbox away,
cut the MCP server. Each script asserts what the record and the trace must
say afterwards.

## Units

Each unit ends with a scripted scenario that runs against the server, a
failure it must survive, and whatever runtime fixes it forced — committed
with tests, as always.

- **P1. Deploy.** Compose project on the server (image built from the
  branch; Postgres for memory, run state and budgets; Redis for the task
  store; the GitHub MCP server in stdio via Docker); one agent, one manual
  task, `run_now` over the tunnel completes and its trace reads end to end.
  Failure to survive: the container restarted while a run is in flight — the
  run resumes and finishes with one trace across segments.
- **P2. Reproduce in a sandbox.** The `reproduce` sub-agent checks out the
  repository in a sandbox (Docker on the server first, then E2B), runs the
  test suite for a named test, and reports the failure with its output as an
  artifact. Failure to survive: the sandbox killed mid-run — the run says so,
  is recorded, and the next attempt starts a fresh sandbox.
- **P3. Fix behind an approval.** The `fix` sub-agent writes a change, runs
  the tests again in the sandbox, and asks to push a branch and open a PR;
  the run pauses; a person approves over HTTP; the PR exists with the trace
  linked in its body. Failure to survive: the worker killed after the push
  and before the PR — recovery never pushes twice (the push is not
  idempotent) and tells the model the outcome is unknown.
- **P4. Budgets in dollars.** A daily application budget and a per-issue
  request budget on a real model; a day's work stops at the cap and waits; a
  top-up over HTTP lets it finish; the bill matches the ledger. Failure to
  survive: two workers on the same budget — the ledger's compare-and-swap
  holds across processes on Postgres.
- **P5. Its own failures as work.** The `triage` sub-agent reads the
  runtime's telemetry (failed runs, their summaries) and the repository's
  issues and CI, clusters them into work items, and schedules them. This is
  the Engine-shaped loop: the steward improving the runtime that runs it.
  Failure to survive: a flood of duplicates — one item per cause.
- **P6. The page.** A small page on OmniServe: what the steward is doing
  now (SSE), the runs it has done, each with its trace, its spend, and the
  approval that let it push. This is the public proof.
- **P7. A week unattended.** The steward runs on its schedule for seven days.
  What is measured: memory and file descriptors per day, store sizes,
  budget spend against the ledger, every restart and what it did. Anything
  that grows, leaks, or drifts is a runtime bug and is fixed.
- **P8. The write-up.** What broke, what was fixed, what it cost, with the
  traces — the page that answers "why should I trust this".

## What this plan needs from the maintainer

- A GitHub token for the steward, scoped to the repository (contents,
  issues, pull requests), and whether it may act on the real repository from
  the start or on a fork first.
- The model and key it should spend on, and the daily amount it may spend.
- The server's `.env` populated with those; nothing is committed.

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |
