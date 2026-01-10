<p align="center">
  <img src="assets/IMG_5292.jpeg" alt="OmniCoreAgent Logo" width="250"/>
</p>

<h1 align="center">🚀 OmniCoreAgent</h1>

<p align="center">
  <strong>The AI Agent Framework Built for Production</strong><br>
  <em>Switch memory backends at runtime. Manage context automatically. Deploy with confidence.</em>
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
  <a href="https://omnirexflora-labs.github.io/omnicoreagent">Docs</a>
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
    memory_router=MemoryRouter("redis"),  # Start with Redis
    agent_config={
        "context_management": {"enabled": True},  # Auto-manage long conversations
        "guardrail_config": {"strict_mode": True},  # Block prompt injections
    }
)

async def main():
    # Run the agent
    result = await agent.run("What's the weather in Tokyo?")
    print(result["response"])
    
    # Switch to MongoDB at runtime — no restart needed
    await agent.switch_memory_store("mongodb")
    
    # Keep running with a different backend
    result = await agent.run("How about Paris?")
    print(result["response"])

asyncio.run(main())
```

**What just happened?**
- ✅ Registered a custom tool with type hints
- ✅ Built an agent with memory persistence
- ✅ Enabled automatic context management
- ✅ Switched from Redis to MongoDB *while running*

---

## ⚡ Quick Start

```bash
pip install omnicoreagent
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
| **Runtime Backend Switching** | Switch Redis ↔ MongoDB ↔ PostgreSQL without restarting |
| **Context Engineering** | Session memory + agent loop context + tool offloading = no token exhaustion |
| **Tool Response Offloading** | Large tool outputs saved to files, 98% token savings ⚡ NEW |
| **Built-in Guardrails** | Prompt injection protection out of the box |
| **MCP Native** | Connect to any MCP server (stdio, SSE, HTTP with OAuth) |
| **Background Agents** | Schedule autonomous tasks that run on intervals |
| **Workflow Orchestration** | Sequential, Parallel, and Router agents for complex tasks |
| **Production Observability** | Metrics, tracing, and event streaming built in |

---

<details>
<summary><strong>📋 Table of Contents</strong></summary>

