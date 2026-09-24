# Scale: load evidence, any SQL database, one shared index

Status: agreed with the maintainer 2026-09-23 ("can you work on those").
The three things missing before telling a stranger to depend on this
runtime, from the assessment that day: no load evidence, a task store that
is SQLite-only, and a telemetry index that is a local file.

## Why they are as they are

- **The SQL task store is SQLite-only** because of how it persists: every
  operation loads the whole state into memory, mutates it, and writes it all
  back inside a `BEGIN IMMEDIATE` transaction, using the database file as one
  lock (`store/sql.py`). That is correct and simple for one machine, and
  wrong for a shared database: it would serialize every worker in the cluster
  and rewrite every row each time.

  Reading the code to plan this unit found the real shape of the problem, and
  it is larger than the assessment said. **Every durable task store does
  this**, not just the SQL one: `RedisTaskStore` and `MongoDbTaskStore` are
  both `SerializedTaskStore`, which takes a lock over the whole store, loads
  all of its state, mutates it in memory and writes all of it back
  (`store/serialized.py`). So the cost of one write grows with everything the
  store holds, and nothing prunes run history — only deleting a task deletes
  its runs. Measured on SQLite (median of 20 ordinary run writes):

  | runs held | one write |
  |---|---|
  | 100 | 56 ms |
  | 500 | 157 ms |
  | 2000 | 344 ms |

  The steward has about forty runs, which is why this has never been felt.
  A deployment with a few thousand runs of history spends a third of a second
  on every run write, on every backend, and gets slower for as long as it
  runs.

  The memory store already speaks any SQL database through SQLAlchemy with
  versioned compare-and-swap (`memory_store/sql_db_memory.py`), which is the
  shape the task store needs: one row per entity, and a write that touches
  one row.
- **The telemetry index is a local SQLite file** because T4 of the telemetry
  storage plan built the archive for one process and deferred a shared index:
  bodies already go through the workspace storage interface (local, S3, R2),
  but two OmniServe processes cannot share the index that finds them.

## Units

- **S1. Load evidence.** A harness that drives many runs at a chosen
  concurrency through the agent and through OmniServe, with governance,
  budgets and telemetry on and a scripted model (no provider, no cost), and
  reports throughput, latency percentiles, CPU, memory, telemetry growth and
  what it checked: every run completed, every charge landed once, no leaked
  task, thread or file descriptor. A small version runs in the suite so the
  numbers stay honest; the full run is recorded in
  `engineering/validation/scale.md`.
- **S2. A task store that writes one row, not the world.** One row per
  entity, versioned compare-and-swap, claiming with row locks (`SELECT … FOR
  UPDATE SKIP LOCKED` where the database has it, an immediate transaction on
  SQLite), over SQLAlchemy in the same idiom as the SQL memory store: SQLite
  as now, Postgres and MySQL as well. The store contract suite runs against
  it, as do the two-worker races. A test in the suite holds the property that
  motivates the unit — a write's cost does not grow with what the store
  holds — by counting what one mutation writes, not by timing it. Redis and
  MongoDB keep their snapshot stores for now; the same treatment for them is
  S2b, and their cost is recorded here.
- **S3. A shared telemetry index.** The archive's index behind an interface,
  with the SQLite implementation as it is and a Postgres one beside it;
  bodies in the workspace's object storage when the index is shared.

  The shape: `TelemetryIndex` is the ten questions the archive asks of its
  index (keep this row, drop these, does it hold this trace, the headers
  matching a filter, the traces with a cursor after this one, the ones that
  ended before then, the highest cursor, the payloads referred to). The
  current SQLite code becomes `SqliteTelemetryIndex` and stays the default —
  no new dependency, and the archive still opens nothing until a trace is
  archived. Beside it, `SqlTelemetryIndex` over SQLAlchemy Core speaks any
  database, in the idiom S2 established.

  Configuration on `TelemetryConfig`: `archive_index_url` for the database,
  `archive_bodies_backend` for where bodies go (`local` as now, or `s3`/`r2`
  through the workspace storage factory, which already reads the deployment's
  bucket configuration). Both unset is exactly today's behaviour.

  What the tests must hold: one contract suite over both implementations, and
  two archives on one index and one bodies store finding each other's traces,
  streaming across both, and agreeing on the cursor — the property S4 needs.
- **S4. Two server processes on one deployment.** The point of S2 and S3:
  two OmniServe processes on one Postgres, each serving runs, neither losing
  nor duplicating work. Proved on the steward's server.

  In two parts, because one of them belongs in CI and the other does not:

  - In the suite: two `BackgroundAgentManager`s over one SQL task store,
    given a set of runs to claim. Every run runs exactly once, every claim is
    fenced by its lease, and a manager killed mid-run has its work taken over
    by the other. On SQLite and on PostgreSQL.
  - On the server: a second OmniServe process beside the steward's, both on
    one PostgreSQL database and one shared bodies directory, queued runs
    divided between them, and each process able to read the other's finished
    traces through the shared index.

  The steward's own tasks stay where they are for this. Its task store is
  Redis, and moving a live deployment's operational state mid-P7 to prove a
  property is the wrong trade: the second process runs its own scripted agent
  against its own database, on the same host, under the same image. Migrating
  the steward to SQL is a separate decision, with its own Redis-to-SQL import
  to write first.

CI gains a Postgres service for S2 and S3.

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |
| S1 | done | | One process is one core: throughput flat at ~15 runs/s from concurrency 1 to 100, latency growing in step. The durable log is 45% of a run's CPU; full capture costs 10% more than the privacy-first default. Found and fixed: the archive walked every finished trace twice (-7% CPU a run). `engineering/validation/scale.md` |
| S2 | done | | A row per entity over SQLAlchemy: SQLite, PostgreSQL or MySQL, version CAS, `FOR UPDATE SKIP LOCKED` where the database has it, and the snapshot store's state imported once. One write: 48.7 ms → 3.2 ms with 2000 runs held, and flat. Contract suite on SQLite and PostgreSQL, CI gains a Postgres service. Redis and MongoDB still snapshot stores (S2b) |
| S3 | done | | The index behind an interface: the SQLite file as the default, `SqlTelemetryIndex` over SQLAlchemy for a shared one, bodies in a shared directory or the deployment's bucket. Found and fixed: two processes on one archive handed out the same stream cursors (`[1, 1, 2, 2]`); a shared index now issues blocks |
