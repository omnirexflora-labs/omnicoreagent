# AGENTS.md — a map of OmniCoreAgent for coding agents

OmniCoreAgent is an open Python runtime for AI agents: the loop, tools, memory
and workspace, plus the policy, sandbox, durable run record, budgets and the
evidence of every run. This file tells an agent where things are and how to
work here. People are welcome too; the [README](./README.md) is the tour.

## Answer a question from the right place

| You want to know | Look in |
|---|---|
| How to use a feature | [`docs/`](./docs) — the published docs; start at `docs/index.mdx`, the nav is `docs.json` |
| What an option does, exactly | the source it is defined in (below); every setting has a comment |
| Why something is built the way it is | [`engineering/architecture/`](./engineering/architecture) — one plan per piece of work, with the decisions |
| What broke in real use and how it was fixed | [`engineering/validation/production-proving.md`](./engineering/validation/production-proving.md) |
| A complete, runnable example | [`cookbook/`](./cookbook) and [`apps/steward/`](./apps/steward) |

## Where the code is

Everything is under `src/omnicoreagent/`.

| Package | What it holds | Start at |
|---|---|---|
| `core/runtime/` | `OmniCoreAgent`, its configuration (`AgentConfig` and friends) | `omnicore_agent.py`, `config.py` |
| `core/agents/` | the loop: steps, model calls, tool calls, the final answer | `base.py`, `llm_step.py`, `native_tools.py` |
| `core/tools/` | local tools, the `execute` tool, code mode (Monty), result offloading | `local_tools_registry.py`, `code_mode.py` |
| `core/telemetry/` | traces, the trajectory builder, stores, exporters, redaction | `recorder.py`, `trajectory.py` |
| `core/memory_store/`, `core/workspace/` | session memory backends; the agent's files | |
| `core/guardrails/`, `core/skills/`, `core/summarizer/` | prompt-injection guard; skills; context summaries | |
| `core/credentials.py` | the runtime's own credentials, kept from the model and every record | |
| `governance/` | policies (allow, ask, deny), approvals, budgets, capability descriptors | `defaults.py`, `capabilities.py` |
| `sandbox/` | sandbox providers: Docker, E2B, Modal, Daytona, Vercel, HTTP, `local` | `base.py` |
| `mcp_clients_connection/` | MCP client: transports, OAuth, reconnects | `client.py` |
| `background/` | scheduled and manual background runs, task stores | |
| `serve/` | OmniServe: the REST/SSE server and its CLI (`omniserve`) | |
| `cli/` | the `omnicoreagent` CLI: `run` (headless) and `harbor` | `__init__.py`, `headless.py`, `harbor.py` |
| `harbor/` | running this agent on Harbor / Terminal-Bench | `agent.py`, `trial.py` |

## Run the tests the way CI does

```bash
uv sync --all-extras --all-groups --locked
uv run --no-sync ruff check
uv run --no-sync pytest tests -m "not requires_api_key and not requires_network and not OpenAIIntegration"
```

Tests live in `tests/`, named for what they hold (`test_code_mode.py`,
`test_harbor_trial.py`, …). A few are guards on the repository itself, and will
fail if you break these rules:

- `test_docs_code.py` — every Python block in the README and docs parses and
  imports only what exists; every link, extra and CLI command named resolves.
- `test_docs_claims.py` — user-facing docs name only `LLM_API_KEY` as the
  model key variable.
- `test_complete_mediation.py` — every place the package starts a process or
  opens a socket is listed and justified.
- `test_extras.py` — `pip install "omnicoreagent[all]"` includes every extra
  but `harbor`.

## How work is done here

- **A plan first** for anything more than a fix: `engineering/architecture/<topic>-plan.md`,
  with the units of work and the decisions.
- **A failing test first**, then the change; one unit per commit.
- **Comments say why**, in full sentences, and name the incident a rule came
  from when there was one. Match the surrounding code.
- **Docs change with the code** they describe; examples are real and are run.
- **Secrets never in the repository**: the only model key variable is
  `LLM_API_KEY`, read from the environment or a `.env` that is not committed.
