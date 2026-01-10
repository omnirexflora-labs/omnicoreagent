import asyncio
import httpx
import json
import typer
from decouple import config
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

# Redis bus monitor functions
from omnidaemon.cli.redis_bus_monitor import (
    bus_list_streams,
    bus_watch_live,
    bus_inspect_dlq,
    bus_inspect_stream,
    bus_list_groups,
    bus_get_stats,
)

app = typer.Typer(
    name="omni",
    help="OmniDaemon CLI — manage agents, tasks, and event bus",
    add_completion=False,
)

bus_app = typer.Typer(name="bus", help="Monitor Redis Stream event bus")
agent_app = typer.Typer(name="agent", help="Manage running agents")
task_app = typer.Typer(name="task", help="Publish and retrieve tasks")

app.add_typer(bus_app, name="bus")
app.add_typer(agent_app, name="agent")
app.add_typer(task_app, name="task")

# Config
REDIS_URL_DEFAULT = config("REDIS_URL")
EVENT_BUS_TYPE = config("EVENT_BUS_TYPE")
API_URL = config("OMNIDAEMON_API_URL")

console = Console()


# -------------------------
# ASCII ART
# -------------------------
OMNIDAEMON_ASCII = """[bold blue]
╔═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║  ██████╗ ███╗   ███╗███╗   ██╗██╗██████╗  █████╗ ███████╗███╗   ███╗ ██████╗ ███╗   ██╗                                   ║
║ ██╔═══██╗████╗ ████║████╗  ██║██║██╔══██╗██╔══██╗██╔════╝████╗ ████║██╔═══██╗████╗  ██║                                   ║
║ ██║   ██║██╔████╔██║██╔██╗ ██║██║██║  ██║███████║█████╗  ██╔████╔██║██║   ██║██╔██╗ ██║                                   ║
║ ██║   ██║██║╚██╔╝██║██║╚██╗██║██║██║  ██║██╔══██║██╔══╝  ██║╚██╔╝██║██║   ██║██║╚██╗██║                                   ║
║ ╚██████╔╝██║ ╚═╝ ██║██║ ╚████║██║██████╔╝██║  ██║███████╗██║ ╚═╝ ██║╚██████╔╝██║ ╚████║                                   ║
║  ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═══╝╚═╝╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═══╝                                   ║
║                                                                                                                             ║
║                                          [cyan]The Universal Background AI Agent Runtime[/]                                 ║
╚═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╝
"""


# -------------------------
# Redis Bus Commands
# -------------------------
def _ensure_redis_stream():
    if EVENT_BUS_TYPE != "redis_stream":
        typer.echo(
            "'omni bus' commands only work with EVENT_BUS_TYPE=redis_stream", err=True
        )
        raise typer.Exit(code=1)


@bus_app.command()
def list(redis_url: str = REDIS_URL_DEFAULT):
    """List all streams."""
    _ensure_redis_stream()
    asyncio.run(bus_list_streams(redis_url=redis_url))


@bus_app.command()
def watch(
    interval: int = typer.Option(2, "--interval", "-i"),
    json: bool = typer.Option(False, "--json"),
    redis_url: str = typer.Option(REDIS_URL_DEFAULT, "--redis-url"),
):
    """Live dashboard for event bus."""
    _ensure_redis_stream()
    asyncio.run(bus_watch_live(interval=interval, as_json=json, redis_url=redis_url))


@bus_app.command()
def dlq(
    topic: str,
    limit: int = typer.Option(5, "--limit", "-n"),
    redis_url: str = typer.Option(REDIS_URL_DEFAULT, "--redis-url"),
):
    """Inspect dead-letter queue for a topic."""
    _ensure_redis_stream()
    asyncio.run(bus_inspect_dlq(topic=topic, limit=limit, redis_url=redis_url))


