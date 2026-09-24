# One trial, end to end

What an evaluation harness does to this runtime, done for real: a task
directory with failing tests, an agent whose commands run **on this machine** in
that directory, and a verifier that checks afterwards the way a harness would.

It exists because the local sandbox provider and the headless command were both
tested thoroughly in isolation and neither had been run the way Harbor will run
them. The first real attempt failed — and not in either of them (see *What it
has caught*).

```bash
# in a container, as the user the harness runs as
pip install pytest                      # the task's verifier needs it
export LLM_API_KEY=...                  # a real model: this is not a rehearsal
python trial.py --drive agent           # through agent.run()
python trial.py --drive cli             # through `omnicoreagent run` (Harbor's path)
```

It prints a JSON report and exits non-zero if any check fails, so it can run
unattended.

## What it checks

Not only "the tests pass" — that would also be true of an agent that deleted
them:

| Check | Why |
|---|---|
| the tests failed before | the task is really broken to begin with |
| the tests pass after | the agent did the work |
| the tests were not changed | by digest: it did not get there by editing the test |
| nothing was left in the task directory | a harness verifies those files |
| the run finished | a terminal state, not a pause nobody answered |
| every command ran on the host surface | the trace says `execution_surface: host` |
| the agent ran commands | it did not answer from the prompt alone |
| the workspace was not copied in or out | on this machine there is nothing to copy |

The last three are read from the trace the run wrote, not from what the agent
said about itself.

## What a harness has to get right

- **Point the workspace outside the task.** It defaults to `./workspace`, and
  the command's working directory is the task, so the agent's workspace would
  appear among the files the verifier checks. `agent.py` sets
  `workspace_config.workspace_dir` elsewhere on purpose.
- **Allow host commands explicitly.** Every built-in profile denies or asks
  about `process.exec` on the `host` surface until a rule allows it; `agent.py`
  shows the rule.
- **Name the environment a task needs** (`environment_passthrough`) rather than
  inheriting all of it, so the agent's provider key does not reach the model's
  commands.
- **Invoke the command as a module** (`python -m omnicoreagent.cli run ...`):
  a console script is not always on PATH.

## What it has caught

- A retry the runtime handled becoming a failed run: with `temperature` set
  against a reasoning model, the provider refuses the parameter, the runtime
  drops it and retries — and the *recording* of that retry raised
  `KeyError: 'message'`, ending the run as `provider_error` with nothing in the
  trace to say why. The trial still sets `temperature`, so it keeps exercising
  that path.
- `python -m omnicoreagent.cli` not working at all: the package had no
  `__main__.py`.
- The workspace bridge copying a task's own files into the agent's workspace as
  if a command had made them, and the workspace over the task's directory.
