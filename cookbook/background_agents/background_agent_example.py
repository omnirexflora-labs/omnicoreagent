#!/usr/bin/env python3
"""
Background Agent Example - Demonstrates the new standardized TaskRegistry approach.
"""

import asyncio
import os


# TOP-LEVEL IMPORTS (Recommended for most use cases)
from omnicoreagent import (
    BackgroundAgentManager,
    MemoryRouter,
    EventRouter,
    ToolRegistry,
    logger,
)

# LOW-LEVEL IMPORTS (Alternative approach for advanced users)
# from omnicoreagent.omni_agent.background_agent.background_agent_manager import (
#     BackgroundAgentManager,
# )
# from omnicoreagent.core.memory_store.memory_router import MemoryRouter
# from omnicoreagent.core.events.event_router import EventRouter
# from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
# from omnicoreagent.core.utils import logger


async def create_tool_registry() -> ToolRegistry:
    """Create and configure ToolRegistry with local tools."""
    tool_registry = ToolRegistry()

    # Register some local tools for background agents
    @tool_registry.register_tool("file_monitor")
    def monitor_files(directory: str = "/tmp") -> str:
        """Monitor files in a directory."""

        try:
            if not os.path.exists(directory):
                return f"Directory {directory} does not exist"

            files = os.listdir(directory)
            file_count = len(files)
            sample_files = files[:5] if files else []

            return (
                f"Found {file_count} files in {directory}. Sample files: {sample_files}"
            )
        except Exception as e:
            return f"Error monitoring directory {directory}: {str(e)}"

    @tool_registry.register_tool("system_status")
    def get_system_status() -> str:
        """Get realistic system status information."""
        import time
        import random

        # Generate realistic system metrics
        cpu_usage = random.uniform(5.0, 85.0)
        memory_usage = random.uniform(20.0, 90.0)
        disk_usage = random.uniform(30.0, 95.0)
        uptime_hours = random.randint(1, 720)  # 1 hour to 30 days
        active_processes = random.randint(50, 300)

        # Add some system alerts based on thresholds
        alerts = []
        if cpu_usage > 80:
            alerts.append("High CPU usage detected")
        if memory_usage > 85:
            alerts.append("High memory usage detected")
        if disk_usage > 90:
            alerts.append("Disk space running low")

        status_report = f"""System Status Report:
• CPU Usage: {cpu_usage:.1f}%
• Memory Usage: {memory_usage:.1f}%
• Disk Usage: {disk_usage:.1f}%
• System Uptime: {uptime_hours} hours
• Active Processes: {active_processes}
• Timestamp: {time.strftime("%Y-%m-%d %H:%M:%S")}"""

        if alerts:
            status_report += f"\n\n⚠️  Alerts: {'; '.join(alerts)}"

        return status_report

    @tool_registry.register_tool("log_analyzer")
    def analyze_logs(log_file: str = "/var/log/syslog") -> str:
        """Analyze log files for patterns."""
        import random
        import time

        try:
            # Simulate log analysis with realistic data
            total_lines = random.randint(1000, 50000)
            error_count = random.randint(0, 50)
            warning_count = random.randint(5, 200)
            critical_count = random.randint(0, 10)

            # Generate some realistic log patterns
            log_patterns = []
            if error_count > 0:
                log_patterns.append(
                    f"Authentication failures: {random.randint(0, error_count // 2)}"
                )
            if warning_count > 0:
                log_patterns.append(
                    f"Service restarts: {random.randint(0, warning_count // 3)}"
                )
            if critical_count > 0:
                log_patterns.append(f"System errors: {critical_count}")

            analysis = f"""Log Analysis Report:
• Total Log Lines: {total_lines:,}
• Error Count: {error_count}
• Warning Count: {warning_count}
• Critical Count: {critical_count}
• Analysis Time: {time.strftime("%H:%M:%S")}

Patterns Found:"""

            if log_patterns:
                for pattern in log_patterns:
                    analysis += f"\n• {pattern}"
            else:
                analysis += "\n• No significant patterns detected"

            return analysis

        except Exception as e:
            return f"Error analyzing logs: {str(e)}"

    @tool_registry.register_tool("directory_info")
    def get_directory_info(directory: str = "/tmp") -> str:
        """Get detailed information about a directory."""
        import time

        try:
            if not os.path.exists(directory):
                return f"Directory {directory} does not exist"

            stats = os.stat(directory)
            files = os.listdir(directory)
            file_count = len(files)

            # Get some basic file info
            file_types = {}
            total_size = 0
            for file in files[:20]:  # Check first 20 files
                file_path = os.path.join(directory, file)
                try:
                    if os.path.isfile(file_path):
                        file_types["files"] = file_types.get("files", 0) + 1
                        total_size += os.path.getsize(file_path)
                    elif os.path.isdir(file_path):
                        file_types["directories"] = file_types.get("directories", 0) + 1
                except:  # noqa: E722
                    pass

            return f"""Directory Analysis: {directory}
• Total Items: {file_count}
• Files: {file_types.get("files", 0)}
• Directories: {file_types.get("directories", 0)}
• Total Size: {total_size:,} bytes
• Last Modified: {time.ctime(stats.st_mtime)}"""
        except Exception as e:
            return f"Error getting directory info: {str(e)}"

    @tool_registry.register_tool("simple_calculator")
    def calculate(operation: str, a: float, b: float = 0) -> str:
        """Perform simple mathematical calculations.

        Args:
            operation: "add", "subtract", "multiply", "divide"
            a: First number
            b: Second number (default 0)
        """
        try:
            if operation.lower() == "add":
                result = a + b
                operation_name = "addition"
            elif operation.lower() == "subtract":
                result = a - b
                operation_name = "subtraction"
            elif operation.lower() == "multiply":
                result = a * b
                operation_name = "multiplication"
            elif operation.lower() == "divide":
                if b == 0:
                    return "Error: Division by zero"
                result = a / b
                operation_name = "division"
            else:
                return f"Unknown operation: '{operation}'. Supported: 'add', 'subtract', 'multiply', 'divide'"

            return f"Result of {operation_name}({a}, {b}): {result}"
        except Exception as e:
            return f"Calculation error: {str(e)}"

    logger.info("Created ToolRegistry with local tools")
    return tool_registry


