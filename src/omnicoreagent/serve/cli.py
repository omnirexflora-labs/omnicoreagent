"""
OmniServe CLI - Command-line interface for quick server deployment.

Usage:
    omniserve run --agent path/to/agent.py --port 8000
    omniserve quickstart --provider gemini --model gemini-2.0-flash
    omniserve config --show
"""

import os
import sys
import importlib.util
from pathlib import Path
from typing import Optional

import click


def _load_agent_from_file(path: str):
    """
    Load an agent from a Python file.

    The file should define an `agent` variable or an `create_agent()` function.
    """
    file_path = Path(path).resolve()
    if not file_path.exists():
        raise click.ClickException(f"Agent file not found: {path}")

    # Load the module
    spec = importlib.util.spec_from_file_location("agent_module", file_path)
    if spec is None or spec.loader is None:
        raise click.ClickException(f"Failed to load module from: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_module"] = module

    # Add the file's directory to path for relative imports
    sys.path.insert(0, str(file_path.parent))

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise click.ClickException(f"Error loading agent file: {e}")

    # Look for agent variable or create_agent function
    if hasattr(module, "agent"):
        return module.agent
    elif hasattr(module, "create_agent"):
        return module.create_agent()
    else:
        raise click.ClickException(
            "Agent file must define an 'agent' variable or 'create_agent()' function"
        )


@click.group()
@click.version_option(version="0.0.1", prog_name="omniserve")
def cli():
    """OmniServe - Production-ready API server for AI agents.

    Deploy OmniCoreAgent as a REST/SSE API with a single command.
    """
    pass


@cli.command()
@click.option(
    "--agent",
    "-a",
    type=click.Path(exists=True),
    help="Path to Python file containing the agent",
)
@click.option("--host", "-h", default=None, help="Host to bind to (default: 0.0.0.0)")
@click.option(
    "--port", "-p", default=None, type=int, help="Port to bind to (default: 8000)"
)
@click.option("--workers", "-w", default=1, type=int, help="Number of workers")
@click.option("--no-docs", is_flag=True, help="Disable Swagger UI")
@click.option("--cors-origins", default="*", help="Comma-separated CORS origins")
@click.option("--auth-token", default=None, help="Enable auth with this token")
@click.option(
    "--rate-limit", default=None, type=int, help="Rate limit (requests per minute)"
)
def run(
    agent: Optional[str],
    host: Optional[str],
    port: Optional[int],
    workers: int,
    no_docs: bool,
    cors_origins: str,
    auth_token: Optional[str],
    rate_limit: Optional[int],
):
    """Run an agent as an API server.

    Example:
        omniserve run --agent my_agent.py --port 8000
    """
    from omnicoreagent import OmniServe, OmniServeConfig

    if agent is None:
        raise click.ClickException(
            "Please specify an agent file with --agent or use 'omniserve quickstart'"
        )

    # Load the agent
    click.echo(f"📦 Loading agent from: {agent}")
    loaded_agent = _load_agent_from_file(agent)
    click.echo(f"✅ Loaded agent: {loaded_agent.name}")

    # Build config
    config = OmniServeConfig(
        host=host or "0.0.0.0",
        port=port or 8000,
        workers=workers,
        enable_docs=not no_docs,
        cors_origins=[o.strip() for o in cors_origins.split(",")],
        auth_enabled=auth_token is not None,
        auth_token=auth_token,
        rate_limit_enabled=rate_limit is not None,
        rate_limit_requests=rate_limit or 100,
        rate_limit_window=60,
    )

    # Start server
    click.echo("")
    click.echo("=" * 50)
    click.echo("🚀 OmniServe v0.0.1")
    click.echo("=" * 50)
    click.echo(f"Agent: {loaded_agent.name}")
    click.echo(f"Server: http://{config.host}:{config.port}")
    if config.enable_docs:
        click.echo(f"Docs: http://{config.host}:{config.port}/docs")
    click.echo(f"Metrics: http://{config.host}:{config.port}/prometheus")
    click.echo("")
    click.echo("Features Enabled:")
    click.echo(
        f"  • Auth: {'✓ (Bearer token)' if config.auth_enabled else '✗ (use --auth-token to enable)'}"
    )
    click.echo(
        f"  • Rate Limit: {'✓ ' + str(config.rate_limit_requests) + '/min' if config.rate_limit_enabled else '✗ (use --rate-limit N to enable)'}"
    )
    click.echo(f"  • CORS: {config.cors_origins}")
    click.echo(
        f"  • Background API: {'✓' if config.background_enabled else '✗'}, "
        f"task store: {config.background_task_store}"
    )
    click.echo("")
    click.echo(
        "💡 Available options: --auth-token, --rate-limit, --cors-origins, --no-docs"
    )
    click.echo("   Run 'omniserve run --help' for all options")
    click.echo("=" * 50)
    click.echo("")

    server = OmniServe(loaded_agent, config=config)
    server.start()


