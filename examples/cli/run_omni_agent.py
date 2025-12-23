#!/usr/bin/env python3
"""
OmniAgent Unified Interface
A comprehensive CLI/Web interface for OmniAgent with all features.
"""

import asyncio
import argparse
import sys
import subprocess
from typing import Optional
import time
import logging
from all_sub_agents import weather_agent, research_agent, filesystem_agent

logger = logging.getLogger("omnicoreagent")

# TOP-LEVEL IMPORTS (Recommended for most use cases)
from omnicoreagent import (
    OmniAgent,
    MemoryRouter,
    EventRouter,
    BackgroundAgentManager,
    ToolRegistry,
)

# LOW-LEVEL IMPORTS (Alternative approach for advanced users)
# from omnicoreagent.omni_agent.agent import OmniAgent
# from omnicoreagent.core.memory_store.memory_router import MemoryRouter
# from omnicoreagent.core.events.event_router import EventRouter
# from omnicoreagent.omni_agent.background_agent.background_agent_manager import (
#     BackgroundAgentManager,
# )
# from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
# from omnicoreagent.core.utils import logger

# instantiate the tool registry
tool_registry = ToolRegistry()

MCP_TOOLS = [
    {
        "name": "mysql",
        "command": "uv",
        "args": [
            "--directory",
            "/home/abiorh/ai/mcp_servers/mysql_mcp_server",
            "run",
            "mysql_mcp_server",
        ],
        "env": {
            "MYSQL_HOST": "localhost",
            "MYSQL_PORT": "3306",
            "MYSQL_USER": "root",
            "MYSQL_PASSWORD": "Lucifer_001",
            "MYSQL_DATABASE": "mcp_learning",
        },
    },
    {
        "name": "mcp-pinecone",
        "command": "uv",
        "args": [
            "--directory",
            "/home/abiorh/ai/mcp_servers/mcp-pinecone",
            "run",
            "mcp-pinecone",
        ],
        "env": {
            "PINECONE_API_KEY": "api-key-1234567890",
            "PINECONE_INDEX_NAME": "ocpp-index",
        },
    },
    # {
    #     "name": "new-mcpserver",
    #    "transport_type": "streamable_http",
    #     "url": "http://0.0.0.0:9000/mcp",
    #     "headers": {
    #         "Authorization": "Bearer api-key-1234567890"
    #     }
    # },
    {
        "name": "filesystem",
        "command": "npx",
        "args": [
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "/home/abiorh/Desktop",
            "/home/abiorh/ai/",
        ],
    },
    {
        "name": "google-maps",
        "command": "npx",
        "args": [
            "-y",
            "@modelcontextprotocol/server-google-maps",
        ],
        "env": {
            "GOOGLE_MAPS_API_KEY": "",
        },
    },
    {
        "name": "graph_memory",
        "command": "docker",
        "args": [
            "run",
            "-i",
            "-v",
            "claude-memory:/app/dist",
            "--rm",
            "mcp/memory",
        ],
    },
    {
        "name": "time",
        "command": "uvx",
        "args": ["mcp-server-time", "--local-timezone=America/New_York"],
    },
    # {
    #     "name": "edgeone-pages-mcp-server",
    #     "command": "npx",
    #     "args": [
    #         "edgeone-pages-mcp"
    #     ],
    # },
    # {
    #     "name": "puppeteer",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@modelcontextprotocol/server-puppeteer"
    #     ],
    # },
    # {
    #     "name": "firecrawl-mcp",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "firecrawl-mcp"
    #     ],
    #     "env": {
    #         "FIRECRAWL_API_KEY": "fc-af1b3ac1a0c2402485402fd0e34da158"
    #     },
    # },
    # {
    #     "name": "postgres",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@modelcontextprotocol/server-postgres",
    #         "postgresql://localhost/mydb"
    #     ],
    # },
    # {
    #     "name": "baidu-map",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@baidumap/mcp-server-baidu-map"
    #     ],
    #     "env": {
    #         "BAIDU_MAP_API_KEY": "xxx"
    #     },
    # },
    # {
    #     "name": "blender",
    #     "command": "uvx",
    #     "args": [
    #         "blender-mcp"
    #     ],
    # },
    # {
    #     "name": "figma-developer-mcp",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "figma-developer-mcp",
    #         "--stdio"
    #     ],
    #     "env": {
    #         "FIGMA_API_KEY": "<your-figma-api-key>"
    #     },
    # },
    # {
    #     "name": "serper",
    #     "command": "uvx",
    #     "args": [
    #         "serper-mcp-server"
    #     ],
    #     "env": {
    #         "SERPER_API_KEY": "<Your Serper API key>"
    #     },
    # },
    # {
    #     "name": "qiniu",
    #     "command": "uvx",
    #     "args": [
    #         "qiniu-mcp-server"
    #     ],
    #     "env": {
    #         "QINIU_ACCESS_KEY": "YOUR_ACCESS_KEY",
    #         "QINIU_SECRET_KEY": "YOUR_SECRET_KEY",
    #         "QINIU_REGION_NAME": "YOUR_REGION_NAME",
    #         "QINIU_ENDPOINT_URL": "YOUR_ENDPOINT_URL",
    #         "QINIU_BUCKETS": ""
    #     }
    # },
    # {
    #     "name": "github",
    #     "command": "docker",
    #     "args": [
    #         "run",
    #         "-i",
    #         "--rm",
    #         "-e",
    #         "GITHUB_PERSONAL_ACCESS_TOKEN",
    #         "mcp/github"
    #     ],
    #     "env": {
    #         "GITHUB_PERSONAL_ACCESS_TOKEN": "<YOUR_TOKEN>"
    #     },
    # },
    # {
    #     "name": "Bucket",
    #     "command": "npx",
    #     "args": [
    #         "mcp-remote@latest",
    #         "https://app.bucket.co/api/mcp?appId=<YOUR APP ID>"
    #     ],
    # },
    #  {
    #     "name": "redis",
    #     "command": "docker",
    #     "args": [
    #         "run",
    #         "-i",
    #         "--rm",
    #         "mcp/redis",
    #         "redis://host.docker.internal:6379"
    #     ],
    # },
    # {
    #     "name": "gitlab",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@modelcontextprotocol/server-gitlab"
    #     ],
    #     "env": {
    #         "GITLAB_PERSONAL_ACCESS_TOKEN": "<YOUR_TOKEN>",
    #         "GITLAB_API_URL": "https://gitlab.com/api/v4"
    #     },
    # },
    # {
    #     "name": "jina-mcp-tools",
    #     "command": "npx",
    #     "args": [
    #         "jina-mcp-tools"
    #     ],
    #     "env": {
    #         "JINA_API_KEY": "your_jina_api_key_here"
    #     },
    # },
    # {
    #     "name": "howtocook-mcp",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "howtocook-mcp"
    #     ],
    # },
    # {
    #     "name": "perplexity-ask",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@chatmcp/server-perplexity-ask"
    #     ],
    #     "env": {
    #         "PERPLEXITY_API_KEY": "YOUR_API_KEY_HERE"
    #     },
    # },
    # {
    #     "name": "mcp-server-flomo",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@chatmcp/mcp-server-flomo"
    #     ],
    #     "env": {
    #         "FLOMO_API_URL": "https://flomoapp.com/iwh/xxx/xxx/"
    #     },
    # },
    # {
    #     "name": "sequential-thinking",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@modelcontextprotocol/server-sequential-thinking"
    #     ],
    # },
    # {
    #     "name": "fetch",
    #     "command": "uvx",
    #     "args": [
    #         "mcp-server-fetch"
    #     ],
    # },
    # {
    #     "name": "302ai-browser-use-mcp",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@302ai/browser-use-mcp"
    #     ],
    #     "env": {
    #         "302AI_API_KEY": "YOUR_API_KEY_HERE"
    #     },
    # },
    # {
    #     "name": "slack",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@modelcontextprotocol/server-slack"
    #     ],
    #     "env": {
    #         "SLACK_BOT_TOKEN": "xoxb-your-bot-token",
    #         "SLACK_TEAM_ID": "T01234567",
    #         "SLACK_CHANNEL_IDS": "C01234567, C76543210"
    #     },
    # },
    # # {
    # #     "name": "zhipu-web-search-sse",
    # #     "url": "https://open.bigmodel.cn/api/mcp/web_search/sse?Authorization={you ak/sk}",
    # #     "transport": "streamable_http"
    # # },
    # {
    #     "name": "sentry",
    #     "command": "uvx",
    #     "args": [
    #         "mcp-server-sentry",
    #         "--auth-token",
    #         "YOUR_SENTRY_TOKEN"
    #     ],
    # },
    # {
    #     "name": "context7",
    #     "command": "bunx",
    #     "args": [
    #         "-y",
    #         "@upstash/context7-mcp",
    #         "--api-key",
    #         "YOUR_API_KEY"
    #     ],
    # },
    # {
    #     "name": "amap-maps",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@amap/amap-maps-mcp-server"
    #     ],
    #     "env": {
    #         "AMAP_MAPS_API_KEY": "api_key"
    #     },
    # },
    # {
    #     "name": "search1api",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "search1api-mcp"
    #     ],
    #     "env": {
    #         "SEARCH1API_KEY": "YOUR_SEARCH1API_KEY"
    #     },
    # },
    # {
    #     "name": "mcpadvisor",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@xiaohui-wang/mcpadvisor"
    #     ],
    # },
    # {
    #     "name": "neon",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@neondatabase/mcp-server-neon",
    #         "start",
    #         "{NEON_API_KEY}"
    #     ],
    # },
    # {
    #     "name": "mailtrap",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "mcp-mailtrap"
    #     ],
    #     "env": {
    #         "MAILTRAP_API_TOKEN": "your_mailtrap_api_token",
    #         "DEFAULT_FROM_EMAIL": "your_sender@example.com"
    #     },
    # },
    # {
    #     "name": "brave-search",
    #     "command": "docker",
    #     "args": [
    #         "run",
    #         "-i",
    #         "--rm",
    #         "-e",
    #         "BRAVE_API_KEY",
    #         "mcp/brave-search"
    #     ],
    #     "env": {
    #         "BRAVE_API_KEY": "YOUR_API_KEY_HERE"
    #     },
    # },
    # {
    #     "name": "tempmail",
    #     "command": "npx",
    #     "args": [
    #         "mcp-server-tempmail"
    #     ],
    #     "env": {
    #         "TEMPMAIL_API_KEY": "your-api-key-here",
    #         "TEMPMAIL_BASE_URL": "https://chat-tempmail.com"
    #     },
    # },
    # {
    #     "name": "crawlbase",
    #     "command": "npx",
    #     "args": [
    #         "@crawlbase/mcp@latest"
    #     ],
    #     "env": {
    #         "CRAWLBASE_TOKEN": "your_token_here",
    #         "CRAWLBASE_JS_TOKEN": "your_js_token_here"
    #     },
    # },
    # {
    #     "name": "memorious",
    #     "command": "uvx",
    #     "args": [
    #         "memorious-mcp"
    #     ],
    # },
    # {
    #     "name": "anycrawl-mcp",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "anycrawl-mcp-server"
    #     ],
    #     "env": {
    #         "ANYCRAWL_API_KEY": "<YOUR_TOKEN>",
    #         "ANYCRAWL_BASE_URL": "https://api.anycrawl.dev",
    #         "LOG_LEVEL": "info"
    #     },
    # },
    # {
    #     "name": "supabase-mcp",
    #     "command": "npx",
    #     "args": [
    #         "-y",
    #         "@smithery/cli@latest",
    #         "run",
    #         "@supabase-community/supabase-mcp",
    #         "--key",
    #         "c328ca96-70ec-4dd2-885d-4942a561281d",
    #         "--profile",
    #         "clinical-sawfish-qODzWU"
    #     ],
    # },
]


