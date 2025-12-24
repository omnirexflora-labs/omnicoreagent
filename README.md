<p align="center">
  <img src="assets/IMG_5292.jpeg" alt="OmniCoreAgent Logo" width="250"/>
</p>

<h1 align="center">🚀 OmniCoreAgent</h1>

<p align="center">
  <strong>Production-Ready AI Agent Framework</strong><br>
  Build autonomous AI agents that think, reason, and execute complex tasks.
</p>

<p align="center">
  <a href="https://pepy.tech/projects/omnicoreagent"><img src="https://static.pepy.tech/badge/omnicoreagent" alt="PyPI Downloads"></a>
  <a href="https://badge.fury.io/py/omnicoreagent"><img src="https://badge.fury.io/py/omnicoreagent.svg" alt="PyPI version"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
  <a href="https://github.com/omnirexflora-labs/omnicoreagent/commits/main"><img src="https://img.shields.io/github/last-commit/omnirexflora-labs/omnicoreagent" alt="Last Commit"></a>
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-core-features">Features</a> •
  <a href="#-examples">Examples</a> •
  <a href="#-configuration">Configuration</a> •
  <a href="https://omnirexflora-labs.github.io/omnicoreagent">Documentation</a>
</p>

---

## 📋 Table of Contents

<details>
<summary><strong>Click to expand</strong></summary>

