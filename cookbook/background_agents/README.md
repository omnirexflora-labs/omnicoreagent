# Background Agents: "Set-and-Forget" Automation

> **Empower your AI to work autonomously.** Background Agents allow you to schedule tasks, monitor systems, and process data streams without constant human intervention—all while maintaining high resilience and detailed observability.

## Why Background Agents?

OmniCoreAgent's background system is designed for **production reliability**:
*   **🛡️ Resilient**: Built-in mechanisms for **timeouts**, **automatic retries**, and **queue backpressure** ensure your system stays stable even when tasks fail or stall.
*   **⏰ Flexible Scheduling**: Run tasks on a strict frequency (e.g., "every 5 seconds") or precise calendar schedules (e.g., "every Monday at 9 AM") using Cron syntax.
*   **👀 Observable**: Every action, error, and status change is emitted as a structured event. Inspect agent state and metrics in real-time.
*   **🎮 Full Control**: Pause, resume, update, or stop agents on the fly without restarting the entire system.

## Key Concepts

*   **BackgroundAgentManager**: The central orchestrator that manages the lifecycle (creation, scheduling, deletion) of all background agents.
*   **BackgroundOmniCoreAgent**: A specialized agent that runs an internal worker loop to process tasks from a queue.
*   **TaskRegistry**: The persistent configuration store that defines *what* an agent does and *how* it should behave (retries, timeouts, etc.).

## Quick Start

Here is the correct way to initialize and run a simple background monitor:

```python
import asyncio
from omnicoreagent import BackgroundAgentManager

async def main():
    manager = BackgroundAgentManager()

    # Define the configuration
    agent_config = {
        "agent_id": "system_monitor",
        "model_config": {"provider": "openai", "model": "gpt-4o-mini"},
        # Schedule: Run every 60 seconds
        "interval": 60,
        # Resilience: Limit queue to 5 pending tasks to prevent overload
        "queue_size": 5, 
    }
    
    # Define the task logic
    task_config = {
        "query": "Check system CPU usage and return 'High' if > 80%.",
        "timeout": 30,       # Kill task if it takes > 30s
        "max_retries": 3,    # Retry 3 times on failure
    }
    
    # Combine and create
    agent_config["task_config"] = task_config
    await manager.create_agent(agent_config)

    # Start the system
    await manager.start()
    
    # Keep running...
    try:
        while True: await asyncio.sleep(1)
    except KeyboardInterrupt:
        await manager.shutdown()

asyncio.run(main())
```

## Advanced Configuration (`TaskConfig`)

The `task_config` dictionary gives you fine-grained control over execution:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | **Required** | The natural language instruction for the agent. |
| `interval` | `int` or `str` | `3600` | **Int**: Seconds between runs.<br>**String**: Cron expression (e.g., `* * * * *` for every minute). |
| `timeout` | `int` | `300` | (Seconds) Hard limit for task execution. Raises `TimeoutError` if exceeded. |
| `max_retries` | `int` | `3` | Number of times to retry a failed task before marking it as error. |
| `retry_delay` | `int` | `60` | (Seconds) Wait time between retries. |
| `queue_size` | `int` | `100` | Max pending tasks. Incoming tasks are dropped if queue is full. |
| `session_id` | `str` | `uuid4` | Persistent ID for event tracking across multiple runs. |

## Feature Deep Dive

### 1. Robustness & Resilience
*   **Timeouts**: Prevents "zombie" tasks from blocking the worker forever.
*   **Timezone Awareness**: All internal scheduling uses UTC to prevent daylight savings issues.
*   **Graceful Shutdown**: When `manager.shutdown()` is called, agents stop accepting new tasks but allow currently running tasks to finish (up to a limit), ensuring no data corruption.

### 2. Manual Triggering
Need to run a report *right now* instead of waiting for the schedule?
```python
await manager.run_task_now("system_monitor", {"query": "Run immediate check!"})
```

### 3. Lifecycle Management
You can modify agents while the application is running:
*   `manager.pause_agent("id")`: Temporarily stop scheduling new tasks.
*   `manager.resume_agent("id")`: Resume scheduling.
*   `manager.update_agent_config("id", {"interval": 10})`: Change the schedule dynamically.
*   `manager.delete_agent("id")`: Remove the agent and clean up resources.

### 4. Observability
Get a complete snapshot of the system state:
```python
status = await manager.get_manager_status()
print(f"Active Agents: {status['running_agents']}")
```
Or stream events in real-time using the `EventRouter`.

## What to Read Next

*   **[background_agent_example.py](./background_agent_example.py)**: A comprehensive **"Kitchen Sink"** example file that demonstrates every feature listed above in a runnable script.
