"""``omnicoreagent harbor``: Harbor's own CLI, with this runtime as the agent.

Every subcommand passes through untouched except for four things done around
it — a default agent, model names made routable, credentials put in the child's
environment and nowhere else, and plain sentences instead of tracebacks when
something a trial needs is missing. Each is held here; ``doctor`` and ``results`` have their own
tests.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from omnicoreagent.cli import cli
from omnicoreagent.cli.harbor import (
    DEFAULT_AGENT,
    HarborWrapperError,
    credential_environment,
    harbor_arguments,
    normalize_model,
)

KEY = "sk-test-not-a-real-key-000"


# --- the default agent ---------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["run", "-p", "task"],
        ["exec", "tasks/"],
        ["job", "start", "-p", "task"],
        ["trial", "start", "-p", "task"],
        ["job", "init", "-p", "task"],
        ["trial", "init", "-p", "task"],
    ],
)
def test_a_command_that_runs_an_agent_runs_this_one_by_default(args):
    argv = harbor_arguments(args)

    assert argv[argv.index("--agent") + 1] == DEFAULT_AGENT


@pytest.mark.parametrize(
    "agent_args",
    [["-a", "oracle"], ["--agent", "oracle"], ["--agent=oracle"], ["-aoracle"]],
)
def test_an_agent_named_by_the_user_is_kept(agent_args):
    argv = harbor_arguments(["run", "-p", "task", *agent_args])

    assert DEFAULT_AGENT not in argv
    assert argv == ["run", "-p", "task", *agent_args]


def test_a_config_file_names_its_own_agent():
    argv = harbor_arguments(["run", "-c", "job.yaml"])

    assert DEFAULT_AGENT not in argv


@pytest.mark.parametrize(
    "args",
    [["view", "jobs"], ["job", "resume", "-p", "jobs/x"], ["init", "--task", "org/t"], ["--version"], []],
)
def test_every_other_command_passes_through_untouched(args):
    assert harbor_arguments(args) == args


def test_the_default_goes_before_a_separator():
    argv = harbor_arguments(["run", "-p", "task", "--", "-a", "not-an-option"])

    assert argv.index("--agent") < argv.index("--")
    assert argv[-2:] == ["-a", "not-an-option"]


# --- model names ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "routed"),
    [
        ("gpt-5.6-terra", "openai/gpt-5.6-terra"),
        ("o3", "openai/o3"),
        ("o4-mini", "openai/o4-mini"),
        ("claude-sonnet-5", "anthropic/claude-sonnet-5"),
        ("gemini-2.5-pro", "gemini/gemini-2.5-pro"),
        ("deepseek-chat", "deepseek/deepseek-chat"),
        ("openai/gpt-5.6-terra", "openai/gpt-5.6-terra"),
        ("openrouter/meta/llama-4", "openrouter/meta/llama-4"),
    ],
)
def test_a_model_is_given_the_provider_it_is_routed_by(model, routed):
    assert normalize_model(model) == routed


def test_a_model_whose_provider_cannot_be_told_is_refused_with_a_sentence():
    """Guessing would be wrong in a way that costs a trial: LiteLLM routes a bare
    gemini name to Vertex, which wants cloud credentials, not a key."""
    with pytest.raises(HarborWrapperError, match="provider/model"):
        normalize_model("mystery-model-7b")


@pytest.mark.parametrize("flag", ["-m", "--model"])
def test_every_model_flag_is_normalized(flag):
    argv = harbor_arguments(["run", "-p", "t", flag, "gpt-5.6-terra", flag, "claude-sonnet-5"])

    assert "openai/gpt-5.6-terra" in argv
    assert "anthropic/claude-sonnet-5" in argv


def test_the_equals_spelling_of_a_model_is_normalized():
    argv = harbor_arguments(["run", "-p", "t", "--model=gpt-5.6-terra"])

    assert "--model=openai/gpt-5.6-terra" in argv


# --- credentials ---------------------------------------------------------------


def test_the_runtimes_key_is_handed_to_the_provider_under_its_own_name():
    env = credential_environment({"LLM_API_KEY": KEY}, ["run", "-m", "openai/gpt-5.6-terra"])

    assert env == {"OPENAI_API_KEY": KEY}


def test_a_key_the_user_already_set_is_never_replaced():
    env = credential_environment(
        {"LLM_API_KEY": KEY, "ANTHROPIC_API_KEY": "theirs"},
        ["run", "-m", "anthropic/claude-sonnet-5"],
    )

    assert env == {}


def test_a_provider_whose_credential_is_not_a_key_is_not_given_one():
    """Bedrock wants an AWS key pair; an LLM key under AWS_ACCESS_KEY_ID would
    be wrong, and wrong quietly."""
    env = credential_environment(
        {"LLM_API_KEY": KEY}, ["run", "-m", "bedrock/anthropic.claude-v2"]
    )

    assert env == {}


def test_without_a_key_nothing_is_added():
    assert credential_environment({}, ["run", "-m", "openai/gpt-5.6-terra"]) == {}


def test_the_key_can_come_from_a_dotenv_file_and_only_the_key(tmp_path):
    """Read, not loaded: the rest of that file is the user's business."""
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"LLM_API_KEY={KEY}\nDATABASE_URL=postgres://secret\n")

    env = credential_environment({}, ["run", "-m", "openai/gpt-5.6-terra"], dotenv=dotenv)

    assert env == {"OPENAI_API_KEY": KEY}


