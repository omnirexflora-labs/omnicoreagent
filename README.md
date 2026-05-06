<p align="center">
  <img src="assets/IMG_5292.jpeg" alt="OmniCoreAgent Logo" width="250"/>
</p>

<h1 align="center">🚀 OmniCoreAgent</h1>

<p align="center">
  <strong>The Open Agent Harness Built for Production</strong><br>
  <em>Run autonomous agents with tools, memory, context management, guardrails, and deployment paths.</em>
</p>

<p align="center">
  <a href="https://pepy.tech/projects/omnicoreagent"><img src="https://static.pepy.tech/badge/omnicoreagent" alt="PyPI Downloads"></a>
  <a href="https://badge.fury.io/py/omnicoreagent"><img src="https://badge.fury.io/py/omnicoreagent.svg" alt="PyPI version"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-see-it-in-action">See It In Action</a> •
  <a href="./cookbook">📚 Cookbook</a> •
  <a href="#-core-features">Features</a> •
  <a href="https://docs-omnicoreagent.omnirexfloralabs.com/docs">Docs</a>
</p>

---

## 🎬 See It In Action

```python
import asyncio
from omnicoreagent import OmniCoreAgent, MemoryRouter, ToolRegistry

# Create tools in seconds
tools = ToolRegistry()

@tools.register_tool("get_weather")
def get_weather(city: str) -> dict:
    """Get current weather for a city."""
    return {"city": city, "temp": "22°C", "condition": "Sunny"}

# Build a production-ready agent
agent = OmniCoreAgent(
    name="assistant",
    system_instruction="You are a helpful assistant with access to weather data.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    local_tools=tools,
    memory_router=MemoryRouter("in_memory"),
    agent_config={
        "context_management": {"enabled": True},  # Auto-manage long conversations
        "guardrail_config": {"strict_mode": True},  # Block prompt injections
        "enable_subagents": True,  # Optional dynamic worker spawning
    }
)

async def main():
    # Run the agent
    result = await agent.run("What's the weather in Tokyo?")
    print(result["response"])
    
    # Production backends such as Redis, MongoDB, and Postgres are optional extras.
    # Install only the backend you use, then switch at runtime when configured.

asyncio.run(main())
```

**What just happened?**
- ✅ Registered a custom tool with type hints
- ✅ Built an agent with memory
- ✅ Enabled automatic context management
- ✅ Kept production backends optional until you need them

---

## ⚡ Quick Start

```bash
pip install omnicoreagent
```

Install production extras only when you need them:

```bash
pip install "omnicoreagent[redis]"       # Redis memory and event streams
pip install "omnicoreagent[postgres]"    # PostgreSQL / SQL memory
pip install "omnicoreagent[mongodb]"     # MongoDB memory
pip install "omnicoreagent[s3]"          # S3 / R2 workspace files
pip install "omnicoreagent[serve]"       # OmniServe REST/SSE API server
pip install "omnicoreagent[all]"         # Everything
```

```bash
echo "LLM_API_KEY=your_api_key" > .env
```

```python
from omnicoreagent import OmniCoreAgent

agent = OmniCoreAgent(
    name="my_agent",
    system_instruction="You are a helpful assistant.",
    model_config={"provider": "openai", "model": "gpt-4o"}
)

result = await agent.run("Hello!")
print(result["response"])
```

**That's it.** You have an AI agent with session management, memory, and error handling.

> 📚 **Want to learn more?** Check out the [Cookbook](./cookbook) — progressive examples from "Hello World" to production deployments.

---

## 🎯 What Makes OmniCoreAgent Different?

