"""OmniCoreAgent as a Harbor agent: installed in the task container, run there.

Harbor hands an installed agent an environment and a context. This puts the
runtime in the container, writes the agent file, runs one headless run, and
reports what the run says — tokens, cost, and a trajectory in Harbor's own
format so a trial of this agent compares with a trial of any other.

Everything it decides lives in ``omnicoreagent.harbor.trial``, which imports
nothing of Harbor's; this module is the part that speaks to Harbor.

    harbor run --agent omnicoreagent.harbor:OmniCoreAgentHarbor \
      --model openai/gpt-5.6-terra --task-id <task>
"""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from typing import Annotated, Any, ClassVar, override

from pydantic import Field

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import BaseInstalledAgent
from harbor.agents.model_connection import (
    ModelConnectionSpec,
    ResolvedModelConnection,
)
from harbor.agents.options import Cli, InstalledAgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.utils.env import is_sensitive_env_key

from omnicoreagent.harbor.trial import (
    DEFAULT_ENVIRONMENT_PASSTHROUGH,
    agent_file_source,
    atif_trajectory,
    build_wheel,
    failed_mcp_servers,
    host_runtime,
    install_source,
    run_command,
    usage_from_result,
)

# Where the agent's own files go: outside the task's directory, so they are not
# among the files a verifier checks.
AGENT_DIR = "/installed-agent/omnicoreagent"
WORKSPACE_DIR = f"{AGENT_DIR}/workspace"
AGENT_FILE = f"{AGENT_DIR}/trial_agent.py"
# Harbor gives the agent /logs/agent as its own directory, and reads the ATIF
# trajectory from trajectory.json there. The run's own output — which includes a
# trajectory of ours by the same name — goes in a subdirectory of it, so the two
# do not collide.
AGENT_LOGS = "/logs/agent"
OUTPUT_DIR = f"{AGENT_LOGS}/omnicoreagent"
# What the run wrote, as the host sees it: logs_dir *is* the agent directory.
NATIVE_SUBDIR = "omnicoreagent"
# The runtime gets an interpreter of its own. Installing it into the task's
# Python could change a version the task depends on, and a modern Debian or
# Ubuntu refuses that install outright (PEP 668).
VENV_DIR = f"{AGENT_DIR}/venv"
PYTHON = f"{VENV_DIR}/bin/python"
# tiktoken downloads its encodings on first use, and a task may close the network
# while the agent runs; they are fetched at install, while it is open.
TIKTOKEN_CACHE_DIR = f"{AGENT_DIR}/tiktoken"
_ENCODINGS = ("cl100k_base", "o200k_base")


class OmniCoreAgentOptions(InstalledAgentOptions):
    """What a trial can change, as ``harbor run --agent-kwarg`` flags."""

    max_steps: Annotated[int, Cli("--max-steps")] = Field(
        default=60, description="Model turns the agent may take."
    )
    command_timeout: Annotated[int, Cli("--command-timeout")] = Field(
        default=300, description="Seconds any one shell command may run."
    )
    approval_mode: Annotated[str, Cli("--approval-mode")] = Field(
        default="deny",
        description=(
            "What answers an approval nobody is there to give: stop, allow, deny."
        ),
    )
    budget_mode: Annotated[str, Cli("--budget-mode")] = Field(
        default="stop", description="What answers an exhausted budget: stop or deny."
    )
    capture: Annotated[str, Cli("--capture")] = Field(
        default="full",
        description=(
            "What telemetry records: full keeps what the model was sent, which a "
            "trainer or a reviewer needs; default is privacy-first."
        ),
    )
    record_token_details: Annotated[bool, Cli("--record-token-details")] = Field(
        default=False,
        description="Keep the tokens the model chose and their logprobs.",
    )
    completion_review: Annotated[int, Cli("--completion-review")] = Field(
        default=0,
        ge=0,
        le=3,
        description=(
            "How many times the agent's final answer is reviewed before it is "
            "accepted: it is asked for each requirement and the check that showed "
            "it, and works on where it has none. Off (0) by default: on a hard "
            "task, five trials each way showed no gain for twice the cost."
        ),
    )
    run_timeout: Annotated[int | None, Cli("--run-timeout")] = Field(
        default=None,
        description=(
            "Seconds the run gives itself. Harbor enforces the task's own agent "
            "timeout but does not tell an installed agent what it is, and a run "
            "stopped from outside writes no result and no trajectory. Set this a "
            "little under the task's [agent] timeout_sec to have the run end "
            "itself and leave its evidence behind."
        ),
    )
    install_spec: Annotated[str | None, Cli("--install-spec")] = Field(
        default=None,
        description=(
            "What the container installs instead of this runtime: a pip "
            "requirement, a path in the container, or a VCS URL."
        ),
    )
    wheel: Annotated[str | None, Cli("--wheel")] = Field(
        default=None,
        description=(
            "A wheel on this machine to upload and install. By default the "
            "container gets this very runtime: its release, or a wheel built "
            "from its source when it is a development build."
        ),
    )


