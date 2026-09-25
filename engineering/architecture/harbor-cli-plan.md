# `omnicoreagent harbor`: the whole Harbor CLI, with this runtime as the agent

Status: asked for by the maintainer 2026-09-25 ("a full cli feature … end to
end … i dont want a single bug"). Follows `harbor-adapter-plan.md` (H1–H3 done,
PR #271).

## What a wrapper around Harbor has to do

`omnicoreagent harbor <anything>` is Harbor's own CLI with four things done
around it, and every other subcommand — `view`, `job resume`, `trial`,
`analyze`, `check`, `dataset`, `download` — passed through untouched, so the
wrapper never falls behind Harbor:

1. **A default agent**: this runtime, unless `-a` or a config file names another.
2. **Credentials that never touch disk or argv**: they go into the child
   process's environment only.
3. **Model names routed**: a bare name gets the provider prefix LiteLLM routes on.
4. **Fail early and plainly**: no Docker is a sentence, not a traceback.

And what a user of *this* agent needs that Harbor cannot know:

| | Harbor alone | `omnicoreagent harbor` |
|---|---|---|
| default agent | none | `omnicoreagent.harbor.agent:OmniCoreAgentHarbor` |
| credentials | the provider's own variable | `LLM_API_KEY` (env or `./.env`), mapped to the provider's own variable in the child environment only |
| models | as given | a provider prefix for families whose provider is not in doubt |
| preflight | — | Docker, the `harbor` extra, a key for the model, and a runtime the container can install |
| ours only | — | `doctor`, `results` (below) |

## The adapter gaps this has to close first

Found by reading Harbor's `BaseAgent` beside our adapter, not by a trial —
each is a flag a user can pass today that **silently does nothing**:

- **The installed runtime is the release, not the one running the command.**
  PyPI's `omnicoreagent` has no `cli` module yet, so a trial without
  `install_spec` fails with `No module named omnicoreagent.cli` (it did, on the
  server, 2026-09-24). A harness must run the agent it was asked to run: by
  default the wrapper builds a wheel of the host's own runtime and the adapter
  uploads and installs that — same version on both sides, no network to GitHub.
- **MCP servers are dropped.** Harbor hands the agent `mcp_servers` from the task
  (`[[environment.mcp_servers]]`) and `--mcp-config`; the adapter never reads
  them.
- **Skills are dropped.** `--skill` puts a directory in the container and sets
  `skills_dir`; the adapter never reads it, and the runtime has skills.
- **Resume and load.** Checked, not a gap: Harbor itself refuses
  `--resume-trajectory` and `--load-trajectory` for an agent whose capabilities
  do not declare them, with a sentence naming the agent. A test holds the
  capabilities at false.
- **`--ae KEY=VALUE`** must be shown to reach the run's environment.

## Units

Each unit: failing test first, the full suite on the server the way CI runs it,
a commit, a push. A unit that touches the container is also proved by a real
trial on the server.

- **C1. No flag is silently ignored.** Done. Proved by tests against Harbor's
  own classes and by real trials on the server, each with no `install_spec`, so
  the container got a wheel of the runtime running the command:
  `receipts-subtotal`, `house-report-skill` (the agent found the house format
  with `read_skill_file`) and `rates-over-mcp` (both servers connected, stdio in
  the task container and streamable-HTTP in a sidecar; the agent called both
  tools). The first MCP trial found two more things: the task's server crashed
  on start (mcp 2.x renamed `FastMCP`) and **the run went on to reward 1.0 by
  reading the answer out of the server's source**, with nothing Harbor reads
  saying the server was dead. The task now keeps one answer in a container the
  agent cannot read, and the adapter puts each MCP server's state in the ATIF
  `extra` and a failed one in Harbor's metadata and log. The skills needed a
  runtime setting of their own, `skills_dir`, so they are found where Harbor
  put them rather than copied into the task's directory.
- **C2. `omnicoreagent harbor` passes through.** Done. The command becomes
  Harbor (`exec`), so its exit code, signals and terminal are Harbor's. Default
  agent for `run`, `exec`, `job start|init`, `trial start|init`; a config file
  names its own. `-m` is prefixed only for families whose provider is not in
  doubt — LiteLLM routes a bare gemini name to Vertex, which wants cloud
  credentials — and any other bare name is refused with a sentence.
  `LLM_API_KEY` goes to the provider under the first name in Harbor's own
  provider table, never over a key the user set, never to a provider whose
  credential is not a key. Proved on the server: a bare `-m gpt-5.6-terra` with
  only `LLM_API_KEY` set ran our agent to reward 1.0, and **no file of the job
  holds the key**.
- **C3. `omnicoreagent harbor doctor`.** Done. Python, Harbor, a Docker daemon
  that answers, a key for the model (present — never printed), and what the
  container installs; `--container` installs the agent into a throwaway task
  container with `--install-only` (75 s on the server).
- **C4. `omnicoreagent harbor results <job-dir>`.** Done. Read with Harbor's own
  `TrialResult`: per trial the outcome (passed, partial, failed, errored,
  running), reward, our status, cost, tokens, steps, the line of an exception
  that says what went wrong rather than the command that raised it, MCP servers
  the run could not use, and whether the run wrote a result at all; totals with
  an error counted as not passed — Harbor's own exit code is 0 for a job whose
  every trial errored. Tested on three real trials copied from the server.
- **C5. Dropped.** Harbor's own `harbor init --task` writes a task skeleton;
  `omnicoreagent harbor init` passes through to it. A second one would drift.
- **C6. A task set, through the command.** Done, on the server, every job
  through `omnicoreagent harbor run` with only `LLM_API_KEY` set
  (`engineering/validation/harbor_failures`, each task passed by the oracle
  first). The three passing tasks passed as one concurrent job. An unknowable
  answer scored 0 and the agent said it could not find it rather than invent
  one. A command that never finishes timed out and the agent said so. A run
  with a short deadline ended itself as `timeout` and left its evidence. A
  step limit ends as `error (max_steps)`, now reported as such. An agent-phase
  allowlist passes with `--allow-agent-host` for the model's host, and fails
  saying it cannot reach it without. The set found findings 53 and 54.
- **C7. The user's page.** Done: `docs/how-to-guides/harbor.mdx`, held by
  `tests/test_docs_claims.py` like the others.

## Operating rule on the server

Other jobs run on the same machine. A process of ours is
stopped only by the PID recorded when it started, after `ps` shows the command
line is ours — never by a name or pattern.

## Not in this plan

A key store of our own: the runtime's convention is `LLM_API_KEY`,
and a second place for a secret is a second place to leak it. Harbor Hub
(`--launch`, `upload`) passes through but is not tested here.
