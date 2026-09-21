# Telemetry storage: record once, index, store bodies apart

Status: proposed 2026-09-21, agreed with the maintainer the same day ("yes it
sounds right"). Found by P7 of the
[production proving plan](production-proving-plan.md): the repository
steward's OmniServe went from 303 MiB to 942 MiB on its first trace read
after a restart, and that read took 6.3 seconds.

## What was measured

The steward's trace file on the server, 2026-09-21: 234.7 MiB, 85 traces,
40,239 records, about 2.8 MiB per trace. The JSONL store reads the whole file
into memory on first use (about 2.7 times its size), keeps up to seven days
of traces there, and scans all of them to list or filter.

| What the bytes are | MiB | Share | Records |
| --- | --- | --- | --- |
| `context.assembly` span and event | 75.9 | 32% | 1,046 |
| `model.call` span and event | 72.2 | 31% | 1,046 |
| `policy_request_created` and `policy_decision_allow` events | 46.5 | 20% | 21,631 |
| `sandbox_exec_completed` events | 11.0 | 5% | 405 |
| everything else | 29.1 | 12% | 16,111 |

Two findings in that table:

1. **The model's context is recorded four times per call**: in the context
   assembly span and its event, and in the model call span and its event.
   Each copy is the whole conversation, cut at 64 KB. So every call writes
   about 250 KB, the next call writes it again with one more message, and
   the steward's long runs are recorded *truncated* anyway. Growth is
   quadratic in the length of a run, and the evidence is still partial.
2. **Every authorization writes the full request twice**, about 2.2 KB each:
   about 20 policy records per model call, from sandbox commands and
   workspace operations.

## What is wrong with the design

- **Everything lives in memory.** The store is an in-memory index loaded
  from one file. Memory grows with retained history; a restart pays the
  whole load before the first read.
- **No index.** Listing traces, filtering by run or status, and
  `list_failed_runs` read every trace.
- **One process.** Two OmniServe replicas would each see their own file.
- **The format duplicates.** See above.

Object storage alone does not fix it: it is right for the bulk bytes, cheap
and with lifecycle expiry, but it cannot answer "failed runs of this agent
this week" without opening every object, and objects cannot be appended to
while a trace is written event by event.

## The design

Behind the existing `AbstractTelemetryStore` interface, so callers,
OmniServe routes, the trajectory reader and the evidence adapter do not
change:

- **Record each thing once.** A model call's context is recorded once, on
  its context assembly; the model call refers to it. Each message is stored
  once per trace, by the digest the trace already computes; a context is a
  list of digests. No truncation is then needed for the context: a
  100-message run stores 100 messages, not 100 conversations. An
  authorization records its request once and the decision by reference.
- **An index in a database the application already runs.** One row per
  trace: trace id, run id, parent trace, session, agent, status, evidence
  status, start and end, tokens, cost, bytes, and where the body lives.
  SQLite for a single node, Postgres for a service; the interface allows
  Redis and MongoDB later. Listing and filtering are indexed queries.
- **Bodies apart, one per trace.** A running trace stays in memory while it
  runs, as now, and is written once when it ends: a local directory, or the
  workspace's object storage (S3, R2) where the payload store already
  writes. Reading a trace reads one body. A small cache keeps recent ones.
- **Live streaming unchanged.** Running traces stream from memory, as now;
  stream cursors are kept in the index, so a client resumes across a
  restart.
- **Retention** deletes index rows and bodies together, or leaves bodies to
  an object-storage lifecycle rule.

Targets, measured on the steward under P7:

- memory flat regardless of retained history: OmniServe at rest within
  50 MiB of its empty-store baseline with a week of traces;
- the first trace read after a restart under 200 ms;
- listing a run's traces under 50 ms with 10,000 traces;
- bytes per model call bounded by the new messages of that call, not the
  length of the run;
- `capture: "full"` traces of the steward's runs `complete`, not `partial`.

## Units

Each: a test that fails first, the fix, the full suite, commit and push.
The steward on the server is the live check.

- **T1. Record the context once.** The model call refers to its context
  assembly instead of repeating it; the event mirrors of both spans refer to
  the span. Test: a scripted ten-step run's trace bytes per model call, and
  the trajectory reader still shows each call's full input.
- **T2. Messages by digest.** Each message stored once per trace; a context
  is a list of digests; the trajectory reader and evidence adapter
  reassemble it. The 64 KB cut then applies to a single message, not to a
  conversation. Test: a 100-message run is `complete` at full capture and
  its size grows linearly with its length.
- **T3. Authorizations once.** The policy request recorded once, the
  decision by reference. Test: records per sandbox command.
- **T4. The index.** A `TelemetryIndex` with SQLite and Postgres
  implementations; `list_traces`, filters, stream cursors and retention
  queries through it. Test: the store contract suite passes over it, and
  listing does not read bodies.
- **T5. Bodies apart.** A trace body written once when the trace ends, to a
  local directory or the workspace's object storage; read one at a time with
  a bounded cache. Test: memory does not grow with retained traces; a
  restart reads nothing until asked.
- **T6. Retention and migration.** Retention through the index;
  `omnicoreagent telemetry migrate` converts a `traces.jsonl` into index and
  bodies. The steward's file is migrated and P7 measures the targets above.

T1–T3 shrink what is written and help the current store at once; T4–T6
change where it is kept. JSONL stays available for local development.

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |
