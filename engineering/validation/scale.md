# What the runtime does under load

Scale plan, S1. Every measurement before this one was of a single request.
This is many runs at once, with governance, budgets, guardrails and
telemetry all on, so that what the runtime costs and where it stops are
facts rather than guesses.

The harness is `engineering/validation/load_test.py`; `tests/test_load_under_concurrency.py`
runs a small version of it in the suite, so the claims that must hold at any
size keep holding.

## How it was measured

A scripted model stands in for a provider: it asks for a tool `--steps`
times, then answers. It has no latency and no cost of its own, so every
millisecond and every byte below belongs to the runtime. What a run does
otherwise is real: strict policy, an application and a request budget, full
guardrails, sessions and run state in a shared memory store, telemetry
capturing in full to the durable JSONL log with the archive behind it.

Measured on the steward's server (12 cores, load average 0.5, nothing else
running) inside the steward image, 200 runs a point. Timings taken on a
laptop under load are not evidence and are not reported here: the first
attempt at this matrix was made on one, and it reported 0.9 runs a second
where an idle machine reports 15.

Every number below was measured before the fix at the end of this report,
so the tables are one consistent set; the fix is measured against them.

Each point checks itself: every run must finish and answer its own
question, the shared budget ledger must equal runs x calls x price to the
cent, and the process must give back every task and file descriptor. All 13
points below did. Threads are the exception and are meant to: asyncio's
default executor keeps the workers it grew (1 to 18 at concurrency 10), and
they are idle.

## Throughput and latency against concurrency

200 runs, 2 tool steps (3 model calls) each, full capture, durable log:

| concurrency | runs/s | CPU ms/run | p50 ms | p95 ms | peak RSS MiB |
|---|---|---|---|---|---|
| 1 | 15.0 | 54.7 | 48.8 | 71.4 | 99 |
| 5 | 14.9 | 56.2 | 257.3 | 314.7 | 105 |
| 10 | 12.9 | 55.7 | 512.7 | 5755.6 | 107 |
| 25 | 15.5 | 55.2 | 1288.4 | 4002.1 | 112 |
| 50 | 15.5 | 55.1 | 2527.3 | 5418.9 | 121 |
| 100 | 15.1 | 54.5 | 6612.2 | 8434.2 | 142 |

Throughput does not move. Latency grows exactly in step with concurrency
(p50 ~ concurrency / 15). CPU per run is flat, and 15 runs/s x 55 ms is
0.82 of one core.

**One process is one event loop is one core.** Concurrency inside a process
buys nothing but a queue: the work is CPU-bound Python, and the GIL serves
it one run at a time. This is the ceiling to design around, and it is why
the scale plan's answer is more processes (S4), not more tasks. It is also
not as tight as it looks in production: these runs have no provider latency,
where a real run spends most of its wall time waiting on a model. A process
that is 80% idle waiting on providers serves far more than 15 concurrent
runs; what it cannot exceed is ~15 runs/s of the runtime's own work.

Memory is flat in the number of runs and grows only with concurrency:
+43 MiB from 1 to 100 concurrent runs, about 0.4 MiB a run in flight.
Nothing accumulates: 200 runs at concurrency 1 and 200 at concurrency 100
rest at the same size.

## What telemetry costs

| point | runs/s | CPU ms/run | bytes/run |
|---|---|---|---|
| full capture, durable log | 12.9 | 55.7 | 197,999 |
| default capture, durable log | 16.4 | 50.8 | 165,597 |
| full capture, memory only | 23.1 | 30.7 | 0 |

Writing the durable log is 45% of what a run costs the runtime (25 ms of
the 56). Capturing in full rather than the privacy-first default adds 10%
CPU and 20% bytes on top of that — the smaller half of the decision, which
is what made `capture: "full"` defensible as the default.

A run's trace is ~200 KB at 3 model calls. Per model call it is 66 KB at 3
calls, 64 KB at 7, 48 KB at 13: what a trace keeps grows with the run, not
with the square of it, which is what the context digests were for.

| steps | model calls | runs/s | CPU ms/run | CPU ms/call | bytes/run |
|---|---|---|---|---|---|
| 2 | 3 | 12.9 | 55.7 | 18.6 | 197,999 |
| 6 | 7 | 6.6 | 131.5 | 18.8 | 451,279 |
| 12 | 13 | 3.7 | 254.5 | 19.6 | 627,636 |

A run costs the same per model call however long it gets. Nothing in the
runtime re-reads the whole conversation on each turn.

## What the HTTP surface costs

The same load through `OmniServe`'s `/run/sync`, in-process over ASGI:

