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

## P6: what the first server ramp found, and the fixes (approved 2026-10-07)

Server ramp, measured on the capped 2-core instance with the fake provider (0.8–3 s per call):

- **Throughput and latency.** Throughput topped out at 3–4.5 requests a second. Chat p50 latency was 4 s at 10 users and 37 s at 100. Runtime overhead per step, p95, was 240 ms at 10 users and 18 s at 100.
- **Event loop.** The worst stall was 852 ms.
- **Failures.** 46 runs failed. All of them failed on the same error: `RuntimeError: Could not record the budget change for application:…`. Each was mislabelled `provider_error`, with `error: null` in the run record. In ten of them the refund had already been written.
- **Profile** (py-spy, 30 users):
  - About 45% of samples were the SQL memory store's per-operation work: `do_ping` on every checkout, plus a commit and a rollback each time.
  - Postgres connections were pinned at 13.
  - About 5% was `_close_dead_segments`, which lists traces on every resume.

**Track 1: budgets (decision a).**
- Each budget change becomes one atomic statement: `spent = spent + x` guarded by the limit, in SQL, Redis and Mongo, and atomically in SQLite and in memory.
- Holds become rows of their own, not one shared JSON blob.
- A charge that cannot be recorded after the work ran never fails the run. It is retried, logged, and visible.
- Hammer test: 200 concurrent runs against one budget, no failure, exact total.

**Track 2: throughput.**
- An admission limit per process.
  - The default is derived from the CPUs the process actually has, with one optional setting.
  - When the limit is reached, a request waits briefly for a slot, then gets `503` with `Retry-After`.
  - The default number is set from the knee measured after this track.
- The SQL store stops pinging on every checkout and drops the needless commit and rollback per operation, and its pool is sized properly.
- `_close_dead_segments` is made cheap: it does not list traces on every resume.

**Track 3: resilience.**
- Provider 500s are retried, and `Retry-After` is respected.
- A failed run's record carries its real error and the right cause.
- A user message is never lost when the memory store fails mid-request.
- Orphaned runs (dead process, lapsed lease) are picked up automatically by the server.

**Then.** Observability: the `/prometheus` count/sum bug, plus run, model, budget and approval metrics, and why a run resumed. After that, the ramp, the soak and the chaos runs again, compared with the numbers above.

## P6, round trips and event-loop CPU: what was cut and what was measured (2026-10-07)

The one finish line still missed was runtime overhead per step, p95 under 100 ms. The server profile
said the event loop was busy 18% of the time and the rest was waiting for about 54 Postgres
transactions a run through a small thread pool. This track counted first, then cut. The counter is
`apps/support_desk/load/roundtrips.py`; `tests/test_desk_roundtrips.py` holds the counts under a
ceiling for the refund scenario (lookup, refund, approval, resume; three model calls, two tool calls;
request, session and application budgets) so they cannot creep back.

| Per refund run, SQL memory store | Before | After |
|---|---|---|
| Budget transactions | 27 | 11 |
| Run-state transactions | 26 | 21 |
| Message transactions | 7 | 7 |
| Transactions, total | 60 | 39 |
| Statements, total (each a round trip on a remote database) | 107 | 80 |
| Budget statements | 74 | 52 |

What changed, in the order it was done:

- **R2, budgets.** A model call holds its cost on every scope and settles all of them. The store now
  applies every scope of one call in ONE transaction (SQL, in memory), all or none, rows locked in key
  order; the first refusal in scope order is the one reported; releasing a resumed run's stale holds
  and reading the counters at the end of a run are batched too. UPDATE and DELETE answer with the row
  they changed where the database can (`RETURNING`), so a hold is removed in one statement and a
  meter's new total needs no read. Redis and MongoDB stay per key, and the reason is written next to
  `batches_budget_changes`: Redis keys carry their own hash tag (one script cannot span them on a
  cluster) and MongoDB's multi-document transactions need a replica set; their calls are cheap and
  each is already atomic. The hammer, ledger, stale-hold and batch tests pass on SQLite, in memory,
  Postgres 16, Redis 7 and MongoDB 7 (containers started for the run).
- **R3, run state.** The step boundary was a read of the record and then a save of the step. The save
  already merges a newer version, so the boundary is the save alone (`begin_step`): 3 reads fewer. The
  end of a run no longer reads back a record it just wrote to look for workers it never started, and a
  run ID made by `generate_run_id` is not looked up. The database calls run on an executor of their own
  with one thread per pool connection.