| Feature | What It Means For You |
|---------|----------------------|
| **Agent Harness Runtime** | Tool loop, memory, context management, guardrails, skills, sub-agents, and serving in one open package |
| **Runtime Backend Switching** | Switch Redis ↔ MongoDB ↔ PostgreSQL without restarting |
| **Cloud Workspace Storage** | Agent files persist in AWS S3 or Cloudflare R2 ⚡ NEW |
| **Context Engineering** | Session memory + agent loop context + tool offloading = no token exhaustion |
| **Tool Response Offloading** | Large tool outputs saved to files, 98% token savings |
| **Built-in Guardrails** | Prompt injection protection out of the box |
| **MCP Native** | Connect to any MCP server (stdio, SSE, HTTP with OAuth) |
| **Background Agents** | Schedule autonomous tasks that run on intervals |
| **Workflow Orchestration** | Sequential, Parallel, and Router agents for complex tasks |
| **Runtime Visibility** | Metrics, event streams, and compact in-house trace summaries |

---

## 🎯 Core Features

> 📖 **Full documentation**: [docs-omnicoreagent.omnirexfloralabs.com/docs](https://docs-omnicoreagent.omnirexfloralabs.com/docs)

| # | Feature | Description | Docs |
|---|---------|-------------|------|
| 1 | **OmniCoreAgent** | The heart of the framework — production agent with all features | [Overview →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/overview) |
| 2 | **Multi-Tier Memory** | 5 backends (Redis, MongoDB, PostgreSQL, SQLite, in-memory) with runtime switching | [Memory →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/memory) |
| 3 | **Context Engineering** | Dual-layer system: agent loop context management + tool response offloading | [Context →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/context-engineering) |
| 4 | **Event System** | Real-time event streaming with runtime switching | [Events →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/events) |
| 5 | **MCP Tools** | Connect external MCP tool servers (stdio, streamable_http, SSE) with OAuth | [MCP →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/mcp) |
| 6 | **Dynamic Subagents** | Spawn one or many focused workers from OmniCoreAgent with workspace files | [Sub-Agents →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/sub-agents) |
| 7 | **Local Tools** | Register any Python function as an AI tool via ToolRegistry | [Local Tools →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/local-tools) |
| 8 | **Agent Skills** | Polyglot packaged capabilities (Python, Bash, Node.js) | [Skills →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/skills) |
| 9 | **Workspace Files** | Persistent file storage with S3/R2/Local backends | [Workspace →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/workspace-files) |
| 10 | **Harness Defaults** | Enable subagents, workspace files, context management, and tool offloading on OmniCoreAgent | [Sub-Agents →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/sub-agents) |
| 11 | **Background Agents** | Schedule autonomous tasks on intervals | [Background →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/background-agents) |
| 12 | **Workflows** | Sequential, Parallel, and Router agent orchestration | [Workflows →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/workflows) |
| 13 | **BM25 Tool Retrieval** | Auto-discover relevant tools from 1000+ using BM25 search | [Advanced Tools →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/advanced-tools) |
| 14 | **Guardrails** | Prompt injection protection with configurable sensitivity | [Guardrails →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/core-concepts/guardrails) |
| 15 | **Runtime Visibility** | Per-request metrics, event streams, and compact in-house trace summaries | [Observability →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/observability) |
| 16 | **Universal Models** | 9 providers via LiteLLM (OpenAI, Anthropic, Gemini, Groq, Ollama, etc.) | [Models →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/models) |
| 17 | **OmniServe** | Turn any agent into a production REST/SSE API with one command | [OmniServe →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/omniserve) |

---

## 📚 Examples & Cookbook

All examples are in the **[Cookbook](./cookbook)** — organized by use case with progressive learning paths.

| Category | What You'll Build | Location |
|----------|-------------------|----------|
| **Getting Started** | Your first agent, tools, memory, events | [cookbook/getting_started](./cookbook/getting_started) |
| **Workflows** | Sequential, Parallel, Router agents | [cookbook/workflows](./cookbook/workflows) |
| **Background Agents** | Scheduled autonomous tasks | [cookbook/background_agents](./cookbook/background_agents) |
| **Production** | Metrics and guardrails | [cookbook/production](./cookbook/production) |
---

## ⚙️ Configuration

### Environment Variables

```bash
# Required
LLM_API_KEY=your_api_key

# Optional: workspace backend for artifacts and workspace files
OMNICOREAGENT_WORKSPACE_BACKEND=local  # local, s3, or r2
OMNICOREAGENT_WORKSPACE_DIR=.omnicoreagent/workspace
AWS_S3_BUCKET=your-s3-bucket           # when backend=s3
R2_BUCKET_NAME=your-r2-bucket          # when backend=r2
```

### Agent Configuration

```python
agent_config = {
    "max_steps": 15,                    # Max reasoning steps
    "tool_call_timeout": 30,            # Tool timeout (seconds)
    "request_limit": 0,                 # 0 = unlimited
    "total_tokens_limit": 0,            # 0 = unlimited
    "memory_config": {"mode": "sliding_window", "value": 10000},
    "enable_advanced_tool_use": True,   # BM25 tool retrieval
    "enable_subagents": True,           # Dynamic focused workers
    "enable_agent_skills": True,        # Specialized packaged skills
    "enable_workspace_files": True,    # Workspace files: notes, logs, scratchpads
    "context_management": {"enabled": True},
    "tool_offload": {"enabled": True}
}
```

> 📖 **Full configuration reference**: [Configuration Guide →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/configuration)

---

## 🧪 Testing & Development

```bash
# Clone
git clone https://github.com/omnirexflora-labs/omnicoreagent.git
cd omnicoreagent

# Setup
uv venv && source .venv/bin/activate
uv sync --dev

# Test
pytest tests/ -v
pytest tests/ --cov=src --cov-report=term-missing
```

---

## 🔍 Troubleshooting

| Error | Fix |
|-------|-----|
| `Invalid API key` | Check `.env`: `LLM_API_KEY=your_key` |
| `ModuleNotFoundError` for Redis/Postgres/Mongo/S3/OmniServe | Install the matching extra, e.g. `pip install "omnicoreagent[redis]"` |
| `Redis connection failed` | Start Redis or use `MemoryRouter("in_memory")` |
| `MCP connection refused` | Ensure MCP server is running |

> 📖 **More troubleshooting**: [Basic Usage Guide →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/how-to-guides/basic-usage)

---

## 📝 Changelog

See the full [Changelog →](https://docs-omnicoreagent.omnirexfloralabs.com/docs/changelog) for version history.

---

## 🤝 Contributing

```bash
# Fork & clone
git clone https://github.com/omnirexflora-labs/omnicoreagent.git

# Setup
uv venv && source .venv/bin/activate
uv sync --dev
pre-commit install

# Submit PR
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## 📄 License

MIT License — see [LICENSE](LICENSE)

---

## 👨‍💻 Author & Credits

**Created by [Abiola Adeshina](https://github.com/Abiorh001)**

- **GitHub**: [@Abiorh001](https://github.com/Abiorh001)
- **X (Twitter)**: [@abiorhmangana](https://x.com/abiorhmangana)
- **Email**: abiolaadedayo1993@gmail.com

### 🌟 The OmniRexFlora Ecosystem

| Project | Description |
|---------|-------------|
| [🧠 OmniMemory](https://github.com/omnirexflora-labs/omnimemory) | Self-evolving memory for autonomous agents |
| [🤖 OmniCoreAgent](https://github.com/omnirexflora-labs/omnicoreagent) | Production-ready AI agent framework (this project) |

### 🙏 Acknowledgments

Built on: [LiteLLM](https://github.com/BerriAI/litellm), [FastAPI](https://fastapi.tiangolo.com/), [Redis](https://redis.io/), [Pydantic](https://pydantic-docs.helpmanual.io/), [APScheduler](https://apscheduler.readthedocs.io/)

---

<p align="center">
  <strong>Building the future of production-ready AI agent frameworks</strong>
</p>

<p align="center">
  <a href="https://github.com/omnirexflora-labs/omnicoreagent">⭐ Star us on GitHub</a> •
  <a href="https://github.com/omnirexflora-labs/omnicoreagent/issues">🐛 Report Bug</a> •
  <a href="https://github.com/omnirexflora-labs/omnicoreagent/issues">💡 Request Feature</a> •
  <a href="https://docs-omnicoreagent.omnirexfloralabs.com/docs">📖 Documentation</a>
</p>
