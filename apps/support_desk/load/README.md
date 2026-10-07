# Load, chaos and observability harnesses for the support desk

Three scripts push on the support desk (`apps/support_desk/`) and measure it:

| Directory | Script | Question |
| --- | --- | --- |
| `load/` | `loadtest.py` | How many customers at once, how fast, with how much overhead, and does every refund happen exactly once? |
| `chaos/` | `chaos.py` | When something breaks mid-run (the desk, Postgres, Redis, the model, a tool), is any refund done twice, any run left stuck, any `Retry-After` ignored? |
| `observe/` | `check.py` | Can each of those faults be explained from the run's trace, from Jaeger and from `/prometheus` alone? |

The plan is `engineering/architecture/production-readiness-plan.md`. The model
is always the fake provider, so nothing here spends money. Each script writes a
`result.json` and a Markdown report under its own `results/` directory (not
committed), and prints the report.

## What you need

- Docker with Compose v2, and a repository checkout with `uv sync --all-extras --all-groups --locked` done.
  The scripts need only `httpx`; `uv run --no-sync python ...` finds it.
- Free ports. The commands below use `18800` (desk), `19000` (fake provider), `15434` (Postgres), `6381`-style
  `16381` (Redis) and `16686` (Jaeger). Change them with the `DESK_*_PORT` variables, and tell the scripts with
  `DESK_URL` and `DESK_FAKE_URL_PUBLIC`.
- Everything the scripts do to Docker is by the **Compose project name** (`-p`): they find the containers by
  label and touch only `desk`, `postgres`, `redis` (chaos) or read `docker stats` of `desk` and `pg_stat_activity`
  of `postgres` (load). They never run a pattern kill and never touch another project.

## The load profile

The fake provider answers under a priced model name, so its runs charge the budgets at real prices and a few
hundred of them would stop the load with budget pauses that say nothing about the runtime. Start the desk with
`DESK_PROFILE=load`. It:

- lifts the dollar budgets to a billion (the per-request tool-call cap stays at 20);
- turns on the debug routes (behind the same token): `GET /_debug/ledger` (the refund ledger),
  `GET /_debug/lag` (the event-loop lag probe) and `POST /_debug/tool_delay` (the slow-tool hooks).

Without it the scripts refuse to start. It is never the default.

The other hooks, all off unless set: `DESK_LEASE_SECONDS` (the run lease, default 60), `DESK_TOOL_TIMEOUT`
(default 30), `DESK_TOOL_DELAY` and `DESK_REFUND_HOLD` (also settable through `/_debug/tool_delay`), and
`DESK_DEBUG=1` (the debug routes without the load profile).

## The exact commands for the server

Run from the repository root. The desk is capped by `compose.yml` at 2 CPUs and 4 GB, the instance the plan's
numbers are for; `load/compose.caps.yml` also caps its neighbours (Postgres 1 CPU / 1 GB, Redis 0.5 / 256 MB,
the fake provider 1 / 512 MB). Use your own project name: it is the only thing the scripts act on.