- **R4, event-loop CPU.** Token counts, redactions and a message's privacy-safe digest form are kept by
  a digest of the content, in bounded caches; `context_evidence` no longer puts each message in
  canonical form twice; telemetry IDs come from one read of the random source per 256.

What was measured, in process against the fake provider, Postgres 16 on the same machine, 32 runs, the
machine shared with other jobs (load average 10 to 30 on 4 cores, so wall numbers move by tens of
percent between identical runs; the event loop's own CPU per step is the steadier number). Per step,
with 30 earlier exchanges in each session:

| | Before | After |
|---|---|---|
| Concurrency 1: wall / event-loop CPU / p95 | 710 / 145 / 944 ms | 393 / 103 / 514 ms |
| Concurrency 32: wall / event-loop CPU | 568 / 156 ms | 333 / 116 ms |

The same without earlier exchanges, two interleaved rounds: wall at concurrency 1 went from about 900 to
about 610 ms per step, at concurrency 32 from about 670 to about 480; the event loop's CPU from about 132
to about 115 ms. With a simulated 8 ms round trip per statement (the cross-zone case) wall at
concurrency 1 went from 894 to 689 ms per step.

What the numbers do not say. The server's p95 target is for a 2-core container under a real ramp, and
this machine is not that; the event loop still spends roughly 100 ms of CPU a step here, most of it in
the model client and the telemetry writes, which this track did not touch. The next ramp on the server
is the measure. The dedicated executor made no difference at loopback latency and about 13% at
concurrency 32 with 8 ms statements (459 to 398 ms per step), inside the noise of this machine; it is
kept because it removes a shared bottleneck, not because this run proved it.

What was left alone, and why.

- **The run configuration is still recorded on every resume.** Each trace segment is read on its own: the
  trajectory header comes from the first `run_configuration` event of a trace, and the version metadata
  is per trace. Skipping it would leave a resumed segment without a header. It is cheap now (the memo
  above) instead of absent.
- **The history snapshot, `tool_started`, a completed call and its result, the approval marks and the
  finish keep their own saves.** Deferring the history snapshot to the next step's save, saving a
  completed call together with its result message, or merging the approval's "used" mark into a later
  save would each remove a transaction. Each also widens a crash window: the resumed run would lack its
  history, know a finished call only as started, or run a call whose approval was not yet recorded as
  used. Those are the durability contract, so they stay.
- **Messages are one transaction each** (6 writes, 1 read): each is a message a resumed run must find.
- **Redis and MongoDB budgets stay per key** (above).

Found on the way, not caused by this track: a person's approval could fail with `RunStateConflict` when
the run's last heartbeat landed between `decide`'s read and its save. `decide` now reads again and
re-checks, up to ten times.

## P7: decisions after the third soak (approved 2026-10-08)

The third soak left the process's own memory flat (`anon` at 339.2 MiB from minute 10 to 30) and the
container growing 3.9%: kernel slab, one inode and dentry per trace body file. The same fact is an
operational problem on its own: about 4.7 body files per visit, kept for the 30-day retention, is
millions of files a month in one directory. Two decisions follow.

1. **Trace bodies are packed into segment files, and export is documented for large deployments.**
   - A local archive appends each body to a segment file, one per writer process per hour
     (`bodies/segments/<hour>-<writer>.seg`). The index row's `body` names the segment and the body's
     offset and length, so a read is one `pread`. Writers never share a segment, so a shared
     `archive_bodies_path` needs no lock to append.
   - Replacing a trace appends a new copy and repoints the row; the old bytes are dead until the
     segment goes. Retention deletes a segment when no index row points into it any more.
   - Old per-trace files (`bodies/<trace_id>.json`, written by 0.5.1 and earlier) are still read and
     still pruned: a row whose `body` is a plain name is read the old way. Nothing is rewritten.
   - Object storage keeps one object per trace. A bucket has no inodes and no append; packing there
     would cost a read-modify-write per trace.
   - The scale guide says a busy deployment exports traces (OTLP to Jaeger or Tempo) and shortens
     local retention.
2. **The overhead finish line is a measured MISS, documented, not chased.** Runtime overhead per step
   is 81 ms p50 and 182 ms p95 at 10 users on the capped container (from 102 and 240 ms). About 100 ms
   of event-loop CPU a step remains, most of it in the model client library and the telemetry write.
   Against model calls of 1 to 10 seconds it is a few percent of a run. The number and its cause go in
   the scale guide; the client library's cost is backlog.
