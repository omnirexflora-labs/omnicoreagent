# Scale out the support desk

One OmniServe process uses about one core, whatever the CPU cap, so its
admission limit is 24 concurrent runs (see "How many runs one process takes" in
`docs/how-to-guides/omniserve.mdx`). To use more cores, run more replicas behind
a load balancer. This directory is that setup: the desk's 2-core budget split
into two 1-core replicas (`desk`, `desk2`) behind nginx (`lb`, `least_conn`),
sharing the one Postgres and Redis.

Sharing the store is what makes it safe. A run's lease says which replica owns
it, the orphan sweep resumes a run whose replica died, and saves are
version-checked, so two replicas cannot overwrite each other. Any replica can
take any request for a run.

| File | What it is |
| --- | --- |
| `compose.replicas.yml` | An override for `../compose.yml`: caps `desk` at 1 CPU, adds `desk2` (the same service, 1 CPU) and the `lb` nginx |
| `nginx.conf` | The two replicas as one upstream, with SSE buffering off |

## Run it

From the repository root:

```bash
docker compose -p support-desk-scale \
  -f apps/support_desk/compose.yml -f apps/support_desk/scale/compose.replicas.yml up -d --build
curl -s localhost:8810/ready
```

The load balancer is on `127.0.0.1:8810` (`DESK_LB_PORT`). The replicas are on
`8800` and `8801` (`DESK_PORT`, `DESK_PORT2`) for looking at one of them. Each
replica takes 24 runs at once, so the pair takes about 48; to change that, set
`OMNICOREAGENT_SERVE_MAX_CONCURRENT_RUNS`.

Stop it, and its volumes, with
`docker compose -p support-desk-scale -f apps/support_desk/compose.yml -f apps/support_desk/scale/compose.replicas.yml down -v`.

## Load it

Point the load harness (`../load/README.md`) at the load balancer instead of a
desk. Start the stack with `DESK_PROFILE=load` in the environment, then:

```bash
export DESK_PROJECT=support-desk-scale DESK_URL=http://127.0.0.1:8810
uv run --no-sync python apps/support_desk/load/loadtest.py --stages 24:120,32:120,48:120,64:120 \
  --out apps/support_desk/load/results/scale
```

The harness reads the ledger and the runs through the same address, so its
exactly-once check covers both replicas. Its container samples (`docker stats`)
are of the `desk` container only.

## What it gave

Measured on a server on 2026-10-07: the fake model at 0.8 to 3 seconds a call,
Postgres, the 2-core budget split as here, `least_conn`.

| Concurrent users | Throughput | p95 |
| --- | --- | --- |
| 24 | 6.2/s | 7.3 s |
| 32 | 7.7/s | 8.1 s |
| 48 | 9.4/s | 9.8 s |
| 64 | 9.3/s | 13.7 s |

The peak is 9.4/s against 5.5/s for one process on the same two cores: about
1.7 times. Each replica's knee is about 24, so the pair bends at about 48.
1,087 approved refunds made 1,087 ledger rows: each happened exactly once, across
both replicas.