```bash
export DESK_PORT=18800 DESK_FAKE_PORT=19000 DESK_POSTGRES_PORT=15434 DESK_REDIS_PORT=16381
export DESK_PROFILE=load DESK_LEASE_SECONDS=60
export DESK_PROJECT=deskload DESK_URL=http://127.0.0.1:18800 DESK_FAKE_URL_PUBLIC=http://127.0.0.1:19000

# 1. Start the stack (builds the images the first time).
docker compose -p deskload -f apps/support_desk/compose.yml -f apps/support_desk/load/compose.caps.yml up -d --build
curl -s localhost:18800/ready          # {"ready":true,...}

# 2. The ramp: 10, then 50, then 100 customers, 2 + 2 + 10 minutes (the plan's finish line 1).
uv run --no-sync python apps/support_desk/load/loadtest.py --mode ramp --out apps/support_desk/load/results/ramp

# 3. The soak: 50 customers for 30 minutes (finish line 2). Start from a fresh stack so memory is not carried over:
docker compose -p deskload -f apps/support_desk/compose.yml -f apps/support_desk/load/compose.caps.yml restart desk
uv run --no-sync python apps/support_desk/load/loadtest.py --mode soak --out apps/support_desk/load/results/soak

# 4. Chaos: 22 rounds (two of each of 11 faults), 30 customers each, about 5 minutes a round (finish line 3).
uv run --no-sync python apps/support_desk/chaos/chaos.py --rounds 22 --users 30 --out apps/support_desk/chaos/results/full

# 5. Observability (finish line 4): a second stack with Jaeger, then the checklist.
docker compose -p deskload -f apps/support_desk/compose.yml -f apps/support_desk/load/compose.caps.yml down -v
export DESK_JAEGER_PORT=16686
docker compose -p deskobs -f apps/support_desk/compose.yml -f apps/support_desk/load/compose.caps.yml \
  -f apps/support_desk/observe/compose.override.yml up -d --build
DESK_PROJECT=deskobs uv run --no-sync python apps/support_desk/observe/check.py --run --users 10 \
  --project deskobs --jaeger-url http://127.0.0.1:16686 --out apps/support_desk/observe/results/full

# 6. Stop everything cleanly: only these two projects, and their volumes.
docker compose -p deskobs -f apps/support_desk/compose.yml -f apps/support_desk/observe/compose.override.yml down -v
docker compose -p deskload -f apps/support_desk/compose.yml down -v
```

If a script is interrupted, stop it with Ctrl-C (or `kill <its PID>`); the desk keeps running, and the next run
starts clean because every run uses its own session names. A chaos round that was cut off may leave the desk,
Postgres or Redis stopped: `docker compose -p deskload ... up -d` brings them back.

A quick check that the harnesses work on a machine, a few minutes in all:

```bash
uv run --no-sync python apps/support_desk/load/loadtest.py --mode smoke --model-latency 0.3,0.8
uv run --no-sync python apps/support_desk/chaos/chaos.py --faults desk_kill,provider_429 --rounds 2 --users 8 --warmup 10 --after 20
```

## load/loadtest.py

`--mode ramp` is stages `10:120,50:120,100:600` (users:seconds); `--mode soak` is `50:1800`; `--mode smoke` is two
short stages. `--stages 20:60,40:60` sets your own. A **customer** (one asyncio task each) visits over and over:
new session; asks where an order is; then either asks a help question (30%, a fifth of those streamed over SSE)
or asks for a refund of a unique amount. The refund pauses the run for approval. **Staff** (one per five customers)
approve over HTTP after a second or so (10% are denied), then resume. Then the customer thinks for 1 to 3 seconds
and visits again.

What it records:

- **Client latency** of every request, p50/p95/p99, per kind and per stage. `/prometheus` is not used for latency:
  its histogram stops counting at 1000 observations.
- **Errors by kind**: `timeout`, `connect`, `disconnect`, `http_NNN`, `run_<status>` (the run did not finish), and so on.
- **Throughput**: requests per second and completed visits per second, per stage.
- **Samples every 5 s**: the desk container's memory and CPU (`docker stats` for that container only), Postgres
  connections (`pg_stat_activity` for database `desk`) and the event-loop lag (the desk's lag probe: a task
  that sleeps 50 ms and records how late it wakes; the worst lag in each 5 s).
- **Runtime overhead per step**, below.
- **Correctness at the end**: every approved refund is in the ledger exactly once, nothing was refunded that was
  denied, and no run is left `running`, `interrupted` or `awaiting_budget`, or waiting for a person.

### How the runtime overhead is computed

After the load stops, the script reads `GET /runs/{id}/trajectory` for a spread sample of runs (200 by default,
evenly across the stages). A trajectory has one segment per stretch of a run, each with its steps. For each step:

```
overhead = step.duration_ms - sum(model_call.facts.latency_ms for each model call in the step)
```

`latency_ms` is what the runtime measured around the provider call, so the subtraction removes exactly the time
spent waiting on the model, and what is left is the loop, the policy check, the memory writes, the telemetry and
the tool itself. The tool's own time is inside the figure, as the plan defines it ("the step time minus the model
time"); the report prints the tool call time next to it (the tools here are a SQLite read and a write), so the two
can be told apart. Steps with no model call (a resumed refund) are listed on their own. A coarser figure is also
given: per segment, the whole segment's duration minus its model latency, which also counts the set-up before
the first step. If a model call has no `latency_ms` it is counted in `model_calls_without_latency` and left out;
the fake provider's own latency is the fallback to subtract if that ever happens.