@bus_app.command()
def inspect(
    stream: str,
    limit: int = typer.Option(5, "--limit", "-n"),
    redis_url: str = typer.Option(REDIS_URL_DEFAULT, "--redis-url"),
):
    """View recent messages in a stream."""
    _ensure_redis_stream()
    asyncio.run(bus_inspect_stream(stream=stream, limit=limit, redis_url=redis_url))


@bus_app.command()
def groups(
    stream: str,
    redis_url: str = typer.Option(REDIS_URL_DEFAULT, "--redis-url"),
):
    """Show consumer groups for a stream."""
    _ensure_redis_stream()
    asyncio.run(bus_list_groups(stream=stream, redis_url=redis_url))


@bus_app.command()
def stats(
    json: bool = typer.Option(False, "--json"),
    redis_url: str = typer.Option(REDIS_URL_DEFAULT, "--redis-url"),
):
    """Show one-shot stats across topics."""
    _ensure_redis_stream()
    asyncio.run(bus_get_stats(as_json=json, redis_url=redis_url))


# -------------------------
# HTTP API Helper
# -------------------------
def _ensure_api_available():
    try:
        resp = httpx.get(f"{API_URL}/health", timeout=2)
        if resp.status_code == 200:
            return
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    console.print(f"[red] OmniDaemon API not reachable at {API_URL}[/]")
    console.print(
        "[dim] Make sure your agent script is running with OMNIDAEMON_API_ENABLED=true[/]"
    )
    raise typer.Exit(1)


def _call_api(method: str, path: str, **kwargs):
    url = f"{API_URL}{path}"
    try:
        if method == "GET":
            resp = httpx.get(url, timeout=5)
        elif method == "POST":
            resp = httpx.post(url, timeout=5, **kwargs)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if resp.status_code == 404:
            console.print(f"[red] Not found: {path}[/]", err=True)
            raise typer.Exit(1)
        elif resp.status_code >= 400:
            console.print(
                f"[red] API error ({resp.status_code}): {resp.text}[/]", err=True
            )
            raise typer.Exit(1)
        return resp.json()
    except Exception as e:
        console.print(f"[red] Error: {e}[/]", err=True)
        raise typer.Exit(1)