async def create_agents(manager: BackgroundAgentManager, tool_registry: ToolRegistry):
    """Create background agents with tasks using TaskRegistry approach."""

    # Agent 1: File Monitor Agent
    file_monitor_config = {
        "agent_id": "file_monitor_agent",
        "system_instruction": "You are a file system monitoring agent. Monitor files and report changes. Use the available tools to check directories and files.",
        "model_config": {
            "model": "gpt-4.1",
            "temperature": 0.6,
            "provider": "openai",
            "top_p": 0.9,
            "max_context_length": 50000,
        },
        "mcp_tools": [
            {
                "name": "filesystem",
                "transport_type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            }
        ],
        "local_tools": tool_registry,  # Use our ToolRegistry
        "agent_config": {
            "max_steps": 10,
            "tool_call_timeout": 30,
            "request_limit": 1000,
            "memory_config": {"mode": "token_budget", "value": 5000},
        },
        "interval": 30,  # Run every 30 seconds for testing
        "max_retries": 2,
        "retry_delay": 30,
        "debug": True,
    }

    # Agent 2: System Monitor Agent
    system_monitor_config = {
        "agent_id": "system_monitor_agent",
        "system_instruction": "You are a system monitoring agent. Check system status and provide basic information. Use the available tools to get system information.",
        "model_config": {
            "model": "gpt-4.1",
            "temperature": 0.6,
            "provider": "openai",
            "top_p": 0.9,
            "max_context_length": 50000,
        },
        "mcp_tools": [],
        "local_tools": tool_registry,
        "agent_config": {
            "max_steps": 10,
            "tool_call_timeout": 30,
            "request_limit": 1000,
            "memory_config": {"mode": "token_budget", "value": 5000},
        },
        "interval": 45,  # Run every 45 seconds for testing
        "max_retries": 3,
        "retry_delay": 60,
        "debug": True,
    }

    # Agent 3: Calculator Agent
    calculator_config = {
        "agent_id": "calculator_agent",
        "system_instruction": "You are a calculator agent. Perform mathematical calculations and provide results. Use the simple_calculator tool for basic operations.",
        "model_config": {
            "model": "gpt-4.1",
            "temperature": 0.6,
            "provider": "openai",
            "top_p": 0.9,
            "max_context_length": 50000,
        },
        "mcp_tools": [],
        "local_tools": tool_registry,
        "agent_config": {
            "max_steps": 10,
            "tool_call_timeout": 30,
            "request_limit": 1000,
            "memory_config": {"mode": "token_budget", "value": 5000},
        },
        "interval": 60,  # Run every 60 seconds for testing
        "max_retries": 2,
        "retry_delay": 45,
        "debug": True,
    }

    # Create agents with tasks (task_config gets moved to TaskRegistry automatically)
    agents_data = [
        {
            "config": file_monitor_config,
            "task_config": {
                "query": "Check the /tmp directory and provide information about files and directories. Use the file_monitor and directory_info tools to analyze the directory contents.",
                "description": "File system monitoring task",
            },
        },
        {
            "config": system_monitor_config,
            "task_config": {
                "query": "Check system status and provide basic system information. Use the system_status tool to get current system metrics.",
                "description": "System monitoring task",
            },
        },
        {
            "config": calculator_config,
            "task_config": {
                "query": "Perform some sample calculations. Use the simple_calculator tool to add 15 and 25 (operation='add'), then multiply 7 and 8 (operation='multiply').",
                "description": "Calculator task",
            },
        },
    ]

    created_agents = []

    for agent_data in agents_data:
        try:
            # Combine config and task_config (task_config will be moved to TaskRegistry)
            full_config = agent_data["config"].copy()
            full_config["task_config"] = agent_data["task_config"]

            # Create agent (task_config gets automatically moved to TaskRegistry)
            result = manager.create_agent(full_config)

            created_agents.append(
                {
                    "agent_id": result["agent_id"],
                    "session_id": result["session_id"],
                    "event_stream_info": result["event_stream_info"],
                    "task_query": result["task_query"],
                }
            )

            logger.info(f"✅ Created agent: {result['agent_id']}")
            logger.info(f"   Session ID: {result['session_id']}")
            logger.info(f"   Task Query: {result['task_query']}")

        except Exception as e:
            logger.error(f"❌ Failed to create agent: {e}")

    return created_agents


async def demonstrate_task_management(manager: BackgroundAgentManager):
    """Demonstrate TaskRegistry operations."""

    logger.info("\n" + "=" * 60)
    logger.info("DEMONSTRATING TASK REGISTRY OPERATIONS")
    logger.info("=" * 60)

    # 1. List all registered tasks
    logger.info("\n📋 All registered tasks:")
    all_tasks = manager.list_tasks()
    for task_id in all_tasks:
        task_config = manager.get_task_config(task_id)
        logger.info(f"   {task_id}: {task_config.get('description', 'No description')}")

    # 2. Update a task
    logger.info("\n🔄 Updating task for file_monitor_agent:")
    updated_task = {
        "query": "Enhanced file monitoring: Check both /tmp and /var/log directories for changes and report detailed findings.",
        "description": "Enhanced file system monitoring task",
        "enhanced_mode": True,
    }

    success = manager.update_task_config("file_monitor_agent", updated_task)
    if success:
        logger.info("   ✅ Task updated successfully")
        updated_config = manager.get_task_config("file_monitor_agent")
        logger.info(f"   New query: {updated_config['query']}")
    else:
        logger.error("   ❌ Failed to update task")

    # 3. Add a new task for an existing agent
    logger.info("\n➕ Adding new task for system_monitor_agent:")
    new_task = {
        "query": "Perform detailed system analysis including disk usage and network status.",
        "description": "Detailed system analysis task",
        "analysis_level": "detailed",
    }

    success = manager.register_task("system_monitor_agent", new_task)
    if success:
        logger.info("   ✅ New task registered successfully")
        # Note: The agent will still use the original task until we update it
        current_config = manager.get_task_config("system_monitor_agent")
        logger.info(f"   Current task: {current_config['description']}")
    else:
        logger.error("   ❌ Failed to register new task")

    # 4. Show task registry status
    logger.info("\n📊 Task Registry Status:")
    logger.info(f"   Total registered tasks: {len(manager.list_tasks())}")
    for task_id in manager.list_tasks():
        exists = manager.task_registry.exists(task_id)
        logger.info(f"   {task_id}: {'✅' if exists else '❌'}")


async def demonstrate_event_streaming(manager: BackgroundAgentManager, created_agents):
    """Demonstrate event streaming for background agents."""

    logger.info("\n" + "=" * 60)
    logger.info("DEMONSTRATING EVENT STREAMING")
    logger.info("=" * 60)

    # Get all event streaming information
    all_event_info = manager.get_all_event_info()

    logger.info("\n📡 Event Streaming Information:")
    logger.info(f"   Shared Event Store: {all_event_info['shared_event_store']}")
    logger.info(f"   Shared Memory Store: {all_event_info['shared_memory_store']}")

    for agent_id, event_info in all_event_info["agents"].items():
        logger.info(f"\n   Agent: {agent_id}")
        logger.info(f"   Session ID: {event_info['session_id']}")
        logger.info(f"   Event Store Type: {event_info['event_store_type']}")
        logger.info(f"   Event Store Available: {event_info['event_store_available']}")

    # Demonstrate event streaming for one agent
    if created_agents:
        agent_id = created_agents[0]["agent_id"]
        session_id = created_agents[0]["session_id"]

        logger.info(f"\n🎯 Streaming events for agent: {agent_id}")
        logger.info(f"   Session ID: {session_id}")

        # Start event streaming in background
        async def stream_events():
            try:
                agent = manager.get_agent(agent_id)
                if agent:
                    async for event in agent.stream_events(session_id):
                        logger.info(f"   📡 Event: {event.type} - {event.payload}")
            except Exception as e:
                logger.error(f"   ❌ Event streaming error: {e}")

        # Start streaming task
        streaming_task = asyncio.create_task(stream_events())

        # Let it run for a few seconds
        await asyncio.sleep(5)

        # Cancel streaming
        streaming_task.cancel()
        try:
            await streaming_task
        except asyncio.CancelledError:
            pass

        logger.info("   ✅ Event streaming demonstration completed")


async def demonstrate_agent_management(manager: BackgroundAgentManager):
    """Demonstrate agent management operations."""

    logger.info("\n" + "=" * 60)
    logger.info("DEMONSTRATING AGENT MANAGEMENT")
    logger.info("=" * 60)

    # 1. Get manager status
    status = manager.get_manager_status()
    logger.info("\n📊 Manager Status:")
    logger.info(f"   Running: {status['manager_running']}")
    logger.info(f"   Total Agents: {status['total_agents']}")
    logger.info(f"   Total Tasks: {status['total_tasks']}")
    logger.info(f"   Scheduler Running: {status['scheduler_running']}")

    # 2. Get individual agent statuses
    logger.info("\n🤖 Agent Statuses:")
    for agent_id in manager.list_agents():
        agent_status = manager.get_agent_status(agent_id)
        if agent_status:
            logger.info(f"   {agent_id}:")
            logger.info(f"     Running: {agent_status['is_running']}")
            logger.info(f"     Has Task: {agent_status['has_task']}")
            logger.info(f"     Run Count: {agent_status['run_count']}")
            logger.info(f"     Error Count: {agent_status['error_count']}")
            logger.info(f"     Scheduled: {agent_status['scheduled']}")

    # 3. Demonstrate pausing/resuming
    logger.info("\n⏸️  Pausing file_monitor_agent:")
    try:
        manager.pause_agent("file_monitor_agent")
        logger.info("   ✅ Agent paused")

        # Check status
        status = manager.get_agent_status("file_monitor_agent")
        logger.info(f"   Scheduled: {status['scheduled']}")

        # Resume
        logger.info("\n▶️  Resuming file_monitor_agent:")
        manager.resume_agent("file_monitor_agent")
        logger.info("   ✅ Agent resumed")

    except Exception as e:
        logger.error(f"   ❌ Error: {e}")

    # 4. Get metrics
    logger.info("\n📈 Agent Metrics:")
    metrics = manager.get_all_metrics()
    for agent_id, agent_metrics in metrics.items():
        logger.info(f"   {agent_id}:")
        logger.info(f"     Run Count: {agent_metrics['run_count']}")
        logger.info(f"     Error Count: {agent_metrics['error_count']}")
        logger.info(f"     Last Run: {agent_metrics['last_run']}")
        logger.info(f"     Has Task: {agent_metrics['has_task']}")


async def demonstrate_runtime_task_operations(manager: BackgroundAgentManager):
    """Demonstrate runtime task operations via API simulation."""

    logger.info("\n" + "=" * 60)
    logger.info("DEMONSTRATING RUNTIME TASK OPERATIONS")
    logger.info("=" * 60)

    # Simulate API calls for runtime task management

    # 1. Add a new task for an existing agent
    logger.info("\n➕ Adding new task via 'API':")
    new_task_config = {
        "query": "Perform emergency system check and report critical issues immediately. Use system_status and log_analyzer tools.",
        "description": "Emergency system check task",
        "priority": "high",
        "emergency_mode": True,
    }

    success = manager.register_task("system_monitor_agent", new_task_config)
    logger.info(f"   Task registration: {'✅ Success' if success else '❌ Failed'}")

    # 2. Update existing task with better calculator operations
    logger.info("\n🔄 Updating calculator task via 'API':")
    updated_calculator_task = {
        "query": "Perform advanced calculations. Use simple_calculator tool to: 1) add 25 and 75 (operation='add'), 2) multiply 12 and 8 (operation='multiply'), 3) subtract 100 from 250 (operation='subtract').",
        "description": "Advanced calculator task with multiple operations",
        "calculation_level": "advanced",
        "operations": ["add", "multiply", "subtract"],
    }

    success = manager.update_task_config("calculator_agent", updated_calculator_task)
    logger.info(
        f"   Calculator task update: {'✅ Success' if success else '❌ Failed'}"
    )

    # 3. Update file monitor task with enhanced monitoring
    logger.info("\n🔄 Updating file monitor task via 'API':")
    updated_file_task = {
        "query": "Enhanced file system monitoring: Check both /tmp and /var/log directories. Use file_monitor and directory_info tools to provide detailed analysis of both directories.",
        "description": "Enhanced file system monitoring task",
        "enhanced_mode": True,
        "directories": ["/tmp", "/var/log"],
    }

    success = manager.update_task_config("file_monitor_agent", updated_file_task)
    logger.info(
        f"   File monitor task update: {'✅ Success' if success else '❌ Failed'}"
    )

    # 4. Show final task registry state
    logger.info("\n📋 Final Task Registry State:")
    all_tasks = manager.list_tasks()
    for task_id in all_tasks:
        task_config = manager.get_task_config(task_id)
        logger.info(f"   {task_id}: {task_config.get('description', 'No description')}")
        logger.info(f"      Query: {task_config.get('query', 'No query')[:100]}...")

    # 5. Demonstrate task removal
    logger.info("\n🗑️  Removing task via 'API':")
    success = manager.remove_task("file_monitor_agent")
    logger.info(f"   Task removal: {'✅ Success' if success else '❌ Failed'}")

    # 6. Show updated task registry state
    logger.info("\n📋 Updated Task Registry State:")
    all_tasks = manager.list_tasks()
    for task_id in all_tasks:
        task_config = manager.get_task_config(task_id)
        logger.info(f"   {task_id}: {task_config.get('description', 'No description')}")


async def main():
    """Main function demonstrating the background agent system."""

    logger.info("🚀 Starting Background Agent Example")
    logger.info("=" * 60)

    try:
        # 1. Initialize components
        logger.info("\n📦 Initializing Components...")

        # Create memory and event routers
        memory_router = MemoryRouter(memory_store_type="in_memory")
        event_router = EventRouter(event_store_type="in_memory")

        # Create tool registry
        tool_registry = await create_tool_registry()

        # Create background agent manager
        manager = BackgroundAgentManager(
            memory_router=memory_router, event_router=event_router
        )

        logger.info("✅ Components initialized")

        # 2. Create agents with tasks
        logger.info("\n🤖 Creating Background Agents...")
        created_agents = await create_agents(manager, tool_registry)

        if not created_agents:
            logger.error("❌ No agents were created successfully")
            return

        logger.info(f"✅ Created {len(created_agents)} agents")

        # 3. Demonstrate TaskRegistry operations
        await demonstrate_task_management(manager)

        # 4. Demonstrate event streaming
        await demonstrate_event_streaming(manager, created_agents)

        # 5. Demonstrate agent management
        await demonstrate_agent_management(manager)

        # 6. Demonstrate runtime task operations
        await demonstrate_runtime_task_operations(manager)

        # 7. Start the manager
        logger.info("\n🚀 Starting Background Agent Manager...")
        manager.start()

        # 8. Show final status
        logger.info("\n📊 Final Manager Status:")
        final_status = manager.get_manager_status()
        logger.info(f"   Manager Running: {final_status['manager_running']}")
        logger.info(f"   Total Agents: {final_status['total_agents']}")
        logger.info(f"   Scheduler Running: {final_status['scheduler_running']}")

        # 9. Set up real-time event monitoring for all agents
        logger.info("\n🎯 Setting up real-time event monitoring...")
        monitoring_tasks = []

        for agent_data in created_agents:
            agent_id = agent_data["agent_id"]
            session_id = agent_data["session_id"]

            # Create monitoring task for each agent
            async def monitor_agent_events(agent_id: str, session_id: str):
                try:
                    agent = manager.get_agent(agent_id)
                    if agent:
                        logger.info(
                            f"📡 Starting event monitoring for {agent_id} (Session: {session_id})"
                        )
                        async for event in agent.stream_events(session_id):
                            logger.info(
                                f"🎯 [{agent_id}] Event: {event.type} - {event.payload}"
                            )
                except Exception as e:
                    logger.error(f"❌ Event monitoring error for {agent_id}: {e}")

            # Start monitoring task
            task = asyncio.create_task(monitor_agent_events(agent_id, session_id))
            monitoring_tasks.append(task)

        # 10. Keep running for much longer to see agents in action
        logger.info("\n⏰ Running for 5 minutes to observe agents in action...")
        logger.info("   You should see events when agents start their tasks!")
        logger.info("   (Press Ctrl+C to stop)")

        # Add real-time task updates during execution
        async def real_time_updates():
            """Perform real-time task updates while system is running."""
            await asyncio.sleep(60)  # Wait 1 minute before first update

            logger.info("\n🔄 REAL-TIME TASK UPDATE: Updating calculator task...")
            new_calc_task = {
                "query": "Perform complex calculations. Use simple_calculator to: 1) add 50 and 150, 2) multiply 25 and 4, 3) subtract 200 from 500. Show all results.",
                "description": "Complex calculator task with multiple operations",
                "complex_mode": True,
            }
            success = manager.update_task_config("calculator_agent", new_calc_task)
            logger.info(
                f"   Real-time calculator update: {'✅ Success' if success else '❌ Failed'}"
            )

            await asyncio.sleep(60)  # Wait another minute

            logger.info("\n🔄 REAL-TIME TASK UPDATE: Updating system monitor task...")
            new_sys_task = {
                "query": "Comprehensive system health check. Use system_status to get detailed metrics, then use log_analyzer to check for any critical issues. Provide a complete health report.",
                "description": "Comprehensive system health check",
                "comprehensive_mode": True,
            }
            success = manager.update_task_config("system_monitor_agent", new_sys_task)
            logger.info(
                f"   Real-time system monitor update: {'✅ Success' if success else '❌ Failed'}"
            )

            await asyncio.sleep(60)  # Wait another minute

            logger.info("\n🔄 REAL-TIME TASK UPDATE: Adding new emergency task...")
            emergency_task = {
                "query": "EMERGENCY: Perform immediate system diagnostics. Use all available tools to check system status, analyze logs, and report any critical issues that need immediate attention.",
                "description": "Emergency system diagnostics",
                "emergency_mode": True,
                "priority": "critical",
            }
            success = manager.register_task("system_monitor_agent", emergency_task)
            logger.info(
                f"   Emergency task addition: {'✅ Success' if success else '❌ Failed'}"
            )

        # Start real-time updates task
        real_time_task = asyncio.create_task(real_time_updates())

        # Run for 5 minutes (300 seconds) instead of 30 seconds
        await asyncio.sleep(300)

        # Cancel real-time updates
        real_time_task.cancel()
        try:
            await real_time_task
        except asyncio.CancelledError:
            pass

        # 11. Show final metrics
        logger.info("\n📈 Final Agent Metrics:")
        metrics = manager.get_all_metrics()
        for agent_id, agent_metrics in metrics.items():
            logger.info(f"   {agent_id}:")
            logger.info(f"     Run Count: {agent_metrics['run_count']}")
            logger.info(f"     Error Count: {agent_metrics['error_count']}")
            logger.info(f"     Last Run: {agent_metrics['last_run']}")
            logger.info(f"     Has Task: {agent_metrics['has_task']}")

        # 12. Shutdown
        logger.info("\n🛑 Shutting down...")

        # Cancel monitoring tasks
        for task in monitoring_tasks:
            task.cancel()

        # Wait for monitoring tasks to finish
        await asyncio.gather(*monitoring_tasks, return_exceptions=True)

        manager.shutdown()

        logger.info("✅ Background Agent Example completed successfully!")

    except KeyboardInterrupt:
        logger.info("\n⏹️  Interrupted by user")
        if "manager" in locals():
            manager.shutdown()
    except Exception as e:
        logger.error(f"❌ Error in main: {e}")
        if "manager" in locals():
            manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
