# 🍳 OmniCoreAgent Cookbook

> **Copy, paste, run.** Build agents that actually work.

## 🚀 Where to Start

**New to OmniCoreAgent?** Start with [getting_started](./getting_started) — progressive examples from "Hello World" to production deployments.

**Know what you're building?** Find your use case below.

**Just want to see it work?**

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

---

## 📚 Build by Use Case

### 🤖 I want to build my first agent
→ **[getting_started](./getting_started)** — Basic agents, tools, memory, events, and configuration.

### 🛠️ I want to use custom tools
→ **[getting_started/agent_with_local_tools.py](./getting_started/agent_with_local_tools.py)** — Register Python functions as tools.

### 🔌 I want to connect MCP servers
→ **[getting_started/agent_with_mcp_tools.py](./getting_started/agent_with_mcp_tools.py)** — Connect to external MCP servers.

### 👥 I want agents working together
→ **[workflows](./workflows)** — Sequential, Parallel, and Router agents.

### 🚁 I want agents running autonomously
→ **[background_agents](./background_agents)** — Scheduled tasks and queue-based execution.

### 🏭 I want to build a real application
→ **[advanced_agent](./advanced_agent)** — E-commerce, flight booking, customer support, due diligence.

### 🏆 I want to see full production applications
→ **[showcase](./showcase)** — Complete applications with Docker, Prometheus, Grafana, and more.

### 🚀 I want to run in production
→ **[production](./production)** — Metrics, guardrails, and observability.

---

## 📂 Directory Structure

```
cookbook/
├── getting_started/     # Start here! Progressive learning path
├── workflows/           # Multi-agent orchestration patterns
├── background_agents/   # Scheduled and autonomous agents
├── advanced_agent/      # Real-world application examples
├── showcase/            # 🆕 Full production applications
└── production/          # Production-ready configurations
```

---

## 🏆 Showcase Applications

| Application | Description | Features |
|-------------|-------------|----------|
| [OmniAudit](./showcase/omniavelis) | Healthcare Claims Audit System | Multi-agent pipeline, ERISA compliance |
| [DevOps Copilot](./showcase/devops_copilot_agent) | AI-Powered DevOps Automation | Docker, Prometheus, Grafana |
| [Deep Code Agent](./showcase/deep_code_agent) | Code Analysis with Sandbox | Sandbox execution, session management |

---

## 🎯 Featured Examples

| Example | What You'll Build |
|---------|-------------------|
| [E-commerce Shopper](./advanced_agent/e_commerce_personal_shopper_agent.py) | Personal shopping assistant with cart, preferences, recommendations |
| [Flight Booking](./advanced_agent/flightBooking_agent.py) | Travel agent with search, booking, and itinerary management |
| [Customer Support](./advanced_agent/real_time_customer_support_agent.py) | Support agent with ticket handling and escalation |
| [Due Diligence](./advanced_agent/ai_due_diligence_agent/) | Investment research with web search, analysis, and reporting |

---

## 🤝 Contributing

Want to add a cookbook example? See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.