# -------------------------
# Agent Commands (with Rich)
# -------------------------
@agent_app.command("list")
def agent_list():
    """List all agents in a beautiful table."""
    _ensure_api_available()
    data = _call_api("GET", "/agents")

    table = Table(title="🧠 OmniDaemon Agents", box=box.ROUNDED)
    table.add_column("Topic", style="cyan", no_wrap=True)
    table.add_column("Name", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Tools", style="dim")
    table.add_column("Description", style="dim")
    table.add_column("Config", style="magenta")

    for topic, agents in data.items():
        for agent in agents:
            status = (
                "[green]running[/]"
                if agent["status"] == "running"
                else "[red]stopped[/]"
            )
            tools = ", ".join(agent.get("tools", [])) or "—"
            table.add_row(
                topic,
                agent["name"],
                status,
                tools,
                agent.get("description", "—"),
                json.dumps(agent.get("config", {})),
            )

    console.print(table)


# @agent_app.command("start")
# def agent_start(topic: str, name: str):
#     """Start an agent."""
#     _ensure_api_available()
#     _call_api("POST", f"/agents/{topic}/{name}/start")
#     console.print(f"[green] Started agent '{name}' on topic '{topic}'[/]")


# @agent_app.command("stop")
# def agent_stop(topic: str, name: str):
#     """Stop an agent."""
#     _ensure_api_available()
#     _call_api("POST", f"/agents/{topic}/{name}/stop")
#     console.print(f"[yellow]⏹ Stopped agent '{name}' on topic '{topic}'[/]")


# @agent_app.command("start-all")
# def agent_start_all():
#     """Start all agents."""
#     _ensure_api_available()
#     _call_api("POST", "/agents/start")
#     console.print("[green] Started all agents[/]")


# @agent_app.command("stop-all")
# def agent_stop_all():
#     """Stop all agents."""
#     _ensure_api_available()
#     _call_api("POST", "/agents/stop")
#     console.print("[yellow]⏹ Stopped all agents[/]")


# -------------------------
# Task Commands
# -------------------------
@task_app.command("publish")
def task_publish(topic: str, payload: str, reply_to: str = None):
    """Publish a new task."""
    try:
        payload_dict = json.loads(payload)
    except json.JSONDecodeError:
        console.print("[red] Invalid JSON payload[/]", err=True)
        raise typer.Exit(1)

    body = {"topic": topic, "payload": payload_dict}
    if reply_to:
        body["reply_to"] = reply_to

    result = _call_api("POST", "/tasks", json=body)
    console.print(f"[green]✅ Task published: {result['task_id']}[/]")


@task_app.command("result")
def task_result(task_id: str):
    """Get task result."""
    _ensure_api_available()
    result = _call_api("GET", f"/tasks/{task_id}")
    console.print(
        Panel(
            json.dumps(result, indent=2),
            title=f"📥 Task Result: {task_id}",
            border_style="green",
        )
    )


# -------------------------
# Health Command
# -------------------------
@app.command()
def health():
    """Check OmniDaemon health."""
    _ensure_api_available()
    data = _call_api("GET", "/health")

    content = "\n".join(
        [
            f"[bold cyan]Runner ID:[/]\t{data['runner_id']}",
            f"[bold cyan]Status:[/]\t{data['status']}",
            f"[bold cyan]Event Bus:[/]\t{'✅ Connected' if data['event_bus_connected'] else '❌ Disconnected'}",
            f"[bold cyan]Topics:[/]\t{', '.join(data['subscribed_topics'])}",
            f"[bold cyan]Agents:[/]\t{data['agents']}",
            f"[bold cyan]Uptime:[/]\t{data['uptime_seconds']:.1f} seconds",
        ]
    )

    console.print(Panel(content, title="❤️ Health", border_style="green", expand=False))


# -------------------------
# Metrics Command
# -------------------------
@app.command()
def metrics():
    """Show detailed task processing metrics per agent."""
    _ensure_api_available()
    data = _call_api("GET", "/metrics")

    # Build rich table
    table = Table(title="📊 OmniDaemon Metrics", box=box.ROUNDED)
    table.add_column("Topic", style="cyan", no_wrap=True)
    table.add_column("Agent", style="green")
    table.add_column("Received", justify="right", style="dim")
    table.add_column("Processed", justify="right", style="green")
    table.add_column("Failed", justify="right", style="red")
    table.add_column("Avg Time (s)", justify="right", style="blue")
    table.add_column("Total Time (s)", justify="right", style="cyan")

    for topic, agents in data.items():
        for agent_name, stats in agents.items():
            table.add_row(
                topic,
                agent_name,
                str(stats["tasks_received"]),
                str(stats["tasks_processed"]),
                str(stats["tasks_failed"]),
                f"{stats['avg_processing_time_sec']:.3f}",
                f"{stats['total_processing_time']:.3f}",
            )

    console.print(table)


# -------------------------
# Info Command (ASCII Art)
# -------------------------
@app.command()
def info():
    """Show OmniDaemon info and ASCII art."""
    console.print(OMNIDAEMON_ASCII)
    console.print("\n[bold green]Welcome to OmniDaemon![/]\n")
    console.print("Use [bold]omni --help[/] to see available commands.\n")
    console.print(
        "[dim]💡 Tip: Start an agent script with OMNIDAEMON_API_ENABLED=true to use CLI control.[/]"
    )


# -------------------------
# Run Command (stub)
# -------------------------
@app.command()
def run(module: str):
    """Run agents from a Python module (e.g., 'agents.py')."""
    console.print(f"[cyan]Running agents from {module}... (not implemented yet)[/]")


# -------------------------
# Entry Point
# -------------------------
if __name__ == "__main__":
    app()