def test_the_key_never_reaches_the_arguments():
    """Arguments are visible to every user of the machine and are written into
    the job's config.json; the environment is neither."""
    args = ["run", "-p", "task", "-m", "gpt-5.6-terra"]
    argv = harbor_arguments(args)
    env = credential_environment({"LLM_API_KEY": KEY}, argv)

    assert KEY not in " ".join(argv)
    assert env["OPENAI_API_KEY"] == KEY


# --- the command itself --------------------------------------------------------


def _invoke(monkeypatch, args, env=None):
    """Run the command, capturing what it would hand to Harbor."""
    import omnicoreagent.cli.harbor as wrapper

    handed: dict = {}
    # What the developer's shell exports is not what these tests are about.
    for name in ("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def fake_exec(argv, environment):
        handed["argv"] = argv
        handed["env"] = environment
        return 0

    monkeypatch.setattr(wrapper, "_exec_harbor", fake_exec)
    monkeypatch.setattr(wrapper, "_docker_available", lambda: True)
    result = CliRunner().invoke(cli, ["harbor", *args], env=env or {}, catch_exceptions=False)
    return result, handed


def test_the_command_hands_harbor_its_arguments_and_the_key_separately(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result, handed = _invoke(
        monkeypatch, ["run", "-p", "task", "-m", "gpt-5.6-terra"], env={"LLM_API_KEY": KEY}
    )

    assert result.exit_code == 0, result.output
    assert handed["argv"][:1] == ["run"]
    assert "openai/gpt-5.6-terra" in handed["argv"]
    assert DEFAULT_AGENT in handed["argv"]
    assert handed["env"]["OPENAI_API_KEY"] == KEY
    assert KEY not in " ".join(handed["argv"])


def test_help_is_harbors_own(monkeypatch):
    result, handed = _invoke(monkeypatch, ["run", "--help"])

    assert handed["argv"] == ["run", "--help"]


def test_a_missing_docker_is_a_sentence(monkeypatch):
    import omnicoreagent.cli.harbor as wrapper

    monkeypatch.setattr(wrapper, "_docker_available", lambda: False)
    monkeypatch.setattr(wrapper, "_exec_harbor", lambda argv, env: 0)

    result = CliRunner().invoke(cli, ["harbor", "run", "-p", "task", "-m", "openai/x"])

    assert result.exit_code != 0
    assert "Docker" in result.output
    assert "Traceback" not in result.output


def test_viewing_results_does_not_need_docker(monkeypatch):
    import omnicoreagent.cli.harbor as wrapper

    handed = {}
    monkeypatch.setattr(wrapper, "_docker_available", lambda: False)
    monkeypatch.setattr(
        wrapper, "_exec_harbor", lambda argv, env: handed.setdefault("argv", argv) and 0
    )

    result = CliRunner().invoke(cli, ["harbor", "view", "jobs"])

    assert result.exit_code == 0, result.output
    assert handed["argv"] == ["view", "jobs"]


def test_a_missing_harbor_says_how_to_install_it(monkeypatch):
    import omnicoreagent.cli.harbor as wrapper

    monkeypatch.setattr(wrapper, "_harbor_installed", lambda: False)
    monkeypatch.setattr(wrapper, "_docker_available", lambda: True)

    result = CliRunner().invoke(cli, ["harbor", "run", "-p", "task"])

    assert result.exit_code != 0
    assert "omnicoreagent[harbor]" in result.output


def test_an_unroutable_model_is_a_sentence_not_a_traceback(monkeypatch):
    result, handed = _invoke(monkeypatch, ["run", "-p", "task", "-m", "mystery-model-7b"])

    assert result.exit_code != 0
    assert "provider/model" in result.output
    assert "argv" not in handed
