"""Whether a Harbor trial of this agent can run here, before one is spent.

    omnicoreagent harbor doctor -m gpt-5.6-terra
    omnicoreagent harbor doctor -m gpt-5.6-terra --container

Each thing a trial needs, checked and named: Python, Harbor, a Docker daemon
that answers, a key for the model's provider (present or not — never its
value), and what the task container will install. ``--container`` installs the
agent into a real, throwaway task container with Harbor's ``--install-only``.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import click

from omnicoreagent.cli.harbor import (
    DEFAULT_AGENT,
    HarborWrapperError,
    _provider_key_names,
    credential_environment,
    normalize_model,
)
from omnicoreagent.harbor.trial import host_runtime, install_source

_TASK_TOML = """schema_version = "1.4"
artifacts = []

[task]
name = "omnicoreagent-labs/doctor"
version = "1.0.0"
description = "A container to install the agent into, and nothing else."

[verifier]
timeout_sec = 60.0

[agent]
timeout_sec = 60.0

[environment]
network_mode = "public"
build_timeout_sec = 600.0
os = "linux"
"""
_DOCKERFILE = """FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \\
      python3 python3-pip python3-venv ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
"""


def _harbor_version() -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("harbor")
    except PackageNotFoundError:
        return None


def _docker_server_version() -> str | None:
    """The daemon's version when it answers, else None."""
    if shutil.which("docker") is None:
        return None
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = completed.stdout.strip()
    return found if completed.returncode == 0 and found else None


def _install_only_trial(
    model: str, environment: dict[str, str], agent_kwargs: tuple[str, ...] = ()
) -> str | None:
    """Install the agent into a throwaway task container; the error, or None."""
    from omnicoreagent.cli.harbor_results import summarize_job

    with tempfile.TemporaryDirectory(prefix="omnicoreagent-doctor-") as scratch:
        task = Path(scratch) / "doctor"
        (task / "environment").mkdir(parents=True)
        (task / "tests").mkdir()
        (task / "task.toml").write_text(_TASK_TOML)
        (task / "instruction.md").write_text("Nothing to do.\n")
        (task / "environment" / "Dockerfile").write_text(_DOCKERFILE)
        (task / "tests" / "test.sh").write_text("#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n")
        jobs = Path(scratch) / "jobs"
        completed = subprocess.run(
            [
                sys.executable, "-m", "harbor.cli.main", "run",
                "-p", str(task), "-a", DEFAULT_AGENT, "-m", model,
                "-o", str(jobs), "-n", "1", "-y", "-q", "--install-only",
                *(item for kwarg in agent_kwargs for item in ("--ak", kwarg)),
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        job_dirs = [path for path in jobs.iterdir() if path.is_dir()] if jobs.is_dir() else []
        if not job_dirs:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-3:]
            return "Harbor did not start a trial: " + " ".join(tail)
        for trial in summarize_job(job_dirs[0])["trials"]:
            if trial["error"]:
                return trial["error"]
    return None


class _Report:
    def __init__(self) -> None:
        self.failed = False

    def line(self, state: str, name: str, detail: str) -> None:
        if state == "FAIL":
            self.failed = True
        click.echo(f"  {state:<5} {name:<10} {detail}")


def _check_model(report: _Report, model: str | None) -> str | None:
    if not model:
        report.line("skip", "model", "no -m given; pass one to check its key")
        return None
    try:
        routed = normalize_model(model)
    except HarborWrapperError as exc:
        report.line("FAIL", "model", exc.message)
        return None
    provider = routed.split("/", 1)[0]
    names = _provider_key_names(provider)
    own = next((name for name in names if os.environ.get(name)), None)
    handed = credential_environment(os.environ, ["-m", routed], dotenv=Path.cwd() / ".env")
    if own:
        report.line("ok", "model", f"{routed}: key present in {own}")
    elif handed:
        report.line(
            "ok", "model", f"{routed}: key present (LLM_API_KEY, handed on as {next(iter(handed))})"
        )
    else:
        wanted = " or ".join(names[:1]) or "the provider's key"
        report.line("FAIL", "model", f"{routed}: no key; set LLM_API_KEY (or {wanted})")
        return None
    return routed


def _check_runtime(
    report: _Report, *, spec: str | None = None, wheel: str | None = None
) -> None:
    version, root = host_runtime()
    try:
        kind, source = install_source(
            version=version,
            source_root=root,
            spec=spec,
            wheel=wheel,
            build=lambda path: f"a wheel built from {path}",
        )
    except ValueError as exc:
        report.line("FAIL", "runtime", str(exc))
        return
    if kind == "spec":
        report.line("ok", "runtime", f"{version}; the container installs {source}")
        return
    if wheel:
        report.line("ok", "runtime", f"{version}; the container installs the wheel {source}")
        return
    # A development runtime is built into a wheel with uv, or else with this
    # Python's pip; a venv made by uv has no pip, and the doctor said "ok"
    # while every trial then failed with "No module named pip".
    if shutil.which("uv") is None and importlib.util.find_spec("pip") is None:
        report.line(
            "FAIL",
            "runtime",
            f"{version} (development) must be built into a wheel, and neither uv "
            "is on PATH nor pip in this Python: install uv, or pip",
        )
        return
    report.line("ok", "runtime", f"{version} (development); the container installs {source}")


@click.command("doctor")
@click.option("-m", "--model", default=None, help="The model a trial will use, to check its key.")
@click.option(
    "--container",
    is_flag=True,
    help="Also install the agent into a throwaway task container (takes a few minutes).",
)
@click.option(
    "--ak",
    "--agent-kwarg",
    "agent_kwargs",
    multiple=True,
    help="As for `run`: install_spec=<requirement or URL> or wheel=<path> is what the container installs.",
)
def doctor_command(model: str | None, container: bool, agent_kwargs: tuple[str, ...]) -> None:
    """Check that a Harbor trial of this agent can run on this machine."""
    report = _Report()
    click.echo("omnicoreagent harbor doctor")
    report.line("ok", "python", f"{platform.python_version()} ({sys.executable})")
    harbor = _harbor_version()
    if harbor:
        report.line("ok", "harbor", harbor)
    else:
        report.line("FAIL", "harbor", "not installed: pip install 'omnicoreagent[harbor]'")
    docker = _docker_server_version()
    if docker:
        report.line("ok", "docker", f"daemon {docker} answers")
    else:
        report.line("FAIL", "docker", "no Docker daemon answers `docker info`; install or start Docker")
    routed = _check_model(report, model)
    # The same --ak a trial would get: a development build with no source
    # could never pass without it (the See and Improve stranger test).
    chosen = dict(item.split("=", 1) for item in agent_kwargs if "=" in item)
    _check_runtime(report, spec=chosen.get("install_spec"), wheel=chosen.get("wheel"))
    if container:
        if report.failed or not routed:
            report.line("skip", "container", "fix the checks above (and pass -m) first")
        else:
            environment = dict(os.environ)
            environment.update(
                credential_environment(environment, ["-m", routed], dotenv=Path.cwd() / ".env")
            )
            error = _install_only_trial(routed, environment, agent_kwargs)
            if error:
                report.line("FAIL", "container", error)
            else:
                report.line("ok", "container", "the agent installed into a task container")
    click.echo("  one or more checks failed" if report.failed else "  ready for a trial")
    sys.exit(1 if report.failed else 0)
