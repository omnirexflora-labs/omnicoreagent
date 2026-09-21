# Production proving plan: the repository steward

Status: in progress. Proposed 2026-09-20; chosen by the maintainer: "steward, Hetzner". P1, P2, P4 and P6 done 2026-09-21; P3 (waiting on the token) and P5 in progress.
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
  store; GitHub through the hosted MCP server); one agent, one manual
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
| P1 | Done | `c425cfe` | Deployed on the maintainer's Hetzner server as compose project `steward` (OmniServe from the branch's image, Postgres for memory/run state/budgets, Redis for tasks; loopback only; a bearer token; secrets in `/opt/steward/.env`). What deploying it found, in order: (1) **neither Dockerfile could build** — the package version comes from git tags and an image has no git, so `uv pip install .` failed; both now pass the version in. (2) Under a strict policy, **connecting to an MCP server is a capability** (`mcp.server.connect`), and so is **a background task's lifecycle** (`background.task.create/update/pause/resume/delete`) and **its runs** (`background.run.start/cancel`) — three deploys found them one at a time, and the policy is now proved against the runtime's real request builders before an image is built. (3) **Policy-snapshot pinning proved itself**: a task registered under the previous policy hash was refused under the new one — and then could not be deleted either, an orphan with no way out; pausing and deleting under a newer policy are now allowed (the safe direction), running stays refused, with a test. (4) **The injection guardrail blocked every governed background run**: two bare-token patterns took the run id in the runtime's own workspace preamble (and any commit SHA, UUID or trace id) for an encoded payload — eight hits, CRITICAL; the patterns are gone, decode intent carries its own weight, and the engine's leetspeak folding (`base64` → `base6a`) that had silenced the old decode-intent pattern is matched as it really arrives. (5) **A real MCP schema crashed the run-configuration digest**: JSON-schema `"type": ["string", "null"]` is unhashable and the opaque-block membership test raised before the first model call; the privacy filter had the same check; both now test a string. (6) A `runtime_error` event carries the error's type and message but not its traceback, even at `capture="full"` — the traceback had to be reproduced locally; a gap to close. (7) **The first real model call was refused** — `temperature=0.2` on a reasoning model — and the run ended as a provider error; the runtime now retries once without the parameter the provider names, records the retry with the name, and does not send it again (a refusal that names nothing still fails); the steward sets no temperature. Also noticed: `tests/test_llm.py::test_cookbook_…` imports `cookbook` and passes only inside the full suite (import-order coupling), a test-hygiene item. Also learned: persistent SSH tunnels do not survive this harness's shell, so the scenario runs on the server itself. (8) **A killed run was failed, not resumed**: with the serve container killed at step 3 of a run (two tool calls done, trace open at a model call, the agent's record resumable), expired-lease recovery treated the lost attempt as a failed one and, with no retry left, ended the run as `lease expired` — although the supervisor's re-dispatch already calls `agent.run(run_id=...)`, which continues from the checkpoint. Recovery now reads the agent's checkpoint first: a resumable one closes the attempt as `interrupted`, requeues with one more attempt, and the next attempt (reason `recovery`) resumes; a current heartbeat holds the run instead; and retries are counted from attempts that failed, not from the attempt number (`c425cfe`). Result: P1.2 — `run_now` completes in 48 s, 11 model calls, GitHub reads through MCP, 175 events, one trace, and the steward names issue #177. P1.3 — the container is killed 8 s into a run; after the restart the run completes: attempt 1 `lease_expired` → `interrupted`, attempt 2 `recovery` → `completed`, the agent's record at step 8 joining 2 trace segments (a `run_resumed` event), 5 model calls in all. The steward's task store is Redis because the SQL task store is SQLite-only (gap recorded above); sandboxes are E2B because the container guide forbids the Docker socket. |
| P2 | Done | `c252368`, fixes through `80628fb` | The steward reproduces a real failing test in an E2B sandbox through a delegated worker (`scenario_p2.py`; the test is `tests/test_llm.py::test_cookbook_…`, which imports a module that is not a package of the repository and fails whenever it runs outside the full suite — found by probing the sandbox). What building it found, in order: (1) **No application could say what its sandbox is.** The docs promised "no network unless your policy allows it", but the run's execution scope was always built with the default manifest, so nothing could ask for the network, an image or a working directory — `governance_config.sandbox_manifest` now carries that, read at startup and authorized when the session opens (`d9f5bc2`). (2) **An ask raised inside a tool call did not pause the run.** The sandbox's network approval (and, by the same path, any `ask` on `process.exec`) was recorded against no tool call: the tool errored with the reason, the run went on to "success", and a pending approval was left on its record. The governed tool runner now marks the call it is executing and the sandbox layer names it on every authority request; the run pauses on that approval and continues the call after a decision (`d9f5bc2`). (3) **A killed sandbox was a slow command.** E2B reports a sandbox that died mid-command as a timeout, then "not found" for every command after; the adapter took each for a command that ran long, and the run never learned its sandbox was gone nor got another. The adapter now asks the sandbox whether it still runs; the scope drops a lost session and opens a fresh one for the next command; the model is told; the trace records `lost: true`; every sandbox event carries `sandbox_ref`, the provider's own id (`614cf71`). (4) **The guardrail blocked the run before any model call** — the third guardrail defect found by this application: leetspeak folding turned the run id into letter runs (padding, ×7), the digits the fold cannot map made the same token "letters with digits" (×7), and `_reasoning_override` matched the spaced-out-word pattern (which accepted plain words). Identifiers are no longer folded, padding is matched on the text as written, spaced-out words need a gap, and heavy leetspeak is a heuristic on the original (`02946ae`). (5) **A worker could not be built under budgets**: the steward sets budgets in `governance_config`, the parent's policy carries them once built, and a worker made from the parent's config *and* the parent's policy was refused ("budgets cannot be set when the policy already has budgets") — the derived policy is now the one source (`8051bc3`). (6) **A risk word was a substring**: `uv sync` printed "pydantic" five times, the guardrail's keyword counter found "dan" in each, called the tool output "very dense attack keywords" and blocked it; words are counted as words now (`2a90d2a`). Also noted: with `guardrail_mode: full` a *suspicious* tool output is blocked by default — for an agent working on a code repository (test names, docstrings, pip and pytest chatter) that default blocks ordinary work; the steward sets `suspicious_output_action: flag` and the default is a question for the maintainer. (7) **A lock left by a dead process bricked the deployment**: the container was recreated while the worker held the Redis task-store lock (lease five minutes, acquisition gave up after thirty seconds), and every restart crash-looped with "Timed out acquiring Redis task-store lock" until the lease lapsed. The lease is thirty seconds now, acquisition waits out a full lease, and a live holder is named (`50f9342`, Redis and MongoDB). (8) **The sandbox bridge copies the whole agent workspace**: every `execute` lists and uploads the agent's workspace root — every earlier run's files, P1's `events.jsonl` included — and hashes all of it after each command; a long-lived steward pays more per command with every run it has done. Recorded for P7 (a week unattended), where it will be measured and fixed. Noted, not fixed: the E2B default template is Debian 12 with Python 3.11, 2 cores and 478 MB — `uv` fetches 3.12 in seconds, the suite would not fit; a checkout under the bridged working directory would be listed and hashed after every command (the instructions clone elsewhere). Result: P2.1 — the run completes in 167 s; three traces in the family (the steward's and its worker's), a worker delegated to, an E2B sandbox opened with the network on, 35 commands, and the answer quotes `ModuleNotFoundError: No module named 'cookbook'` from the test alone and from its file. P2.2 — the sandbox the trace names is killed at E2B mid-run; the run completes with the same reproduction: one command reported the loss, the session is recorded as lost, a fresh sandbox opened after it (three sessions in all, 27 commands). The reproduced failure is a real defect of this repository, left for P3 to fix behind an approval. |
| P4 | Done | `1044029`, fixes `5397c97`, `628f5b7`, `11d7c51` | Budgets in dollars on the real model (`scenario_p4.py`): the proving deployment's request budget lowered to $0.08 so one ordinary piece of work runs out mid-way; a grant over HTTP and a resume let it finish; the ledger's figure for the run is checked against its traces' model costs and the application's day counter; and two processes charge one Postgres-backed key at once. What building it found: (1) **A background run that ran out of budget was "completed".** The supervisor knew one kind of pause; an agent stopping for a top-up (`awaiting_budget`) was recorded as a completed run with "Waiting for budget …" as its answer, and over HTTP the request trace of such a pause was marked failed. The run now parks in `awaiting_budget` exactly as in `awaiting_approval`, and `resume_run` accepts either (`5397c97`). (2) **Nobody could read what a budget had spent.** A person could top up over HTTP but not see the ledger; `agent.budget_status(run_id)` and `GET /runs/{run_id}/budget` list every budget covering a run with its limit, spend, reservation and key (`628f5b7`). (3) **A charge that lost the race was given up on.** Two processes on one key: the compare-and-swap tried eight times five milliseconds apart and then raised "Could not record the budget change" — one process's charges lost, and in a real run the model call with them. A charge now waits out the burst with jittered backoff (`11d7c51`). Observed, not changed: a model call is held at its worst case first — `max_tokens` (4,000 here) at the output price, about five cents — while the calls actually cost a fraction of a cent to three cents (a whole read-the-repo run: 56k tokens, $0.0196). A small budget is therefore governed by the hold, not the spend: an $0.08 cap let one run finish and stopped another at $0.031 because the next hold did not fit. That is the never-overspend rule working as written; whether the hold should be closer to the expected cost is a question for the write-up. Result: P4.1 — with the request cap at $0.03 the run parks as `awaiting_budget` before its first call (hold $0.055, needs $0.0247 more), never overspends; a $0.60 grant over HTTP and a resume finish it in a second trace segment; the ledger says $0.0268 for the run, its traces say $0.0268 over 5 model calls, and the application's day counter grew by $0.0268. P4.2 — 600 charges from two processes on one Postgres key: every one landed once (0.600 of 0.600), both processes clean, 4 s. Done on P3's deployment while P3 waited on its token; the request cap is back at $1.00. |
| P6 | Done | `161861e`, `e653518` | The page (`page.html`, `scenario_p6.py`): what the steward is doing now (the running run's events, streamed), the runs it has done with their status, spend, approvals and trace links, and the work items it has scheduled — served by the steward's own OmniServe at `/steward/` on the API's origin. What building it found: **an application had nowhere to put a page** — OmniServe served the agent's API and nothing else, so a page would have needed a second origin, CORS and the token in a browser on another host. An agent file may now define `router`/`routers` and `public_paths`; `omniserve run` mounts them beside the API behind the same middleware, the named paths need no token, everything else keeps it (`161861e`). Result: the page is served without a token (200), the sources it reads refuse a request without one (401 ×3), and for a real run every source answers — the run's record (trace segments, approvals), its budgets ($0.0843 of $1.00 for the first P1 run), its trace (175 events), and the event stream replay (12 events in the first chunk). The page is not public on the internet: it sits behind the same SSH tunnel as the API until a reverse proxy with TLS is in front. |

