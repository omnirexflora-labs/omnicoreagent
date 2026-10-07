# Production readiness: one serious app, under load and failure

Status: approved (2026-10-07). The maintainer approved the finish lines, the server sharing
(2 cores and 4 GB, alongside their jobs) and the $3 live cap.

## Why

0.5.1 is correct on the paths we test. We do not yet know how it behaves with many users at once,
when its databases or the model provider misbehave, or whether an on-call engineer could explain a
bad run from the record alone. The steward found three real bugs in a day by doing real work. This
plan does the same for production concerns: build one real app, push it with load and failure,
measure, and let the evidence pick the fixes.

## Constraints

- **One app.** The live model budget is $8 in total. This plan may spend at most **$3** of it, on
  proving the app works for real. Everything under load uses a **fake model provider** at no cost.
- **The test server is shared with the maintainer's own jobs.** It has 8 cores and 15 GB of RAM.
  - Our load containers are capped at about 2 cores and 4 GB (`docker run --cpus 2 --memory 4g`).
  - Chaos actions touch only our own containers, by name or ID: the app, its Postgres and its Redis.
  - Nothing else on the server is ever stopped.
  - Because of the caps, the numbers are for a 2-core box. That is a fair, small production
    instance.

## The app: a support desk

`apps/support_desk/`: OmniServe serving one agent to many users at once.

- **Users:** each user has a session. They chat over HTTP and get the reply streamed over SSE.
- **Tools:**
  - `lookup_order`, `search_kb`, `issue_refund`.
  - `issue_refund` asks a person over a rule. Agents approve it over HTTP.
- **Budgets:** a per-session budget and an application budget.
- **Storage:** Postgres for run state and memory, Redis for background work, a telemetry store,
  and Prometheus metrics.
- **Shape:** it is small, but shaped like a real deployment: Docker Compose, a README, a load
  script, and a chaos script.

This app is also the example for "how to run OmniCoreAgent in production".

## Units

| # | Unit | What it produces |
|---|---|---|
| P1 | **A fake model provider** for load and fault tests | An OpenAI-compatible HTTP server in a container. It returns scripted tool calls and answers, with a set latency (for example 0.8–3 s) and injected faults (429 with `Retry-After`, 500, timeouts, slow streams). The agent reaches it through `model_config`, the same way it reaches a real provider, so the whole stack is exercised. |
| P2 | **The support desk app** | `apps/support_desk/`: agent, tools, policy, budgets, Compose file, README. Proved live once, end to end, within the $3 cap. |
| P3 | **Load harness and baseline** | A script that ramps 10 → 50 → 100 concurrent sessions against the fake provider. It records: p50/p95/p99 runtime overhead per step (the step time minus the model time), throughput, event-loop stalls, memory over a 30-minute soak, and DB connections. It produces a report with numbers. |
| P4 | **Chaos harness** | Scripted faults during load: `kill -9` of the app mid-run; restarting Postgres and Redis mid-run; provider 429/500/timeouts; a slow tool. For every run it records whether it ended correctly. |
| P5 | **Observability check** | Prometheus scraped during load; OTLP export to a local Jaeger container. A checklist: can every injected fault be explained from the trace and metrics alone? Gaps are listed. |
| P6 | **Fixes from the evidence** | Each finding is rated, then fixed, test first, under the stop rule below. |

## Finish lines (decided now, so this ends)

On the capped 2-core instance, with the fake provider:

1. **Concurrency.** 100 concurrent sessions for 10 minutes.
   - Zero failed requests that were not injected.
   - Runtime overhead p95 under 100 ms per step, excluding model time.
   - No event-loop stall over 500 ms.
2. **Soak.** 30 minutes at 50 sessions: memory grows less than 15% after warm-up, and DB
   connections stay bounded.
3. **Resilience.** 50 chaos runs.
   - No non-idempotent call runs twice.
   - No run is left stuck: each ends completed, failed with a reason, or waiting for a person, within
     its lease plus 2 minutes.
   - Provider 429s respect `Retry-After`.
4. **Observability.** Every injected fault is explainable from its trace or metrics alone.
5. **The app.** It is documented and runnable from its README, and proved live once.

## Stop rule

The same rule as 0.5.0, so this does not become another loop.

- **CORE:** a promise broken under load or failure. Examples: a double-run call, a stuck run, data
  loss, a crash. These are fixed in this round.
- **MISS:** a finish line not met. Fixed if the cause is ours and the fix is contained; otherwise
  measured, documented, and moved to the next release.
- **LATER:** anything else goes to the backlog.

When finish lines 1–5 are met, or every miss is explained, the round ends with a release (0.5.2
or 0.6.0, by what changed) and the app published.

## Decisions for the maintainer

1. **The finish lines above.** Approve them, or change any number.
2. **Server sharing.** Our containers are capped at 2 cores and 4 GB, and run alongside the
   maintainer's jobs, as agreed.
3. **Live spend.** A hard cap of $3, used only for P2's live proof. The fake provider covers
   everything else.