class OmniCoreAgentHarbor(BaseInstalledAgent):
    """The OmniCoreAgent runtime, solving a Harbor task in its own container."""

    capabilities = AgentCapabilities(atif=True)
    MODEL_CONNECTION: ClassVar[ModelConnectionSpec | None] = ModelConnectionSpec(
        passthrough=True
    )
    options_model = OmniCoreAgentOptions

    @staticmethod
    @override
    def name() -> str:
        return "omnicoreagent"

    @override
    def version(self) -> str | None:
        return self._version or "latest"

    @override
    def get_version_command(self) -> str | None:
        return f"{PYTHON} -m pip show omnicoreagent | sed -n 's/^Version: //p'"

    @override
    def parse_version(self, stdout: str) -> str:
        return stdout.strip().splitlines()[-1].strip() if stdout.strip() else "unknown"

    # --- what the model is, and how its key arrives -----------------------

    def _option(self, name: str, default: Any) -> Any:
        """One of this agent's own options, or its default when none was given."""
        return getattr(self.options, name, default) if self.options is not None else default

    @property
    def _provider(self) -> str:
        access: ResolvedModelConnection = self.model_connection
        return access.provider or self._parsed_model_provider or "openai"

    @property
    def _model(self) -> str:
        return self._parsed_model_name or (self.model_name or "")

    def _api_key_variables(self) -> tuple[str, ...]:
        """The variables Harbor put the credential in, for the agent file to read.

        Harbor resolves a provider's credentials into an environment mapping; the
        names differ by provider, so the adapter passes what it actually saw. The
        values never reach a file or a command line from here.
        """
        access: ResolvedModelConnection = self.model_connection
        names = tuple(
            name
            for name in (access.env or {})
            if "API_KEY" in name.upper() or "TOKEN" in name.upper()
        )
        fallback = {
            "openai": ("OPENAI_API_KEY",),
            "anthropic": ("ANTHROPIC_API_KEY",),
            "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            "azure": ("AZURE_API_KEY",),
        }.get(self._provider, ("LLM_API_KEY",))
        return names or fallback

    def _model_env(self) -> dict[str, str]:
        """What the run needs in its environment: the provider's own variables."""
        access: ResolvedModelConnection = self.model_connection
        return {
            "PYTHONUNBUFFERED": "1",
            "TIKTOKEN_CACHE_DIR": TIKTOKEN_CACHE_DIR,
            # LiteLLM otherwise fetches its price table from GitHub on every
            # start — slow, and cut where the network is closed.
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            **{k: v for k, v in (access.env or {}).items()},
        }

    # --- install ----------------------------------------------------------

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(
            environment,
            ("python3", "python_pip", "python_venv", "git", "curl", "ca_certificates"),
        )
        kind, source = await self._install_source()
        await self.exec_as_root(
            environment,
            command=(
                f"mkdir -p {AGENT_DIR} {WORKSPACE_DIR} {OUTPUT_DIR} {AGENT_DIR}/dist && "
                f"chmod -R 777 {AGENT_DIR} {AGENT_LOGS}"
            ),
        )
        if kind == "wheel":
            remote = f"{AGENT_DIR}/dist/{Path(source).name}"
            await environment.upload_file(source, remote)
            await self.exec_as_root(environment, command=f"chmod 644 {shlex.quote(remote)}")
            specification = shlex.quote(remote)
        else:
            specification = shlex.quote(source)
        await self.exec_as_agent(
            environment,
            command=(
                "set -eu; "
                # The task's Python stays as the task left it.
                f"python3 -m venv {VENV_DIR}; "
                f"{PYTHON} -m pip install --quiet --upgrade pip; "
                f"{PYTHON} -m pip install --quiet {specification}; "
                f"{PYTHON} -c 'import omnicoreagent; print(omnicoreagent.__version__ "
                "if hasattr(omnicoreagent, \"__version__\") else \"installed\")'"
            ),
            timeout_sec=900,
        )
        # Not fatal: without the cache the run counts tokens by estimate.
        encodings = ", ".join(repr(name) for name in _ENCODINGS)
        await self.exec_as_agent(
            environment,
            command=(
                f"mkdir -p {TIKTOKEN_CACHE_DIR} && "
                f"TIKTOKEN_CACHE_DIR={TIKTOKEN_CACHE_DIR} {PYTHON} -c "
                + shlex.quote(
                    f"import tiktoken\nfor name in ({encodings},): tiktoken.get_encoding(name)"
                )
                + " || echo 'tiktoken encodings not fetched; token counts will be estimates'"
            ),
            timeout_sec=300,
        )

    async def _install_source(self) -> tuple[str, str]:
        """This runtime for the container, unless the trial named another."""
        version, root = host_runtime()
        return await asyncio.to_thread(
            install_source,
            version=version,
            source_root=root,
            spec=self._option("install_spec", None),
            wheel=self._option("wheel", None),
            build=lambda path: build_wheel(path, version=version),
        )

    # --- what the trial gave the agent -----------------------------------

    _MCP_TRANSPORTS = {"stdio": "stdio", "sse": "sse", "streamable-http": "streamable_http"}

    def _mcp_servers(self) -> list[dict[str, Any]]:
        """The trial's MCP servers, in the runtime's own shape."""
        servers = []
        for server in self.mcp_servers:
            transport = self._MCP_TRANSPORTS.get(server.transport)
            if transport is None:
                raise ValueError(
                    f"MCP server {server.name!r} uses transport "
                    f"{server.transport!r}, which this agent cannot connect to."
                )
            entry: dict[str, Any] = {"name": server.name, "transport_type": transport}
            if transport == "stdio":
                entry["command"] = server.command
                if server.args:
                    entry["args"] = list(server.args)
            else:
                entry["url"] = server.url
            servers.append(entry)
        return servers

    def _passthrough(self) -> tuple[str, ...]:
        """The names the model's commands may see: the defaults, and what the
        trial set with ``--ae`` — except a credential, which stays with the run."""
        added = tuple(
            name
            for name in self.extra_env
            if not is_sensitive_env_key(name) and name not in DEFAULT_ENVIRONMENT_PASSTHROUGH
        )
        return DEFAULT_ENVIRONMENT_PASSTHROUGH + added

    # --- run --------------------------------------------------------------

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        await super().setup(environment)
        await self._write_agent_file(environment)

    async def _write_agent_file(self, environment: BaseEnvironment) -> None:
        source = agent_file_source(
            task_dir=await self._task_dir(environment),
            workspace_dir=WORKSPACE_DIR,
            model=self._model,
            provider=self._provider,
            api_key_variables=self._api_key_variables(),
            base_url=getattr(self.model_connection, "configured_base_url", None),
            max_steps=self._option("max_steps", 60),
            command_timeout=self._option("command_timeout", 300),
            capture=self._option("capture", "full"),
            record_token_details=bool(self._option("record_token_details", False)),
            passthrough=self._passthrough(),
            mcp_servers=self._mcp_servers(),
            skills_dir=self.skills_dir,
            completion_review=int(self._option("completion_review", 0)),
        )
        await self._upload_config_text(
            environment,
            content=source,
            remote_path=AGENT_FILE,
            filename="trial_agent.py",
        )

    async def _task_dir(self, environment: BaseEnvironment) -> str:
        """The directory the task's own commands run in."""
        result = await self.exec_as_agent(environment, command="pwd")
        found = (getattr(result, "stdout", "") or "").strip().splitlines()
        return found[-1].strip() if found else "/app"

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        command = run_command(
            instruction=self.render_instruction(instruction),
            agent_file=AGENT_FILE,
            output_dir=OUTPUT_DIR,
            task_dir=await self._task_dir(environment),
            python=PYTHON,
            timeout_seconds=self._option("run_timeout", None),
            approval_mode=self._option("approval_mode", "deny"),
            budget_mode=self._option("budget_mode", "stop"),
            provenance={
                "adapter": "harbor",
                "harbor_agent": self.name(),
            },
            log_file=f"{OUTPUT_DIR}/omnicoreagent.txt",
        )
        # A non-zero exit is a finished trial with a verdict, not a broken run:
        # the exit code names the terminal state and result.json explains it.
        await self.exec_as_agent(
            environment,
            command=f"({command}) || true",
            env=self._model_env(),
        )

    # --- what the run said ------------------------------------------------

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        """Read what the trial wrote, after Harbor has synced the logs back."""
        result = self._read_json(self.logs_dir / NATIVE_SUBDIR / "result.json")
        trajectory = self._read_json(self.logs_dir / NATIVE_SUBDIR / "trajectory.json")
        # The result reports the tokens; the run's totals hold the cost and what
        # the provider served from its cache.
        usage = usage_from_result(result, trajectory=trajectory)
        context.n_input_tokens = usage["n_input_tokens"]
        context.n_output_tokens = usage["n_output_tokens"]
        context.n_cache_tokens = usage["n_cache_tokens"]
        if usage["cost_usd"] is not None:
            context.cost_usd = usage["cost_usd"]
        metadata = dict(context.metadata or {})
        metadata.update(
            {
                key: value
                for key, value in (
                    ("omnicoreagent_status", usage["status"]),
                    ("omnicoreagent_exit_code", usage["exit_code"]),
                    ("omnicoreagent_run_id", (result or {}).get("run_id")),
                    ("omnicoreagent_termination_reason", usage["termination_reason"]),
                    ("omnicoreagent_detail", usage["detail"]),
                )
                if value is not None
            }
        )
        failed = failed_mcp_servers(trajectory)
        if failed:
            # The run went on without these servers' tools; a reward earned
            # without them is not the reward the task meant to measure.
            metadata["omnicoreagent_mcp_failed"] = failed
            self.logger.warning(
                "MCP servers the run could not use: %s", "; ".join(failed)
            )
        context.metadata = metadata
        # Harbor reads the per-model usage out of this file itself, right after
        # this call, so it is written before returning rather than later.
        self._write_atif(result, trajectory)

    def _write_atif(
        self, result: dict[str, Any] | None, trajectory: dict[str, Any] | None
    ) -> None:
        if not trajectory:
            self.logger.debug("No trajectory to convert; the run wrote none")
            return
        try:
            document = atif_trajectory(
                trajectory,
                agent_name=self.name(),
                agent_version=self.version() or "unknown",
                session_id=self.session_id or (result or {}).get("session_id"),
                model_name=self.model_name,
            )
            (self.logs_dir / "trajectory.json").write_text(
                json.dumps(document, indent=2, default=str)
            )
        except Exception as exc:  # the trial's verdict does not depend on this
            self.logger.debug(f"Could not convert the trajectory to ATIF: {exc}")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None
