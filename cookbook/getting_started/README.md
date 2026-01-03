# Getting Started with OmniCoreAgent

Welcome to the **OmniCoreAgent** learning path. This guide is designed to take you from writing your first line of code to building production-ready, autonomous agents with persistent memory and event streaming.

Typically, you should follow these examples in order, as each one introduces new concepts that build upon the last.

---

## 📚 The Learning Path

| # | File | Key Concepts Learned |
|---|------|----------------------|
| 1 | [first_agent.py](./first_agent.py) | **The Basics**: Initializing `OmniCoreAgent` and running a simple query. |
| 2 | [agent_with_models.py](./agent_with_models.py) | **Models**: Switching providers (OpenAI, Anthropic, Gemini, Groq, Ollama). |
| 3 | [agent_with_local_tools.py](./agent_with_local_tools.py) | **Local Tools**: registering Python functions as tools, type hinting, and returning structured data (`dict`). |
| 4 | [agent_with_mcp_tools.py](./agent_with_mcp_tools.py) | **MCP Integration**: Connecting to external **Model Context Protocol** servers (e.g., filesystem, git). |
| 5 | [agent_with_all_tools.py](./agent_with_all_tools.py) | **Hybrid Architecture**: Combining fast local business logic with powerful external MCP tools. |
| 6 | [agent_with_memory.py](./agent_with_memory.py) | **Persistence**: Using `MemoryRouter` to store conversation history in Redis, Postgres, MongoDB, or SQLite. |
| 7 | [agent_with_memory_switching.py](./agent_with_memory_switching.py) | **Runtime Agility**: Switching memory backends on the fly (e.g., dev -> prod) without restarting the agent. |
| 8 | [agent_with_events.py](./agent_with_events.py) | **Observability**: Using `EventRouter` to stream real-time events (thoughts, tool calls) to a UI or log. |
| 9 | [agent_with_event_switching.py](./agent_with_event_switching.py) | **Scale**: Switching event backends at runtime (e.g., In-Memory -> Redis Streams). |
| 10| [agent_configuration.py](./agent_configuration.py) | **Advanced Config**: Setting timeouts, limits, and safety guardrails. |

---

## 🚀 Quick Start

```bash
# Start from the beginning
python cookbook/getting_started/first_agent.py

# Switch models
python cookbook/getting_started/agent_with_models.py

# Progress through the tools examples
python cookbook/getting_started/agent_with_local_tools.py
```

---

## 🛠️ Prerequisites

Before running the examples, ensure you have the environment set up.

1.  **Install the Package**:
    ```bash
    pip install omnicoreagent
    ```

2.  **Environment Variables**:
    Create a `.env` file in your project root. The agent strictly requires `LLM_API_KEY`.
    ```bash
    # Required (works for OpenAI, Anthropic, Gemini, Groq, etc.)
    LLM_API_KEY=your_key_here

    # Optional (for persistence examples)
    REDIS_URL=redis://localhost:6379/0
    DATABASE_URL=postgresql://user:pass@localhost:5432/db
    MONGODB_URI=mongodb://localhost:27017/omnicoreagent
    ```

3.  **MCP Requirements** (Step 4+):
    You need Node.js installed to run the standard MCP filesystem server.
    ```bash
    npm install -g npx
    ```

---

## 📖 Deep Dive into the Examples

### 1. Your First Agent (`first_agent.py`)
This is the atomic unit of the framework. You initialize an `OmniCoreAgent` with a name, system instruction, and model configuration.
- **Why**: Understand the minimal viable setup.
- **Note**: The `.env` file is loaded automatically by the framework.

### 2. Multi-Model Support (`agent_with_models.py`)
OmniCoreAgent isn't tied to OpenAI. You can swap models just by changing the config.
- **Providers**: Supports OpenAI, Anthropic, Gemini, Groq, Mistral, Ollama (local), and more.
- **Abstraction**: The agent code remains the same; only the `model_config` dict changes.

### 3. Local Tools (`agent_with_local_tools.py`)
Agents become powerful when they can *do* things. Here, you register your own Python functions using the `@tool.register_tool` decorator.
- **Crucial**: Use Python type hints! The LLM uses these to understand inputs.
- **Best Practice**: Return a dictionary (e.g., `{"status": "success", "data": ...}`) so the agent can check for errors deterministically.

### 4. MCP Tools (`agent_with_mcp_tools.py`)
The [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) is a standard for connecting AI to systems.
- **Concept**: Instead of writing a wrapper for every API, you connect to an MCP server.
- **Transport**: This example uses `stdio` to run a local Node.js process (the filesystem server) and communicate with it directly.

### 5. Hybrid Tools (`agent_with_all_tools.py`)
Real-world agents need both.
- **Pattern**: Use MCP for generic capabilities (reading files, querying DBs) and Local Tools for specific business logic (analyzing that data, formatting reports).

### 6. Persistent Memory (`agent_with_memory.py`)
By default, memory is `in_memory` (lost on restart). This example introduces the `MemoryRouter`.
- **Backends**:
    - `redis`: Fast, key-value storage. Best for production chat cache.
    - `database`: SQL (Postgres, SQLite). Best for structured, long-term logs.
    - `mongodb`: Document store. Flexible schema.

### 7. Runtime Memory Switching (`agent_with_memory_switching.py`)
A unique feature of OmniCoreAgent. You can switch the storage backend while the agent is running.
- **Use Case**: Start in "fast mode" (in-memory) for a quick calculation, then switch to "audit mode" (Postgres) to save the final report.

### 8. Event Streaming (`agent_with_events.py`)
Don't just wait for the final answer. Watch the agent "think."
- **EventRouter**: Captures every step—thought, tool call, result, message.
- **Usage**: Use `async for event in agent.stream_events()` to build real-time UIs (like ChatGPT's typing effect).

### 9. Event Switching (`agent_with_event_switching.py`)
Similar to memory, you can route events dynamically.
- **Use Case**:
    - **Development**: Use `in_memory` to print events to the console.
    - **Production**: Switch to `redis_stream` to broadcast events to a monitoring dashboard or external service.

### 10. Advanced Configuration (`agent_configuration.py`)
Production agents need limits.
- **Cost Control**: `total_tokens_limit` stops runaways.
- **Safety**: `max_steps` prevents infinite thinking loops.
- **Reliability**: `tool_call_timeout` ensures your agent doesn't hang waiting for an API.

---

## 🚀 Next Steps

Once you've mastered these basics, explore the **Workflows** directory to see how to chain multiple agents together, or **Production** for guardrails and metrics.
