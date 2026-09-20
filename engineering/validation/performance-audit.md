# Performance audit: before and after

Asked for by the maintainer on 2026-09-20: "stop adding more features, test
everything rigorously from the beginning to the end — everything that is
slowing down request, startup, telemetry, subagent, background." This page is
the result: what was measured, what it showed, what changed, and what was
left as it is on purpose. The plan and the per-unit record are in
`engineering/architecture/end-to-end-audit-plan.md`.

## How it was measured

Every number here is CPU time of the framework's own work: the model answers
instantly, tools return instantly, and the stores are in memory, so a
millisecond is ours and not a provider's. They were taken on the maintainer's
machine — the slowest one available, on purpose: "if it's fast here it's fast
everywhere" — with `engineering/validation/perf_probe.py`, which also
*counts* what one request does (serializations, telemetry events and spans,
trace reads and writes), because a count is a fact about the code where a
millisecond is a fact about the machine.

Counts were the guide throughout. Each finding below became a test that holds
its count to a bound, so it cannot quietly return.

## One request, before and after

CPU per request, instant model, in-memory stores, quiet machine:

| Request | Before the audit | After |
| --- | --- | --- |
| No-op (one model call, no tools) | 140 ms | **18.8 ms** |
| One tool call | 222 ms | 48.9 ms |
| Governed tool call | 226 ms | 47.4 ms |
| Governed, with budgets | 242 ms | 100 ms before A10; A10 removed the largest self-time item left in it (20 schema deep copies per request), and its CPU could not be isolated afterwards on a desktop that was using a third of the machine |
| First request of a process, 500 traces on disk | ~1,040 ms | the same as any request |
| A streamed delta | 51 µs | 28 µs |
| A background run, beyond its own request | ~69 ms, 36 store calls | ~34 ms, 25 store calls |
| `import litellm` (8.6 s of CPU) | inside the first request | at server startup, off the loop |

And what one no-op request does:

| | Before | After |
| --- | --- | --- |
| Objects serialized | 7,675 (16,867 with a durable store) | 2,516 |
| Full-trace deep copies at the end of a run | 4 | 1 |
| Walks of each event into the durable store | ~7 | 1 |
| Hops to the writer thread | one per record (~30) | one per turn of the loop |
| Deep copies of tool schemas | 20 | 0 |
| Saves of the run record (one tool call) | 11 | 8 |
| Saves of the run record (no-op) | 6 | 4 |
| Redaction decisions computed | ~4,900 | once per distinct key |

Startup was already right and stayed so: `import omnicoreagent` is 30–90 ms
of which our own part is under 30, building an agent is under a millisecond,
and `initialize()` loads no provider client.

## What was found, in the order it was found

1. **Serialization walked every record twice** (A2). `to_plain` went through
   `dataclasses.asdict`, which walks the whole object and deep-copies its
   leaves, and then walked the result again. One walk now; identical output;
   an event is recorded 2.6× faster.
2. **The durable store did the most work of anything** (A3, A3b). Each event
   was walked about seven times on its way in; the finished trace was read
   back, deep-copied, four times at the end of every run; replaying a stored
   trace sorted its events after every one of them (quadratic); pruning
   deep-copied every stored trace to read a timestamp. A fresh process's first
   request against 500 stored traces cost a full second. Now: one dump per
   record feeds both disk and memory, a run reads its trace back once (and
   totals it without copying), replay adopts what it parsed and sorts once,
   pruning reads the timestamp. The first request costs what any other does,
   and cost is flat to 1,500 stored traces.
3. **Deciding whether a key is a secret was the largest single cost at steady
   state** (A4): ~4,900 decisions per request, each re-splitting every pattern
   with two uncompiled regexes. Decisions are made once per key and
   remembered; every decision is held to what it was, case by case.
4. **The writer thread was 60% of a request** (A5′, A5″). Each of a request's
   ~30 records opened, wrote and closed the file, and each hopped to the
   writer thread on its own: 60 ms of a 99 ms request. The file is opened
   once, and records go to the thread together, once per turn of the loop.
   *Decided with the maintainer* (see below).
5. **`import litellm` is 8.6 s of CPU and landed inside the first request**
   (A-llm). It is lazy so that building an agent stays light. A server now
   warms it as it starts, off the event loop; the background worker does the
   same. `initialize()` is untouched.