| concurrency | runs/s | CPU ms/run | p50 ms |
|---|---|---|---|
| 1 | 13.0 | 61.8 | 56.4 |
| 10 | 13.4 | 65.9 | 617.7 |
| 50 | 13.2 | 64.7 | 3081.9 |

Serving a run over HTTP costs ~10 ms more than calling the agent object,
about 18%, and does not change the shape: the same ceiling, the same flat
throughput.

## What this found

**The archive walked every finished trace twice.** Storing a trace built
its plain form for the body, then built it again to index the payloads it
refers to. A trace is the largest thing the runtime keeps, so the second
walk was most of what storing one cost: removing it took a run from 54.7 to
51.0 ms of CPU (-7%) and the ceiling from 15.0 to 16.2 runs/s. Fixed in
`archive.py`, held by `test_a_stored_trace_is_walked_once`.

What remains of the write path is not waste: a finished trace is converted
once for the log's record, once for the archive body, and copied once when
the recorder reads it back to end it — the copy is what keeps a stored trace
from changing under a reader.

**A measurement bug in the harness itself**, worth recording because it made
the served surface look nearly half as expensive as it is (35 ms a run
against 66): the scripted model kept its
own step counter, and the served path shares one agent between concurrent
requests, so the counter interleaved and some runs answered before taking
their steps. The ledger check caught it — the runs had not paid for the
calls the report assumed. The model now reads how far a run has got from
that run's own messages and keeps no state, so one model object serves any
number of concurrent runs.

## What a durable write costs (S2)

The durable task stores were snapshot stores: every mutation took a lock over
the whole store, read all of its state, mutated it in memory and wrote all of
it back. `RedisTaskStore` and `MongoDbTaskStore` still do. So the cost of one
write grew with everything the store had ever kept — and nothing prunes run
history, so it only ever grew.

`engineering/validation/task_store_cost.py` fills a store with finished runs
and times an ordinary run write. On the server, median of ten writes:

| store | 100 runs held | 500 | 2000 |
|---|---|---|---|
| SQL, whole state (before) | 5.1 ms | 14.7 ms | 48.7 ms |
| Redis, whole state (ships today) | 6.6 ms | 20.6 ms | 103.5 ms |
| SQL rows on SQLite (after) | 1.5 ms | 3.1 ms | 3.2 ms |
| SQL rows on PostgreSQL (after) | 5.1 ms | 4.8 ms | 7.0 ms |

A row per entity is flat where whole state is linear: ten times cheaper at two
thousand runs on SQLite, and Redis — what the steward runs — spends a tenth of a
second on every background write at that size. The steward has about forty runs,
which is why nobody had felt it.

`tests/test_task_store_write_scope.py` keeps the property without timing
anything: it counts the runs one write touches at 21 runs held and at 201, and
they have to be the same number.

The store is now any SQL database. The contract suite runs against SQLite and
PostgreSQL (CI has both), and `tests/test_sql_task_store_shared.py` proves what
a shared store has to do: two stores on one database see the same runs and
leases, six workers claiming six runs at once take one each, and state the old
snapshot store wrote is taken over on first use.

## One archive, several processes (S3)

The archive keeps one body per trace through the workspace storage interface,
and finds them through an index. That index was a SQLite file in the archive's
own directory, so two server processes could share the bodies and not the index
that finds them. It is now an interface with two implementations: the SQLite
file, still the default and needing nothing installed, and `SqlTelemetryIndex`
over SQLAlchemy, which speaks any database. A deployment sets
`archive_index_url` and either `archive_bodies_path` or
`archive_target: "object_storage"`.

`tests/test_telemetry_index.py` asks both implementations the same ten
questions, and `tests/test_telemetry_shared_archive.py` holds the property S4
needs: two stores, each with its own write-ahead log, one shared index and one
set of bodies, each answering for traces the other finished. Both run on SQLite
and on PostgreSQL.

**What that found.** Writing the harder test — both processes recording before
either finished — showed the two of them handing out the same stream positions:
cursors `[1, 1, 2, 2]`, because each counted from the highest it had seen and a
running trace's events are only in its own process's log. A reader resuming
after position 1 would have lost an event, silently. A shared index now issues
blocks of positions (32 at a time, one round trip per block), so two processes
cannot be given the same one. A process alone does not ask, and its cursors are
unchanged.

What a cursor promises, stated plainly, because a shared index weakens it: a
position is unique across the deployment, and ordered within a trace, a run and
a session — which is what a stream is scoped to. It is not a clock across
processes: two processes holding different blocks can finish traces in an order
their positions do not reflect.

## What is not measured here

- More than one process, one shared database, one shared bucket: S4.
- A real provider's latency and failures under load.
- The background scheduler's own throughput (this drives `run` and
  `/run/sync`).
