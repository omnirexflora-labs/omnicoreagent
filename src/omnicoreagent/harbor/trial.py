"""What a Harbor trial needs decided, with nothing of Harbor's imported.

The adapter in ``agent`` is thin on purpose: Harbor gives it an environment and
a context, and everything else — what the agent file in the container says, the
command that runs it, what a finished run reports, and the trajectory Harbor
reads afterwards — is here, where it can be tested without a task container.

Four things a harness has to get right, learned by running one
(``engineering/validation/harbor_trial``), are decided here rather than left to
whoever writes the configuration:

- the agent's workspace goes **outside** the task directory, or it appears among
  the files the verifier checks;
- host commands are allowed by an explicit rule, because every built-in profile
  denies or asks about them;
- the environment is passed through **by name**, so the task gets what it needs
  without the model's commands being handed every credential the process holds;
- the command is invoked as a module, because a console script is not always on
  PATH in a task container.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

# The version of Harbor's trajectory format this writes.
ATIF_SCHEMA_VERSION = "ATIF-v1.8"

# What a task's own tooling usually needs from the environment. Named, rather
# than inheriting everything: a provider key is not a task's business.
DEFAULT_ENVIRONMENT_PASSTHROUGH = (
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TERM",
    "TZ",
    "CARGO_HOME",
    "GOPATH",
    "GOROOT",
    "JAVA_HOME",
    "NODE_PATH",
    "npm_config_cache",
)

_AGENT_FILE = '''"""The agent for one Harbor trial, written by the adapter.

