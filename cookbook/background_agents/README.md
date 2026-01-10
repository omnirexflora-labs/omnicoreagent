# Background Agents

> Build autonomous agents that run on schedules, monitor systems, and work without human intervention.

## What You'll Learn

| Example | Key Concepts |
|---------|--------------|
| [background_agent_example.py](./background_agent_example.py) | Scheduled tasks, interval execution, queue-based execution, agent lifecycle management |

## When to Use Background Agents

- **Monitoring**: Check system health, analyze logs, detect anomalies
- **Automation**: Process incoming data, trigger workflows, send notifications
- **Batch Processing**: Run periodic analysis, generate reports, sync data

## Quick Start

```python
from omnicoreagent import BackgroundAgent

# Create a background agent that runs every 5 minutes
agent = BackgroundAgent(
    name="monitor",
    schedule="*/5 * * * *",  # Cron syntax
    task="Check system metrics and alert if CPU > 80%"
)

await agent.start()
```

## Key Features

- **Cron-style scheduling**: Run at specific times or intervals
- **Queue-based execution**: Process tasks from a queue
- **Lifecycle management**: Start, stop, pause agents
- **Error recovery**: Automatic retries and error handling

---

**Next**: Check out [Production](../production) for metrics and guardrails.
