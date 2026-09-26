# Run-record retention

Status: planned 2026-09-27. Open from the roadmap: durable run records
accumulate forever in the memory store.

## What is there

Every `agent.run()` keeps a durable record in the memory store (in memory,
SQL, Redis, MongoDB): status, step, usage, trace IDs, tool call states,
approvals, budget requests, outcomes, and the run's saved context. The store
contract can save, read and list records, not delete them, and nothing
removes a finished run's record. On a long-lived deployment the store grows by
one record per request, for good. (The request's budget counter had the same
problem; audit A8 fixed it by settling it onto the record.)

Telemetry already has retention: finished traces are pruned after
`retention_days` (7 by default), automatically once per agent, observable
through `telemetry_retention_status` and `GET /telemetry/retention`.

## Design

- **Only finished runs are pruned**: `completed`, `failed`, `blocked`,
  `cancelled`, `timeout`. A run that is `running`, `awaiting_approval`,
  `awaiting_budget` or `interrupted` is waiting for a person or a resume and is
  never removed, however old.
- **Age is measured from `created_at`**, when the run started. (Planned as
  `updated_at`; changed while building RR1: the SQL table has no `updated_at`
  column, and adding one needs a migration on every existing database, while
  `created_at` lets every store delete in one query.) An outcome recorded
  after a run's record is gone raises `LookupError`, which the docs say: keep
  records as long as outcomes can arrive.
- **The user decides**: `run_retention_days=None` keeps every record forever
  (the maintainer, 2026-09-27: "some might want it saved forever"); 30 is only
  the default when it is not set.
- **Setting**: `agent_config["run_retention_days"]`; `None` keeps every
  record.
- **When**: once per agent, like telemetry, on the first run; and on demand,
  `await agent.prune_runs()`, which returns what it removed.
- **Store contract**: `delete_run_states(run_ids)` on every store; a store
  without it keeps its records and pruning reports that.
- **Observable**: the result of the last pruning is in
  `telemetry_retention_status()` (renamed in the docs "retention status") and
  `GET /telemetry/retention`.
- Conversation history (session messages) is not part of this; it has its own
  memory settings.

## Decision (the maintainer's, 2026-09-27): 30 days

The default for `run_retention_days`:

- **30 days (recommended).** Records are small; audit questions (who approved
  what, what a run spent) and late outcomes (a PR merged next week) outlive
  the 7-day traces. The store stops growing without losing recent history.
- **7 days**, matching telemetry: one window for everything, but a run's
  record disappears as soon as its traces do.
- **Keep forever (`None`)**, pruning only when configured: no surprise
  deletions, but the store grows unless someone sets it.

## Status

RR1–RR3 done (2026-09-27).

## Units

- RR1. `delete_run_states` on all four stores, with the store contract test.
- RR2. `prune_runs()` and `run_retention_days`, automatic once per agent,
  never an unfinished run; reported in the retention status.
- RR3. Docs: durable runs (retention), the settings reference (generated),
  observability (retention status).
