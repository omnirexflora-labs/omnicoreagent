# End-to-end audit plan

Status: proposed 2026-09-20. Asked for by the maintainer: "stop adding more
features, test everything rigorously from the beginning to the end — everything
that is slowing down request, startup, telemetry, subagent, background. A
serious audit end to end."

No new capability is added under this plan. Everything here is measurement,
correction of what the measurements show, and proof that it stays corrected.

## How this is measured

A request is timed against a model that answers instantly, so every
millisecond measured is the framework's own work, not a provider's. Each unit
states the number it is trying to move, the number it moved it to, and leaves
a committed probe so the number can be taken again.

`engineering/validation/perf_probe.py` is that probe: startup, a no-op
request, a request with a tool call, a governed request, a budgeted request, a
subagent delegation, and a background run, each reported as calls and
milliseconds per request. It runs on demand, not in the test suite, and the
suite gets bounds (not timings) where a regression would be structural: a
count of store writes, a count of serializations, a count of full-trace reads.

## What the first pass already measured

On this machine, python 3.12, in-memory stores, no tools, an instant model:

| Measure | Before |
| --- | --- |
| `import omnicoreagent` | 130 ms total, 29 ms of it ours |
| Agent construction + `initialize()` | 1–4 ms warm |
| One no-op request | ~100 ms |
| `to_plain` calls per no-op request | **7,675** |
| Full-trace reads (deep copies) per request | **4** |
| Telemetry events per no-op request | 11 events, 6 spans |

The two structural findings behind those numbers:

1. **Serialization does the work twice.** `to_plain` calls
   `dataclasses.asdict`, which already walks and *deep-copies* the whole
   object (its leaves go through `copy.deepcopy`), and then `to_plain` walks
   the result again. Every event, span, payload and metadata dict pays this
   twice on the way in.
2. **Every request reads its whole trace back, copied.** `_run_summary` reads
   the finished trace to total it up, and the store returns a deep copy
   (`model_dump` then `from_dict` — two more full walks). The cost grows with
   the size of the trace, so the longer the run, the more the ending costs.

## Units

- **A1. The probe.** `perf_probe.py` with the cases above, so every later
  claim is checked rather than asserted. No behaviour change.
- **A2. Serialization.** One walk instead of two, no deep copy of leaves;
  identical output, proved by comparing before and after on every telemetry
  type. Bound the count in the suite so it cannot silently return.
- **A3. Reading a trace back.** Total a run without copying its whole trace
  (and without reading it twice); keep the copy where a caller can mutate what
  it gets. Bound the number of full reads per request.
- **A4. Redaction.** `_should_redact_key` is called once per key per payload
  and recompiles what it can precompute; make the decision cheap and cached,
  with the same results.
- **A5. Durable runs and memory.** Count the store writes one request makes
  (run state at step boundaries, write-ahead tool records, history writes);
  remove writes that say nothing new; confirm the lease heartbeat does not
  wake more often than it must.
- **A6. Subagents and background.** The cost of a delegation and of a
  background run, end to end: what each spawns, what it copies, what it keeps
  alive afterwards. Anything left running after a run ends is a leak and is
  fixed here.
- **A7. Streaming.** Per-delta cost on the streaming path, which pays the
  serialization cost most often.
- **A8. Leaks and lifetimes.** Tasks, HTTP sessions, file handles, and
  contextvars: what a process holds after a thousand requests, and after a
  crash mid-request. The Daytona client found during live testing (closed only
  on the happy path) is the shape of what this looks for.
- **A9. The report.** One page in `engineering/validation/` with the numbers
  before and after, what changed, and what remains slow on purpose.

## What this plan will not do

- Change what is recorded. A request that records less because it is faster is
  not a faster request, it is a smaller record, and the telemetry goal is a
  trajectory that can be read from end to end.
