# Production

> Deploy OmniCoreAgent with confidence. Metrics, guardrails, and observability.

## Examples

| File | What You'll Learn |
|------|-------------------|
| [metrics_observability.py](./metrics_observability.py) | Track tokens, requests, response times, and costs |
| [guardrails.py](./guardrails.py) | Protect agents from prompt injection attacks |

---

## 📊 Metrics & Observability

Track everything your agent does:

```python
from omnicoreagent import OmniCoreAgent

agent = OmniCoreAgent(
    name="monitored_agent",
    system_instruction="You are a helpful assistant.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    agent_config={
        "request_limit": 100,
        "total_tokens_limit": 50000,
    },
)

# After running queries...
metrics = await agent.get_metrics()
print(f"Total requests: {metrics['total_requests']}")
print(f"Total tokens: {metrics['total_tokens']}")
print(f"Input tokens: {metrics['total_input_tokens']}")
print(f"Output tokens: {metrics['total_output_tokens']}")
```

**Available Metrics**:
- `total_requests` — Number of `agent.run()` calls
- `total_tokens` — Total tokens used (input + output)
- `total_input_tokens` — Tokens sent to LLM
- `total_output_tokens` — Tokens received from LLM

---

## 🛡️ Guardrails

Protect against prompt injection attacks:

```python
agent = OmniCoreAgent(
    name="protected_agent",
    system_instruction="You are a customer service agent.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    agent_config={
        "guardrail_config": {
            "enabled": True,
            "strict_mode": True,  # Block suspicious inputs
        },
    },
)
```

**Guardrails Detect**:
- Prompt injection attempts
- Jailbreak attempts
- System prompt extraction attempts
- Role manipulation attacks

---

## 🔧 Cost Control

Set limits to prevent runaway costs:

```python
agent_config={
    "request_limit": 100,        # Max requests per session
    "total_tokens_limit": 50000, # Max tokens before stopping
    "max_steps": 10,             # Max reasoning loops
}
```

---

## 📈 Integration with Prometheus

For full observability, see the [Showcase DevOps Copilot](../showcase/devops_copilot_agent) which includes:
- Prometheus metrics endpoint
- Grafana dashboards
- Rate limiting
- Audit logging

---

**Previous**: [Background Agents](../background_agents) — Scheduled autonomous tasks