@cli.command()
@click.option(
    "--provider",
    "-p",
    default="gemini",
    help="LLM provider (openai, gemini, anthropic)",
)
@click.option("--model", "-m", default="gemini-2.0-flash", help="Model name")
@click.option("--name", "-n", default="QuickAgent", help="Agent name")
@click.option(
    "--instruction",
    "-i",
    default="You are a helpful AI assistant.",
    help="System instruction",
)
@click.option("--port", default=8000, type=int, help="Port to bind to")
@click.option("--host", default="0.0.0.0", help="Host to bind to")
def quickstart(
    provider: str,
    model: str,
    name: str,
    instruction: str,
    port: int,
    host: str,
):
    """Start a quick agent server without writing any code.

    Example:
        omniserve quickstart --provider openai --model gpt-4o --port 8000
    """
    from omnicoreagent import OmniCoreAgent, OmniServe, OmniServeConfig

    click.echo(f"🚀 Creating {provider}/{model} agent...")

    # Create agent
    agent = OmniCoreAgent(
        name=name,
        system_instruction=instruction,
        model_config={
            "provider": provider,
            "model": model,
        },
        debug=False,
    )

    config = OmniServeConfig(
        host=host,
        port=port,
        enable_docs=True,
        cors_origins=["*"],
    )

    click.echo("")
    click.echo("=" * 50)
    click.echo("🚀 OmniServe v0.0.1 - Quickstart")
    click.echo("=" * 50)
    click.echo(f"Agent: {name}")
    click.echo(f"Model: {provider}/{model}")
    click.echo(f"Server: http://{host}:{port}")
    click.echo(f"Docs: http://{host}:{port}/docs")
    click.echo(f"Metrics: http://{host}:{port}/prometheus")
    click.echo("")
    click.echo("Features (default):")
    click.echo("  • Auth: ✗ (use 'omniserve run' with --auth-token to enable)")
    click.echo("  • Rate Limit: ✗ (use 'omniserve run' with --rate-limit to enable)")
    click.echo("  • CORS: * (all origins)")
    click.echo("")
    click.echo("💡 For more control, use 'omniserve run --agent my_agent.py'")
    click.echo("   Options: --auth-token, --rate-limit, --cors-origins")
    click.echo("=" * 50)
    click.echo("")
    click.echo("Test with:")
    click.echo(f"  curl -X POST http://{host}:{port}/run/sync \\")
    click.echo('    -H "Content-Type: application/json" \\')
    click.echo('    -d \'{"query": "Hello!"}\'')
    click.echo("")

    server = OmniServe(agent, config=config)
    server.start()


@cli.command("config")
@click.option(
    "--show", is_flag=True, help="Show current configuration from environment"
)
@click.option("--env-example", is_flag=True, help="Print example shell environment")
def config_cmd(show: bool, env_example: bool):
    """View or generate configuration.

    Example:
        omniserve config --show
        eval "$(omniserve config --env-example)"
    """
    from omnicoreagent.serve import OmniServeConfig

    if env_example:
        click.echo("""# OmniCoreAgent / OmniServe
# Most local runs need only the model key.
export LLM_API_KEY=your_api_key_here

# Optional server overrides
# export OMNICOREAGENT_SERVE_PORT=8000
# export OMNICOREAGENT_SERVE_HOST=0.0.0.0
# export OMNICOREAGENT_SERVE_AUTH_ENABLED=true
# export OMNICOREAGENT_SERVE_AUTH_TOKEN=change-me
# export OMNICOREAGENT_SERVE_RATE_LIMIT_ENABLED=true
# export OMNICOREAGENT_SERVE_RATE_LIMIT_REQUESTS=100

# Optional background task persistence
# Background APIs and the worker are enabled by default with in-memory task state.
# Use SQL when tasks, runs, attempts, leases, and retries must survive restarts.
# export OMNICOREAGENT_BACKGROUND_TASK_STORE=sql
# export OMNICOREAGENT_BACKGROUND_TASK_STORE_URL=sqlite:///.omnicoreagent/background.db
""")
        return

    if show:
        config = OmniServeConfig.from_env()
        click.echo("Current OmniServe Configuration:")
        click.echo("-" * 40)
        for key, value in config.model_dump().items():
            # Mask auth token
            if key == "auth_token" and value:
                value = value[:4] + "****" + value[-4:] if len(value) > 8 else "****"
            click.echo(f"  {key}: {value}")
        return

    click.echo("Use --show to view current config or --env-example for template")


