<p align="center">
  <img src="assets/IMG_5292.jpeg" alt="OmniCoreAgent Logo" width="250"/>
</p>

<h1 align="center">OmniCoreAgent</h1>

<p align="center">
  <strong>Open Python Agent Harness and Runtime for Production AI Agents</strong><br />
  <em>Everything around the model: parallel tool batches, structured observations, loop detection, memory, workspace files, MCP tools, subagents, background tasks, and serving.</em>
</p>

<p align="center">
  <a href="https://pepy.tech/projects/omnicoreagent"><img src="https://static.pepy.tech/badge/omnicoreagent" alt="PyPI Downloads"></a>
  <a href="https://badge.fury.io/py/omnicoreagent"><img src="https://badge.fury.io/py/omnicoreagent.svg" alt="PyPI version"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> -
  <a href="#choose-your-path">Choose Your Path</a> -
  <a href="#what-makes-it-different">What Makes It Different</a> -
  <a href="#install-only-what-you-need">Install</a> -
  <a href="./cookbook">Cookbook</a> -
  <a href="#features">Features</a> -
  <a href="https://docs-omnicoreagent.omnirexfloralabs.com/docs">Docs</a>
</p>

---

## Quick Start

```bash
pip install omnicoreagent
```

```bash
export LLM_API_KEY=your_api_key
```

```python
import asyncio
from omnicoreagent import OmniCoreAgent

agent = OmniCoreAgent(
    name="assistant",
    system_instruction="You are a helpful assistant.",
    model_config={"provider": "openai", "model": "gpt-4o"},
)

async def main():
    result = await agent.run("Research the top 3 open-source agent runtimes and summarize them.")
    print(result["response"])
    await agent.cleanup()

asyncio.run(main())
```

That is the smallest path: one agent, one model, the harness loop, session memory,
guardrails, workspace files, error handling, and metrics around each run.

Context management, tool output offloading, BM25 tool retrieval, subagents, skills,
cloud workspace storage, and production backends are opt-in so a small agent stays
small.

> Ready to go deeper? The [Cookbook](./cookbook) has progressive examples from
> hello world to production deployments.

---

## Choose Your Path

