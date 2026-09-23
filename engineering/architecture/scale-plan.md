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
  and rewrite every row each time. The Redis and MongoDB stores are proper
  per-key stores, which is why they are the durable choices today. The memory
  store already speaks any SQL database through SQLAlchemy with versioned
  compare-and-swap (`memory_store/sql_db_memory.py`), which is the shape the
  task store needs.
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
- **S2. A task store for any SQL database.** Row per entity, versioned
  compare-and-swap, claiming with row locks (`SELECT … FOR UPDATE SKIP
  LOCKED` where the database has it), over SQLAlchemy: SQLite as now,
  Postgres and MySQL as well. The store contract suite runs against it, and
  the two-worker races that Redis and MongoDB already pass.
- **S3. A shared telemetry index.** The archive's index behind an interface,
  with the SQLite implementation as it is and a Postgres one beside it;
  bodies in the workspace's object storage when the index is shared.
- **S4. Two server processes on one deployment.** The point of S2 and S3:
  two OmniServe processes on one Postgres and one bucket, each serving runs,
  neither losing or duplicating work. Proved on the steward's server.

CI gains a Postgres service for S2 and S3.

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |
| S1 | done | | One process is one core: throughput flat at ~15 runs/s from concurrency 1 to 100, latency growing in step. The durable log is 45% of a run's CPU; full capture costs 10% more than the privacy-first default. Found and fixed: the archive walked every finished trace twice (-7% CPU a run). `engineering/validation/scale.md` |
