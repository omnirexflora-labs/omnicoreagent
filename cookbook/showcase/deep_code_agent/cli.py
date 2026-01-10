# deep_coder/cli.py
import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from rich.table import Table
from rich.progress import SpinnerColumn, TextColumn, Progress
from rich.layout import Layout
from rich.align import Align
from rich import box

from code_agent_runner import DeepCodingAgentRunner
from observability_globals import log, metrics, health, audit, CONFIG


console = Console()


class DeepCodeAgentCLI:
    def __init__(self):
        self.runner = DeepCodingAgentRunner()
        self.session_id = self.runner.session_id
        self._shutdown = False

    async def initialize(self):
        await self.runner.initialize()

    def _render_welcome(self):
        title = Text("Deep Code Agent", style="bold #00FFA3")
        subtitle = Text("Secure • Intelligent • Production-Grade", style="dim")
        framework = Text("Powered by OmniCoreAgent", style="italic #888888")

        panel = Panel(
            Align.center(
                Text("\n").join([title, subtitle, Text(), framework]), vertical="middle"
            ),
            border_style="bright_green",
            box=box.ROUNDED,
            padding=(1, 2),
            title="[bold]🚀 Deep Code Agent[/bold]",
            subtitle=f"[dim]Session: {self.session_id[:8]}...[/dim]",
        )
        console.print(panel)

        # Config summary table
        table = Table(show_header=False, box=box.MINIMAL, padding=(0, 1))
        table.add_column(style="bold cyan", width=20)
        table.add_column(style="white")
        table.add_row("Model", f"{CONFIG.model.provider}/{CONFIG.model.model}")
        table.add_row("Sandbox", "Docker + Seccomp")
        table.add_row("Memory Backend", CONFIG.agent.memory_tool_backend or "disabled")
        table.add_row("Storage", CONFIG.storage.memory_store_type)
        table.add_row("Timeout", f"{CONFIG.coding.sandbox_timeout_seconds}s")
        console.print(Panel(table, title="⚙️  Configuration", border_style="blue"))

    def _print_user(self, msg: str):
        console.print(
            f"\n[bold blue][[/bold blue][bold #00FFA3]User[/bold #00FFA3][bold blue]][/bold blue] {msg}"
        )

    def _print_agent_stream_start(self):
        self._live = Live(
            Panel(
                Text("Thinking...", style="dim"),
                title="🧠 Deep Code Agent",
                border_style="green",
                expand=False,
            ),
            refresh_per_second=10,
            console=console,
        )
        self._live.start()

    def _print_agent_stream_update(self, content: str):
        if hasattr(self, "_live") and self._live.is_started:
            # Detect if it's code (heuristic)
            if "```" in content or content.strip().startswith(
                ("def ", "class ", "import ", "{", "[")
            ):
                try:
                    syntax = Syntax(
                        content, "python", theme="monokai", line_numbers=False
                    )
                    renderable = Panel(syntax, border_style="green", expand=False)
                except:
                    renderable = Panel(content, border_style="green", expand=False)
            else:
                renderable = Panel(
                    content,
                    title="🧠 Deep Code Agent",
                    border_style="green",
                    expand=False,
                )
            self._live.update(renderable)

    def _print_agent_stream_end(self, final_content: str):
        if hasattr(self, "_live"):
            self._live.stop()
        # Final render with syntax detection
        if "```" in final_content:
            lines = final_content.split("```")
            parts = []
            for i, chunk in enumerate(lines):
                if i % 2 == 1:  # Code block
                    lang = chunk.split("\n")[0].strip()
                    code = "\n".join(chunk.split("\n")[1:])
                    if lang in (
                        "python",
                        "js",
                        "javascript",
                        "ts",
                        "typescript",
                        "bash",
                        "json",
                        "yaml",
                    ):
                        parts.append(
                            Syntax(code, lang, theme="monokai", line_numbers=False)
                        )
                    else:
                        parts.append(Syntax(code, "text", theme="monokai"))
                else:
                    if chunk.strip():
                        parts.append(Text(chunk))
            console.print(Panel(Text("\n").join(parts), border_style="green"))
        else:
            console.print(
                Panel(final_content, title="🧠 Deep Code Agent", border_style="green")
            )

    def _print_error(self, msg: str):
        console.print(f"[bold red]{msg}[/bold red]")

    def _print_info(self, msg: str):
        console.print(f"[bold cyan]  {msg}[/bold cyan]")

    async def _handle_command(self, cmd: str) -> bool:
        """Return True if command was handled"""
        if cmd in {"/exit", "/quit"}:
            return False

        elif cmd == "/help":
            help_md = f"""
# 🧠 Deep Code Agent — Help

## Natural Language
Ask anything:
- "Write unit tests for utils.py"
- "Fix the bug in login.py"
- "Refactor this function to be async"

## Code Ingestion
- Provide a `.tar.gz` path or `.git` URL when prompted

## Core Commands
| Command | Description |
|--------|-------------|
| `/metrics` | Show Prometheus metrics |
| `/health` | Run system health checks |
| `/audit` | View last 50 audit entries |
| `/tools` | List available tools |
| `/history` | Show conversation history |
| `/clear` | Clear session history |
| `/exit` | Quit |

## Output
- Code blocks are syntax-highlighted
- File changes shown as diffs
- Tarball download link provided on completion

> 💡 **Tip**: Always review changes before applying!
            """
            console.print(Markdown(help_md))
            return True

        elif cmd == "/metrics":
            self._print_info("Exporting metrics...")
            console.print(Syntax(metrics.export(), "prometheus", theme="monokai"))
            return True

        elif cmd == "/health":
            status = health.run()
            table = Table(title="Health Status", box=box.ROUNDED)
            table.add_column("Component", style="cyan")
            table.add_column("Status", style="green")
            for k, v in status.items():
                if k == "overall":
                    continue
                table.add_row(k, "Healthy" if v is True else "Unhealthy")
            table.add_row(
                "Overall", "Healthy" if status.get("overall") else "Unhealthy"
            )
            console.print(table)
            return True

        elif cmd == "/audit":
            try:
                log_path = Path(CONFIG.security.audit_log_file)
                if log_path.exists():
                    lines = log_path.read_text().strip().split("\n")[-50:]
                    content = "\n".join(lines)
                    console.print(
                        Panel(
                            Syntax(content, "json", theme="monokai"),
                            title="Audit Log (last 50)",
                        )
                    )
                else:
                    self._print_info("No audit log yet.")
            except Exception as e:
                self._print_error(f"Failed to read audit log: {e}")
            return True

        elif cmd == "/tools":
            tools = await self.runner.agent.list_all_available_tools()
            table = Table(title="Available Tools", box=box.ROUNDED)
            table.add_column("Name", style="bold cyan")
            table.add_column("Description")
            for tool in tools:
                name = tool.get("name")
                desc = tool.get("description")
                table.add_row(name, desc)
            console.print(table)
            return True

        elif cmd == "/history":
            history = await self.runner.agent.get_session_history(self.session_id)
            if history:
                for i, entry in enumerate(history, 1):
                    role = entry.get("role", "unknown")
                    content = entry.get("content", "")
                    timestamp = entry.get("timestamp", "")
                    metadata = entry.get("metadata", {})

                    console.print(
                        Panel(
                            Syntax(
                                content,
                                "xml" if content.strip().startswith("<") else "json",
                                theme="monokai",
                            ),
                            title=f"[{i}] {role.upper()} @ {timestamp} ({metadata.get('agent_name', '-')})",
                            border_style="cyan",
                        )
                    )
            else:
                self._print_info("No history yet.")
            return True

        elif cmd == "/clear":
            await self.runner.agent.clear_session_history(self.session_id)
            self._print_info("Session history cleared.")
            return True

        return False

    async def run(self):
        self._render_welcome()
        console.print("\n[bold]Ready. Describe your coding task or use /help.[/bold]\n")

        def signal_handler(sig, frame):
            console.print("\n[bold yellow]Shutting down gracefully...[/bold yellow]")
            self._shutdown = True
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        while not self._shutdown:
            try:
                user_input = Prompt.ask(
                    f"\n[bold blue][[/bold blue][bold #00FFA3]DeepCodeAgent[/bold #00FFA3][bold blue]][/bold blue]"
                ).strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue

            if await self._handle_command(user_input):
                continue

            if user_input in {"/exit", "/quit"}:
                break

            # Handle normal query
            self._print_user(user_input)

            self._print_agent_stream_start()
            try:
                response = await self.runner.handle_chat(query=user_input)
                if response and "response" in response:
                    self._print_agent_stream_end(response["response"])
                else:
                    self._print_error("Agent returned no response.")
            except Exception as e:
                self._print_error(f"Agent error: {e}")
            finally:
                if hasattr(self, "_live"):
                    self._live.stop()

        self._print_info("Goodbye! 🚀")

    async def shutdown(self):
        if self.runner.agent:
            await self.runner.agent.cleanup()
        self.runner.sandbox_executor.cleanup_session(self.session_id)
        metrics.session_end()
        metrics.set_health(False)
        log.info(f"Session ended: {self.session_id}")


async def main():
    cli = DeepCodeAgentCLI()
    try:
        await cli.initialize()
        await cli.run()
    finally:
        await cli.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