6. **Budgets made seven writes and seven reads of one key for one request**
   (A5, budgets). Charges that happen together go together: five and five.
7. **A background run re-read its task ten times and its run ten times**
   (A6): every event written to the workspace fetched both again. Once now.
   A delegation to a sub-agent builds nothing per request and leaves nothing
   alive; it costs about two requests, which is what it is.
8. **Streaming redacted the whole envelope of every delta** (A7): about five
   regex passes per one-character delta, and a phone-shaped run id was at
   risk of being rewritten into a marker. The delta's text is redacted, once.
9. **A request's budget counter never went away** (A8): one key per request,
   forever, on every durable store. A finished run's spend goes on its record
   and its counter is removed. Nothing else leaks: tasks, threads and file
   descriptors are flat over 400 requests.
10. **Tool schemas were deep-copied at every step** (A10): twenty per request,
    for readers. Copied once, when the catalog is built.
11. **The run record was rewritten for every message** (A11): 4 of a tool
    call's 11 saves were the whole record saved again for each message stored
    to history, beyond the durable-runs contract of step boundaries and
    write-ahead around tool calls. Assistant and user messages now ride on
    the contract save that always follows them; a tool's *result* message is
    still written the moment it exists, because the record keeps a completed
    call's state and not its result, and a resumed run gets the result from
    that message. 11 → 8, 6 → 4. *Decided with the maintainer.*

Two things were found to be *not* problems and are recorded as such: the
per-request cost does not grow with the number of stored traces (it was this
machine, not the code), and a delegation does not construct a new agent,
recorder or store.

## Decided with the maintainer

- **Durable telemetry records are batched to the writer thread** (A5″). The
  thread stays, so a hung disk cannot hang the event loop and
  `persistence_timeout_seconds` still bounds the wait; records go in one hop
  per turn of the loop, in order; ending a trace waits for the file, so a
  finished run is fully on disk before `run()` returns. What changed: an
  event is on disk within the same turn of the loop rather than before its
  own `await` returns. On a hard kill the last few events of an *unfinished*
  trace can be lost — the same class of loss as the operating system's write
  buffer, since nothing called fsync before either. Chosen over writing
  inline (a stalled disk would stall every request) and over leaving it.

- **Per-message run-state saves are deferred** (A11). Every save the
  durable-runs contract names stays synchronous — start, history before the
  first model call, each step, write-ahead before and after each tool, finish,
  suspend, and the lease heartbeat — and a tool's result message is saved the
  moment it exists. An assistant or user message rides on the next of those.
  On a hard kill inside that window the message is lost with the turn it was
  in, which the resumed run re-does from its checkpoint; a completed tool is
  never run again, because its result was saved with it. The maintainer was
  offered 11 → 7; it is 11 → 8 for that reason, and the reason is the point.
- **The durable telemetry store's in-memory mirror stays at 1,000 traces.**
  `JsonlTelemetryStore` keeps a copy of up to `memory_max_traces` finished
  traces in memory to serve reads and streams; it plateaus at roughly 150 MB
  per process and does not grow past that. Left as it is and documented here;
  a smaller default, or reading older traces from the file on demand, is a
  change for when a deployment's memory budget says so.

## Left as it is, on purpose

- **What is recorded.** No request records less than it did. The telemetry
  goal is a trajectory that can be read from end to end, and a smaller record
  is not a faster request.
- **Privacy redaction, policy checks, and write-ahead records** are the cost
  of what they are for and were not traded for speed. Redaction got cheaper
  by remembering decisions, not by making fewer of them.
- **The in-memory store deep-copies on every read and write** of run and
  budget state (the 39 deep copies left in a budgeted request). That is a
  development store isolating itself; SQL, Redis and MongoDB serialize
  instead and do not pay it.
- **`import litellm` itself.** 4.4 s of it is litellm eagerly importing its
  Anthropic handler and logging, 2.5 s the `openai` SDK. Not ours to make
  cheaper; ours to move off the request path, which is done.

## Taking the numbers again

    uv run python engineering/validation/perf_probe.py
    uv run python engineering/validation/perf_probe.py --rounds 40 --json

Run it before and after a change meant to make something faster and put the
two outputs side by side. The counts should not move unless the record
itself changed; the milliseconds are this machine's.
