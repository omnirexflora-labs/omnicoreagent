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

- **Record each thing once.** Each message is stored once per trace, by the
  digest the trace already computes, and so is each tool catalog. A model
  call records which messages it was sent: the previous call's list it
  extends and what it appends. No truncation is then needed for the
  context: a 100-message run stores 100 messages, not 100 conversations. An
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
- **T3. Routine checks summarized.** Measured after T1 and T2 (below): the
  policy records were not a per-call cost but the sandbox workspace bridge
  checking every workspace file before every run's first command, each
  recorded as a request and an allow (8,812 of 10,831 requests; up to 983
  per trace). The engine can authorize without recording what it allows;
  a refusal, an ask, a failure and anything the policy audits are always
  recorded. The bridge records one summary per copy. Test: a copy of 30
  files and one refused file records the refusal and one summary.
- **T3b. The tool catalog once across traces.** After T2 the largest item
  in a short run is its tool catalog, about 70 KB per trace for the
  steward's 60 tools, identical in every run. Stored once, content
  addressed, where the payload store keeps large values.
- **T4. The archive.** A `TelemetryArchive` of finished traces: one body per
  trace (the trace and each event's stream cursor), written through the
  workspace storage interface, so a local directory, S3 or R2; and a SQLite
  index, one row per trace: trace, run, parent, session, task, agent,
  workflow, model, status, start and end, the trace's first and last stream
  cursor, its payload references, its size and where its body is. It can
  put, get, list by filter (headers from the index, bodies only for the
  matches), give the events after a cursor, remove, and say its highest
  cursor. Test: its own contract, and listing reads no body that does not
  match. Postgres follows the same interface when a deployment needs a
  shared index.
- **T5. Running traces in the log, finished traces in the archive.** The
  JSONL store keeps what it is good at, durability while a trace is written:
  every record is appended as it happens, so a crash loses nothing. When a
  trace ends, it is written to the archive and leaves memory, and the log is
  compacted to the traces still running when it has grown. Reads look at
  running traces first, then the archive through a small cache; listing
  narrows through the index; a stream resumed from an old cursor reads the
  archived traces whose cursors are after it. The cursor counter continues
  from the archive's highest after a restart. A trace that receives a record
  after it ended (an exporter failure) is updated in the archive. Test:
  memory does not grow with finished traces; a restart reads only running
  traces; the store contract and stream-resume tests pass over it.
- **T6. Retention and migration.** Retention through the index. Migration
  needs no command: the first start replays the old log, finds its traces
  finished, archives them and compacts the log. The steward's 235 MiB file
  is migrated this way and P7 measures the targets above.

T1–T3 shrink what is written and help the current store at once; T4–T6
change where it is kept. JSONL stays available for local development.

### Measured after T1 and T2

The steward's three scheduled runs of 2026-09-21 23:16 to 2026-09-22 05:21
UTC, recorded with T1 and T2: 1,533 KB for 6 traces. The largest items were
`context_message` (212 KB, 88 records), `context_tools` (209 KB, 3
records: one catalog per trace), and span ends (148 KB). Policy records were
not in the top ten: in the P3 runs they came from the workspace bridge,
whose workspace held two clones of the repository (938 files) that a worker
had copied back from its sandbox.

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |
| T1 | Done | `abd3dc8` | The model call span keeps the one copy of the context (exporters read it there); the context assembly span and event record digests; the model call event points to its span; the trajectory reader resolves the request from the span, capture state included. The scripted acceptance run: 89 KB to 58 KB per model call (-35%), evidence still complete. On the steward, where the four copies were 63% of the file, the expected cut is close to half; P7 measures it. |
| T2 | Done | `b5ef5fc` | Each message a `context_message` event once per trace, digest in metadata; the tool catalog a `context_tools` event once; a model call records the previous call's list it extends and what it appends; the context assembly keeps its digest list at every capture level, once and compact (it was written five times per call). The trajectory reader and exporters rebuild each whole request, in any span order. A 48-step run with 3 KB tool results: 13.8 MB and *partial* before T1, 2.4 MB and complete now; 6x the steps is 5.6x the size. |
| T3 | Done | `c4c6190` | `authorize_all(record_allows=False)`: an allowed request is not recorded on its own; refusals, asks, failures and audited decisions always are. The bridge records one `policy_decisions_summarized` per copy (allowed count, refused paths). The P3 runs' 8,812 per-file request and allow pairs (about 19 MB) become one summary per copy. |