Commands run on this machine, in the task's own directory: the container is the
boundary, so an isolating sandbox would be running them in the wrong place.
Policy still governs every command — they are authorized as ``process.exec`` on
the ``host`` surface — so this allows exactly that.
"""

import os

from omnicoreagent import OmniCoreAgent
from omnicoreagent.governance import (
    PolicyEffect,
    PolicyRule,
    PolicyRuleConditions,
    build_default_policy,
)

TASK_DIR = {task_dir!r}
WORKSPACE_DIR = {workspace_dir!r}
MODEL = {model!r}
PROVIDER = {provider!r}
API_KEY_VARIABLES = {api_key_variables!r}
BASE_URL = {base_url!r}
MAX_STEPS = {max_steps!r}
COMMAND_TIMEOUT = {command_timeout!r}
CAPTURE = {capture!r}
PASSTHROUGH = {passthrough!r}
# The MCP servers the trial declared (the task's, and ``--mcp-config``).
MCP_SERVERS = {mcp_servers!r}
# Where Harbor put the trial's skills, or None.
SKILLS_DIR = {skills_dir!r}


def _policy():
    """A profile, with host commands allowed and the asks that block them gone.

    A deny always beats an allow, so this starts from a profile that asks rather
    than one that denies outright.
    """
    policy = build_default_policy("interactive-dev")
    dropped = {{"ask_process_exec", "ask_high_risk", "ask_sandbox_network"}}
    if MCP_SERVERS:
        # Declared by whoever set up the trial; asking about them would refuse
        # every call, since nobody is there to answer.
        dropped |= {{"ask_mcp_server_start", "ask_mcp_tool_call"}}
        policy.rules.allow[:0] = [
            PolicyRule(
                rule_id="allow_trial_mcp_servers",
                effect=PolicyEffect.ALLOW,
                capability="mcp.server.*",
            ),
            PolicyRule(
                rule_id="allow_trial_mcp_tools",
                effect=PolicyEffect.ALLOW,
                capability="tool.mcp.call",
            ),
        ]
    policy.rules.ask = [rule for rule in policy.rules.ask if rule.rule_id not in dropped]
    policy.rules.allow.insert(
        0,
        PolicyRule(
            rule_id="allow_host_commands",
            effect=PolicyEffect.ALLOW,
            capability="process.exec",
            conditions=PolicyRuleConditions(execution_surface="host"),
        ),
    )
    return policy


def _model_config():
    config = {{"provider": PROVIDER, "model": MODEL}}
    for name in (*API_KEY_VARIABLES, "LLM_API_KEY"):
        key = os.environ.get(name)
        if key:
            config["api_key"] = key
            break
    if BASE_URL:
        config["base_url"] = BASE_URL
    return config


agent = OmniCoreAgent(
    name="omnicoreagent",
    system_instruction=(
        "You are solving a task in the working directory. Use the execute tool "
        "to run shell commands: read files, change them, and check your work. "
        "Keep going until the task is done. Do not change the tests."
    ),
    model_config=_model_config(),
    mcp_tools=MCP_SERVERS,
    agent_config={{
        "max_steps": MAX_STEPS,
        "tool_call_timeout": COMMAND_TIMEOUT,
        "enable_workspace_files": True,
        "enable_agent_skills": bool(SKILLS_DIR),
        "skills_dir": SKILLS_DIR,
        # Outside the task's directory: the workspace would otherwise appear
        # among the files the verifier checks.
        "workspace_config": {{"workspace_dir": WORKSPACE_DIR}},
        "governance_config": {{
            "enabled": True,
            "policy": _policy(),
            "sandbox_config": {{
                "provider": "local",
                "options": {{"environment_passthrough": list(PASSTHROUGH)}},
            }},
            "sandbox_manifest": {{
                "working_dir": TASK_DIR,
                "network_policy": {{"default": "allow"}},
                "filesystem_policy": {{"default": "allow"}},
            }},
        }},
    }},
    telemetry_config={{
        "capture": CAPTURE,
        "storage": "jsonl",
        "storage_path": WORKSPACE_DIR + "/telemetry/traces.jsonl",
        "retention_days": None,
        "record_token_details": {record_token_details!r},
    }},
)
'''


def agent_file_source(
    *,
    task_dir: str,
    workspace_dir: str,
    model: str,
    provider: str,
    api_key_variables: tuple[str, ...] = ("LLM_API_KEY",),
    base_url: str | None = None,
    max_steps: int = 60,
    command_timeout: int = 300,
    capture: str = "full",
    record_token_details: bool = False,
    passthrough: tuple[str, ...] = DEFAULT_ENVIRONMENT_PASSTHROUGH,
    mcp_servers: list[dict[str, Any]] | None = None,
    skills_dir: str | None = None,
) -> str:
    """The agent file the adapter writes into the task container.

    It never holds a credential: the key is read from the environment Harbor put
    it in, under the names Harbor used — which depend on the provider, so the
    adapter passes the list it saw rather than guessing one.
    """
    return _AGENT_FILE.format(
        task_dir=task_dir,
        workspace_dir=workspace_dir,
        model=model,
        provider=provider,
        api_key_variables=tuple(api_key_variables),
        base_url=base_url,
        max_steps=max_steps,
        command_timeout=command_timeout,
        capture=capture,
        record_token_details=record_token_details,
        passthrough=tuple(passthrough),
        mcp_servers=list(mcp_servers or []),
        skills_dir=skills_dir,
    )


def run_command(
    *,
    instruction: str,
    agent_file: str,
    output_dir: str,
    task_dir: str,
    python: str = "python3",
    timeout_seconds: float | None = None,
    approval_mode: str = "deny",
    budget_mode: str = "stop",
    provenance: dict[str, str] | None = None,
    log_file: str | None = None,
) -> str:
    """The command that runs one trial, as a shell line.

    Run by the agent's own interpreter, not the task's: the runtime lives in a
    virtual environment of its own so that installing it cannot change a version
    the task depends on — and a modern Debian or Ubuntu refuses to install into
    its system Python at all (PEP 668).

    As a module, not a console script: a checkout on ``PYTHONPATH`` has no
    script, and a virtual environment that is not activated has it somewhere a
    harness would have to guess.
    """
    parts = [
        python,
        "-m",
        "omnicoreagent.cli",
        "run",
        "--agent",
        agent_file,
        "-i",
        instruction,
        "--approval-mode",
        approval_mode,
        "--budget-mode",
        budget_mode,
        "-o",
        output_dir,
    ]
    if timeout_seconds:
        parts += ["--timeout", str(int(timeout_seconds))]
    for key, value in sorted((provenance or {}).items()):
        parts += ["--provenance", f"{key}={value}"]
    command = " ".join(shlex.quote(part) for part in parts)
    # The task's own directory, so a command's relative paths mean what the
    # task means by them.
    command = f"cd {shlex.quote(task_dir)} && {command}"
    if log_file:
        command = f"{command} 2>&1 | tee {shlex.quote(log_file)}"
    return command


# --- what the container installs ------------------------------------------
#
# A harness must run the agent it was asked to run. PyPI's release can be behind
# the runtime running the command — it had no ``cli`` module when this adapter
# was written — so by default the container gets exactly this runtime: the same
# release when this is one, and otherwise a wheel built from this source.


def _is_release(version: str) -> bool:
    """A version PyPI can have: no development or local segment."""
    try:
        parsed = Version(version)
    except InvalidVersion:
        return False
    return not parsed.is_devrelease and parsed.local is None


def install_source(
    *,
    version: str,
    source_root: Path | str | None,
    spec: str | None = None,
    wheel: str | None = None,
    build: Callable[[Path], str],
) -> tuple[str, str]:
    """What the container installs, as ``("spec", requirement)`` or
    ``("wheel", host path)``.

    In order: a requirement the trial named; a wheel it named; this runtime's
    own release; a wheel built from this runtime's source. A development build
    with no source to build from is an error that says what to pass, never a
    silent fall back to whatever PyPI has.
    """
    if spec:
        return ("spec", spec)
    if wheel:
        if not Path(wheel).is_file():
            raise ValueError(f"There is no such wheel: {wheel}")
        return ("wheel", str(wheel))
    if _is_release(version):
        return ("spec", f"omnicoreagent=={version}")
    if source_root is not None and (Path(source_root) / "pyproject.toml").is_file():
        return ("wheel", build(Path(source_root)))
    raise ValueError(
        f"This runtime is a development build ({version}) with no source to build "
        "a wheel from, so the container cannot be given the same one. Pass "
        "--agent-kwarg install_spec=<requirement or URL> or wheel=<path>."
    )


def host_runtime() -> tuple[str, Path | None]:
    """This runtime's version, and its source checkout when it runs from one."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        found = version("omnicoreagent")
    except PackageNotFoundError:
        found = "0.0.0+unknown"
    import omnicoreagent

    root = Path(omnicoreagent.__file__).resolve().parents[2]
    pyproject = root / "pyproject.toml"
    is_ours = pyproject.is_file() and 'name = "omnicoreagent"' in pyproject.read_text()
    return found, (root if is_ours else None)


