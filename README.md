<p align="center">
  <img src="assets/IMG_5292.jpeg" alt="OmniCoreAgent" width="200"/>
</p>

<h1 align="center">OmniCoreAgent</h1>

<p align="center">
  <strong>The open Python runtime for AI agents that have to hold up in production.</strong><br />
  <em>Governed, sandboxed, durable, budgeted, and recorded — every run, end to end.</em>
</p>

<p align="center">
  <a href="https://pepy.tech/projects/omnicoreagent"><img src="https://static.pepy.tech/badge/omnicoreagent" alt="PyPI Downloads"></a>
  <a href="https://badge.fury.io/py/omnicoreagent"><img src="https://badge.fury.io/py/omnicoreagent.svg" alt="PyPI version"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
</p>

<p align="center">
  <a href="https://docs-omnicoreagent.omnirexfloralabs.com/docs">Docs</a> ·
  <a href="https://docs-omnicoreagent.omnirexfloralabs.com/docs/getting-started/quickstart">Quickstart</a> ·
  <a href="./cookbook">Cookbook</a> ·
  <a href="./engineering/validation/production-proving.md">Proof</a> ·
  <a href="https://docs-omnicoreagent.omnirexfloralabs.com/docs/getting-started/use-docs-with-ai-tools">Ask AI</a>
</p>

---

A model is not an agent. The runtime around it is what makes it usable in an
application: the loop, the tools, memory, the files it works on, and — once
the agent can do real things — the policy that says what it may do, the
sandbox its code runs in, the record that survives a crash, the budget that
stops it spending, and the trace a person can read afterwards.

OmniCoreAgent is that runtime. One agent object, from a first script to a
governed background worker on a server.

## Install

```bash
pip install omnicoreagent
export LLM_API_KEY=your_api_key      # the key for the provider in model_config
```

## Quick start

```python
import asyncio
from omnicoreagent import OmniCoreAgent, ToolRegistry

tools = ToolRegistry()

@tools.register_tool("lookup_order")
def lookup_order(order_id: str) -> dict:
    """Look an order up in the application's own store."""
    return {"order_id": order_id, "status": "shipped"}

agent = OmniCoreAgent(
    name="support",
    system_instruction="You answer questions about orders, using the tools.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    local_tools=tools,
)

async def main():
    result = await agent.run("Where is order 1042?", session_id="customer-7")
    print(result["response"])
    await agent.cleanup()

asyncio.run(main())
```

That is the whole loop: the model calls tools (independent calls run in one
batch), results come back as structured observations, the session remembers,
files land in a workspace, the injection guardrail watches, and the run is
recorded. Everything below is opt-in.

Works with OpenAI, Anthropic, Gemini, Groq, DeepSeek, Mistral, Azure,
OpenRouter and Ollama through one `model_config`
([models](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/models)).

## What production needs, and where it is

| Need | What the runtime does | Read |
|---|---|---|
| **Tools** | Your Python functions, and MCP servers (stdio, SSE, streamable HTTP, OAuth) through one catalog; parallel batches; loop detection by call signature; tool retrieval for large tool sets. | [Local tools](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/local-tools), [MCP](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/mcp) |
| **Governance** | A policy — allow, ask, deny — over every capability the agent has: each tool, each MCP server, the sandbox, the network, delegation, background runs. `ask` pauses the run for a person. Hashed, so it cannot widen at runtime. | [Security model](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/security-model) |
| **Execution** | An `execute` tool whose commands run in a sandbox — Docker, E2B, Modal, Daytona, Vercel, or your own — with no network unless the policy allows it, never your credentials, and the workspace bridged in and out. A sandbox that dies is reported and replaced. | [Execution](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/execution), [Providers](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/sandbox-providers) |
| **Durable runs** | Every run has a record: its step, its tool calls, its approvals. A run pauses for an approval or a top-up and resumes where it stopped; a run whose process died continues from its checkpoint; a call that was interrupted is never silently repeated. | [Durable runs](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/durable-runs) |
| **Budgets** | Limits in dollars, tokens, calls, sandbox seconds, per request, session, agent, or application, per day or month; a model call is held at its worst case before it is made; a run that runs out waits for a person. | [Durable runs](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/durable-runs) |
| **Memory and context** | Session memory in memory, Redis, Postgres/SQL, or MongoDB; context managed before each model call; large tool outputs offloaded to workspace files. | [Memory](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/memory), [Context](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/context-engineering) |
| **Sub-agents** | Workers spawned by the lead under the same policy and budgets, each with its own trace linked to the parent's. | [Sub-agents](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/sub-agents) |
| **Background work** | Scheduled and manual tasks with a durable task store (Redis, MongoDB, SQL), leases, retries, recovery after a restart, one run per task at a time. | [Background agents](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/background-agents) |
| **Telemetry** | One trace per run, readable end to end — every model call, tool call, sandbox command, approval and budget decision — complete by default (`capture: "default"` leaves model prompts out), personal data redacted; exported to OTLP, LangSmith, Opik or JSONL. | [Observability](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/observability) |
| **Serving** | `omniserve run --agent agent.py`: REST and SSE for runs, approvals, budgets, background tasks, traces; auth, rate limits, metrics; your own pages beside the API. | [OmniServe](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/omniserve) |

