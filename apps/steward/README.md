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
```

Each line the scenario prints is something a person would check by hand; a
`FAIL` stops it. The trace of every run is at
`GET /telemetry/runs/{run_id}/trace`, and every pull request the steward opens
links the run that produced it.

## Budgets

Deliberately low while proving, so the cap is hit early and seen: $1.00 a day
for the whole steward and $0.20 per piece of work (`STEWARD_DAILY_USD`,
`STEWARD_REQUEST_USD`). A run that runs out pauses and waits for a top-up:
`POST /runs/{run_id}/budget` with `{"decision": "grant", "amount": ..., "approver": ...}`,
then `POST /runs/{run_id}/resume`.