_built: dict[Path, str] = {}
_build_lock = threading.Lock()


def build_wheel(source_root: Path, *, version: str | None = None) -> str:
    """A wheel of the source at ``source_root``, built once per process.

    Trials run concurrently in one Harbor process; they share the build. A
    checkout without ``.git`` (a copied tree) has no version to derive, so it is
    built with the version the running runtime reports.
    """
    root = Path(source_root).resolve()
    with _build_lock:
        if root in _built and Path(_built[root]).is_file():
            return _built[root]
        out = Path(tempfile.mkdtemp(prefix="omnicoreagent-wheel-"))
        env = dict(os.environ)
        if not (root / ".git").exists() and version:
            env["UV_DYNAMIC_VERSIONING_BYPASS"] = version
        uv = shutil.which("uv")
        command = (
            [uv, "build", "--wheel", "--out-dir", str(out), str(root)]
            if uv
            else [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), str(root)]
        )
        completed = subprocess.run(
            command, env=env, capture_output=True, text=True, check=False
        )
        wheels = sorted(out.glob("omnicoreagent-*.whl"))
        if completed.returncode != 0 or not wheels:
            tail = (completed.stderr or completed.stdout or "").strip()[-800:]
            raise RuntimeError(f"Could not build a wheel of {root}: {tail}")
        _built[root] = str(wheels[-1])
        return _built[root]


# --- what the run wrote -------------------------------------------------------
#
# The headless command writes two files: ``result.json``, the verdict and what it
# spent, and ``trajectory.json``, the run's own trajectory. A run has a trace per
# segment — one per attempt, and a resumed run has several — so its steps live
# under ``segments[*].trajectory.steps`` rather than at the top level. Reading
# them from the wrong place is not a small mistake: Harbor accepted a trajectory
# with no steps in it, and the trial looked fine.


