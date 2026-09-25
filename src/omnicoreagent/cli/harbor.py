"""``omnicoreagent harbor``: Harbor's own CLI, with this runtime as the agent.

    omnicoreagent harbor run -p ./my-task -m gpt-5.6-terra
    omnicoreagent harbor run -d terminal-bench@2.0 -m claude-sonnet-5 -n 4
    omnicoreagent harbor view jobs

Every Harbor subcommand passes through untouched except for four things done
around it:

- **a default agent**: a command that runs an agent runs this one unless it is
  told otherwise (``-a``) or given a config file that names one;
- **model names routed**: a bare family name gets the provider prefix LiteLLM
  routes on, and a name whose provider cannot be told is refused rather than
  guessed;
- **credentials in the environment and nowhere else**: ``LLM_API_KEY`` is handed
  to the provider under the name Harbor reads for it, in the child's
  environment — never in its arguments, which every user of the machine can see
  and Harbor writes into the job's ``config.json``;
- **sentences, not tracebacks**, when Docker or the ``harbor`` extra is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import click

DEFAULT_AGENT = "omnicoreagent.harbor.agent:OmniCoreAgentHarbor"

# Commands that run an agent, and so get this one by default.
_AGENT_COMMANDS = {
    ("run",),
    ("exec",),
    ("job", "start"),
    ("trial", "start"),
    ("job", "init"),
    ("trial", "init"),
}
# Commands that start containers, and so need Docker.
_DOCKER_COMMANDS = {
    ("run",),
    ("exec",),
    ("check",),
    ("analyze",),
    ("job", "start"),
    ("job", "resume"),
    ("job", "regrade"),
    ("trial", "start"),
    ("trial", "regrade"),
    ("task", "start-env"),
}
_GROUPS = {"job", "trial", "task", "dataset", "hub", "cache", "plugins", "auth", "version", "agent", "adapter"}

# Bare model names whose provider is not in doubt. Anything else must be named
# ``provider/model``: LiteLLM routes a bare gemini name to Vertex, for one, which
# wants cloud credentials rather than a key — a guess that fails a trial late.
_MODEL_FAMILIES = (
    (("gpt-", "chatgpt-", "o1", "o3", "o4"), "openai"),
    (("claude-",), "anthropic"),
    (("gemini-",), "gemini"),
    (("deepseek-",), "deepseek"),
)


class HarborWrapperError(click.ClickException):
    """A problem stated as a sentence, before Harbor is started."""


def _before_separator(args: Sequence[str]) -> list[str]:
    args = list(args)
    return args[: args.index("--")] if "--" in args else args


def _command_path(args: Sequence[str]) -> tuple[str, ...]:
    words = [arg for arg in _before_separator(args) if not arg.startswith("-")]
    if not words:
        return ()
    if words[0] in _GROUPS and len(words) > 1:
        return (words[0], words[1])
    return (words[0],)


def _has_option(args: Sequence[str], short: str, long: str) -> bool:
    for arg in _before_separator(args):
        if arg in (short, long) or arg.startswith((f"{long}=", f"{short}=")):
            return True
        if arg.startswith(short) and not arg.startswith("--") and len(arg) > len(short):
            return True
    return False


def _insert_before_separator(args: list[str], extra: list[str]) -> list[str]:
    at = args.index("--") if "--" in args else len(args)
    return [*args[:at], *extra, *args[at:]]


def normalize_model(model: str) -> str:
    """``model`` as ``provider/model``, or a sentence saying to name one."""
    if "/" in model:
        return model
    lowered = model.lower()
    for prefixes, provider in _MODEL_FAMILIES:
        if lowered.startswith(prefixes):
            return f"{provider}/{model}"
    raise HarborWrapperError(
        f"Cannot tell which provider serves {model!r}. Name it as provider/model, "
        "for example openai/gpt-5.6-terra or anthropic/claude-sonnet-5."
    )


def _normalize_models(args: list[str]) -> list[str]:
    out: list[str] = []
    it = iter(enumerate(args))
    separator = args.index("--") if "--" in args else len(args)
    for index, arg in it:
        if index >= separator:
            out.append(arg)
        elif arg in ("-m", "--model") and index + 1 < separator:
            out.append(arg)
            _, value = next(it)
            out.append(normalize_model(value))
        elif arg.startswith(("--model=", "-m=")):
            flag, _, value = arg.partition("=")
            out.append(f"{flag}={normalize_model(value)}")
        else:
            out.append(arg)
    return out


def harbor_arguments(args: Sequence[str]) -> list[str]:
    """What Harbor is run with: the user's arguments, with this agent as the
    default and model names routed."""
    argv = list(args)
    if (
        _command_path(argv) in _AGENT_COMMANDS
        and not _has_option(argv, "-a", "--agent")
        and not _has_option(argv, "-c", "--config")
    ):
        argv = _insert_before_separator(argv, ["--agent", DEFAULT_AGENT])
    return _normalize_models(argv)


def _models(args: Sequence[str]) -> list[str]:
    found: list[str] = []
    before = _before_separator(args)
    for index, arg in enumerate(before):
        if arg in ("-m", "--model") and index + 1 < len(before):
            found.append(before[index + 1])
        elif arg.startswith(("--model=", "-m=")):
            found.append(arg.partition("=")[2])
    return found


def _provider_key_names(provider: str) -> tuple[str, ...]:
    """The variables Harbor reads a provider's key from, from Harbor's own table."""
    try:
        from harbor.agents import model_connection
    except ImportError:
        return ()
    aliases = getattr(model_connection, "_PROVIDER_ALIASES", {})
    access = model_connection.PROVIDERS.get(aliases.get(provider, provider))
    return tuple(access.api_key_envs) if access else ()


def credential_environment(
    environment: Mapping[str, str],
    args: Sequence[str],
    *,
    dotenv: Path | None = None,
) -> dict[str, str]:
    """What to add to Harbor's environment so the model's provider has a key.

    ``LLM_API_KEY`` — from the environment, or read (not loaded) from ``dotenv``
    — goes under the first name Harbor reads for the provider. A key the user set
    under that name is theirs and is never replaced; a provider whose credential
    is not a key (an AWS key pair, a cloud project) is given nothing.
    """
    key = environment.get("LLM_API_KEY")
    if not key and dotenv is not None and dotenv.is_file():
        from dotenv import dotenv_values

        key = dotenv_values(dotenv).get("LLM_API_KEY")
    if not key:
        return {}
    added: dict[str, str] = {}
    for model in _models(args):
        provider = model.split("/", 1)[0] if "/" in model else None
        names = _provider_key_names(provider) if provider else ()
        if not names or any(environment.get(name) for name in names):
            continue
        name = names[0]
        if not name.endswith(("API_KEY", "TOKEN")):
            continue
        added[name] = key
    return added


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _harbor_installed() -> bool:
    try:
        import harbor.cli.main  # noqa: F401
    except ImportError:
        return False
    return True


def _exec_harbor(argv: list[str], environment: dict[str, str]) -> int:
    """Become Harbor: its exit code, signals and terminal are the command's own."""
    command = [sys.executable, "-m", "harbor.cli.main", *argv]
    if os.name == "posix":
        os.execve(sys.executable, command, environment)
    return subprocess.run(command, env=environment, check=False).returncode


