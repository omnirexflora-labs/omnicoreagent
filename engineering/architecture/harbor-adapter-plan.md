# The Harbor adapter

Status: agreed with the maintainer 2026-09-24 ("we can then put our effort on
the harbor adapter in full"). The runtime pieces it needs are merged: host
execution (#266), the headless command (#265), the retry fix that a real trial
found (#268), and the trial itself as evidence (#269).

## What Harbor is, here

Terminal-Bench's execution layer, version 0.23.0 on the steward's server. A task is a container; the agent is
installed **into that container** and solves the task by running commands in
it; a verifier then checks the container. That is why an external sandbox is
the wrong shape and the `local` provider exists.

An agent is a class deriving from `harbor.agents.installed.base.BaseInstalledAgent`:

| What Harbor calls | What it is for |
|---|---|
| `name()`, `version()` | how the agent is identified in results |
| `install(environment)` | put the agent in the task container |
| `run(instruction, environment, context)` | solve the task; fill `context` |
| `populate_context_post_run(context)` | read logs after the trial syncs them |
| `capabilities` | `atif`, `resume`, `handoff`, … |
| `MODEL_CONNECTION` | how the provider's credentials reach the container |
| `options_model` | the agent's own `--flags` on `harbor run` |

Harbor loads an agent **by import path** (`harbor.agents.factory` →
`import_class`), so the adapter does not have to live in Harbor's own package.

## Decisions

- **It lives in this repository**, at `src/omnicoreagent/harbor/`, and is
  referenced as `omnicoreagent.harbor:OmniCoreAgentHarbor`. It is versioned with
  the runtime it drives, and a change to the CLI and its adapter land together.
  Nothing in the runtime imports it, so no user pays for it; `omnicoreagent[harbor]`
  installs what developing it needs.
- **It drives the headless command**, not the Python API:
  `python -m omnicoreagent.cli run --agent … -i … -o /logs/agent`. One process,
  one instruction, files on disk, an exit code — which is what a harness wants,
  and what #265 was for. Running it as a module is deliberate: a console script
  is not always on PATH in a task container.
- **The sandbox provider is `local`**, with the task's own directory as the
  working directory. The four things a harness must get right are already known
  from the trial and will be set by the adapter rather than left to a user:
  the agent's workspace goes outside the task directory, host commands are
  allowed by an explicit rule, the environment is passed through by name rather
  than inherited whole, and the command is invoked as a module.
- **Credentials come from Harbor**, through `MODEL_CONNECTION` passthrough: it
  puts the provider's key in the container's environment, and the agent file
  reads it. The adapter never writes a key into a file or a command line.
- **ATIF**: Harbor's own trajectory format (`harbor.models.trajectories.Trajectory`:
  `agent`, `steps`, `final_metrics`, …). Our `trajectory.json` is close in
  shape. The adapter converts it, declares `capabilities.atif = True`, and
  writes `trajectory.json` where Harbor expects it, so a trial of this agent can
  be compared with a trial of any other.

## The trajectory we write

ATIF is a format for comparing agents, so the mapping is decided here rather
than left to whoever reads it:

| ATIF | What we put there |
|---|---|
| step 1 | what the agent was asked (`source: user`) |
| one step per model call | `source: agent`, `message` = what the model itself said |
| `tool_calls` | the calls of the runtime step that asked for them |
| `observation.results[*].extra` | outcome, exit code, the sandbox that ran it, the rule that allowed it |
| `metrics` | the call's own tokens and cost; `extra` adds finish reason, latency, provider response id, the provider's own model name |
| `model_name` | the model **as the harness names it**, so the usage Harbor computes from the file is keyed like its other results |
| `final_metrics` | the run's totals, including the cost `result.json` does not carry |
| `extra` | run id, status, trace ids, and `evidence_status` when the recording was not complete |

Two rules behind that table, both learned the hard way (finding 51):

- A step's message is what the **model** said, never the last thing sent to it.
  A trajectory that puts the prompt in the agent's mouth is worse than one with
  no message at all, because whoever fine-tunes on it learns the mistake.
- The steps come from `segments[*].trajectory.steps`: a run has a trace per
  attempt. The converter is tested against a captured trajectory of a real
  trial, and the file it writes is read back by Harbor's own validator in a
  test, because to the harness an invalid trajectory and a missing one look the
  same.

## Units

- **H1. The adapter runs a task.** Done. `install` and `run`, the agent file written
  into the container, the model connection mapped to our `model_config`, the
  policy that allows host commands, and `context` filled with what the run
  spent. Proved by a real Terminal-Bench task on the server rather than by a
  mock: on 2026-09-24 the adapter installed itself into a fresh Ubuntu 24.04
  task container, ran the task, and the verifier gave it reward 1.0, with
  Harbor's context carrying the tokens and the run id.
- **H2. The trajectory Harbor can read.** Done, and proved twice over: the
  document validates against Harbor's own model in a test, and a real trial's
  file gave Harbor the per-model usage it computes from it (finding 51 is the
  first attempt, which did not).
- **H3. The agent's own options.** Done. Model, approval mode, budget mode,
  timeouts, capture and the install spec, as `harbor run --agent-kwarg`s through
  `InstalledAgentOptions`, each one documented and defaulted the way a trial
  should behave (approvals `deny`, budgets `stop`, capture `full`).
- **H4. A trial, and then a task set.** The trial is done and recorded in
  `engineering/validation/harbor_task/README.md`: reward 1.0, seven ATIF steps,
  $0.0161. The task set is next, to see failures one task cannot show — a task
  the agent should fail, one where a command times out, and one large enough to
  reach the step limit.

## What is not in this plan

Resume, handoff, and loading a trajectory as a session (`capabilities.resume`,
`handoff`, `load_*`). Our runtime can resume a run, so they are reachable, but
a first adapter that runs a task and reports honestly is worth more than a
broad one that does each thing halfway.
