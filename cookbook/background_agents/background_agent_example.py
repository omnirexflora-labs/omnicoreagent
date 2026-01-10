#!/usr/bin/env python3
"""
Background Agent "Kitchen Sink" Example
=======================================
This example demonstrates EVERY capability of the BackgroundAgentManager:
1. Interval Scheduling
2. Cron Scheduling
3. Manual/On-Demand Tasks
4. Lifecycle Management (Pause, Resume, Stop, Delete)
5. Runtime Configuration Updates
6. Deep Observability (Status & Metrics)
"""

import asyncio
import os
import random
import time
import logging
from datetime import datetime, timezone

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("background_demo")

from omnicoreagent import (
    BackgroundAgentManager,
    MemoryRouter,
    EventRouter,
    ToolRegistry,
)

# --- Tools ---

async def create_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register_tool("system_check")
    def system_check(scope: str = "basic") -> str:
        """Simulate checking system stats."""
        val = random.randint(1, 100)
        return f"System ({scope}) OK: {val}%"

    @registry.register_tool("generate_report")
    def generate_report(topic: str) -> str:
        """Simulate generating a report."""
        return f"Report generated for {topic} at {datetime.now().time()}"

    return registry

# --- Main Demo ---

async def main():
    logger.info("🚀 Starting Background Agent 'Kitchen Sink' Demo")
    
    # 1. Initialize Components
    manager = BackgroundAgentManager()
    tool_registry = await create_tool_registry()
    
    try:
        # 2. Create Agents
        
        # Agent A: High Frequency Monitor (Interval = 2s)
        logger.info("\n--- 1. Creating Interval Agent (Monitor) ---")
        monitor_config = {
            "agent_id": "monitor_agent",
            "model_config": {"provider": "openai", "model": "gpt-4o-mini"},
            "local_tools": tool_registry,
            "interval": 2, 
            "queue_size": 10
        }
        monitor_task = {
            "query": "Run system_check(scope='basic')",
            "interval": 2
        }
        # Create agent accepts config with task_config separate or embedded
        # For clarity, we'll embed it here as allowed by manager.create_agent
        monitor_config["task_config"] = monitor_task
        await manager.create_agent(monitor_config)

        # Agent B: Cron Reporter (Every 5 seconds for demo purposes)
        # Note: In real world, cron is like "0 9 * * *" (Daily at 9am)
        # APScheduler CronTrigger supports 5 fields: minute, hour, day, month, day_of_week
        # We'll use a fast cron for demo: "* * * * *" (Every minute) -> actually lets just use interval for speed
        # But to demonstrate CRON syntax support in the backend:
        logger.info("\n--- 2. Creating Cron Agent (Reporter) ---")
        reporter_config = {
            "agent_id": "cron_agent",
            "system_instruction": "You are a diligent reporter. Always generate concise status reports.",
            "model_config": {"provider": "openai", "model": "gpt-4o"},
            "local_tools": tool_registry,
            # "mcp_tools": ["filesystem"], # Example: Connect to MCP servers
            # Pass a cron string as 'interval' to use CronTrigger
            # Warning: Standard cron is minute-resolution. For demo speed we might need interval,
            # but let's try to stick to the 'kitchen sink' promise and use a real cron string.
            # This means it will run at the next minute boundary.
            "interval": "* * * * *" 
        }
        reporter_task = {
            "query": "Generate a daily report.",
            # "interval" in task_config takes precedence
            "interval": "* * * * *",
            "max_retries": 5,      # Retry up to 5 times on failure
            "retry_delay": 10,     # Wait 10s between retries
            "session_id": "daily-report-session" # Persistent session ID
        }
        reporter_config["task_config"] = reporter_task
        await manager.create_agent(reporter_config)

        # Start everything
        await manager.start()
        logger.info("✅ Manager started.")

        # 3. Observe Basic Operation
        logger.info("\n--- 3. Observing Operation (5s) ---")
        await asyncio.sleep(5)

        # 4. Manual Task Trigger
        logger.info("\n--- 4. Manual Task Trigger ---")
        logger.info("Injecting an urgent task into monitor_agent...")
        await manager.run_task_now(
            "monitor_agent", 
            {"query": "Run system_check(scope='FULL_SCAN')", "timeout": 5}
        )
        await asyncio.sleep(2)

        # 5. Lifecycle Management: Pause & Resume
        logger.info("\n--- 5. Pause & Resume ---")
        logger.info("Pausing monitor_agent...")
        await manager.pause_agent("monitor_agent")
        
        logger.info("Monitor paused. Waiting 3s (should see no monitor logs)...")
        await asyncio.sleep(3)
        
        logger.info("Resuming monitor_agent...")
        await manager.resume_agent("monitor_agent")
        await asyncio.sleep(3)

        # 6. Runtime Configuration Update
        logger.info("\n--- 6. Runtime Config Update ---")
        logger.info("Updating monitor_agent to run FASTER (interval=1s)...")
        new_task_config = {
            "query": "Run system_check(scope='turbo')",
            "interval": 1
        }
        await manager.update_task_config("monitor_agent", new_task_config)
        # We also need to tell manager to re-schedule it if we changed interval in task config
        # The update_task_config only updates registry.
        # To apply config changes to scheduling, we normally use update_agent_config or restart.
        # Let's use the explicit update_agent_config which re-schedules.
        await manager.update_agent_config("monitor_agent", {"interval": 1})
        
        await asyncio.sleep(3)

        # 7. Lifecycle Management: Delete
        logger.info("\n--- 7. Delete Agent ---")
        logger.info("Deleting cron_agent...")
        await manager.delete_agent("cron_agent")
        
        status = await manager.get_manager_status()
        logger.info(f"Agents remaining: {status['agents']}")

        # 8. Status & Metrics
        logger.info("\n--- 8. Status & Metrics ---")
        metrics = await manager.get_all_metrics()
        for agent_id, m in metrics.items():
            logger.info(f"Agent {agent_id}: Runs={m['run_count']}, Errors={m['error_count']}")

        # 9. Shutdown
        logger.info("\n--- 9. Shutdown ---")
        await manager.shutdown()
        logger.info("✅ Demo Complete")

    except Exception as e:
        logger.error(f"❌ Demo Failed: {e}")
        await manager.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
