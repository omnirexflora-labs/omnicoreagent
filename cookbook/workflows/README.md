# Workflow Agents

> Orchestrate multiple agents for complex tasks using Sequential, Parallel, and Router patterns.

## Examples

| File | Pattern | Key Concepts |
|------|---------|--------------|
| [sequential_workflow.py](./sequential_workflow.py) | **Sequential** | Chain agents to pass results step-by-step |
| [parallel_agent.py](./parallel_agent.py) | **Parallel** | Run agents concurrently for speed |
| [router_agent.py](./router_agent.py) | **Router** | Intelligently route tasks to specialized agents |

## When to Use Each Pattern

| Pattern | Use When | Example |
|---------|----------|---------|
| **Sequential** | Tasks depend on each other | Research → Analyze → Report |
| **Parallel** | Tasks are independent | Check 3 APIs simultaneously |
| **Router** | Task type determines handler | Customer → Support vs Sales |

## Quick Start

```python
from omnicoreagent import SequentialAgent

workflow = SequentialAgent(
    name="research_pipeline",
    agents=[researcher, analyst, writer],
)

result = await workflow.run("Analyze AI trends in healthcare")
```

---

**Next**: Check out [Background Agents](../background_agents) for scheduled autonomous tasks.