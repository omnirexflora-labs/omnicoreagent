# Getting Started with OmniCoreAgent

Welcome to the **OmniCoreAgent** learning path. This guide takes you from writing your first line of code to building production-ready, autonomous agents with persistent memory, context management, and guardrails.

**Follow the examples in order** — each one builds on the concepts from the previous.

---

## 📚 The Learning Path


| # | File | Key Concepts |
|---|------|--------------|
| 1 | [first_agent.py](./first_agent.py) | **The Basics**: Initialize `OmniCoreAgent` and run a simple query |
| 2 | [agent_with_models.py](./agent_with_models.py) | **Models**: Switch providers (OpenAI, Anthropic, Gemini, Groq, Ollama) |
| 3 | [agent_with_local_tools.py](./agent_with_local_tools.py) | **Local Tools**: Register Python functions as agent tools |
| 5 | [agent_with_all_tools.py](./agent_with_all_tools.py) | **Hybrid Architecture**: Combine local + MCP tools |
| 6 | [agent_with_memory.py](./agent_with_memory.py) | **Persistence**: Store conversations in Redis, Postgres, MongoDB |
| 7 | [agent_with_memory_switching.py](./agent_with_memory_switching.py) | **Runtime Switching**: Change memory backends on the fly |
| 8 | [agent_with_events.py](./agent_with_events.py) | **Observability**: Stream real-time events to UI |
| 9 | [agent_with_event_switching.py](./agent_with_event_switching.py) | **Scale**: Switch event backends at runtime |
| 10 | [agent_with_context_management.py](./agent_with_context_management.py) | **🆕 Context Management**: Handle infinitely long conversations |
| 11 | [agent_with_guardrails.py](./agent_with_guardrails.py) | **🆕 Guardrails**: Protect against prompt injection |
| 12 | [agent_with_metrics.py](./agent_with_metrics.py) | **🆕 Metrics**: Track tokens, requests, and costs |
| 13 | [agent_with_sub_agents.py](./agent_with_sub_agents.py) | **🆕 Sub-Agents**: Build multi-agent systems |
| 14 | [agent_configuration.py](./agent_configuration.py) | **Advanced Config**: All settings in one place |
=======
| # | File | Key Concepts Learned |
|---|------|----------------------|
| 1 | [first_agent.py](./first_agent.py) | **The Basics**: Initializing `OmniCoreAgent` and running a simple query. |
| 2 | [agent_with_models.py](./agent_with_models.py) | **Models**: Switching providers (OpenAI, Anthropic, Gemini, Groq, Ollama). |
| 3 | [agent_with_local_tools.py](./agent_with_local_tools.py) | **Local Tools**: registering Python functions as tools, type hinting, and returning structured data (`dict`). |
# 1. Install
pip install omnicoreagent

# 2. Set your API key
echo "LLM_API_KEY=your_key" > .env

# 3. Run your first agent
python cookbook/getting_started/first_agent.py
```

---

## 🎯 "I just want to..."

| Goal | Example |
|------|---------|
| Build my first agent | [first_agent.py](./first_agent.py) |
| Use a different LLM (Claude, Gemini, etc.) | [agent_with_models.py](./agent_with_models.py) |
| Give my agent tools | [agent_with_local_tools.py](./agent_with_local_tools.py) |
| Connect to MCP servers | [agent_with_mcp_tools.py](./agent_with_mcp_tools.py) |
| Save conversation history | [agent_with_memory.py](./agent_with_memory.py) |
| Handle long conversations | [agent_with_context_management.py](./agent_with_context_management.py) |
| Protect against attacks | [agent_with_guardrails.py](./agent_with_guardrails.py) |
| Track usage and costs | [agent_with_metrics.py](./agent_with_metrics.py) |
| Build multi-agent systems | [agent_with_sub_agents.py](./agent_with_sub_agents.py) |

---

## 🛠️ Prerequisites

```bash
# Required
pip install omnicoreagent

# Create .env file
LLM_API_KEY=your_key_here

# Optional (for persistence examples)
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql://user:pass@localhost:5432/db
MONGODB_URI=mongodb://localhost:27017/omnicoreagent
```

---

## 📖 Key Concepts

### Memory with Summarization
```python
"memory_config": {
    "mode": "sliding_window",
    "value": 50,
    "summary": {
        "enabled": True,
        "retention_policy": "summarize"
    }
}
```
Old messages are summarized, not lost.

### Context Management
```python
"context_management": {
    "enabled": True,
    "mode": "token_budget",  # or "sliding_window"
    "value": 100000,
    "threshold_percent": 75,
    "strategy": "summarize_and_truncate",
    "preserve_recent": 6
}
```
Conversations can run forever without hitting token limits.

#### Choosing the Right Mode

| Mode | Triggers When | Best For |
|------|---------------|----------|
| `sliding_window` | Message count exceeds `value` | Conversational agents with short messages |
| `token_budget` | Token count exceeds `value × threshold%` | Tool-heavy agents with large responses |

**Trade-offs:**

| | `sliding_window` | `token_budget` |
|--|------------------|----------------|
| **Token efficiency** | ✅ Better (smaller contexts) | ⚠️ Larger contexts per call |
| **Predictability** | ✅ Consistent behavior | Depends on message size |
| **Large messages** | ⚠️ Can exceed limits | ✅ Handles safely |
| **Cost** | ✅ Lower cumulative | Higher cumulative |

**Recommendations:**
- **Chatbots / Q&A agents**: Use `sliding_window` with `value: 10-20`
- **Tool-heavy agents** (APIs, web scraping): Use `token_budget` with `value: 8000-16000`
- **Mixed workloads**: Use `token_budget` with lower threshold (50-60%)

### Tool Response Offloading
```python
"tool_offload": {
    "enabled": True,
    "threshold_tokens": 500,  # Offload if response > 500 tokens
    "max_preview_tokens": 150,  # Show first 150 tokens in context
    "storage_dir": ".omnicoreagent_artifacts"
}
```
Large tool responses are automatically saved to files, with only a preview in context.

**How it works:**
1. Tool returns large response (e.g., web search with 50 results)
2. Response saved to `.omnicoreagent_artifacts/` 
3. Agent sees preview + file reference in context
4. Agent uses `read_artifact()` tool to get full content when needed

**Token savings example:**
| Tool Response | Without Offloading | With Offloading |
|---------------|-------------------|-----------------|
| Web search (50 results) | ~10,000 tokens | ~200 tokens |
| Large API response | ~5,000 tokens | ~150 tokens |
| File read (1000 lines) | ~8,000 tokens | ~200 tokens |

**Agent gets 4 built-in tools:**
- `read_artifact(artifact_id)` - Read full content
- `tail_artifact(artifact_id, lines)` - Read last N lines
- `search_artifact(artifact_id, query)` - Search within artifact
- `list_artifacts()` - List all offloaded artifacts

> 💡 *Inspired by Cursor's "dynamic context discovery" and Anthropic's context engineering patterns*

### Guardrails
```python
"guardrail_config": {
    "enabled": True,
    "strict_mode": True
}
```
Built-in protection against prompt injection attacks.

### Metrics
```python
metrics = await agent.get_metrics()
# Returns: total_requests, total_tokens, total_input_tokens, total_output_tokens
```
Track usage for cost control and monitoring.

---

## 🚀 Next Steps

- **[Workflows](../workflows)**: Chain agents together (Sequential, Parallel, Router)
- **[Background Agents](../background_agents)**: Scheduled autonomous tasks
- **[Production](../production)**: Prometheus metrics and observability
- **[Showcase](../showcase)**: Full production applications