A governed agent, in one config:

```python
agent = OmniCoreAgent(
    name="steward",
    system_instruction="...",
    model_config={"provider": "openai", "model": "gpt-5.6-terra"},
    mcp_tools=[{"name": "github", "transport_type": "streamable_http", "url": "https://api.githubcopilot.com/mcp/",
                "headers": {"Authorization": "Bearer ..."}}],
    agent_config={
        "governance_config": {
            "enabled": True,
            "policy": {"name": "steward", "mode": "strict", "rules": {
                "allow": [{"rule_id": "read", "capability": "tool.mcp.call",
                           "target": {"mcp_server": "github", "tool_name": "get_file_contents"}},
                          {"rule_id": "sandbox", "capability": "sandbox.execute"},
                          {"rule_id": "commands", "capability": "process.exec",
                           "constraints": {"sandbox_required": True}}],
                "ask":   [{"rule_id": "pr", "capability": "tool.mcp.call",
                           "target": {"mcp_server": "github", "tool_name": "create_pull_request"}}],
                "deny":  [{"rule_id": "merge", "capability": "tool.mcp.call",
                           "target": {"mcp_server": "github", "tool_name": "merge_pull_request"}}],
            }},
            "budgets": {"application_id": "steward",
                        "application": [{"meter": "model_cost_usd", "limit": 5.0, "window": "day"}],
                        "request": [{"meter": "model_cost_usd", "limit": 1.0}]},
            "sandbox_config": {"provider": "e2b"},
            "sandbox_manifest": {"network_policy": {"default": "allow"}},
        },
    },
    telemetry_config={"capture": "full"},
)
```

## Proof, not a feature list

The runtime is proved by running a real, difficult application on it in
production and hurting it from the outside: a **repository steward** for this
repository — a background agent on a server that reproduces failing tests in
a sandbox, fixes them behind a person's approval, opens pull requests that
link their own trace, triages its own failures into work, and runs on a
schedule for a week. Its code is [`apps/steward/`](./apps/steward); every
scenario asserts what a person would check, against the real server, model
and repository. What it broke and what was fixed — twenty-odd runtime defects
in two days, each with a test — is the
[production proving write-up](./engineering/validation/production-proving.md).

## Install only what you use

```bash
pip install "omnicoreagent[serve]"        # OmniServe REST/SSE
pip install "omnicoreagent[docker]"       # Docker sandboxes; e2b, modal, daytona, vercel likewise
pip install "omnicoreagent[redis]"        # Redis memory and task store; postgres, mongodb likewise
pip install "omnicoreagent[s3]"           # S3 / R2 workspace storage
pip install "omnicoreagent[tokenizer]"    # token-exact context and budget estimates
pip install "omnicoreagent[otel]"         # OTLP export; langsmith, opik likewise
pip install "omnicoreagent[codemode]"     # code mode, in Monty
pip install "omnicoreagent[all]"          # every extra above; Harbor is its own: [harbor]
```

## Cookbook

[Getting started](./cookbook/getting_started) · [Real applications](./cookbook/real_applications) ·
[Background agents](./cookbook/background_agents) · [OmniServe](./cookbook/omniserve) ·
[Production](./cookbook/production)

## Development

```bash
git clone https://github.com/omnirexflora-labs/omnicoreagent.git && cd omnicoreagent
uv venv && source .venv/bin/activate
uv sync --all-extras --all-groups --locked
pytest tests/
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Design notes and plans live in
[`engineering/`](./engineering).

## License and author

MIT — see [LICENSE](LICENSE). Built by [Abiola Adeshina](https://github.com/Abiorh001)
([@abiorhmangana](https://x.com/abiorhmangana)), with
[OmniMemory](https://github.com/omnirexflora-labs/omnimemory) and
[OmniDaemon](https://github.com/omnirexflora-labs/OmniDaemon) in the same family.
Built on [LiteLLM](https://github.com/BerriAI/litellm), [FastAPI](https://fastapi.tiangolo.com/) and [Pydantic](https://docs.pydantic.dev/).
