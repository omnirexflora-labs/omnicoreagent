from devops_copilot_agent import DevOpsCopilotRunner, log, metrics, health, CONFIG
import sys
import signal
import sys
import json
import asyncio

# --------------------------------------------------------------
# 3. Rich UI (optional)
# --------------------------------------------------------------
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.markdown import Markdown
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# --------------------------------------------------------------
# 4. ASCII & Welcome
# --------------------------------------------------------------
ASCII_ART = r"""
   ___                   _   ____                       ____            _ _       _   
  / _ \ _ __ ___  _ __ (_) |  _ \  _____   _____ _ __ / ___|___  _ __ (_) | ___ | |_ 
 | | | | '_ ` _ \| '_ \| | | | | |/ _ \ \ / / _ \ '_ \| |   / _ \| '_ \| | |/ _ \| __|
 | |_| | | | | | | | | | | | |_| |  __/\ V / (_) | |_) | |__| (_) | |_) | | | (_) | |_ 
  \___/|_| |_| |_|_| |_|_| |____/ \___| \_/ \___/| .__/ \____\___/| .__/|_|_|\___/ \__|
                                                  |_|              |_|                
        Secure • Intelligent • DevOps-Powered Assistant
                    Powered by OmniCoreAgent Framework
"""

WELCOME_MESSAGE = f"""
# Welcome to **{CONFIG.agent.name}**
A secure, AI-powered assistant for DevOps tasks, built for production-grade workflows.

**Model**: `{CONFIG.model.provider}/{CONFIG.model.model}`  
**Storage**: `{CONFIG.storage.memory_store_type}`  
**Timeout**: `{CONFIG.devops.timeout_seconds}s` | **Max Output**: `{CONFIG.devops.max_output_chars}`

**🤖 Framework**: Built on [OmniCoreAgent](https://github.com/Abiorh001/omnicoreagent) - A robust agent framework for production systems

✅ **Supported Commands**: `ls`, `grep`, `docker ps`, `kubectl get`, `journalctl`, and more  
❌ **Blocked Operations**: `rm`, `sudo`, `docker run`, `kubectl apply`, etc.  
💻 **Capabilities**: File operations, system monitoring, container management, Kubernetes, log analysis, configuration auditing, and workspace reporting  
📟 **Commands**: `/help`, `/history`, `/clear`, `/tools`, `/events`, `/store_info`, `/switch_store`, `/metrics`, `/health`, `/audit`, `/exit`

### Example Queries
- Analyze build logs for errors
- Verify configuration files in services/
- Generate a workspace report in Markdown
- Audit disk usage
- Check running containers
- List Kubernetes pods

"""