- Change a default that trades safety for speed (privacy redaction, policy
  checks, write-ahead records). Where one of those is the cost, the unit says
  so and leaves it.

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |
| A1 | Complete | `5801a77` | `engineering/validation/perf_probe.py`: startup, a no-op request, one tool call, a governed request, a budgeted request — each timed against an instant model in **CPU time** (a busy machine's wall clock measures the machine, not the code) and separately *counted* (serializations, telemetry events and spans, trace reads and writes per request), on a fresh workspace each run. Counting is done on a separate agent from timing, because wrapping 7,000 calls per request would be most of what the clock then measured. Two findings the probe surfaced on its own: (1) a fresh agent's first request against a durable store that already holds 500 traces costs ~1,000 ms CPU against ~100 ms afterwards — `JsonlTelemetryStore` parses the entire store into memory inside that request (A3); (2) per-request cost is otherwise flat — 25 requests on one agent serialize exactly 7,675 objects each, so nothing accumulates within a process. |
| A2 | Complete | `5801a77` | `to_plain` walked every record twice: `dataclasses.asdict` walks the whole object and deep-copies its leaves before `to_plain` had looked at any of it, then `to_plain` walked the result again. It now walks once, rebuilding dicts, lists and tuples on the way through (so what is recorded still cannot change underneath the recorder) and never calling `asdict`. Recording an event is 2.6× faster and a span 2.2×, with byte-identical output, measured in CPU time over 20,000 repeats. The guard in the suite watches `dataclasses.asdict` itself, because a count of `to_plain` calls cannot see the second walk — it happened inside `asdict`. 5 tests; the telemetry suite (235 tests) unchanged. |
| A3 | Complete | `e3c1532` | The durable store, on the way in and on the way back. In: a record's own dump is handed to the line builder as already plain instead of being walked again, and the stream index keeps a shallow copy of the store's own event rather than a second deep copy — one event went from 107 nodes walked (about seven walks) to 54 (two: one copy for memory, one dump for disk), held by a guard that measures one walk and allows two. Back: `end_span` hands back the patch it wrote so `end_trace` applies each ending to the trace it already holds instead of reading the trace again, and `update_trace_metadata` merges into the recorder's own copy of the trace it started — a finished run reads its trace back twice (to close it and to total it), not four times. Load: replay adopts each trace it just parsed instead of deep-copying it, sorts a trace's events once after replay instead of after every event, and pruning asks the store which traces ended before the cutoff instead of deep-copying all of them to read a timestamp — loading 40 traces went from 81 serializations to 1 (the read's own). A fresh agent's first request against 500 stored traces went from ~1,040 ms CPU to ~320 ms, the same as any later request; steady-state cost is flat to 1,500 stored traces. Everything on disk is byte-for-byte what it was; the telemetry suite (250 tests) is unchanged. 9 guards. |
| A4 | Complete | `4e1ea6b` | Deciding whether a key names a secret was the largest single cost of a steady-state request: about 4,900 decisions per request, each re-splitting every pattern in the redact list with two regexes compiled through `re`'s cache (97,800 `re._compile` calls in ten requests). The two regexes are compiled once; a key's words are split once and remembered; the decision for a (key, patterns) pair is made once and remembered, and forgets nothing it should not — changing the patterns changes the decision. Under the profiler the cost went from 49 s cumulative to 0.6 s across ten requests. Every decision is held to what the previous implementation returned, case by case, across 37 keys chosen to cover the boundaries (`sessiontoken` yes, `tokenizer` no, `max_tokens` no, `clientSecret` yes). 4 tests. |
| A5' | Complete | `ac62510` | The durable store opened, wrote, and closed its file — after checking its directory — for every one of a request's ~30 records, on a worker thread. It now opens the file once and keeps it, flushing each record to the operating system as it is written; closing after each record did exactly that for durability, since nothing called fsync before and nothing does now. A rewrite (compaction) closes the handle first and the next record reopens, so what comes after a rewrite lands after it; `close()` releases the handle and the writer thread. A record is on disk before the call returns, proved by a second store reading it at once. 3 tests. Measured honestly: this did not move the CPU number much — the profile shows the durable path's cost is the serialization, not the syscalls (A3b). |
| A-llm | Complete | `f10eb4c` | `import litellm` costs 8.6 s of CPU on the audit machine (19 s wall), 4.4 s of it litellm eagerly importing its Anthropic handler and logging, 2.5 s the `openai` SDK. It is imported lazily so that building an agent stays light — which meant the first real request of every process paid for it. `LLMConnection.warm_up()` loads it once, off the event loop; OmniServe calls it while it starts and the background worker calls it for the agents it serves as it starts, so no request pays. `initialize()` is untouched: the guard that it loads no provider client still holds, and a warm-up that fails leaves the error to the request that needs the client, which reports it properly. 4 tests, one against the server's startup. |
| A3b | Complete | `f9af390` | Where the serialization that was left came from, by caller: half of it was the two deep copies of the finished trace at the end of a run (2,181 of 4,484 nodes), a fifth was each event and span being dumped twice — once to copy it into memory, once for its line on disk. Totalling a run only reads it, so `_run_summary` now looks at the stored trace through `peek_trace` (the store's own object, for a reader that changes nothing; a store that cannot share returns a copy) instead of a copy of it; the copy that remains is `end_trace`'s, which applies each span's ending locally and so must own what it changes. The durable store dumps an event or span once and both the line on disk and the in-memory copy are built from that one dump. `to_plain` answers the common leaves (`str`, `int`, `float`, `bool`, `None`) before any other check. A no-op request went from 4,484 nodes serialized to 2,516 and from two full-trace reads to one; 91.7 ms CPU against 105.9 before this unit and 140 before the audit, with a tool call 148.9 against 176.6. Then the durable store was taken apart by measurement: with the write done inline it costs 39 ms, with nothing written 25 ms, with the hop to its writer thread 99 ms — **the per-record hop to the writer thread is ~60 ms of a ~99 ms request on this machine** and is the next decision (see A5''). 4 guards. |
| A5'' | Complete | `1824f86` | Decided with the maintainer (2026-09-20): the durable store keeps its writer thread — a hung disk still cannot hang the event loop, and `persistence_timeout_seconds` still bounds the wait — but records go to it in **one hop per turn of the loop** instead of one hop per record. A record is queued and a drain task runs on the next turn, sending everything queued so far in order in one job; it keeps draining while records keep arriving, so there is only ever one drain and the order on disk is the order recorded. `flush()` waits for the file; `end_trace` awaits it, so a finished run is fully on disk before `run()` returns; a rewrite flushes first; `close()` flushes and closes. What changed: an event is on disk within the same turn of the loop rather than before its own `await` returns — on a hard kill the last few events of an *unfinished* trace can be lost, the same class of loss as the operating system's write buffer, since nothing called fsync before either. Measured: a no-op request went from 91.7 ms CPU to **48.8 ms** (140 ms before the audit began), a tool call from 148.9 to 81.3, a governed call from 156.9 to 64.3; the durable store now costs about what the in-memory one does, within this machine's noise. One integrity test changed its mechanism (a caller that gives up waiting now gives up on `flush()`, not on the append) and keeps its property: a timed-out write never interleaves with the next. Tests that read the file after writing now flush first, which is the contract. 6 guards. |
| A5 (budgets) | Complete | `6c9cea7` | What one request asks of the memory store, counted: a no-op request saves its run record 6 times and reads it twice; one tool call saves it 11 times, reads it 3 times and writes 4 history messages; with three budgeted meters it also made 7 budget writes and 7 budget reads — every meter its own read-modify-write, and each warning check reading the key back after the write had just returned the total. On a remote store each is a round trip. Charges that happen together now go to the store together: a model call holds its cost and counts itself in one write (`reserve_and_charge`), settles the hold and counts its tokens in one (`commit(..., also=)`), and every charge of one key goes through one `charge_many`; a refusal on any meter spends nothing on any. The totals the writes return are what the warnings check. 7 writes and 7 reads became 5 and 5 for that request, and reads never exceed writes; the ledger's `reserve()` keeps its contract. Run-state writes are left exactly as they were: 4 of the 11 are per-message saves beyond the "step boundaries and write-ahead around tool calls" contract, and deferring them is a recovery decision put to the maintainer, not taken here. 1 guard. |
