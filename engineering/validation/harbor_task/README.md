# Harbor trials of this adapter

Three tasks of our own, each small enough to read in a minute and each able to
be solved only through the thing it proves:

| Task | Proves | Cannot be passed by |
|---|---|---|
| `receipts-subtotal` | commands on the host surface, in the task's directory | editing the tests (checked by digest) |
| `house-report-skill` | skills Harbor provides (`environment.skills_dir`) | guessing: the format is only in the skill |
| `rates-over-mcp` | MCP over stdio and streamable HTTP | reading a source file: one answer is in a container the agent cannot read |

Each passes with Harbor's `oracle` agent first, which is how a wrong expected
value in `house-report-skill` was caught before any model saw it.

```bash
omnicoreagent harbor run -p engineering/validation/harbor_task/rates-over-mcp -m gpt-5.6-terra
omnicoreagent harbor results jobs
```

## The first trial

A Terminal-Bench task of our own, small enough to read in a minute and strict
enough to fail an agent that cheats: `receipts-subtotal` has two arithmetic bugs
and a verifier that checks the tests were not edited, by digest.

```bash
harbor run -p engineering/validation/harbor_task/receipts-subtotal \
  -a omnicoreagent.harbor.agent:OmniCoreAgentHarbor \
  -m openai/gpt-5.6-terra -n 1 \
  --agent-kwarg run_timeout=780
```

`--agent-kwarg install_spec=git+https://github.com/omnirexflora-labs/omnicoreagent@<branch>`
installs a branch instead of the release, which is how a change to the runtime
is tried before it ships.

## What a trial produced

On the server, 2026-09-24, `gpt-5.6-terra`, one trial, 1m21s:

| What Harbor recorded | |
|---|---|
| reward | **1.0** (tests pass, tests unchanged) |
| tokens | 14,212 in · 764 out · 12,441 of the input served from cache |
| cost | $0.0161, and the same figure under `model_usage["openai/gpt-5.6-terra"]` |
| metadata | our status, exit code and run id |
| `agent/trajectory.json` | ATIF-v1.8: 7 steps — the instruction, then six model calls |

The trajectory is what makes the trial reviewable rather than a number. Each
agent step carries what the model said, the arguments it passed, what came back,
and what the call cost; each observation of a command says how it ran:

```json
{"outcome": "error", "exit_code": 127, "sandbox_provider": "local",
 "matched_rule_ids": ["allow_host_commands"]}
```

That is the evidence that a container is the boundary and policy still governed
every command — `pytest` was missing from the image, the agent found that out
the hard way twice, ran the assertions with `python3` instead, and fixed the
code.

## What a first green trial hid

It scored 1.0 the first time too, and the record of it said nothing: no steps,
no cost, no model usage. See finding 51 in
`engineering/validation/production-proving.md` — the reason a converter between
two formats is now tested against a captured document, and the file we write is
handed to Harbor's own validator in a test.
