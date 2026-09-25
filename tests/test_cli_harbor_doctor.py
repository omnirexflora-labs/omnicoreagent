"""``omnicoreagent harbor doctor``: whether a trial can run here, before one is spent.

Each thing a trial needs is checked and named: Python, Harbor, Docker, a key
for the model (present — never printed), and what the container will install.
``--container`` goes further and installs the agent into a real task container.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from omnicoreagent.cli import cli

KEY = "sk-test-not-a-real-key-000"


@pytest.fixture
def healthy(monkeypatch, tmp_path):
    import omnicoreagent.cli.harbor_doctor as doctor

    monkeypatch.chdir(tmp_path)
    # What the developer's shell exports is not what these tests are about.
    for name in ("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(doctor, "_harbor_version", lambda: "0.23.0")
    monkeypatch.setattr(doctor, "_docker_server_version", lambda: "27.1.1")
    monkeypatch.setattr(doctor, "host_runtime", lambda: ("0.3.10", None))
    return doctor


def _doctor(args, env=None):
    return CliRunner().invoke(cli, ["harbor", "doctor", *args], env=env or {})


def test_a_healthy_machine_passes_every_check(healthy):
    result = _doctor(["-m", "gpt-5.6-terra"], env={"LLM_API_KEY": KEY})

    assert result.exit_code == 0, result.output
    for name in ("python", "harbor", "docker", "model", "runtime"):
        assert f"ok    {name}" in result.output
    assert "openai/gpt-5.6-terra" in result.output
    assert "OPENAI_API_KEY" in result.output


def test_the_key_is_never_printed(healthy):
    result = _doctor(["-m", "openai/gpt-5.6-terra"], env={"LLM_API_KEY": KEY})

    assert KEY not in result.output
    assert KEY[:8] not in result.output


def test_a_missing_key_fails_and_names_what_to_set(healthy):
    result = _doctor(["-m", "openai/gpt-5.6-terra"])

    assert result.exit_code == 1
    assert "FAIL  model" in result.output
    assert "LLM_API_KEY" in result.output


def test_a_key_the_user_set_under_the_providers_own_name_is_found(healthy):
    result = _doctor(["-m", "anthropic/claude-sonnet-5"], env={"ANTHROPIC_API_KEY": KEY})

    assert result.exit_code == 0, result.output
    assert "ANTHROPIC_API_KEY" in result.output


def test_an_unroutable_model_fails_the_model_check(healthy):
    result = _doctor(["-m", "mystery-model-7b"], env={"LLM_API_KEY": KEY})

    assert result.exit_code == 1
    assert "provider/model" in result.output


def test_without_a_model_the_model_check_is_skipped_not_failed(healthy):
    result = _doctor([])

    assert result.exit_code == 0, result.output
    assert "skip  model" in result.output


def test_docker_not_running_fails_with_a_sentence(healthy, monkeypatch):
    monkeypatch.setattr(healthy, "_docker_server_version", lambda: None)

    result = _doctor([])

    assert result.exit_code == 1
    assert "FAIL  docker" in result.output
    assert "Traceback" not in result.output


def test_harbor_missing_fails_and_says_how_to_install_it(healthy, monkeypatch):
    monkeypatch.setattr(healthy, "_harbor_version", lambda: None)

    result = _doctor([])

    assert result.exit_code == 1
    assert "omnicoreagent[harbor]" in result.output


def test_a_released_runtime_says_the_container_gets_the_same_release(healthy):
    result = _doctor([])

    assert "omnicoreagent==0.3.10" in result.output


def test_a_development_runtime_with_its_source_says_a_wheel_is_built(healthy, monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'omnicoreagent'\n")
    monkeypatch.setattr(healthy, "host_runtime", lambda: ("0.3.10.dev5+abc", tmp_path))

    result = _doctor([])

    assert result.exit_code == 0, result.output
    assert "wheel" in result.output
    assert str(tmp_path) in result.output


def test_a_development_runtime_without_source_fails_the_runtime_check(healthy, monkeypatch):
    monkeypatch.setattr(healthy, "host_runtime", lambda: ("0.3.10.dev5+abc", None))

    result = _doctor([])

    assert result.exit_code == 1
    assert "FAIL  runtime" in result.output
    assert "install_spec" in result.output


def test_the_container_check_runs_an_install_only_trial(healthy, monkeypatch):
    """What it hands Harbor: this agent, the model, and --install-only."""
    handed = {}

    def fake_install_only(model, environment):
        handed["model"] = model
        handed["key_in_env"] = environment.get("OPENAI_API_KEY") == KEY
        return None  # no error

    monkeypatch.setattr(healthy, "_install_only_trial", fake_install_only)

    result = _doctor(["-m", "gpt-5.6-terra", "--container"], env={"LLM_API_KEY": KEY})

    assert result.exit_code == 0, result.output
    assert "ok    container" in result.output
    assert handed == {"model": "openai/gpt-5.6-terra", "key_in_env": True}


def test_a_failed_container_install_is_reported_with_its_reason(healthy, monkeypatch):
    monkeypatch.setattr(
        healthy, "_install_only_trial", lambda model, env: "NonZeroAgentExitCodeError: No matching distribution"
    )

    result = _doctor(["-m", "gpt-5.6-terra", "--container"], env={"LLM_API_KEY": KEY})

    assert result.exit_code == 1
    assert "FAIL  container" in result.output
    assert "No matching distribution" in result.output