class OmniAgentCLI:
    """Comprehensive CLI interface for OmniAgent."""

    def __init__(self):
        """Initialize the CLI interface."""
        self.agent: Optional[OmniAgent] = None
        self.memory_router: Optional[MemoryRouter] = None
        self.event_router: Optional[EventRouter] = None
        self.background_manager: Optional[BackgroundAgentManager] = None
        self.session_id: Optional[str] = None

    # Mathematical tools
    @tool_registry.register_tool("calculate_area")
    def calculate_area(length: float, width: float) -> str:
        """Calculate the area of a rectangle."""
        area = length * width
        return f"Area of rectangle ({length} x {width}): {area} square units"

    @tool_registry.register_tool("calculate_perimeter")
    def calculate_perimeter(length: float, width: float) -> str:
        """Calculate the perimeter of a rectangle."""
        perimeter = 2 * (length + width)
        return f"Perimeter of rectangle ({length} x {width}): {perimeter} units"

    # Text processing tools
    @tool_registry.register_tool("format_text")
    def format_text(text: str, style: str = "normal") -> str:
        """Format text in different styles."""
        if style == "uppercase":
            return text.upper()
        elif style == "lowercase":
            return text.lower()
        elif style == "title":
            return text.title()
        elif style == "reverse":
            return text[::-1]
        else:
            return text

    @tool_registry.register_tool("word_count")
    def word_count(text: str) -> str:
        """Count words in text."""
        words = text.split()
        return f"Word count: {len(words)} words"

    # System information tools
    @tool_registry.register_tool("system_info")
    def get_system_info() -> str:
        """Get basic system information."""
        import platform
        import time

        info = f"""System Information:
• OS: {platform.system()} {platform.release()}
• Architecture: {platform.machine()}
• Python Version: {platform.python_version()}
• Current Time: {time.strftime("%Y-%m-%d %H:%M:%S")}"""
        return info

    # Data analysis tools
    @tool_registry.register_tool("analyze_numbers")
    def analyze_numbers(numbers: str) -> str:
        """Analyze a list of numbers."""
        try:
            num_list = [float(x.strip()) for x in numbers.split(",")]
            if not num_list:
                return "No numbers provided"

            total = sum(num_list)
            average = total / len(num_list)
            minimum = min(num_list)
            maximum = max(num_list)

            return f"""Number Analysis:
• Count: {len(num_list)} numbers
• Sum: {total}
• Average: {average:.2f}
• Min: {minimum}
• Max: {maximum}"""
        except Exception as e:
            return f"Error analyzing numbers: {str(e)}"

    # File system tools
    @tool_registry.register_tool("list_directory")
    def list_directory(path: str = ".") -> str:
        """List contents of a directory."""
        import os

        try:
            if not os.path.exists(path):
                return f"Directory {path} does not exist"

            items = os.listdir(path)
            files = [item for item in items if os.path.isfile(os.path.join(path, item))]
            dirs = [item for item in items if os.path.isdir(os.path.join(path, item))]

            return f"""Directory: {path}
• Files: {len(files)} ({files[:5]}{"..." if len(files) > 5 else ""})
• Directories: {len(dirs)} ({dirs[:5]}{"..." if len(dirs) > 5 else ""})"""
        except Exception as e:
            return f"Error listing directory: {str(e)}"

    # Background agent specific tools (from working example)
    @tool_registry.register_tool("file_monitor")
    def monitor_files(directory: str = "/tmp") -> str:
        """Monitor files in a directory."""
        import os

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
        import os
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

    async def initialize(self):
        """Initialize all components."""
        print("🚀 Initializing OmniAgent CLI...")

        # Initialize routers
        self.memory_router = MemoryRouter("redis")
        self.event_router = EventRouter("redis_stream")

        # Initialize agent with exact same config as working example
        self.agent = OmniAgent(
            name="comprehensive_demo_agent",
            # system_instruction="You are a comprehensive AI assistant with access to mathematical, text processing, system information, data analysis, and file system tools. You can perform complex calculations, format text, analyze data, and provide system information. Always use the appropriate tools for the task and provide clear, helpful responses.",
            system_instruction="""
You are TutorAgent, an AI assistant specialized in personalized education. 
You have access to four tools that manage user-specific knowledge and learning progress:

- insert_knowledge: Store new educational content for a user’s knowledge base.
- knowledge_base_retrieval: Search and retrieve stored knowledge for a given user.
- update_user_progress: Record or update a user’s performance on topics and activities.
- get_user_context: Retrieve a user’s complete progress history to guide personalized tutoring.

Always use the most relevant tool when storing, retrieving, or adapting knowledge. 
Provide clear, supportive, and context-aware responses that help learners grow.
""",
            model_config={
                "provider": "mistral",
                "model": "magistral-medium-2509",
                "temperature": 0.3,
                "max_context_length": 1000,
            },
            mcp_tools=MCP_TOOLS,
            local_tools=tool_registry,
            sub_agents=[weather_agent, research_agent, filesystem_agent],
            agent_config={
                "agent_name": "OmniAgent",
                "max_steps": 15,
                "tool_call_timeout": 60,
                "request_limit": 0,  # 0 = unlimited
                "total_tokens_limit": 0,  # or 0 for unlimited
                # --- Memory Retrieval Config ---
                "memory_config": {"mode": "sliding_window", "value": 100},
                # --- Tool Retrieval Config ---
                "enable_advanced_tool_use": True,
                "enable_agent_skills": True,
                "memory_tool_backend": "local",
            },
            memory_router=self.memory_router,
            event_router=self.event_router,
            debug=True,
        )
        await self.agent.connect_mcp_servers()
        # # Initialize background agent manager
        # self.background_manager = BackgroundAgentManager(
        #     memory_router=self.memory_router, event_router=self.event_router
        # )

        print("✅ OmniAgent CLI initialized successfully")

    def print_welcome(self):
        """Print welcome message and available commands."""
        print("\n" + "=" * 80)
        print("🤖 OmniAgent CLI - Unified Interface")
        print("=" * 80)
        print("📋 Available Commands:")
        print("  /chat <message>           - Send a message to the agent")
        print("  /tools                    - List available tools")
        print("  /memory                   - Show memory information")
        print(
            "  /memory_store <type>      - Switch memory backend (in_memory/redis/database)"
        )
        print(
            "  /event_store <type>       - Switch event backend (in_memory/redis_stream)"
        )
        print("  /history                  - Show conversation history")
        print("  /clear_history            - Clear conversation history")
        print("  /save_history <file>      - Save history to file")
        print("  /load_history <file>      - Load history from file")
        print("  /background               - Background agent management")
        print("  /background_create        - Create a background agent")
        print("  /background_start         - Start background agent manager")
        print("  /background_stop          - Stop background agent manager")
        print("  /background_list          - List background agents")
        print("  /background_status        - Show background agent status")
        print("  /background_pause <id>    - Pause a background agent")
        print("  /background_resume <id>   - Resume a background agent")
        print("  /background_delete <id>   - Delete a background agent")
        print("  /task_register <id> <query> - Register a task for background agent")
        print("  /task_list               - List all registered tasks")
        print("  /task_update <id> <query> - Update a task")
        print("  /task_remove <id>        - Remove a task")
        print("  /events                  - Stream events in real-time")
        print("  /agent_info              - Show agent information")
        print("  /help                    - Show this help message")
        print("  /quit                    - Exit the CLI")
        print("=" * 80)
        print("💡 Tip: Use /help for detailed command information")
        print("=" * 80 + "\n")

    async def handle_chat(self, message: str):
        """Handle chat messages and measure total request/response time."""
        if not self.agent:
            print("❌ Agent not initialized")
            return

        print(f"🤖 Processing: {message}")

        # Generate session ID if not exists
        if not self.session_id:
            from datetime import datetime

            self.session_id = (
                f"cli_session_{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}"
            )

        try:
            # Start timer before calling the agent
            overall_start = time.perf_counter()

            # Run the agent and get response
            result = await self.agent.run(message, session_id=self.session_id)

            overall_end = time.perf_counter()
            total_time = overall_end - overall_start

            # Extract response text
            response = result.get("response", "No response received")

            # Log timing and response
            logger.info(f"[CHAT] Total message processing time: {total_time:.3f}s")
            logger.info(f"[CHAT] Request: {message}")
            logger.info(
                f"[CHAT] Response: {response[:300]}{'...' if len(response) > 300 else ''}"
            )

            print(f"🤖 Response: {response}")
            print(f"⏱️ Total time: {total_time:.3f}s")

        except Exception as e:
            logger.error(f"[CHAT] Error while processing message: {e}")
            print(f"❌ Error: {e}")

    async def handle_tools(self):
        """List available tools."""
        if not self.agent:
            print("❌ Agent not initialized")
            return

        if self.agent.local_tools:
            tools = self.agent.local_tools.list_tools()
            print("🔧 Available Tools:")
            for tool in tools:
                # Handle both Tool objects and dictionaries
                if hasattr(tool, "name") and hasattr(tool, "description"):
                    # Tool object
                    print(f"  • {tool.name}: {tool.description}")
                elif (
                    isinstance(tool, dict) and "name" in tool and "description" in tool
                ):
                    # Dictionary format
                    print(f"  • {tool['name']}: {tool['description']}")
                else:
                    # Fallback - just show the tool representation
                    print(f"  • {tool}")
        else:
            print("🔧 No local tools available")

    async def handle_memory(self):
        """Show memory information."""
        if not self.memory_router:
            print("❌ Memory router not initialized")
            return

        info = self.memory_router.get_memory_store_info()
        print("🧠 Memory Information:")
        print(f"  Type: {info['type']}")
        print(f"  Available: {info['available']}")
        print(f"  Store Class: {info.get('store_class', 'N/A')}")

    async def handle_memory_store(self, store_type: str):
        """Switch memory backend."""
        if not self.memory_router:
            print("❌ Memory router not initialized")
            return

        try:
            self.memory_router.switch_memory_store(store_type)
            print(f"✅ Switched to {store_type} memory store")
        except Exception as e:
            print(f"❌ Error switching memory store: {e}")

    async def handle_event_store(self, store_type: str):
        """Switch event backend."""
        if not self.event_router:
            print("❌ Event router not initialized")
            return

        try:
            self.event_router.switch_event_store(store_type)
            print(f"✅ Switched to {store_type} event store")
        except Exception as e:
            print(f"❌ Error switching event store: {e}")

    async def handle_history(self):
        """Show conversation history."""
        if not self.session_id:
            print("❌ No active session")
            return

        if not self.agent:
            print("❌ Agent not initialized")
            return

        try:
            history = await self.agent.get_session_history(self.session_id)
            if history:
                print("📜 Conversation History:")
                for msg in history[-10:]:  # Show last 10 messages
                    print(
                        f"  {msg.get('role', 'unknown')}: {msg.get('content', '')[:100]}..."
                    )
            else:
                print("📜 No conversation history found")
        except Exception as e:
            print(f"❌ Error getting history: {e}")

    async def handle_clear_history(self):
        """Clear conversation history."""
        if not self.session_id:
            print("❌ No active session")
            return

        if not self.agent:
            print("❌ Agent not initialized")
            return

        try:
            await self.agent.clear_session_history(self.session_id)
            print("✅ Conversation history cleared")
        except Exception as e:
            print(f"❌ Error clearing history: {e}")

    async def handle_save_history(self, filename: str):
        """Save conversation history to file."""
        if not self.session_id:
            print("❌ No active session")
            return

        if not self.memory_router:
            print("❌ Memory router not initialized")
            return

        try:
            self.memory_router.save_message_history_to_file(self.session_id, filename)
            print(f"✅ History saved to {filename}")
        except Exception as e:
            print(f"❌ Error saving history: {e}")

    async def handle_load_history(self, filename: str):
        """Load conversation history from file."""
        if not self.session_id:
            print("❌ No active session")
            return

        if not self.memory_router:
            print("❌ Memory router not initialized")
            return

        try:
            self.memory_router.load_message_history_from_file(self.session_id, filename)
            print(f"✅ History loaded from {filename}")
        except Exception as e:
            print(f"❌ Error loading history: {e}")

    async def handle_task_register(self, agent_id: str, query: str):
        """Register a task for a background agent."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        try:
            self.background_manager.register_task(agent_id, {"query": query})
            print(f"✅ Registered task for agent: {agent_id}")
        except Exception as e:
            print(f"❌ Error registering task: {e}")

    async def handle_task_list(self):
        """List all registered tasks."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        try:
            task_ids = self.background_manager.list_tasks()
            if task_ids:
                print("📋 Registered Tasks:")
                for agent_id in task_ids:
                    task_config = self.background_manager.get_task_config(agent_id)
                    if task_config:
                        print(f"  {agent_id}: {task_config.get('query', 'No query')}")
                    else:
                        print(f"  {agent_id}: No task configuration")
            else:
                print("📋 No registered tasks found")
        except Exception as e:
            print(f"❌ Error listing tasks: {e}")

    async def handle_background_start(self):
        """Start the background agent manager."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        try:
            self.background_manager.start()
            print("🚀 Background Agent Manager started!")
            print("✅ All agents are now scheduled to run automatically")
            print("💡 Use /background_status to monitor progress")
        except Exception as e:
            print(f"❌ Error starting background manager: {e}")

    async def handle_background_stop(self):
        """Stop the background agent manager."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        try:
            self.background_manager.shutdown()
            print("⏹️ Background Agent Manager stopped!")
            print("✅ All agents are now paused")
        except Exception as e:
            print(f"❌ Error stopping background manager: {e}")

    async def handle_background_create(self):
        """Create a background agent."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        print("🤖 Welcome to BackgroundOmniAgent Creation!")
        print("=" * 50)
        print("🚀 Create a self-flying agent that runs automatically")
        print("📋 Available agent types:")
        print("  1. file_monitor - Monitor file system changes")
        print("  2. system_monitor - Monitor system status")
        print("  3. calculator - Perform calculations")
        print("  4. custom - Custom agent with specific tools")
        print("=" * 50)

        agent_type = input("Enter agent type (1-4): ").strip()

        agent_configs = {
            "1": {
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
                        "args": [
                            "-y",
                            "@modelcontextprotocol/server-filesystem",
                            "/tmp",
                        ],
                    }
                ],
                "local_tools": tool_registry,
                "agent_config": {
                    "max_steps": 10,
                    "tool_call_timeout": 30,
                    "request_limit": 1000,
                    "memory_config": {"mode": "token_budget", "value": 5000},
                },
                "interval": 30,
                "max_retries": 2,
                "retry_delay": 30,
                "debug": True,
                "task_config": {
                    "query": "Check the /tmp directory and provide information about files and directories. Use the file_monitor and directory_info tools to analyze the directory contents.",
                    "description": "File system monitoring task",
                },
            },
            "2": {
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
                "interval": 30,
                "max_retries": 3,
                "retry_delay": 30,
                "debug": True,
                "task_config": {
                    "query": "Check system status and provide basic system information. Use the system_status tool to get current system metrics.",
                    "description": "System monitoring task",
                },
            },
            "3": {
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
                "interval": 60,
                "max_retries": 2,
                "retry_delay": 45,
                "debug": True,
                "task_config": {
                    "query": "Perform some sample calculations. Use the simple_calculator tool to add 15 and 25 (operation='add'), then multiply 7 and 8 (operation='multiply').",
                    "description": "Calculator task",
                },
            },
            "4": {
                "agent_id": "custom_agent",
                "system_instruction": "You are a custom agent. Use the available tools to perform tasks as requested.",
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
                    "request_limit": 3,
                    "memory_config": {"mode": "token_budget", "value": 5000},
                },
                "interval": 30,
                "max_retries": 2,
                "retry_delay": 30,
                "debug": True,
                "task_config": {
                    "query": input("Enter custom task query: "),
                    "description": "Custom task",
                },
            },
        }

        if agent_type not in agent_configs:
            print("❌ Invalid agent type")
            return

        config = agent_configs[agent_type]
        try:
            print(f"\n🚀 Creating BackgroundOmniAgent: {config['agent_id']}")
            print("⏳ Please wait...")

            result = await self.background_manager.create_agent(config)

            print("✅ BackgroundOmniAgent created successfully!")
            print("=" * 50)
            print(f"🤖 Agent ID: {result['agent_id']}")
            print(f"🆔 Session ID: {result['session_id']}")
            print(f"📡 Event Store: {result['event_stream_info']}")
            print(f"📋 Task: {result['task_query']}")
            print(f"💬 Message: {result['message']}")
            print("=" * 50)
            print("🎯 Your agent is now running automatically!")
            print("⏰ Agent will execute tasks at scheduled intervals")
            print("💡 Use /background_status to monitor its progress")
            print("💡 Use /background_list to see all agents")

            # Check if manager is running, if not, ask user to start it
            status = self.background_manager.get_manager_status()
            if status["manager_running"]:
                print("✅ Background Agent Manager is running automatically")
            else:
                print("⚠️  Background Agent Manager is not running")
                print("💡 Use /background_start to start the manager manually")

        except Exception as e:
            print(f"❌ Error creating background agent: {e}")

    async def handle_background_list(self):
        """List background agents."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        try:
            agents = self.background_manager.list_agents()
            if agents:
                print("🤖 Background Agents:")
                for agent_id in agents:
                    print(f"  • {agent_id}")
            else:
                print("🤖 No background agents found")
        except Exception as e:
            print(f"❌ Error listing agents: {e}")

    async def handle_background_status(self):
        """Show background agent status."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        try:
            status = self.background_manager.get_manager_status()
            print("📊 Background Agent Manager Status:")
            print(f"  Running: {status['manager_running']}")
            print(f"  Total Agents: {status['total_agents']}")
            print(f"  Total Tasks: {status['total_tasks']}")
            print(f"  Scheduler Running: {status['scheduler_running']}")

            # Show individual agent statuses
            agents = self.background_manager.list_agents()
            if agents:
                print("\n🤖 Individual Agent Status:")
                for agent_id in agents:
                    agent_status = self.background_manager.get_agent_status(agent_id)
                    if agent_status:
                        print(f"  {agent_id}:")
                        print(f"    Running: {agent_status['is_running']}")
                        print(f"    Has Task: {agent_status['has_task']}")
                        print(f"    Run Count: {agent_status['run_count']}")
                        print(f"    Error Count: {agent_status['error_count']}")
                        print(f"    Scheduled: {agent_status['scheduled']}")
                    else:
                        print(f"  {agent_id}: No status available")
            else:
                print("\n🤖 No agents found")
        except Exception as e:
            print(f"❌ Error getting status: {e}")

    async def handle_background_pause(self, agent_id: str):
        """Pause a background agent."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        try:
            self.background_manager.pause_agent(agent_id)
            print(f"⏸️ Paused agent: {agent_id}")
        except Exception as e:
            print(f"❌ Error pausing agent: {e}")

    async def handle_background_resume(self, agent_id: str):
        """Resume a background agent."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        try:
            self.background_manager.resume_agent(agent_id)
            print(f"▶️ Resumed agent: {agent_id}")
        except Exception as e:
            print(f"❌ Error resuming agent: {e}")

    async def handle_background_delete(self, agent_id: str):
        """Delete a background agent."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        try:
            self.background_manager.delete_agent(agent_id)
            print(f"🗑️ Deleted agent: {agent_id}")
        except Exception as e:
            print(f"❌ Error deleting agent: {e}")

    async def handle_task_update(self, agent_id: str, query: str):
        """Update a task."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        try:
            self.background_manager.update_task_config(agent_id, {"query": query})
            print(f"✅ Updated task for agent: {agent_id}")
        except Exception as e:
            print(f"❌ Error updating task: {e}")

    async def handle_task_remove(self, agent_id: str):
        """Remove a task."""
        if not self.background_manager:
            print("❌ Background manager not initialized")
            return

        try:
            self.background_manager.remove_task(agent_id)
            print(f"✅ Removed task for agent: {agent_id}")
        except Exception as e:
            print(f"❌ Error removing task: {e}")

    async def handle_events(self):
        """Stream events in real-time."""
        if not self.event_router:
            print("❌ Event router not initialized")
            return

        # Use current session_id or create a default one
        session_id = self.session_id or "cli_events_session"

        print(f"📡 Streaming events for session: {session_id}")
        print("Press Ctrl+C to stop...")

        try:
            async for event in self.event_router.stream(session_id):
                print(f"🎯 Event: {event.type} - {event.timestamp}")
                if hasattr(event, "payload") and event.payload:
                    print(f"  Payload: {event.payload}")
        except KeyboardInterrupt:
            print("\n⏹️ Event streaming stopped")
        except Exception as e:
            print(f"❌ Error streaming events: {e}")

    async def handle_agent_info(self):
        """Show agent information."""
        if not self.agent:
            print("❌ Agent not initialized")
            return

        print("🤖 Agent Information:")
        print(f"  Session ID: {self.session_id or 'None'}")
        print(f"  Agent Name: {self.agent.name}")
        print(f"  Memory Store: {self.memory_router.get_memory_store_info()}")
        print(f"  Event Store: {self.event_router.get_event_store_info()}")
        print(f"  Tools Available: {len(self.agent.local_tools.list_tools())}")

    async def handle_background(self):
        """Show background agent management options."""
        print("🤖 Background Agent Management:")
        print("  Available commands:")
        print("    /background_create     - Create a new background agent")
        print("    /background_start      - Start background agent manager")
        print("    /background_stop       - Stop background agent manager")
        print("    /background_list       - List all background agents")
        print("    /background_status     - Show background agent status")
        print("    /background_pause <id> - Pause a background agent")
        print("    /background_resume <id> - Resume a background agent")
        print("    /background_delete <id> - Delete a background agent")
        print("    /task_register <id> <query> - Register a task for an agent")
        print("    /task_list            - List all registered tasks")
        print("    /task_update <id> <query> - Update a task")
        print("    /task_remove <id>     - Remove a task")
        print(
            "\n💡 Tip: After creating agents, use /background_start to run them automatically!"
        )

    async def handle_help(self):
        """Show detailed help."""
        self.print_welcome()

    async def cleanup(self):
        """Clean up all resources before exit."""
        print("🧹 Cleaning up resources...")

        try:
            # Clean up the main agent
            if self.agent:
                await self.agent.cleanup()
                print("✅ Main agent cleaned up")

            # Clean up background manager
            if self.background_manager:
                self.background_manager.shutdown()
                print("✅ Background agent manager shut down")

            # Clean up routers
            if self.memory_router:
                await self.memory_router.clear_memory()
                print("✅ Memory router cleaned up")

            if self.event_router:
                # Event router cleanup if needed
                print("✅ Event router cleaned up")

            print("🎯 All resources cleaned up successfully!")

        except Exception as e:
            print(f"⚠️  Warning: Some cleanup operations failed: {e}")

    async def run(self):
        """Run the CLI interface."""
        try:
            await self.initialize()
            self.print_welcome()

            print("💬 Start chatting with OmniAgent! Type /help for commands.")
            print()

            while True:
                try:
                    user_input = input("🤖 OmniAgent> ").strip()

                    if not user_input:
                        continue

                    if user_input.lower() in ["/quit", "/exit", "quit", "exit"]:
                        print("👋 Goodbye! Cleaning up...")
                        await self.cleanup()
                        break

                    # Handle commands
                    if user_input.startswith("/"):
                        parts = user_input.split(" ", 1)
                        command = parts[0].lower()
                        args = parts[1] if len(parts) > 1 else ""

                        if command == "/chat":
                            if args:
                                await self.handle_chat(args)
                            else:
                                print("❌ Please provide a message: /chat <message>")
                        elif command == "/tools":
                            await self.handle_tools()
                        elif command == "/memory":
                            await self.handle_memory()
                        elif command == "/memory_store":
                            if args:
                                await self.handle_memory_store(args)
                            else:
                                print(
                                    "❌ Please provide store type: /memory_store <type>"
                                )
                        elif command == "/event_store":
                            if args:
                                await self.handle_event_store(args)
                            else:
                                print(
                                    "❌ Please provide store type: /event_store <type>"
                                )
                        elif command == "/history":
                            await self.handle_history()
                        elif command == "/clear_history":
                            await self.handle_clear_history()
                        elif command == "/save_history":
                            if args:
                                await self.handle_save_history(args)
                            else:
                                print(
                                    "❌ Please provide filename: /save_history <file>"
                                )
                        elif command == "/load_history":
                            if args:
                                await self.handle_load_history(args)
                            else:
                                print(
                                    "❌ Please provide filename: /load_history <file>"
                                )
                        elif command == "/background_create":
                            await self.handle_background_create()
                        elif command == "/background_start":
                            await self.handle_background_start()
                        elif command == "/background_stop":
                            await self.handle_background_stop()
                        elif command == "/background_list":
                            await self.handle_background_list()
                        elif command == "/background_status":
                            await self.handle_background_status()
                        elif command == "/background_pause":
                            if args:
                                await self.handle_background_pause(args)
                            else:
                                print(
                                    "❌ Please provide agent ID: /background_pause <id>"
                                )
                        elif command == "/background_resume":
                            if args:
                                await self.handle_background_resume(args)
                            else:
                                print(
                                    "❌ Please provide agent ID: /background_resume <id>"
                                )
                        elif command == "/background_delete":
                            if args:
                                await self.handle_background_delete(args)
                            else:
                                print(
                                    "❌ Please provide agent ID: /background_delete <id>"
                                )
                        elif command == "/task_register":
                            if args:
                                parts = args.split(" ", 1)
                                if len(parts) == 2:
                                    await self.handle_task_register(parts[0], parts[1])
                                else:
                                    print(
                                        "❌ Please provide agent ID and query: /task_register <id> <query>"
                                    )
                            else:
                                print(
                                    "❌ Please provide agent ID and query: /task_register <id> <query>"
                                )
                        elif command == "/task_list":
                            await self.handle_task_list()
                        elif command == "/task_update":
                            if args:
                                parts = args.split(" ", 1)
                                if len(parts) == 2:
                                    await self.handle_task_update(parts[0], parts[1])
                                else:
                                    print(
                                        "❌ Please provide agent ID and query: /task_update <id> <query>"
                                    )
                            else:
                                print(
                                    "❌ Please provide agent ID and query: /task_update <id> <query>"
                                )
                        elif command == "/task_remove":
                            if args:
                                await self.handle_task_remove(args)
                            else:
                                print("❌ Please provide agent ID: /task_remove <id>")
                        elif command == "/events":
                            await self.handle_events()
                        elif command == "/agent_info":
                            await self.handle_agent_info()
                        elif command == "/background":
                            await self.handle_background()
                        elif command == "/background_start":
                            await self.handle_background_start()
                        elif command == "/background_stop":
                            await self.handle_background_stop()
                        elif command == "/help":
                            await self.handle_help()
                        else:
                            print(f"❌ Unknown command: {command}")
                            print("💡 Type /help for available commands")
                    else:
                        # Treat as chat message
                        await self.handle_chat(user_input)

                except KeyboardInterrupt:
                    print("\n👋 Goodbye! Cleaning up...")
                    await self.cleanup()
                    break
                except Exception as e:
                    print(f"❌ Error: {e}")
        except Exception as e:
            print(f"❌ Fatal error: {e}")
        finally:
            # Ensure cleanup happens even if there's an error
            try:
                await self.cleanup()
            except Exception as e:
                print(f"⚠️  Cleanup error: {e}")


def main():
    """Main entry point."""
    import signal

    def signal_handler(signum, frame):
        """Handle interrupt signals gracefully."""
        print("\n🛑 Received interrupt signal. Shutting down gracefully...")
        sys.exit(0)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description="OmniAgent Unified Interface")
    parser.add_argument(
        "--mode",
        choices=["cli", "web"],
        default="cli",
        help="Interface mode (cli or web)",
    )

    args = parser.parse_args()

    if args.mode == "cli":
        # Run CLI interface
        cli = OmniAgentCLI()
        try:
            asyncio.run(cli.run())
        except KeyboardInterrupt:
            print("\n👋 Interrupted by user. Goodbye!")
        except Exception as e:
            print(f"❌ Fatal error: {e}")
        finally:
            print("🎯 Application shutdown complete.")
    elif args.mode == "web":
        # Run web interface
        print("🌐 Starting web interface...")
        try:
            subprocess.run([sys.executable, "examples/web_server.py"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"❌ Web server error: {e}")
        except KeyboardInterrupt:
            print("\n👋 Web server stopped")
    else:
        print(f"❌ Unknown mode: {args.mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
