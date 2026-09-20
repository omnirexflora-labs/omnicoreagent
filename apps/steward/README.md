# The repository steward

A real application on OmniCoreAgent, run in production to prove the runtime:
a background agent that keeps `omnirexflora-labs/omnicoreagent` — finds a
failing test or an issue, reproduces it in a sandbox against the real test
suite, writes a fix, and opens a pull request for a person to review. Every
write to GitHub asks a person first; merging is not in its power at all.

The plan, the units, and what each must survive:
`engineering/architecture/production-proving-plan.md`.

## What is here

| File | What it is |
| --- | --- |
| `agent.py` | The agent: its instructions, its policy (reads allowed, writes ask, the rest denied), its budgets, GitHub through the hosted MCP server, E2B for sandboxes, Postgres for memory and run state. |
| `Dockerfile` | The image: the runtime with the `serve`, `postgres`, `redis`, `e2b` extras, and this directory. No secrets. |
| `compose.yml` | The deployment: OmniServe + Postgres + Redis, bound to loopback. |
| `scenario_p1.py` | The first proof: deploy, do one piece of work end to end, survive a restart mid-run. |
| `scenario_p2.py` | The second: reproduce a real failing test in an E2B sandbox through a delegated worker; survive the sandbox being killed mid-run. |
| `scenario_p3.py` | The third: fix that test behind a person's approval of each GitHub write, real branch and PR; with `--kill`, the server dies after the push and before the PR, and the push is never repeated. |
| `scenario_p4.py` | The fourth: a run stops at its dollar cap and waits, a top-up over HTTP lets it finish, the ledger matches the traces; `--two-workers` proves the compare-and-swap across processes on Postgres. |
| `scenario_p5.py` | The fifth: triage reads the steward's own failed runs and the issues, schedules one work item per cause, and a second triage schedules nothing new. |
| `page.html` | The page: what the steward is doing now, its runs, their spend, the approvals — served by its own OmniServe at `/steward/` (no token for the page; the token for what it reads). |
| `scenario_p6.py` | The sixth: the page is public on the API's origin, the API still needs the token, and every source the page reads answers for a real run. |

## Running it on a server

The server needs Docker and an env file at `/opt/steward/.env` holding:

```
LLM_API_KEY=...                       # the model's key
GITHUB_PERSONAL_ACCESS_TOKEN=...      # fine-grained, this repository only: contents, issues, pull requests
E2B_API_KEY=...                       # sandboxes
OMNICOREAGENT_SERVE_AUTH_TOKEN=...    # the API's bearer token
```

Then, from a clone of this repository on the server:

```
docker compose -p steward -f apps/steward/compose.yml --env-file /opt/steward/.env up -d --build
```

Everything binds to `127.0.0.1` (API on 8800, Postgres 5433, Redis 6380).
Reach the API over an SSH tunnel:

```
ssh -N -L 8800:127.0.0.1:8800 root@<server>
```

## Proving it

From the machine with the tunnel:

```
STEWARD_TOKEN=<the bearer token> python apps/steward/scenario_p1.py            # one run, end to end
STEWARD_TOKEN=<the bearer token> python apps/steward/scenario_p1.py --restart  # kill it mid-run
STEWARD_TOKEN=<the bearer token> python apps/steward/scenario_p2.py            # reproduce a failing test in a sandbox
STEWARD_TOKEN=<the bearer token> python apps/steward/scenario_p2.py --lose     # kill the sandbox mid-run
STEWARD_TOKEN=<the bearer token> python apps/steward/scenario_p3.py            # fix, approve each write, PR
STEWARD_TOKEN=<the bearer token> python apps/steward/scenario_p3.py --kill     # die after the push, before the PR
STEWARD_TOKEN=<the bearer token> python apps/steward/scenario_p4.py            # cap, top-up, bill (STEWARD_REQUEST_USD=0.08)
STEWARD_TOKEN=<the bearer token> python apps/steward/scenario_p4.py --two-workers
STEWARD_TOKEN=<the bearer token> python apps/steward/scenario_p5.py            # triage, twice
```

Long scenarios are best started detached on the server, so a dropped SSH
session does not kill them:

```
nohup setsid python3 -u apps/steward/scenario_p3.py > /opt/steward/logs/p3.log 2>&1 < /dev/null &
```

On the server itself, set `STEWARD_SSH=` (empty) so the scenarios use Docker
directly instead of SSH, and read the token from the env file:

```
set -a; . /opt/steward/.env; set +a
STEWARD_SSH= STEWARD_TOKEN=$OMNICOREAGENT_SERVE_AUTH_TOKEN python3 apps/steward/scenario_p2.py
```

Each line the scenario prints is something a person would check by hand; a
`FAIL` stops it. The trace of every run is at
`GET /telemetry/runs/{run_id}/trace`, and every pull request the steward opens
links the run that produced it.

## Budgets

Deliberately low while proving, so the cap is hit early and seen: by default
$1.00 a day for the whole steward and $0.20 per piece of work
(`STEWARD_DAILY_USD`, `STEWARD_REQUEST_USD` in the env file; the P1 runs cost
about $0.18 each, so from P2 on the proving deployment sets $5.00 and $1.00).
A run that runs out pauses and waits for a top-up:
`POST /runs/{run_id}/budget` with `{"decision": "grant", "amount": ..., "approver": ...}`,
then `POST /runs/{run_id}/resume`.