| Goal | Start Here |
|------|------------|
| Build your first agent | [Quick Start](#quick-start) |
| Add Python tools | [Local tools cookbook](./cookbook/getting_started/agent_with_local_tools.py) |
| Connect MCP server tools | [MCP tools cookbook](./cookbook/getting_started/agent_with_mcp_tools.py) |
| Manage memory and context | [Getting started cookbook](./cookbook/getting_started) |
| Save files, artifacts, and large tool results | [Tool offload cookbook](./cookbook/getting_started/agent_with_tool_offload.py) |
| Build multi-step workflows | [Workflows cookbook](./cookbook/workflows) |
| Serve an agent over HTTP/SSE | [OmniServe cookbook](./cookbook/omniserve) |
| Understand the runtime internals | [Implementation Map](#implementation-map) |

---

## What Makes It Different

Most agent libraries stop at "LLM plus tool loop." OmniCoreAgent is built around
the runtime problems that show up after that: slow sequential tools, noisy
observations, stuck loops, context exhaustion, MCP server tools, durable
workspace files, and runtime serving.

### 1. Agents call tools in batches instead of forced sequences

The usual tool loop looks like this:

```text
LLM -> call tool A -> wait -> result -> LLM -> call tool B -> wait -> result
```

OmniCoreAgent lets the model request independent tools together:

```text
LLM -> [tool A + tool B + tool C in parallel] -> one structured observation -> LLM
```

The model gets one complete view of the batch before it reasons again. A failed
tool is represented beside the successful tools instead of silently collapsing the
whole step.

Native function calling alone is not the runtime. OmniCoreAgent uses its own
tool-call contract, parser, resolver, parallel runner, and result formatter so
the harness controls the full execution path.

### 2. Tool results become structured observations

Raw tool output is often too noisy for the next reasoning step. Large payloads,
errors, irrelevant fields, and prompt-injection content can all distort the loop.

OmniCoreAgent routes tool results through an observation pipeline:

```text
tool output -> parse -> format -> guardrail check -> offload when configured -> observation -> model
```

The model receives the signal it needs to continue the task, not an unbounded dump
of every byte returned by a tool. When tool offloading is enabled, large outputs
are written into the active workspace and the model receives a readable preview
plus a path it can use later.

### 3. Loop detection uses signatures beyond step counts

`max_steps` is still useful, but it is a blunt instrument. It stops an agent that
is making progress just as quickly as one that is stuck.

OmniCoreAgent tracks SHA256-backed tool-call signatures across the loop. Each
signature is based on the tool name, input, and output for the call. The runtime
detects:

- **Consecutive loops**: the same tool call returns the same result repeatedly.
- **Pattern loops**: the same tool repeats a small interaction pattern.

When the harness stops a loop, the agent gets a reason. That makes debugging the
agent behavior much easier than "max iterations reached."

### 4. The harness is already assembled

OmniCoreAgent ships as a working agent harness, not a bag of disconnected pieces:

```text
model + prompt + loop + tools + memory + context + workspace + guardrails + events
```

Keep it small for simple agents, then turn on the heavier harness pieces when the
workload needs them: MCP tools, BM25 tool retrieval, dynamic subagents, skills,
cloud workspace storage, Redis/Postgres/MongoDB memory, event streams, and
OmniServe.

### 5. Context is managed before the model call

When context management is enabled, OmniCoreAgent checks the active message
history before every LLM request. If the configured threshold is crossed, the
harness automatically applies the selected strategy before calling the model:

```text
messages -> threshold check -> truncate or summarize+truncate -> LLM
```

The system prompt is preserved, recent messages are preserved, and older middle
history is either summarized or removed depending on configuration. If you set
the budget below your model's real context window, the harness acts before the
provider rejects the request.

---

## See It In Action

```python
import asyncio
from omnicoreagent import MemoryRouter, OmniCoreAgent, ToolRegistry

tools = ToolRegistry()

@tools.register_tool("search_web")
def search_web(query: str) -> dict:
    """Search the web for information."""
    return {"results": [f"Result for: {query}"]}

@tools.register_tool("read_file")
def read_file(path: str) -> dict:
    """Read a local project file."""
    return {"path": path, "content": f"Contents of {path}"}

agent = OmniCoreAgent(
    name="research-agent",
    system_instruction=(
        "You are a research assistant. Use tools in parallel when the calls are "
        "independent and you can reason over the results together."
    ),
    model_config={"provider": "openai", "model": "gpt-4o"},
    local_tools=tools,
    memory_router=MemoryRouter("in_memory"),
    agent_config={
        "max_steps": 20,
        "context_management": {"enabled": True},
        "tool_offload": {"enabled": True},
        "enable_subagents": True,
        "enable_advanced_tool_use": True,
    },
)

async def main():
    result = await agent.run(
        "Search for recent AI agent papers and read notes.md. Do both at once "
        "if neither depends on the other."
    )
    print(result["response"])
    await agent.cleanup()

asyncio.run(main())
```

The runtime accepts `search_web` and `read_file` in the same batch, returns both
results together, and continues from one structured observation.

---

## Install Only What You Need

```bash
pip install omnicoreagent                    # Core runtime
pip install "omnicoreagent[redis]"           # Redis memory + event streams
pip install "omnicoreagent[postgres]"        # PostgreSQL / SQL memory
pip install "omnicoreagent[mongodb]"         # MongoDB memory
pip install "omnicoreagent[s3]"              # S3 / R2 workspace storage
pip install "omnicoreagent[serve]"           # OmniServe REST/SSE API
pip install "omnicoreagent[all]"             # Everything
```

Production backends are installable extras. Install only what the agent actually
uses.

---

## Features

| Feature | What It Does |
|---------|--------------|
| **Parallel Batch Tool Execution** | Executes independent tool calls concurrently and returns one combined observation to the model. |
| **Structured Observation Pipeline** | Parses, formats, guardrail-checks, and offloads tool results when configured before the model sees them. |
| **Signature-Based Loop Detection** | Detects repeated SHA256-backed tool-call signatures and repeated tool interaction patterns beyond step-count exhaustion. |
| **MCP Native Tools** | Connects MCP servers over stdio, SSE, and Streamable HTTP, including OAuth-capable remote servers. |
| **Local Tool Registry** | Registers Python functions as tools with inferred schemas and async/sync execution support. |
| **Multi-Tier Memory** | Uses in-memory, Redis, MongoDB, or SQL-backed session history through the memory router. |
| **Runtime Backend Switching** | Switches memory and event backends at runtime when configured. |
| **Workspace Files** | Gives agents a local, S3, or R2-backed file workspace for notes, scratchpads, artifacts, and tool offloads. |
| **Context Engineering** | Checks context before each model call and automatically truncates or summarizes when the configured budget threshold is crossed. |
| **Tool Output Offloading** | Writes large tool results to workspace files and gives the model a preview plus a file reference. |
| **Dynamic Subagents** | Lets the main agent spawn focused workers with isolated context and shared workspace output. |
| **Agent Skills** | Loads packaged capabilities implemented with Python, Bash, or Node.js. |
| **BM25 Tool Retrieval** | Selects relevant tools from large tool sets so the prompt stays focused. |
| **Guardrails** | Adds prompt-injection screening inside the observation path with configurable behavior. |
| **Event System** | Emits structured runtime events for agent runs, tool calls, and streaming integrations. |
| **Workflow Orchestration** | Provides sequential, parallel, and router agents for multi-step application workflows. |
| **Background Agents** | Supports scheduled autonomous tasks for interval-based workloads. |
| **Universal Models** | Routes through LiteLLM to OpenAI, Anthropic, Gemini, Groq, Ollama, DeepSeek, Mistral, OpenRouter, Azure, and Cencori. |
| **OmniServe** | Turns an agent into a REST/SSE service with lifecycle management and metrics. |

---

## Implementation Map

OmniCoreAgent's capabilities are backed by concrete runtime modules:

| Capability | Where It Lives |
|-------|----------------|
| Parallel tool batches | `core/tools/tool_batch_runner.py` |
| XML tool-call contract | `core/agents/xml_parser.py` |
| Structured observations | `core/tools/tool_observation.py` |
| Tool output offloading | `core/workspace/artifacts.py` |
| Automatic context control | `core/agents/llm_step.py`, `core/context_manager.py` |
| Workspace files | `core/workspace/tools.py`, `core/workspace/storage.py` |
| Dynamic subagents | `core/subagents.py` |
| Loop detection | `core/agents/loop_detection.py` |
| MCP server tools | `mcp_clients_connection/client.py` |
| OmniServe | `serve/` |

See the [Agent Harness docs](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/agent-harness)
for the full implementation map.

---

## Cookbook

All examples live in the **[Cookbook](./cookbook)** and are organized by use case.

| Category | What You'll Build |
|----------|-------------------|
| [Getting Started](./cookbook/getting_started) | First agent, tools, memory, events |
| [Workflows](./cookbook/workflows) | Sequential, Parallel, Router agents |
| [Background Agents](./cookbook/background_agents) | Scheduled autonomous tasks |
| [Production](./cookbook/production) | Guardrails, serving, and production patterns |

---

## Configuration

### Environment Variables

For the first run, most hosted model providers only need `LLM_API_KEY`.
OmniCoreAgent defaults memory and events to in-memory storage, workspace files to
local disk, and optional production integrations stay off until you configure
them.

```bash
export LLM_API_KEY=your_api_key
```

Add backend-specific variables only when you opt into Redis, MongoDB, SQL
database storage, S3, R2, or OmniServe deployment settings.

### Full Harness Config Example

The defaults keep the first agent small: workspace files and guardrails are on,
conversation memory is in-memory, and advanced harness pieces stay off until
you enable them. This example shows the production-style switches together.

```python
agent_config = {
    "max_steps": 15,
    "tool_call_timeout": 30,
    "request_limit": 0,                  # 0 = unlimited
    "total_tokens_limit": 0,             # 0 = unlimited
    "memory_config": {
        "mode": "sliding_window",
        "value": 10000,
        "summary": {"enabled": False},
    },
    "enable_workspace_files": True,      # Default on
    "guardrail_mode": "full",            # Default
    "context_management": {"enabled": True},  # Default off
    "tool_offload": {"enabled": True},        # Default off
    "enable_advanced_tool_use": True,         # Default off
    "enable_subagents": True,                 # Default off
    "enable_agent_skills": True,              # Default off
}
```

When `enable_subagents` is true, workspace files are enabled automatically so
subagents write outputs, notes, todos, and artifacts into the active
workspace.

> Full reference: [Configuration Guide](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/configuration)

---

## Development

```bash
git clone https://github.com/omnirexflora-labs/omnicoreagent.git
cd omnicoreagent

uv venv && source .venv/bin/activate
uv sync --dev

pytest tests/ -v
pytest tests/ --cov=src --cov-report=term-missing
```

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `Invalid API key` | Export `LLM_API_KEY` with the key for the provider selected in `model_config`. |
| `ModuleNotFoundError` for Redis / Postgres / MongoDB / S3 | Install the matching extra, for example `pip install "omnicoreagent[redis]"`. |
| `Redis connection failed` | Start Redis or use `MemoryRouter("in_memory")`. |
| `MCP connection refused` | Ensure the MCP server is running before starting the agent. |

> More help: [Basic Usage Guide](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/basic-usage)

---

## Contributing

```bash
git clone https://github.com/omnirexflora-labs/omnicoreagent.git
cd omnicoreagent

uv venv && source .venv/bin/activate
uv sync --dev
pre-commit install
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. PRs are welcome.

---

## License

MIT - see [LICENSE](LICENSE).

---

## Author

**Built by [Abiola Adeshina](https://github.com/Abiorh001)**.

- **GitHub**: [@Abiorh001](https://github.com/Abiorh001)
- **X (Twitter)**: [@abiorhmangana](https://x.com/abiorhmangana)
- **Email**: abiolaadedayo1993@gmail.com

### The OmniRexFlora Ecosystem

| Project | Description |
|---------|-------------|
| [OmniMemory](https://github.com/omnirexflora-labs/omnimemory) | Self-evolving memory for autonomous agents |
| [OmniCoreAgent](https://github.com/omnirexflora-labs/omnicoreagent) | Production agent harness (this project) |
| [OmniDaemon](https://github.com/omnirexflora-labs/OmniDaemon) | Event-driven runtime for running agents as supervised, autonomous infrastructure services |

### Built On

[LiteLLM](https://github.com/BerriAI/litellm) - [FastAPI](https://fastapi.tiangolo.com/) - [Redis](https://redis.io/) - [Pydantic](https://docs.pydantic.dev/)

---

<p align="center">
  <a href="https://github.com/omnirexflora-labs/omnicoreagent">Star on GitHub</a> -
  <a href="https://github.com/omnirexflora-labs/omnicoreagent/issues">Report Bug</a> -
  <a href="https://github.com/omnirexflora-labs/omnicoreagent/issues">Request Feature</a> -
  <a href="https://docs-omnicoreagent.omnirexfloralabs.com/docs">Documentation</a>
</p>