@cli.command("generate-dockerfile")
@click.option(
    "--file",
    "-f",
    "file_path",
    type=click.Path(exists=True),
    help="Path to your agent Python file",
)
@click.option(
    "--output-dir", "-o", default=".", help="Output directory for generated files"
)
def generate_dockerfile(file_path: str, output_dir: str):
    """Generate a Dockerfile for deploying your agent.

    Works both locally and on cloud platforms (Cloud Run, AWS Fargate, Railway).
    - Copies all files into the image
    - Uses environment variables for configuration

    Example:
        omniserve generate-dockerfile --file my_agent.py
    """
    from rich.console import Console

    console = Console()
    console.print("[bold blue]🚀 OmniServe Cloud Deployment Generator[/bold blue]")
    console.print(
        "Generates a cloud-ready Dockerfile for Cloud Run, AWS Fargate, Railway.\n"
    )

    if not file_path:
        console.print(
            "[bold red]Error:[/bold red] Please specify agent file with --file"
        )
        return

    # Calculate relative path from project root to agent
    try:
        rel_path = os.path.relpath(Path(file_path).resolve(), Path.cwd())
    except ValueError:
        rel_path = os.path.basename(file_path)

    # Inspect agent to detect workspace file tools
    workspace_files_enabled = False
    try:
        loaded_agent = _load_agent_from_file(file_path)
        if loaded_agent.agent_config and isinstance(loaded_agent.agent_config, dict):
            workspace_files_enabled = bool(
                loaded_agent.agent_config.get("enable_workspace_files", True)
            )
        console.print(f"[green]✓ Detected agent: {loaded_agent.name}[/green]")
        if workspace_files_enabled:
            console.print("[dim]Workspace file tools enabled[/dim]")
        else:
            console.print("[dim]No workspace file tools configured[/dim]")
    except Exception as e:
        console.print(f"[yellow]Warning: Could not inspect agent ({e})[/yellow]")

    out_path = Path(output_dir)
    out_path.mkdir(exist_ok=True)

    # Build Dockerfile content
    # Only non-sensitive defaults go in Dockerfile
    # Secrets (API keys, S3/R2 creds) are passed at runtime with -e
    env_lines = [
        "# Agent path",
        f"ENV AGENT_PATH=/app/{rel_path}",
        "",
        "# Workspace storage for artifacts and workspace files",
        "ENV OMNICOREAGENT_WORKSPACE_BACKEND=local",
        "ENV OMNICOREAGENT_WORKSPACE_DIR=/tmp/workspace",
    ]

    env_block = "\n".join(env_lines)

    dockerfile_content = f"""FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "omnicoreagent[serve]"

# Copy entire project into image
COPY . /app

{env_block}

EXPOSE 8000

CMD ["sh", "-c", "omniserve run --agent $AGENT_PATH"]
"""

    dockerfile_path = out_path / "Dockerfile"
    with open(dockerfile_path, "w") as f:
        f.write(dockerfile_content)

    console.print(f"\n[bold green]✓ Generated {dockerfile_path}[/bold green]")

    console.print("\n[bold]Next Steps:[/bold]")
    console.print("1. Build the image:")
    console.print("   docker build -t omniserver .")

    console.print("\n2. Run with local workspace:")
    console.print("   docker run -p 8000:8000 -e LLM_API_KEY=$LLM_API_KEY omniserver")

    console.print("\n   Or run with S3/R2 workspace persistence:")
    console.print("   docker run -p 8000:8000 \\")
    console.print("     -e LLM_API_KEY=$LLM_API_KEY \\")
    console.print("     -e OMNICOREAGENT_WORKSPACE_BACKEND=s3 \\")
    console.print("     -e AWS_S3_BUCKET=your-bucket \\")
    console.print("     -e AWS_ACCESS_KEY_ID=... \\")
    console.print("     -e AWS_SECRET_ACCESS_KEY=... \\")
    console.print("     omniserver")

    console.print(
        "\n[yellow]⚠ Local workspace is ephemeral unless you mount a volume.[/yellow]"
    )


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