### Finish-line verdicts

The report puts the plan's numbers next to their targets, PASS or MISS: no failed requests, overhead p95 under
100 ms, no event-loop stall over 500 ms (100 customers for 10 minutes); memory growth under 15% after warm-up and
bounded Postgres connections (the soak). A run below the plan's scale says `(below plan scale)` after the verdict,
so a small check is never mistaken for the real one. Memory growth compares the first and last minute after
warm-up (the first fifth of the run, at most five minutes). Connections are bounded when their maximum after
warm-up is within 25% (or 5) of their median.

## chaos/chaos.py

`python apps/support_desk/chaos/chaos.py --list` shows the faults:

| Fault | What it does |
| --- | --- |
| `desk_kill` | `docker kill` of the desk mid-run; the restart policy (or an explicit start) brings it back |
| `desk_kill_in_refund` | the same, with refunds held 8 s between their ledger write and their return, so the kill lands where a non-idempotent call is most exposed |
| `postgres_restart`, `postgres_kill` | restart / kill of Postgres mid-run |
| `redis_restart` | restart of Redis (the background task store) |
| `provider_429` | the fake provider answers 429 with `Retry-After: 3` to a third of calls, for 40 s |
| `provider_500` | 500 to a third of calls |
| `provider_hang` | a tenth of calls hang past any timeout |
| `provider_slow_stream` | streamed replies pause 1.5 s between chunks |
| `slow_tool` | order lookups and searches take 8 s (`/_debug/tool_delay`) |
| `tool_timeout` | they take longer than the 30 s tool timeout |

A **round** runs `--users` customers (30), waits `--warmup` seconds, injects one fault, holds it, clears it, keeps
the load going `--after` seconds, stops the load, then waits for the desk to settle (until nothing is stuck, at
most the run's lease plus 2 minutes after the fault ended). `--rounds` is the total, taken in turn from `--faults`;
the plan's 50 chaos runs are counted as runs a fault touched (reported as `runs touched`; 22 rounds of 30
customers touch several hundred).

Each round asserts: no refund twice and none issued that nobody approved (the ledger, matched by unique
order-and-amount to the request that made it); no run left stuck past its lease plus 2 minutes; every run that
ended badly has a reason (in its record, or failing that in its trace); and for `provider_429`, whether
`Retry-After` was respected, read from the fake provider's `/_timings` (the gap between a 429 and the next
request of the same conversation).

**The operator.** The runtime does not resume a run whose process died: nothing calls `resume` by itself. After
the fault the script plays the person on call: it resumes `running` runs whose lease has lapsed and decides
refunds nobody decided. The report counts how many runs needed it, and the finish line reads `MISS without an
operator` when any did. `--no-operator` turns it off, and then those runs show as stuck.

## observe/check.py

`observe/compose.override.yml` adds Jaeger (all-in-one, OTLP on the Compose network, UI on `DESK_JAEGER_PORT`) and
sets the desk's `DESK_OTLP_ENDPOINT` to it. `check.py --run` injects each fault once (it runs the chaos harness),
then for each fault: finds a run it touched, reads the run's events and trajectory for text that names the cause,
looks the run's traces up in Jaeger (`/api/traces/<sha256(trace_id)[:16]>`, how the exporter derives the id) and
looks for the cause in the spans, and lists which of the four signals (runs, model errors, budget pauses,
approvals waiting) `/prometheus` has. The output is a checklist: PASS (the trace names the cause and Jaeger has it),
GAP (it does not), or N/A (the fault touched no run). The Gaps section lists what is missing, including a missing
metric. `--chaos-result <result.json>` checks a chaos run that already happened, if Jaeger was up during it.

## Tests

`tests/test_fake_provider.py` and `tests/test_support_desk_app.py` cover the hooks these scripts depend on (the
load profile, the debug routes' lag probe, the slow-tool hooks, the fake provider's timings).