class DevopsCopilotCli:
    def __init__(self) -> None:
        self.console = Console() if RICH_AVAILABLE else None
        self.devops_copilot = DevOpsCopilotRunner()

    def print_styled(self, content: str, style: str = ""):
        if RICH_AVAILABLE:
            if style == "ascii":
                self.console.print(Text.from_ansi(ASCII_ART), justify="center")
            elif style == "welcome":
                self.console.print(Markdown(WELCOME_MESSAGE))
            elif style == "user":
                self.console.print(f"\n[bold blue]>[/bold blue] {content}")
            elif style == "agent":
                self.console.print(
                    Panel(content, title="Omni DevOps Copilot", border_style="green")
                )
            elif style == "error":
                self.console.print(f"[bold red]Error: {content}[/bold red]")
            elif style == "info":
                self.console.print(f"[bold cyan]Info: {content}[/bold cyan]")
            else:
                self.console.print(content)
        else:
            print(content)

    async def run_cli(self):
        self.print_styled("", "ascii")
        self.print_styled("", "welcome")

        def signal_handler(sig, frame):
            self.print_styled("Shutting down...", "info")
            log.info("Shutting down")
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)

        while True:
            try:
                user_input = (
                    Prompt.ask(
                        "\n[bold blue][[/bold blue][bold green]OmniDevOpsCopilot[/bold green][bold blue]][/bold blue]"
                    )
                    if RICH_AVAILABLE
                    else input("\n[OmniDevOpsCopilot] ")
                )
                user_input = user_input.strip()
            except (EOFError, KeyboardInterrupt):
                self.print_styled("Goodbye!", "info")
                break

            if not user_input:
                continue

            if user_input == "/metrics":
                self.print_styled(metrics.export(), "info")
                continue

            if user_input == "/health":
                health_status = health.run()
                self.print_styled(json.dumps(health_status, indent=2), "info")
                metrics.set_health(health_status.get("overall", False))
                continue

            if user_input == "/audit":
                try:
                    with open(self.devops_copilot.cfg.security.audit_log_file) as f:
                        lines = f.readlines()
                        self.print_styled("".join(lines[-50:]), "info")
                except FileNotFoundError:
                    self.print_styled("No audit log yet.", "info")
                continue

            if user_input in {"/exit", "/quit"}:
                self.print_styled("Goodbye!", "info")
                log.info("User exited")
                break

            if user_input == "/history":
                history = await self.devops_copilot.agent.get_session_history(
                    self.devops_copilot.session_id
                )
                self.print_styled(str(history), "info")
                continue

            if user_input == "/clear":
                await self.devops_copilot.agent.clear_session_history(
                    self.devops_copilot.session_id
                )
                self.print_styled("History cleared.", "info")
                continue

            if user_input == "/tools":
                tools = await self.devops_copilot.agent.list_all_available_tools()
                self.print_styled(str(tools), "info")
                continue

            if user_input == "/events":
                events = await self.devops_copilot.agent.get_events(
                    self.devops_copilot.session_id
                )
                self.print_styled(str(events), "info")
                continue

            if user_input.startswith("/switch_store"):
                parts = user_input.split()
                if len(parts) != 2 or parts[1] not in {"redis", "in_memory"}:
                    self.print_styled("Usage: /switch_store [redis|in_memory]", "error")
                    continue
                store_type = parts[1]
                try:
                    self.devops_copilot.agent.switch_memory_store(store_type)
                    self.devops_copilot.agent.switch_event_store(store_type)
                    self.print_styled(f"Switched to {store_type}", "info")
                    log.info(f"Switched store to {store_type}")
                except Exception as e:
                    self.print_styled(f"Switch failed: {e}", "error")
                continue

            if user_input == "/store_info":
                info = {
                    "memory_store": self.devops_copilot.agent.get_memory_store_type(),
                    "event_store": self.devops_copilot.agent.get_event_store_type(),
                    "event_store_available": self.devops_copilot.agent.is_event_store_available(),
                }
                self.print_styled(json.dumps(info, indent=2), "info")
                continue

            if user_input == "/help":
                help_text = """
                # OmniDevOpsCopilot — Full Help Guide

                ## Natural Language Queries
                > Ask anything in plain English:
                docker ps
                kubectl get pods --all-namespaces
                grep -i "error" /var/log/app.log
                The AI will safely execute and respond.

                ---

                ## Core Commands
                | Command | Description |
                |--------|-------------|
                | `/history` | Show full conversation history |
                | `/clear` | Clear current session history |
                | `/tools` | List all available tools |
                | `/events` | View event log for current session |
                | `/store_info` | Show memory & event store status |
                | `/switch_store [redis\|in_memory]` | Switch backend storage |
                | `/metrics` | Show live Prometheus metrics |
                | `/health` | Run health checks |
                | `/audit` | View last 50 audit log entries |
                | `/exit` | Quit the CLI |

                ---

                
                Observability

                Logs: All background tasks are logged
                Metrics: copilot_background_* in Prometheus
                Traces: Full trace in Jaeger (if enabled)


                Powered by OmniCoreAgent Framework
                https://github.com/Abiorh001/omnicoreagent
                """
                self.print_styled(help_text, "info")
                continue

            self.print_styled(user_input, "user")
            response = await self.devops_copilot.handle_chat(
                query=user_input, session_id=self.devops_copilot.session_id
            )
            if response:
                self.print_styled(response.get("response", ""), "agent")
                log.info(
                    f"Query: {user_input[:50]}... → Response length: {len(response['response'])}"
                )

    async def shutdown(self):
        """Cleanup resources and end session"""
        if self.devops_copilot.agent:
            try:
                await self.devops_copilot.agent.cleanup()
                self.print_styled("Cleaned up.", "info")
            except Exception as e:
                self.print_styled(f"Cleanup error: {e}", "error")
        metrics.session_end()
        metrics.set_health(False)
        log.info(f"Session ended: {self.devops_copilot.session_id}")


async def main():
    cli_runner = DevopsCopilotCli()
    try:
        await cli_runner.devops_copilot.initialize()
        await cli_runner.run_cli()
    finally:
        await cli_runner.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