### Getting Started
- [🌐 The OmniRexFlora AI Ecosystem](#-the-omnirexflora-ai-ecosystem)
- [🎯 What is OmniCoreAgent?](#-what-is-omnicoreagent)
- [⚡ Quick Start](#-quick-start)
- [🏗️ Architecture Overview](#️-architecture-overview)

### Core Features
1. [🤖 OmniCoreAgent — The Heart of the Framework](#1--omnicoreagent--the-heart-of-the-framework)
2. [🧠 Multi-Tier Memory System](#2--multi-tier-memory-system-plug--play)
3. [📡 Event System](#3--event-system-plug--play)
4. [🔌 Built-in MCP Client](#4--built-in-mcp-client)
5. [🛠️ Local Tools System](#5-️-local-tools-system)
6. [🧩 Agent Skills System](#6--agent-skills-system-packaged-capabilities)
7. [💾 Memory Tool Backend](#7--memory-tool-backend-file-based-working-memory)
8. [👥 Sub-Agents System](#8--sub-agents-system)
9. [🚁 Background Agents](#9--background-agents)
10. [🔄 Workflow Agents](#10--workflow-agents)
11. [🧠 Advanced Tool Use (BM25)](#11--advanced-tool-use-bm25-retrieval)
12. [📊 Production Observability & Metrics](#12--production-observability--metrics)
13. [🛡️ Prompt Injection Guardrails](#13--prompt-injection-guardrails)
14. [🌐 Universal Model Support](#14--universal-model-support)


### Reference
- [📚 Examples](#-examples)
- [⚙️ Configuration](#️-configuration)
- [🧪 Testing & Development](#-testing--development)
- [🔍 Troubleshooting](#-troubleshooting)
- [🤝 Contributing](#-contributing)
- [👨‍💻 Author & Credits](#-author--credits)

</details>

---

## 🌐 The OmniRexFlora AI Ecosystem

**OmniCoreAgent is part of a complete "Operating System for AI Agents"** — three powerful tools that work together:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     🌐 OmniRexFlora AI Ecosystem                            │
│                    "The Operating System for AI Agents"                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   🧠 OmniMemory                    🤖 OmniCoreAgent                         │
│   ─────────────                    ─────────────────                        │
│   The Brain                        The Worker           ⚡ OmniDaemon       │
│                                                         ─────────────       │
│   • Self-evolving memory      ───► • Agent building     The Runtime         │
│   • Dual-agent synthesis           • Tool orchestration                     │
│   • Conflict resolution            • Multi-backend      • Event-driven  ◄───┤
│   • Composite scoring              • Workflow agents      execution         │
│                                                         • Production        │
│   github.com/omnirexflora-        YOU ARE HERE            deployment       │
│   labs/omnimemory                                       • Framework-        │
│                                                           agnostic          │
│                                                                             │
│                                                         github.com/         │
│                                                         omnirexflora-labs/  │
│                                                         OmniDaemon          │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Tool | Role | Description |
|------|------|-------------|
| [🧠 **OmniMemory**](https://github.com/omnirexflora-labs/omnimemory) | The Brain | Self-evolving memory with dual-agent synthesis & conflict resolution |
| [🤖 **OmniCoreAgent**](https://github.com/omnirexflora-labs/omnicoreagent) | The Worker | Agent building, tool orchestration, multi-backend flexibility |
| [⚡ **OmniDaemon**](https://github.com/omnirexflora-labs/OmniDaemon) | The Runtime | Event-driven execution, production deployment, framework-agnostic |

> 💡 **Like how Linux runs applications, OmniRexFlora runs AI agents** — reliably, at scale, in production.

---

## 🎯 What is OmniCoreAgent?

**OmniCoreAgent** is a production-ready Python framework for building autonomous AI agents that:

| Capability | Description |
|------------|-------------|
| 🤖 **Think & Reason** | Not just chatbots — agents that plan multi-step workflows |
| 🛠️ **Use Tools** | Connect to APIs, databases, files, MCP servers, with Advanced Tool Use |
| 🧠 **Remember Context** | Multi-tier memory: Redis, PostgreSQL, MongoDB, SQLite |
| 🔄 **Orchestrate Workflows** | Sequential, Parallel, and Router agents |
| 🚀 **Run in Production** | Monitoring, observability, error handling built-in |
| 🔌 **Plug & Play** | Switch backends at runtime (Redis ↔ MongoDB ↔ PostgreSQL) |

---

## ⚡ Quick Start

### 1. Install (10 seconds)

```bash
# Using uv (recommended)
uv add omnicoreagent

# Or with pip
pip install omnicoreagent
```

### 2. Set API Key (10 seconds)

```bash
echo "LLM_API_KEY=your_openai_api_key_here" > .env
```

> 💡 Get your key from [OpenAI](https://platform.openai.com/api-keys), [Anthropic](https://console.anthropic.com/), or [Groq](https://console.groq.com/)

### 3. Create Your First Agent (30 seconds)

```python
import asyncio
from omnicoreagent import OmniCoreAgent

async def main():
    agent = OmniCoreAgent(
        name="my_agent",
        system_instruction="You are a helpful assistant.",
        model_config={"provider": "openai", "model": "gpt-4o"}
    )
    
    result = await agent.run("Hello, world!")
    print(result['response'])
    
    await agent.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
```

**✅ That's it!** You just built an AI agent with session management, memory persistence, event streaming, and error handling.

<details>
<summary><strong>🚨 Common Errors & Fixes</strong></summary>

| Error | Fix |
|-------|-----|
| `Invalid API key` | Check `.env` file: `LLM_API_KEY=sk-...` (no quotes) |
| `ModuleNotFoundError` | Run: `pip install omnicoreagent` |
| `Event loop is closed` | Use `asyncio.run(main())` |

</details>

---

## 🏗️ Architecture Overview

```
OmniCoreAgent Framework
├── 🤖 Core Agent System
│   ├── OmniCoreAgent (Main Class)
│   ├── ReactAgent (Reasoning Engine)
│   └── Tool Orchestration
│
├── 🧠 Memory System (5 Backends)
│   ├── InMemoryStore (Fast Dev)
│   ├── RedisMemoryStore (Production)
│   ├── DatabaseMemory (PostgreSQL/MySQL/SQLite)
│   └── MongoDBMemory (Document Storage)
│
├── 📡 Event System
│   ├── InMemoryEventStore (Development)
│   └── RedisStreamEventStore (Production)
│
├── 🛠️ Tool System
│   ├── Local Tools Registry
│   ├── MCP Integration
│   ├── Advanced Tool Use (BM25)
│   └── Memory Tool Backend
│
├── 🚁 Background Agents
│   └── Autonomous Scheduled Tasks
│
├── 🔄 Workflow Agents
│   ├── SequentialAgent
│   ├── ParallelAgent
│   └── RouterAgent
│
├── 🧩 Agent Skills System
│   ├── SkillManager (Discovery)
│   ├── Multi-language Script Dispatcher
│   └── agentskills.io Spec Alignment
│
└── 🔌 Built-in MCP Client
    ├── stdio, SSE, HTTP transports
    └── OAuth & Bearer auth
```

---

## 🎯 Core Features

### 1. 🤖 OmniCoreAgent — The Heart of the Framework

```python
from omnicoreagent import OmniCoreAgent, ToolRegistry, MemoryRouter, EventRouter

# Basic Agent
agent = OmniCoreAgent(
    name="assistant",
    system_instruction="You are a helpful assistant.",
    model_config={"provider": "openai", "model": "gpt-4o"}
)

# Production Agent with All Features
agent = OmniCoreAgent(
    name="production_agent",
    system_instruction="You are a production agent.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    local_tools=tool_registry,
    mcp_tools=[...],
    memory_router=MemoryRouter("redis"),
    event_router=EventRouter("redis_stream"),
    agent_config={
        "max_steps": 20,
        "enable_advanced_tool_use": True,
        "enable_agent_skills": True,
        "memory_tool_backend": "local",
        "guardrail_config": {"strict_mode": True}  # Enable Safety Guardrails
    }
)


# Key Methods
await agent.run(query)                      # Execute task
await agent.run(query, session_id="user_1") # With session context
await agent.connect_mcp_servers()           # Connect MCP tools
await agent.list_all_available_tools()      # List all tools
await agent.swith_memory_store("mongodb")         # Switch backend at runtime!
await agent.get_session_history(session_id)      # Retrieve conversation history
await agent.clear_session_history(session_id)     # Clear history (session_id optional, clears all if None)
await agent.get_events(session_id)               # Get event history
await agent.get_memory_store_type()              # Get current memory router type
await agent.cleanup()                       # Clean up resources and remove the agent and the config
await agent.cleanup_mcp_servers()               # Clean up MCP servers without removing the agent and the config
await agent.get_metrics()                       # Get cumulative usage (tokens, requests, time)
```

> [!TIP]
> Each `agent.run()` call now returns a `metric` field containing fine-grained usage for that specific request.


> 💡 **When to Use**: OmniCoreAgent is your go-to for any AI task — from simple Q&A to complex multi-step workflows. Start here for any agent project.

### 2. 🧠 Multi-Tier Memory System (Plug & Play)

**5 backends with runtime switching** — start with Redis, switch to MongoDB, then PostgreSQL — all on the fly!

```python
from omnicoreagent import OmniCoreAgent, MemoryRouter

# Start with Redis
agent = OmniCoreAgent(
    name="my_agent",
    memory_router=MemoryRouter("redis"),
    model_config={"provider": "openai", "model": "gpt-4o"}
)

# Switch at runtime — no restart needed!
agent.swith_memory_store("mongodb")     # Switch to MongoDB
agent.swith_memory_store("database")    # Switch to PostgreSQL/MySQL/SQLite
agent.swith_memory_store("in_memory")   # Switch to in-memory
agent.swith_memory_store("redis")       # Back to Redis
```

| Backend | Use Case | Environment Variable |
|---------|----------|---------------------|
| `in_memory` | Fast development | — |
| `redis` | Production persistence | `REDIS_URL` |
| `database` | PostgreSQL/MySQL/SQLite | `DATABASE_URL` |
| `mongodb` | Document storage | `MONGODB_URI` |

> 💡 **When to Use**: Use `in_memory` for development/testing, `redis` for production with fast access, `database` for SQL-based systems, `mongodb` for document-heavy applications.

### 3. 📡 Event System (Plug & Play)

Real-time event streaming with runtime switching:

```python
from omnicoreagent import EventRouter

# Start with in-memory
agent = OmniCoreAgent(
    event_router=EventRouter("in_memory"),
    ...
)

# Switch to Redis Streams for production
agent.switch_event_store("redis_stream")
agent.get_event_store_type()                    # Get current event router type
# Stream events in real-time
async for event in agent.stream_events(session_id):
    print(f"{event.type}: {event.payload}")
```

**Event Types**: `user_message`, `agent_message`, `tool_call_started`, `tool_call_result`, `final_answer`, `agent_thought`, `sub_agent_started`, `sub_agent_error`, `sub_agent_result`

> 💡 **When to Use**: Enable events when you need real-time monitoring, debugging, or building UIs that show agent progress. Essential for production observability.

### 4. 🔌 Built-in MCP Client

Connect to any MCP-compatible service with support for multiple transport protocols and authentication methods.

#### Transport Types

**1. stdio** — Local MCP servers (process communication)

```python
{
    "name": "filesystem",
    "transport_type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home"]
}
```

**2. streamable_http** — Remote servers with HTTP streaming

```python
# With Bearer Token
{
    "name": "github",
    "transport_type": "streamable_http",
    "url": "http://localhost:8080/mcp",
    "headers": {
        "Authorization": "Bearer your-token" # optional
    },
    "timeout": 60 # optional
}

# With OAuth 2.0 (auto-starts callback server on localhost:3000)
{
    "name": "oauth_server",
    "transport_type": "streamable_http",
    "auth": {
        "method": "oauth"
    },
    "url": "http://localhost:8000/mcp"
}
```

**3. sse** — Server-Sent Events

```python
{
    "name": "sse_server",
    "transport_type": "sse",
    "url": "http://localhost:3000/sse",
    "headers": {
        "Authorization": "Bearer token" # optional
    },
    "timeout": 60, # optional
    "sse_read_timeout": 120 # optional
}
```

#### Complete Example with All 3 Transport Types

```python
agent = OmniCoreAgent(
    name="multi_mcp_agent",
    system_instruction="You have access to filesystem, GitHub, and live data.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    mcp_tools=[
        # 1. stdio - Local filesystem
        {
            "name": "filesystem",
            "transport_type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home"]
        },
        # 2. streamable_http - Remote API (supports Bearer token or OAuth)
        {
            "name": "github",
            "transport_type": "streamable_http",
            "url": "http://localhost:8080/mcp",
            "headers": {"Authorization": "Bearer github-token"},
            "timeout": 60
        },
        # 3. sse - Real-time streaming
        {
            "name": "live_data",
            "transport_type": "sse",
            "url": "http://localhost:3000/sse",
            "headers": {"Authorization": "Bearer token"},
            "sse_read_timeout": 120
        }
    ]
)

await agent.connect_mcp_servers()
tools = await agent.list_all_available_tools()  # All MCP + local tools
result = await agent.run("List all Python files and get latest commits")
```

#### Transport Comparison

| Transport | Use Case | Auth Methods |
|-----------|----------|-------------|
| `stdio` | Local MCP servers, CLI tools | None (local process) |
| `streamable_http` | Remote APIs, cloud services | Bearer token, OAuth 2.0 |
| `sse` | Real-time data, streaming | Bearer token, custom headers |

> 💡 **When to Use**: Use MCP when you need to connect to external tools and services. Choose `stdio` for local CLI tools, `streamable_http` for REST APIs, and `sse` for real-time streaming data.

---

### 5. 🛠️ Local Tools System

Register any Python function as an AI tool:

```python
from omnicoreagent import ToolRegistry

tools = ToolRegistry()

@tools.register_tool("get_weather")
def get_weather(city: str) -> str:
    """Get weather for a city."""
    return f"Weather in {city}: Sunny, 25°C"

@tools.register_tool("calculate_area")
def calculate_area(length: float, width: float) -> str:
    """Calculate rectangle area."""
    return f"Area: {length * width} square units"

agent = OmniCoreAgent(
    name="tool_agent",
    local_tools=tools,  # Your custom tools!
    ...
)
```

> 💡 **When to Use**: Use Local Tools when you need custom business logic, internal APIs, or any Python functionality that isn't available via MCP servers.

---

### 6. 🧩 Agent Skills System (Packaged Capabilities)

OmniCoreAgent supports the **Agent Skills** specification — self-contained capability packages that provide specialized knowledge, executable scripts, and documentation.

```python
agent_config = {
    "enable_agent_skills": True  # Enable discovery and tools for skills
}
```

**Key Concepts**:
- **Discovery**: Agents automatically discover skills installed in `.agents/skills/[skill-name]`.
- **Activation (`SKILL.md`)**: Agents are instructed to read the "Activation Document" first to understand how to use the skill's specific capabilities.
- **Polyglot Execution**: The `run_skill_script` tool handles scripts in **Python, JavaScript/Node, TypeScript, Ruby, Perl, and Shell** (bash/sh).

**Directory Structure**:
```text
.agents/skills/my-skill-name/
├── SKILL.md        # The "Activation" document (instructions + metadata)
├── scripts/        # Multi-language executable scripts
├── references/     # Deep-dive documentation
└── assets/         # Templates, examples, and resources
```

**Skill Tools**:
- `read_skill_file(skill_name, file_path)`: Access any file within a skill (start with `SKILL.md`).
- `run_skill_script(skill_name, script_name, args?)`: Execute bundled scripts with automatic interpreter detection.

> 📚 **Learn More**: To learn how to create your own agent skills, visit [agentskills.io](https://agentskills.io/).

---

### 7. 💾 Memory Tool Backend (File-Based Working Memory)

A **file-based persistent storage system** that gives your agent a local workspace to save and manage files during long-running tasks. Files are stored in a `./memories/` directory with safe concurrent access and path traversal protection.

```python
agent_config = {
    "memory_tool_backend": "local"  # Enable file-based memory
}

# Agent automatically gets these tools:
# - memory_view: View/list files in memory directory
# - memory_create_update: Create new files or append/overwrite existing ones
# - memory_str_replace: Find and replace text within files
# - memory_insert: Insert text at specific line numbers
# - memory_delete: Delete files from memory
# - memory_rename: Rename or move files
# - memory_clear_all: Clear entire memory directory
```

**How It Works**:
- Files stored in `./memories/` directory (auto-created)
- Thread-safe with file locking for concurrent access
- Path traversal protection for security
- Persists across agent restarts

**Use Cases**:
| Use Case | Description |
|----------|-------------|
| **Long-running workflows** | Save progress as agent works through complex tasks |
| **Resumable tasks** | Continue where you left off after interruption |
| **Multi-step planning** | Agent can save plans, execute, and update |
| **Code generation** | Save code incrementally, run tests, iterate |
| **Data processing** | Store intermediate results between steps |

**Example**: A code generation agent can save its plan to memory, write code incrementally, run tests, and resume if interrupted.

---

### 8. 👥 Sub-Agents System

Delegate tasks to specialized child agents:

```python
weather_agent = OmniCoreAgent(name="weather_agent", ...)
filesystem_agent = OmniCoreAgent(name="filesystem_agent", mcp_tools=MCP_TOOLS, ...)

parent_agent = OmniCoreAgent(
    name="parent_agent",
    sub_agents=[weather_agent, filesystem_agent],
    ...
)
```

> 💡 **When to Use**: Use Sub-Agents when you have specialized agents (e.g., weather, code, data) and want a parent agent to delegate tasks intelligently. Great for building modular, reusable agent architectures.

---

### 9. 🚁 Background Agents

Autonomous agents that run on schedule:

```python
from omnicoreagent import BackgroundAgentService, MemoryRouter, EventRouter

bg_service = BackgroundAgentService(
    MemoryRouter("redis"),
    EventRouter("redis_stream")
)
bg_service.start_manager()

agent_config = {
    "agent_id": "system_monitor",
    "system_instruction": "Monitor system resources.",
    "model_config": {"provider": "openai", "model": "gpt-4o-mini"},
    "interval": 300,  # Run every 5 minutes
    "task_config": {
        "query": "Monitor CPU and alert if > 80%",
        "max_retries": 2
    }
}

await bg_service.create(agent_config)
bg_service.start_agent("system_monitor")
```

**Management**: `start_agent()`, `pause_agent()`, `resume_agent()`, `stop_agent()`, `get_agent_status()`

> 💡 **When to Use**: Perfect for scheduled tasks like system monitoring, periodic reports, data syncing, or any automation that runs independently without user interaction.

---

### 10. 🔄 Workflow Agents

Orchestrate multiple agents for complex tasks:

```python
from omnicoreagent import SequentialAgent, ParallelAgent, RouterAgent

# Sequential: Chain agents step-by-step
seq_agent = SequentialAgent(sub_agents=[agent1, agent2, agent3])
result = await seq_agent.run(initial_task="Analyze and report")

# Parallel: Run agents concurrently
par_agent = ParallelAgent(sub_agents=[agent1, agent2, agent3])
results = await par_agent.run(agent_tasks={
    "analyzer": "Analyze data",
    "processor": "Process results"
})

# Router: Intelligent task routing
router = RouterAgent(
    sub_agents=[code_agent, data_agent, research_agent],
    model_config={"provider": "openai", "model": "gpt-4o"}
)
result = await router.run(task="Find and summarize AI research")
```

> 💡 **When to Use**:
> - **SequentialAgent**: When tasks depend on each other (output of one → input of next)
> - **ParallelAgent**: When tasks are independent and can run simultaneously for speed
> - **RouterAgent**: When you need intelligent task routing to specialized agents

---

### 11. 🧠 Advanced Tool Use (BM25 Retrieval)

Automatically discover relevant tools at runtime using BM25 lexical search:

```python
agent_config = {
    "enable_advanced_tool_use": True  # Enable BM25 retrieval
}
```

**How It Works**:
1. All MCP tools loaded into in-memory registry
2. BM25 index built over tool names, descriptions, parameters
3. User task used as search query
4. Top 5 relevant tools dynamically injected

**Benefits**: Scales to 1000+ tools, zero network I/O, deterministic, container-friendly.

> 💡 **When to Use**: Enable when you have many MCP tools (10+) and want the agent to automatically discover the right tools for each task without manual selection.

---

### 12. 📊 Production Observability & Metrics

#### 📈 Real-time Usage Metrics
OmniCoreAgent tracks every token, request, and millisecond. Each `run()` returns a `metric` object, and you can get cumulative stats anytime.

```python
result = await agent.run("Analyze this data")
print(f"Request Tokens: {result['metric'].request_tokens}")
print(f"Time Taken: {result['metric'].total_time:.2f}s")

# Get aggregated metrics for the agent's lifecycle
stats = await agent.get_metrics()
print(f"Avg Response Time: {stats['average_time']:.2f}s")
```

#### 🔍 Opik Tracing
Monitor and optimize your agents with deep traces:


```bash
# Add to .env
OPIK_API_KEY=your_opik_api_key
OPIK_WORKSPACE=your_workspace
```

**What's Tracked**: LLM call performance, tool execution traces, memory operations, agent workflow, bottlenecks.

```
Agent Execution Trace:
├── agent_execution: 4.6s
    ├── tools_registry_retrieval: 0.02s ✅
    ├── memory_retrieval_step: 0.08s ✅
    ├── llm_call: 4.5s ⚠️ (bottleneck!)
    └── action_execution: 0.03s ✅
```

> 💡 **When to Use**: Essential for production. Use Metrics for cost/performance monitoring, and Opik for identifying bottlenecks and debugging complex agent logic.

---


### 13. 🛡️ Prompt Injection Guardrails

Protect your agents against malicious inputs, jailbreaks, and instruction overrides before they reach the LLM.

```python
agent_config = {
    "guardrail_config": {
        "strict_mode": True,      # Block all suspicious inputs
        "sensitivity": 0.85,      # 0.0 to 1.0 (higher = more sensitive)
        "enable_pattern_matching": True,
        "enable_heuristic_analysis": True
    }
}

agent = OmniCoreAgent(..., agent_config=agent_config)

# If a threat is detected:
# result['response'] -> "I'm sorry, but I cannot process this request due to safety concerns..."
# result['guardrail_result'] -> Full metadata about the detected threat
```

**Key Protections**:
- **Instruction Overrides**: "Ignore previous instructions..."
- **Jailbreaks**: DAN mode, roleplay escapes, etc.
- **Toxicity & Abuse**: Built-in pattern recognition.
- **Payload Splitting**: Detects fragmented attack attempts.

#### ⚙️ Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `strict_mode` | `bool` | `False` | When `True`, any detection (even low confidence) blocks the request. |
| `sensitivity` | `float` | `1.0` | Scaling factor for threat scores (0.0 to 1.0). Higher = more sensitive. |
| `max_input_length` | `int` | `10000` | Maximum allowed query length before blocking. |
| `enable_encoding_detection` | `bool` | `True` | Detects base64, hex, and other obfuscation attempts. |
| `enable_heuristic_analysis` | `bool` | `True` | Analyzes prompt structure for typical attack patterns. |
| `enable_sequential_analysis` | `bool` | `True` | Checks for phased attacks across multiple tokens. |
| `enable_entropy_analysis` | `bool` | `True` | Detects high-entropy payloads common in injections. |
| `allowlist_patterns` | `list` | `[]` | List of regex patterns that bypass safety checks. |
| `blocklist_patterns` | `list` | `[]` | Custom regex patterns to always block. |

> 💡 **When to Use**: Always enable in user-facing applications to prevent prompt injection attacks and ensure agent reliability.


---

### 14. 🌐 Universal Model Support


Model-agnostic through LiteLLM — use any provider:

```python
# OpenAI
model_config = {"provider": "openai", "model": "gpt-4o"}

# Anthropic
model_config = {"provider": "anthropic", "model": "claude-3-5-sonnet-20241022"}

# Groq (Ultra-fast)
model_config = {"provider": "groq", "model": "llama-3.1-8b-instant"}

# Ollama (Local)
model_config = {"provider": "ollama", "model": "llama3.1:8b", "ollama_host": "http://localhost:11434"}

# OpenRouter (200+ models)
model_config = {"provider": "openrouter", "model": "anthropic/claude-3.5-sonnet"}

#mistral ai
model_config = {"provider": "mistral", "model": "mistral-7b-instruct"}

#deepseek
model_config = {"provider": "deepseek", "model": "deepseek-chat"}

#google gemini
model_config = {"provider": "google", "model": "gemini-2.0-flash-exp"}

#azure openai
model_config = {"provider": "azure_openai", "model": "gpt-4o"}
```

**Supported**: OpenAI, Anthropic, Google Gemini, Groq, DeepSeek, Mistral, Azure OpenAI, OpenRouter, Ollama

> 💡 **When to Use**: Switch providers based on your needs — use cheaper models (Groq, DeepSeek) for simple tasks, powerful models (GPT-4o, Claude) for complex reasoning, and local models (Ollama) for privacy-sensitive applications.

## 📚 Examples

### Basic Examples

```bash
python examples/cli/basic.py                    # Simple introduction
python examples/cli/run_omni_agent.py          # All features demo
```

### Custom Agents

```bash
python examples/custom_agents/e_commerce_personal_shopper_agent.py
python examples/custom_agents/flightBooking_agent.py
python examples/custom_agents/real_time_customer_support_agent.py
```

### Workflow Agents

```bash
python examples/workflow_agents/sequential_agent.py
python examples/workflow_agents/parallel_agent.py
python examples/workflow_agents/router_agent.py
```

### Production Examples

| Example | Description | Location |
|---------|-------------|----------|
| **DevOps Copilot** | Safe bash execution, rate limiting, Prometheus metrics | `examples/devops_copilot_agent/` |
| **Deep Code Agent** | Sandbox execution, memory backend, code analysis | `examples/deep_code_agent/` |

---

## ⚙️ Configuration

### Environment Variables

```bash
# Required
LLM_API_KEY=your_api_key

# Optional: Memory backends
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql://user:pass@localhost:5432/db
MONGODB_URI=mongodb://localhost:27017/omnicoreagent

# Optional: Observability
OPIK_API_KEY=your_opik_key
OPIK_WORKSPACE=your_workspace
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
    "enable_agent_skills": True,        # Specialized packaged skills
    "memory_tool_backend": "local"      # Persistent working memory
}
```

### Model Configuration

```python
model_config = {
    "provider": "openai",
    "model": "gpt-4o",
    "temperature": 0.7,
    "max_tokens": 2000,
    "top_p": 0.95
}
```

<details>
<summary><strong>📋 Additional Model Configurations</strong></summary>

```python
# Azure OpenAI
model_config = {
    "provider": "azureopenai",
    "model": "gpt-4",
    "azure_endpoint": "https://your-resource.openai.azure.com",
    "azure_api_version": "2024-02-01"
}

# Ollama (Local)
model_config = {
    "provider": "ollama",
    "model": "llama3.1:8b",
    "ollama_host": "http://localhost:11434"
}
```

</details>

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
| `ModuleNotFoundError` | `pip install omnicoreagent` |
| `Redis connection failed` | Start Redis or use `MemoryRouter("in_memory")` |
| `MCP connection refused` | Ensure MCP server is running |

<details>
<summary><strong>📋 More Troubleshooting</strong></summary>

**OAuth Server Starts**: Normal when using `"auth": {"method": "oauth"}`. Remove if not needed.

**Debug Mode**: `agent = OmniCoreAgent(..., debug=True)`

**Help**: Check [GitHub Issues](https://github.com/omnirexflora-labs/omnicoreagent/issues)

</details>

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
| [⚡ OmniDaemon](https://github.com/omnirexflora-labs/OmniDaemon) | Event-driven runtime engine for AI agents |

### 🙏 Acknowledgments

Built on: [LiteLLM](https://github.com/BerriAI/litellm), [FastAPI](https://fastapi.tiangolo.com/), [Redis](https://redis.io/), [Opik](https://opik.ai/), [Pydantic](https://pydantic-docs.helpmanual.io/), [APScheduler](https://apscheduler.readthedocs.io/)

---

<p align="center">
  <strong>Building the future of production-ready AI agent frameworks</strong>
</p>

<p align="center">
  <a href="https://github.com/omnirexflora-labs/omnicoreagent">⭐ Star us on GitHub</a> •
  <a href="https://github.com/omnirexflora-labs/omnicoreagent/issues">🐛 Report Bug</a> •
  <a href="https://github.com/omnirexflora-labs/omnicoreagent/issues">💡 Request Feature</a> •
  <a href="https://omnirexflora-labs.github.io/omnicoreagent">📖 Documentation</a>
</p>