**Getting Started**: [See It In Action](#-see-it-in-action) • [Quick Start](#-quick-start)

**Core Features**:
[OmniCoreAgent](#1--omnicoreagent--the-heart-of-the-framework) •
[Memory System](#2--multi-tier-memory-system-plug--play) •
[Context Engineering](#3--context-engineering-system) •
[Event System](#4--event-system-plug--play) •
[MCP Client](#5--built-in-mcp-client) •
[Local Tools](#6-️-local-tools-system) •
[Agent Skills](#7--agent-skills-system-packaged-capabilities) •
[Memory Tool Backend](#8--workspace-memory-file-based-persistence)

**Multi-Agent**:
[Sub-Agents](#9--sub-agents-system) •
[Background Agents](#10--background-agents) •
[Workflows](#11--workflow-agents)

**Production**:
[BM25 Tool Retrieval](#12--advanced-tool-use-bm25-retrieval) •
[Observability](#13--production-observability--metrics) •
[Guardrails](#14-️-prompt-injection-guardrails) •
[Model Support](#15--universal-model-support)

**Reference**: [Examples](#-examples--cookbook) • [Configuration](#️-configuration) • [Testing](#-testing--development) • [Contributing](#-contributing)

</details>

---

## 🏗 Architecture Overview

OmniCoreAgent follows a **layered architecture** that separates concerns while enabling rich integrations between components. Here's how it all fits together:

```mermaid
flowchart TB
    %% Entry Points
    subgraph Entry["🚀 Entry Points"]
        User([👤 User / App])
        Clock([⏰ Scheduler])
        API([🌐 External API])
    end

    %% Security Layer - First Line of Defense
    subgraph Security["🛡️ Security Layer"]
        Guard["Guardrails Engine<br/>━━━━━━━━━━━━━━<br/>• Pattern Matching<br/>• Heuristic Analysis<br/>• Entropy Detection<br/>• Encoding Detection"]
    end

    %% Orchestration Layer
    subgraph Orchestration["🎼 Orchestration Layer"]
        direction LR
        WF["Workflow Engine"]
        SEQ["Sequential<br/>Agent"]
        PAR["Parallel<br/>Agent"]
        RTR["Router<br/>Agent"]
        BG["Background<br/>Agent"]

        WF --> SEQ
        WF --> PAR
        WF --> RTR
    end

    %% Core Engine - The Heart
    subgraph Core["🧠 OmniCore Engine"]
        direction TB
        OCA["OmniCoreAgent<br/>━━━━━━━━━━━━━━<br/>• System Instructions<br/>• Model Config<br/>• Agent Config"]
        
        subgraph Processing["Processing Pipeline"]
            direction LR
            CTX["Context<br/>Manager"]
            SUM["Conversation<br/>Summarizer"]
            REACT["ReAct<br/>Loop"]
            OFF["Tool Response<br/>Offloader"]
        end
        
        LLM["LLM Layer<br/>━━━━━━━━━━━━━━<br/>OpenAI • Anthropic<br/>Gemini • Groq • Ollama<br/>Mistral • DeepSeek"]
    end

    %% Capabilities Layer
    subgraph Capabilities["🛠️ Capabilities Layer"]
        direction TB
        TOOLS["Tool Orchestration<br/>━━━━━━━━━━━━━━<br/>• Validation<br/>• Execution<br/>• Error Handling"]
        
        subgraph ToolTypes["Tool Types"]
            direction LR
            LOCAL["Local Tools<br/>(ToolRegistry)"]
            MCP["MCP Client<br/>(stdio/http/sse)"]
            BM25["BM25 RAG<br/>(Dynamic Discovery)"]
            SKILLS["Agent Skills<br/>(Polyglot Scripts)"]
            SUB["Sub-Agents<br/>(Delegation)"]
        end
    end

    %% Infrastructure Layer
    subgraph Infrastructure["💾 Infrastructure Layer"]
        direction TB
        
        subgraph Routers["Routers (Hot-Swappable)"]
            direction LR
            MEM["Memory Router<br/>━━━━━━━━━━━━━━<br/>Session State<br/>Conversation History"]
            EVT["Event Router<br/>━━━━━━━━━━━━━━<br/>Real-time Streaming<br/>Audit Trail"]
        end
        
        subgraph Storage["Storage Backends"]
            direction LR
            REDIS[("Redis")]
            POSTGRES[("PostgreSQL")]
            MONGO[("MongoDB")]
            MEM_STORE[("In-Memory")]
            FS[("File System")]
        end
        
        OBS["Observability<br/>━━━━━━━━━━━━━━<br/>Metrics • Tracing<br/>Token Usage • Opik"]
    end

    %% Connections - Main Flow
    User --> Guard
    API --> Guard
    Clock --> BG
    
    Guard -->|"✓ Safe"| OCA
    Guard -->|"✗ Blocked"| User
    
    BG --> OCA
    WF --> OCA
    SEQ --> OCA
    PAR --> OCA
    RTR --> OCA
    
    OCA --> CTX
    CTX <--> SUM
    CTX --> REACT
    REACT <--> LLM
    REACT --> TOOLS
    TOOLS --> OFF
    
    TOOLS --> LOCAL
    TOOLS --> MCP
    TOOLS --> BM25
    TOOLS --> SKILLS
    TOOLS --> SUB
    SUB -.->|"Recursive"| OCA
    
    %% Infrastructure Connections
    OCA <-.->|"State"| MEM
    OCA <-.->|"Events"| EVT
    OFF <-.->|"Artifacts"| FS
    
    MEM --> REDIS
    MEM --> POSTGRES
    MEM --> MONGO
    MEM --> MEM_STORE
    EVT --> REDIS
    
    OCA <-.->|"Metrics"| OBS

    %% Styling
    style Entry fill:#1abc9c,stroke:#16a085,color:#fff
    style Security fill:#e74c3c,stroke:#c0392b,color:#fff
    style Orchestration fill:#d35400,stroke:#e67e22,color:#fff
    style Core fill:#2c3e50,stroke:#34495e,color:#fff
    style Processing fill:#34495e,stroke:#2c3e50,color:#fff
    style Capabilities fill:#2980b9,stroke:#3498db,color:#fff
    style ToolTypes fill:#3498db,stroke:#2980b9,color:#fff
    style Infrastructure fill:#8e44ad,stroke:#9b59b6,color:#fff
    style Routers fill:#9b59b6,stroke:#8e44ad,color:#fff
    style Storage fill:#95a5a6,stroke:#7f8c8d,color:#fff
```

### Layer Responsibilities

| Layer | Purpose | Key Components |
|-------|---------|----------------|
| **🚀 Entry** | Request sources | User Apps, Schedulers, External APIs |
| **🛡️ Security** | Threat protection | Guardrails (injection detection, encoding checks) |
| **🎼 Orchestration** | Multi-agent coordination | Sequential, Parallel, Router, Background agents |
| **🧠 Core Engine** | Agent execution | ReAct loop, Context Management, Summarization, LLM calls |
| **🛠️ Capabilities** | Tool execution | Local tools, MCP, BM25 discovery, Skills, Sub-agents |
| **💾 Infrastructure** | Persistence & observability | Memory/Event routers, Storage backends, Metrics |

### Data Flow Highlights

1. **Request Path**: Entry → Security (guardrails) → Core → Capabilities → Response
2. **Memory Persistence**: State flows bidirectionally between agent and storage backends
3. **Tool Offloading**: Large responses saved to file system, only previews in context
4. **Event Streaming**: Real-time events pushed to Redis Streams for monitoring


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
        # Memory with summarization
        "memory_config": {
            "mode": "sliding_window",
            "value": 10,
            "summary": {
                "enabled": True,
                "retention_policy": "summarize",
            },
        },
        # Context management for long conversations
        "context_management": {
            "enabled": True,
            "mode": "token_budget",
            "value": 100000,
            "threshold_percent": 75,
            "strategy": "summarize_and_truncate",
            "preserve_recent": 6,
        },
        # Prompt injection guardrails
        "guardrail_config": {
            "enabled": True,
            "strict_mode": True,
        },
    },
)


# Key Methods
await agent.run(query)                      # Execute task
await agent.run(query, session_id="user_1") # With session context
await agent.connect_mcp_servers()           # Connect MCP tools
await agent.list_all_available_tools()      # List all tools
await agent.switch_memory_store("mongodb")   # Switch backend at runtime!
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

#### 🧠 Conversation Summarization

OmniCoreAgent includes **automatic conversation summarization** to manage long conversation histories efficiently. When enabled, older messages are condensed into summaries, keeping context while reducing token usage.

```python
from omnicoreagent import OmniCoreAgent, MemoryRouter

# Configure summarization with sliding window
memory_router = MemoryRouter(
    store_type="redis",
    memory_config={
        "mode": "sliding_window",  # or "token_budget"
        "value": 10,  # Keep last 10 messages (sliding_window) or max tokens (token_budget)
        "summary": {
            "enabled": True,
            "retention_policy": "keep"  # Options: "keep" or "delete"
        }
    }
)

agent = OmniCoreAgent(
    name="summarizing_agent",
    memory_router=memory_router,
    model_config={"provider": "openai", "model": "gpt-4o"}
)
```

**Summarization Modes**:

| Mode | Description | Best For |
|------|-------------|----------|
| `sliding_window` | Keep last N messages, summarize older ones | Predictable memory size |
| `token_budget` | Keep messages within token limit | Cost optimization |

**Retention Policies**:

| Policy | Behavior |
|--------|----------|
| `keep` | Mark summarized messages as inactive (recoverable) |
| `delete` | Permanently remove summarized messages |

**How It Works**:
1. When conversation exceeds configured limit → summarization triggers
2. Older messages are sent to LLM for summary generation
3. Summary replaces older messages in active context
4. Original messages are retained (with `"keep"`) or deleted per policy

> 💡 **When to Use**: Enable summarization for long-running conversations (support bots, research assistants) to maintain context while controlling costs. Use `sliding_window` for predictable behavior, `token_budget` for strict cost control.

### 3. 🔄 Context Engineering System

OmniCoreAgent implements **state-of-the-art context engineering** inspired by patterns from Anthropic and Cursor. This dual-layer approach ensures your agents never hit token limits — even during marathon coding sessions or multi-step research tasks.

```mermaid
flowchart TB
    subgraph Input["📥 Incoming Context"]
        MSG["Messages<br/>(User + Assistant + Tool)"]
        TOOL_RESP["Tool Responses<br/>(Web Search, APIs, Files)"]
    end

    subgraph Layer1["🧠 Layer 1: Agent Loop Context Management"]
        direction TB
        MONITOR["Context Monitor<br/>━━━━━━━━━━━━━━<br/>• Token counting<br/>• Message counting"]
        
        subgraph Modes["Management Modes"]
            direction LR
            TOKEN["token_budget<br/>━━━━━━━━━━<br/>Max total tokens"]
            SLIDE["sliding_window<br/>━━━━━━━━━━<br/>Max message count"]
        end
        
        subgraph Strategies["Overflow Strategies"]
            direction LR
            TRUNC["truncate<br/>━━━━━━━━<br/>Drop oldest<br/>(fast)"]
            SUMTRUNC["summarize_and_truncate<br/>━━━━━━━━━━━━━━<br/>Condense → Drop<br/>(preserves context)"]
        end
        
        RECENT["preserve_recent<br/>━━━━━━━━━━━━━━<br/>Always keep last N<br/>messages protected"]
    end

    subgraph Layer2["💾 Layer 2: Tool Response Offloading"]
        direction TB
        CHECK["Size Check<br/>━━━━━━━━━━━━━━<br/>threshold_tokens: 500<br/>threshold_bytes: 2000"]
        
        subgraph Offload["Offload Process"]
            direction LR
            SAVE["Save to File<br/>(.omnicoreagent_artifacts/)"]
            PREVIEW["Generate Preview<br/>(first ~150 tokens)"]
        end
        
        subgraph Tools["Built-in Artifact Tools"]
            direction LR
            READ["read_artifact()"]
            TAIL["tail_artifact()"]
            SEARCH["search_artifact()"]
            LIST["list_artifacts()"]
        end
    end

    subgraph Output["📤 Optimized Context"]
        CLEAN["Lean Context<br/>━━━━━━━━━━━━━━<br/>• System prompt<br/>• Recent messages<br/>• Summaries<br/>• Tool previews"]
    end

    %% Flow
    MSG --> MONITOR
    TOOL_RESP --> CHECK
    
    MONITOR --> TOKEN
    MONITOR --> SLIDE
    TOKEN --> TRUNC
    TOKEN --> SUMTRUNC
    SLIDE --> TRUNC
    SLIDE --> SUMTRUNC
    TRUNC --> RECENT
    SUMTRUNC --> RECENT
    
    CHECK -->|"> threshold"| SAVE
    CHECK -->|"≤ threshold"| Output
    SAVE --> PREVIEW
    PREVIEW --> Tools
    Tools -.->|"On demand"| Output
    
    RECENT --> CLEAN
    PREVIEW --> CLEAN

    %% Styling
    style Input fill:#3498db,stroke:#2980b9,color:#fff
    style Layer1 fill:#2c3e50,stroke:#34495e,color:#fff
    style Modes fill:#34495e,stroke:#2c3e50,color:#fff
    style Strategies fill:#34495e,stroke:#2c3e50,color:#fff
    style Layer2 fill:#8e44ad,stroke:#9b59b6,color:#fff
    style Offload fill:#9b59b6,stroke:#8e44ad,color:#fff
    style Tools fill:#9b59b6,stroke:#8e44ad,color:#fff
    style Output fill:#27ae60,stroke:#2ecc71,color:#fff
```

#### How the Two Layers Work Together

| Layer | Scope | What It Manages | When It Triggers |
|-------|-------|-----------------|------------------|
| **Context Management** | Agent loop messages | User/Assistant conversation, tool call history | When context exceeds `threshold_percent` of limit |
| **Tool Offloading** | Individual tool responses | Large API responses, file contents, search results | When response exceeds `threshold_tokens` |

---

#### 3.1 Agent Loop Context Management

Prevent token exhaustion during long-running tasks with automatic context management. When enabled, the agent monitors context size and applies truncation or summarization when thresholds are exceeded.

```python
agent_config = {
    "context_management": {
        "enabled": True,
        "mode": "token_budget",  # or "sliding_window"
        "value": 100000,  # Max tokens (token_budget) or max messages (sliding_window)
        "threshold_percent": 75,  # Trigger at 75% of limit
        "strategy": "summarize_and_truncate",  # or "truncate"
        "preserve_recent": 4,  # Always keep last N messages
    }
}
```

**Modes**:

| Mode | Description | Best For |
|------|-------------|----------|
| `token_budget` | Manage by total token count | Cost control, API limits |
| `sliding_window` | Manage by message count | Predictable context size |

**Strategies**:

| Strategy | Behavior | Trade-off |
|----------|----------|-----------|
| `truncate` | Drop oldest messages | Fast, no extra LLM calls |
| `summarize_and_truncate` | Summarize then drop | Preserves context, adds latency |

---

#### 3.2 Tool Response Offloading

Large tool responses are automatically saved to files, with only a **preview** in context. The agent can retrieve full content on demand using built-in tools.

```python
agent_config = {
    "tool_offload": {
        "enabled": True,
        "threshold_tokens": 500,  # Offload responses > 500 tokens
        "max_preview_tokens": 150,  # Show first 150 tokens in context
        "storage_dir": ".omnicoreagent_artifacts"
    }
}
```

**Token Savings Example**:

| Tool Response | Without Offloading | With Offloading | Savings |
|---------------|-------------------|-----------------|---------|
| Web search (50 results) | ~10,000 tokens | ~200 tokens | **98%** |
| Large API response | ~5,000 tokens | ~150 tokens | **97%** |
| File read (1000 lines) | ~8,000 tokens | ~200 tokens | **97%** |

**Built-in Artifact Tools** (automatically available when offloading is enabled):

| Tool | Purpose |
|------|---------|
| `read_artifact(artifact_id)` | Read full content when needed |
| `tail_artifact(artifact_id, lines)` | Read last N lines (great for logs) |
| `search_artifact(artifact_id, query)` | Search within large responses |
| `list_artifacts()` | See all offloaded data in current session |

---

#### Combined Power

Enable both for maximum efficiency:

```python
agent = OmniCoreAgent(
    name="research_agent",
    agent_config={
        "context_management": {"enabled": True, "strategy": "summarize_and_truncate"},
        "tool_offload": {"enabled": True, "threshold_tokens": 500}
    }
)
# Result: Agents that can run indefinitely without token exhaustion
```

> 💡 **When to Use**: Enable for long-running tasks (research, multi-step workflows) where context or tool responses can grow unbounded.


### 4. 📡 Event System (Plug & Play)

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

### 5. 🔌 Built-in MCP Client

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

### 6. 🛠️ Local Tools System

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

### 7. 🧩 Agent Skills System (Packaged Capabilities)

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

### 8. 💾 Workspace Memory (File-Based Persistence)

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

### 9. 👥 Sub-Agents System

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

### 10. 🚁 Background Agents
    
Autonomous agents that run on varying schedules (Interval or Cron) or process tasks from a persistent queue.
    
```python
from omnicoreagent import BackgroundAgentManager, MemoryRouter, EventRouter
    
# Initialize the manager
manager = BackgroundAgentManager(
    memory_router=MemoryRouter("redis"),
    event_router=EventRouter("redis_stream")
)
    
# Create a background agent
background_agent_config = {
    "agent_id": "system_monitor",
    "system_instruction": "Monitor system resources and report anomalies.",
    "model_config": {"provider": "openai", "model": "gpt-4o-mini"},
    "queue_size": 10,  # Max pending tasks
    # Schedule options:
    "interval": 300,        # Integer = seconds (every 5 mins)
    # "interval": "* * * * *", # String = Cron expression (every minute)
    "task_config": {
        "query": "Check metrics and alert if CPU > 80%",
        "timeout": 60,      # Kill task if it hangs
        "max_retries": 3,
        "retry_delay": 10
    }
}
await manager.create_agent(background_agent_config)
    
# Start the system
await manager.start()

# Trigger manually if needed
await manager.run_task_now("system_monitor", {"query": "Immediate check!"})
```

> 📚 **Deep Dive**: Check out the [Background Agents Cookbook](./cookbook/background_agents) for full "Kitchen Sink" examples including lifecycle management (pause/resume/delete) and advanced configuration.

#### 🛠️ Comprehensive API Reference

**BackgroundAgentManager (Orchestrator)**

_Lifecycle Management_
- `await manager.create_agent(config: Dict) -> Dict`: Create, register, and schedule a new background agent.
- `await manager.start()`: Start the manager and all scheduled agents.
- `await manager.shutdown()`: Gracefully stop the manager and all agents.
- `await manager.start_agent(agent_id)`: Start (schedule) a specific agent.
- `await manager.stop_agent(agent_id)`: Stop (unschedule) a specific agent.
- `await manager.pause_agent(agent_id)`: Pause an agent's schedule without stopping its worker.
- `await manager.resume_agent(agent_id)`: Resume a paused agent's schedule.
- `await manager.delete_agent(agent_id)`: Stop, cleanup, and remove an agent completely.

_Task Management_
- `await manager.register_task(agent_id, task_config)`: Register or update a task for an agent.
- `await manager.run_task_now(agent_id, task_config)`: Trigger an immediate execution outside the schedule.
- `await manager.register_and_run(agent_id, task_config)`: Register a task and run it immediately.
- `await manager.update_task_config(agent_id, task_config)`: Update an existing task configuration.
- `await manager.remove_task(agent_id)`: Remove a task configuration.
- `await manager.list_tasks()`: List all agents with registered tasks.

_Status & Monitoring_
- `await manager.get_agent_status(agent_id)`: Get comprehensive status (running, scheduled, last run, errors).
- `await manager.get_manager_status()`: Get overall system status (total agents, running count, resource usage).
- `await manager.list_agents()`: List all registered agent IDs.
- `await manager.is_agent_running(agent_id)`: Check if an agent is currently executing a task.
- `await manager.get_running_agents()`: List all currently executing agents.
- `await manager.get_agent_metrics(agent_id)`: Get performance metrics (run count, errors, timestamps).
- `await manager.get_all_metrics()`: Get metrics for all agents.

_Configuration & Information_
- `await manager.update_agent_config(agent_id, new_config)`: Update agent settings (including model/tools).
- `await manager.get_task_config(agent_id)`: Retrieve current task configuration.
- `await manager.get_agent(agent_id)`: Access the raw `BackgroundOmniCoreAgent` instance.
- `await manager.get_agent_event_info(agent_id)`: Get event stream connection details.
- `await manager.get_all_event_info()`: Get event info for all agents and shared stores.
- `await manager.get_agent_session_id(agent_id)`: Get the persistent session ID.
- `await manager.get_all_session_ids()`: Get a map of all agent session IDs.

---

**BackgroundOmniCoreAgent (The Workers)**

_Execution & Control_
- `await agent.submit_task(task_config)`: Queue a task for reliable execution.
- `await agent.run_task(task_config)`: Interface for scheduler/manual triggers.
- `await agent.start_worker()`: Start the background task processing loop.
- `await agent.stop_worker()`: Gracefully stop the background worker.
- `property agent.is_worker_running`: Check if the worker loop is active.
- `await agent.connect_mcp_servers()`: Establish connections to configured MCP tools.
- `await agent.cleanup()`: comprehensive cleanup of resources, connections, and tasks.

_State & Visibility_
- `await agent.get_status()`: Get health, configuration, and execution state.
- `await agent.get_session_id()`: Get the persistent session ID.
- `await agent.has_task()`: Check if a valid task is registered.
- `await agent.get_task_query()`: Get the current query/instruction being executed.
- `await agent.get_task_config()`: Get the full task configuration dict.

_Events & Streaming_
- `await agent.stream_events(session_id)`: Real-time event generator.
- `await agent.get_events(session_id)`: Retrieve past event history.
- `await agent.get_event_stream_info()`: Connection details for external consumers.
- `await agent.update_config(new_config)`: Hot-reload agent configuration.

> 💡 **When to Use**: Perfect for scheduled tasks like system monitoring, periodic reports, data syncing, or any automation that runs independently without user interaction.

---

### 11. 🔄 Workflow Agents

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

### 12. 🧠 Advanced Tool Use (BM25 Retrieval)

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

### 13. 📊 Production Observability & Metrics

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


### 14. 🛡️ Prompt Injection Guardrails

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

### 15. 🌐 Universal Model Support


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

---

## 📚 Examples & Cookbook

All examples are in the **[Cookbook](./cookbook)** — organized by use case with progressive learning paths.

### Quick Links

| Category | What You'll Build | Location |
|----------|-------------------|----------|
| **Getting Started** | Your first agent, tools, memory, events | [cookbook/getting_started](./cookbook/getting_started) |
| **Workflows** | Sequential, Parallel, Router agents | [cookbook/workflows](./cookbook/workflows) |
| **Background Agents** | Scheduled autonomous tasks | [cookbook/background_agents](./cookbook/background_agents) |
| **Production** | Metrics, guardrails, observability | [cookbook/production](./cookbook/production) |
| **🏆 Showcase** | Full production applications | [cookbook/showcase](./cookbook/showcase) |

### 🏆 Showcase: Full Production Applications

| Application | Description | Features |
|-------------|-------------|----------|
| **[OmniAudit](./cookbook/showcase/omniavelis)** | Healthcare Claims Audit System | Multi-agent pipeline, ERISA compliance |
| **[DevOps Copilot](./cookbook/showcase/devops_copilot_agent)** | AI-Powered DevOps Automation | Docker, Prometheus, Grafana |
| **[Deep Code Agent](./cookbook/showcase/deep_code_agent)** | Code Analysis with Sandbox | Sandbox execution, session management |

### Featured Examples

| Agent | Description | Location |
|-------|-------------|----------|
| **E-commerce Shopper** | Personal shopping with cart, preferences, recommendations | [cookbook/advanced_agent](./cookbook/advanced_agent) |
| **Flight Booking** | Travel agent with search, booking, itineraries | [cookbook/advanced_agent](./cookbook/advanced_agent) |
| **AI Due Diligence** | Investment research with web search, analysis | [cookbook/advanced_agent/ai_due_diligence_agent](./cookbook/advanced_agent/ai_due_diligence_agent) |

```bash
# Start with the basics
python cookbook/getting_started/first_agent.py

# Or explore a full production application
cd cookbook/showcase/devops_copilot_agent && make up
```

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

**OmniAgent → OmniCoreAgent Migration**: If you were using the old `OmniAgent` class, update your imports:
```python
# Old (deprecated)
from omnicoreagent import OmniAgent

# New (recommended)
from omnicoreagent import OmniCoreAgent
```
The `OmniAgent` alias still works but will be removed in a future release.

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