def _segments(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    """The traces of a run, newest attempt last; a single trace is one of them."""
    segments = trajectory.get("segments")
    if isinstance(segments, list) and segments:
        return [
            (segment or {}).get("trajectory") or {}
            for segment in segments
            if isinstance(segment, dict)
        ]
    return [trajectory]


def _totals(trajectory: dict[str, Any]) -> dict[str, Any]:
    """The run's totals, which a single trace carries under the same name."""
    totals = trajectory.get("totals")
    if isinstance(totals, dict) and totals:
        return totals
    for trace in _segments(trajectory):
        candidate = trace.get("totals")
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _mcp_servers_of(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    """Each MCP server's state at the start of the run, from the run header.

    A server that failed to start leaves the run without its tools and nothing
    else to show for it: the run goes on, and an agent may still find its way to
    an answer. The header records it; this puts it where Harbor reads.
    """
    servers: dict[str, dict[str, Any]] = {}
    for trace in _segments(trajectory):
        for server in (trace.get("harness") or {}).get("mcp_servers") or []:
            if not isinstance(server, dict) or not server.get("name"):
                continue
            entry = {
                "name": server["name"],
                "transport_type": server.get("transport_type"),
                "status": server.get("status"),
                "tool_count": server.get("tool_count"),
                "error": server.get("error"),
            }
            # The latest attempt's state wins.
            servers[server["name"]] = {k: v for k, v in entry.items() if v is not None}
    return list(servers.values())


def failed_mcp_servers(trajectory: dict[str, Any] | None) -> list[str]:
    """``name: error`` for each MCP server the run could not use."""
    return [
        f"{server['name']}: {server.get('error') or server.get('status')}"
        for server in _mcp_servers_of(trajectory or {})
        # The client reports connected, disconnected, failed or not_connected.
        if server.get("status") != "connected"
    ]


def _security_warnings_of(trajectory: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for trace in _segments(trajectory):
        for warning in (trace.get("harness") or {}).get("security_warnings") or []:
            code = warning.get("code") if isinstance(warning, dict) else None
            if code and code not in codes:
                codes.append(code)
    return codes


def usage_from_result(
    result: dict[str, Any] | None,
    *,
    trajectory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What a finished run cost, as Harbor's context wants it.

    ``result.json`` reports the tokens but not always the cost — the run's own
    totals hold that — so the trajectory answers what the result left out. A run
    that was interrupted or denied still reports what it spent up to that point;
    nothing here fails because a number is missing.
    """
    usage = ((result or {}).get("usage") or {}) if isinstance(result, dict) else {}
    details = usage.get("details") or {}
    totals = _totals(trajectory or {})
    tokens = totals.get("tokens") or {}

    def number(*candidates: Any) -> Any:
        for candidate in candidates:
            if candidate is not None:
                return candidate
        return None

    return {
        "n_input_tokens": int(
            number(usage.get("request_tokens"), tokens.get("input")) or 0
        ),
        "n_output_tokens": int(
            number(usage.get("response_tokens"), tokens.get("output")) or 0
        ),
        "n_cache_tokens": int(
            number(details.get("cached_input_tokens"), tokens.get("cached_input")) or 0
        ),
        "cost_usd": number(
            usage.get("estimated_cost_usd"), totals.get("estimated_cost_usd")
        ),
        "status": (result or {}).get("status"),
        "exit_code": (result or {}).get("exit_code"),
        # A step limit and a crash share status "error"; this tells them apart.
        "termination_reason": (result or {}).get("termination_reason"),
        "detail": _detail_of(result),
    }


def _detail_of(result: dict[str, Any] | None) -> str | None:
    """What a run that did not succeed said about why, briefly."""
    if not isinstance(result, dict) or result.get("status") in (None, "success"):
        return None
    for value in (result.get("error"), result.get("response")):
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
    return None


def _instruction_of(request: dict[str, Any]) -> str:
    """What the agent was asked, from the first request it recorded."""
    for message in reversed(request.get("messages") or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return ""


def _agent_turns(step: dict[str, Any]) -> list[dict[str, Any]]:
    """The model calls of a step that answered as the agent.

    A context summary is a call the runtime made for itself, not a turn, and a
    step that only summarised is not a step of the interaction.
    """
    return [
        call
        for call in (step.get("model_calls") or [])
        if isinstance(call, dict) and call.get("purpose", "agent_turn") == "agent_turn"
    ]


def _tool_calls_of(step: dict[str, Any]) -> list[dict[str, Any]]:
    calls = []
    for call in step.get("tool_calls") or []:
        raw = call.get("raw_arguments")
        arguments: Any = raw if raw is not None else call.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError):
                arguments = {"raw": arguments}
        if not isinstance(arguments, dict):
            arguments = {"value": arguments}
        calls.append(
            {
                "tool_call_id": call.get("tool_call_id") or "",
                "function_name": call.get("tool_name") or "",
                "arguments": arguments,
            }
        )
    return calls


def _observation_of(step: dict[str, Any]) -> dict[str, Any] | None:
    """What the tools answered, with how each command ran.

    The extra is what a reviewer of an evaluation would otherwise have to open
    our own trace for: whether the call succeeded, the exit code, which sandbox
    ran it and which policy rule allowed it.
    """
    results = []
    for call in step.get("tool_calls") or []:
        observation = call.get("observation") or {}
        content = observation.get("content")
        if content is None:
            error = call.get("error")
            if isinstance(error, dict):
                content = error.get("message") or json.dumps(error)
            elif error:
                content = str(error)
        if content is None:
            continue
        execution = next(iter(call.get("executions") or []), {}) or {}
        extra = {
            "outcome": call.get("outcome"),
            "exit_code": execution.get("exit_code"),
            "sandbox_provider": execution.get("sandbox_provider"),
            "matched_rule_ids": execution.get("matched_rule_ids"),
            "timed_out": True if execution.get("timed_out") else None,
        }
        entry: dict[str, Any] = {
            "source_call_id": call.get("tool_call_id"),
            "content": content if isinstance(content, str) else json.dumps(content),
        }
        extra = {key: value for key, value in extra.items() if value is not None}
        if extra:
            entry["extra"] = extra
        results.append(entry)
    return {"results": results} if results else None


def _logprobs_of(token_details: dict[str, Any]) -> list[float]:
    """The chosen tokens' logprobs, when the run recorded them."""
    content = token_details.get("content") if isinstance(token_details, dict) else None
    if not isinstance(content, list):
        return []
    found = []
    for token in content:
        value = token.get("logprob") if isinstance(token, dict) else None
        if isinstance(value, (int, float)):
            found.append(float(value))
    return found


def _metrics_of(call: dict[str, Any]) -> dict[str, Any] | None:
    facts = call.get("facts") or {}
    tokens = facts.get("tokens") or {}
    response = call.get("response") or {}
    metrics: dict[str, Any] = {
        "prompt_tokens": tokens.get("input"),
        "completion_tokens": tokens.get("output"),
        "cached_tokens": tokens.get("cached_input"),
        "cost_usd": facts.get("estimated_cost_usd"),
    }
    logprobs = _logprobs_of(response.get("token_details") or {})
    if logprobs:
        metrics["logprobs"] = logprobs
    # ATIF has no column for these, and they are what a reader asks first: how
    # the call ended, how long it took, what it cost according to whom, and
    # which provider response to go back to.
    extra = {
        # What actually answered, which can be a dated snapshot of the model the
        # harness asked for.
        "provider_model": _provider_model_of(call),
        "finish_reason": facts.get("finish_reason"),
        "reasoning_tokens": tokens.get("reasoning"),
        "cost_source": facts.get("cost_source"),
        "latency_ms": facts.get("latency_ms"),
        "provider_response_id": facts.get("provider_response_id"),
        "retries": len(facts["retries"]) if facts.get("retries") else None,
        "refused": True if facts.get("refused") else None,
    }
    extra = {key: value for key, value in extra.items() if value is not None}
    metrics = {key: value for key, value in metrics.items() if value is not None}
    if extra:
        metrics["extra"] = extra
    return metrics or None


def _provider_model_of(call: dict[str, Any]) -> str | None:
    """The model the provider says answered, as the run's facts record it."""
    facts = call.get("facts") or {}
    settings = facts.get("request_settings") or {}
    return facts.get("provider_model") or facts.get("model") or settings.get("model")


def _agent_step(
    *,
    step_id: int,
    step: dict[str, Any],
    call: dict[str, Any] | None,
    is_last_call: bool,
    model_name: str | None,
) -> dict[str, Any]:
    """One ATIF step: a model call, and the tools it then asked for.

    The message is what the model itself said, and nothing else. A step whose
    model only called a tool has an empty message; putting the last thing *sent*
    there instead would write the user's prompt into the agent's mouth, and
    whoever trains on this trajectory would learn it.
    """
    response = (call or {}).get("response") or {}
    message = response.get("content")
    entry: dict[str, Any] = {
        "step_id": step_id,
        "source": "agent",
        "message": message if isinstance(message, str) else json.dumps(message or ""),
        # The model as the harness names it, so the usage Harbor computes from
        # this file is keyed the way the rest of its results are; the provider's
        # own name for what answered is in the metrics.
        "model_name": model_name or (_provider_model_of(call) if call else None),
        "llm_call_count": 1 if call else None,
    }
    if step.get("started_at"):
        entry["timestamp"] = step["started_at"]
    # The tools belong to the call that asked for them, which is the step's last.
    if is_last_call:
        tool_calls = _tool_calls_of(step)
        if tool_calls:
            entry["tool_calls"] = tool_calls
        observation = _observation_of(step)
        if observation:
            entry["observation"] = observation
    metrics = _metrics_of(call) if call else None
    if metrics:
        entry["metrics"] = metrics
    return {key: value for key, value in entry.items() if value is not None}


def atif_trajectory(
    trajectory: dict[str, Any],
    *,
    agent_name: str,
    agent_version: str,
    session_id: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """A run's trajectory as Harbor's, so a trial here compares with one there.

    The first step is what the agent was asked; then one step per model call,
    carrying what the model said, what it asked the tools for, what they
    answered, and what the call cost. A step whose model call was not recorded
    (the privacy-first capture keeps no prompts) still appears with its tool
    calls, because the run did happen.
    """
    traces = _segments(trajectory)
    steps: list[dict[str, Any]] = []
    tool_definitions: list[dict[str, Any]] | None = None
    instruction = ""
    for trace in traces:
        for step in trace.get("steps") or []:
            if not isinstance(step, dict):
                continue
            calls = _agent_turns(step)
            for index, call in enumerate(calls):
                request = call.get("request") or {}
                if tool_definitions is None and request.get("tools"):
                    tool_definitions = request["tools"]
                if not instruction:
                    instruction = _instruction_of(request)
                steps.append(
                    _agent_step(
                        step_id=len(steps) + 1,
                        step=step,
                        call=call,
                        is_last_call=index == len(calls) - 1,
                        model_name=model_name,
                    )
                )
            if not calls:
                steps.append(
                    _agent_step(
                        step_id=len(steps) + 1,
                        step=step,
                        call=None,
                        is_last_call=True,
                        model_name=model_name,
                    )
                )

    status = trajectory.get("status") or next(
        (trace.get("status") for trace in traces if trace.get("status")), None
    )
    if instruction:
        # Renumber: ATIF requires the ids to run 1, 2, 3 … in order.
        for offset, step in enumerate(steps, start=2):
            step["step_id"] = offset
        steps.insert(
            0, {"step_id": 1, "source": "user", "message": instruction}
        )
    if not steps:
        # Harbor's model requires a step. A run that ended before it took one
        # says so, rather than leaving a file nobody can read.
        steps = [
            {
                "step_id": 1,
                "source": "system",
                "message": f"The run recorded no steps (status: {status or 'unknown'}).",
            }
        ]

    totals = _totals(trajectory)
    tokens = totals.get("tokens") or {}
    trace_ids = [trace["trace_id"] for trace in traces if trace.get("trace_id")]
    evidence = [
        trace.get("evidence_status") for trace in traces if trace.get("evidence_status")
    ]
    document: dict[str, Any] = {
        "schema_version": ATIF_SCHEMA_VERSION,
        "session_id": session_id
        or trajectory.get("session_id")
        or next((trace.get("session_id") for trace in traces), None),
        # Unique to this document: a run is one document, whatever it holds, and
        # a trace handed to us on its own is identified by its own id — a trace
        # carries the run's id too, so the order matters.
        "trajectory_id": (
            trajectory.get("run_id")
            if trajectory.get("segments")
            else trajectory.get("trace_id") or trajectory.get("run_id")
        )
        or next(iter(trace_ids), None),
        "agent": {
            "name": agent_name,
            "version": agent_version,
            "model_name": model_name,
            "tool_definitions": tool_definitions,
        },
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": tokens.get("input"),
            "total_completion_tokens": tokens.get("output"),
            "total_cached_tokens": tokens.get("cached_input"),
            "total_cost_usd": totals.get("estimated_cost_usd"),
            "total_steps": len(steps) or None,
        },
        # What a reader of this trajectory would otherwise have to guess: the run
        # it came from, its traces, and whether anything was left out of it.
        "extra": {
            "run_id": trajectory.get("run_id"),
            "status": status,
            "evidence_status": next(
                (state for state in evidence if state != "complete"),
                next(iter(evidence), None),
            ),
            "trace_ids": trace_ids or None,
            "attempt": trajectory.get("attempt"),
            "mcp_servers": _mcp_servers_of(trajectory) or None,
            "security_warnings": _security_warnings_of(trajectory) or None,
        },
    }
    document["agent"] = {
        key: value for key, value in document["agent"].items() if value is not None
    }
    document["final_metrics"] = {
        key: value
        for key, value in document["final_metrics"].items()
        if value is not None
    } or None
    document["extra"] = {
        key: value for key, value in document["extra"].items() if value is not None
    } or None
    return {key: value for key, value in document.items() if value is not None}