def run_harbor(args: Sequence[str]) -> int:
    args = list(args)
    asks_help = any(arg in ("--help", "-h") for arg in _before_separator(args))
    if not _harbor_installed():
        raise HarborWrapperError(
            "Harbor is not installed. Install it with: pip install 'omnicoreagent[harbor]'"
        )
    if _command_path(args) in _DOCKER_COMMANDS and not asks_help and not _docker_available():
        raise HarborWrapperError(
            "Docker is required to run a Harbor trial, and `docker` is not on PATH. "
            "Install Docker, or start it, and try again."
        )
    argv = args if asks_help else harbor_arguments(args)
    environment = dict(os.environ)
    environment.update(
        credential_environment(environment, argv, dotenv=Path.cwd() / ".env")
    )
    return _exec_harbor(argv, environment)


@click.command(
    "harbor",
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
        "allow_interspersed_args": False,
        "help_option_names": [],
    },
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def harbor_command(args: tuple[str, ...]) -> None:
    """Harbor's CLI with this runtime as the default agent (see `harbor --help`).

    Two commands are this runtime's own, and neither name is Harbor's:
    `results` (what a job did) and `doctor` (whether a trial can run here).
    """
    own = _own_commands()
    if args and args[0] in own:
        own[args[0]].main(
            args=list(args[1:]),
            prog_name=f"omnicoreagent harbor {args[0]}",
            standalone_mode=True,
        )
        return
    sys.exit(run_harbor(args))


def _own_commands() -> dict[str, click.Command]:
    from omnicoreagent.cli.harbor_doctor import doctor_command
    from omnicoreagent.cli.harbor_results import results_command

    return {"results": results_command, "doctor": doctor_command}
