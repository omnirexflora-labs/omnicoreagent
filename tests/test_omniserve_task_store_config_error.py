"""A task store named without its URL is a config error, not a traceback.

Found writing the OmniServe page (D7): OMNICOREAGENT_BACKGROUND_TASK_STORE=redis
without its URL ended `omniserve run` in a raw traceback, raised while the
server was built, when every other config mistake prints a clean
"Invalid OmniServe config" first.
"""

from __future__ import annotations

from click.testing import CliRunner

from omnicoreagent.serve.cli import cli


def test_a_redis_task_store_without_its_url_is_a_clean_config_error(tmp_path, monkeypatch):
    agent_file = tmp_path / "agent.py"
    agent_file.write_text("agent = None\n")
    monkeypatch.setenv("OMNICOREAGENT_BACKGROUND_TASK_STORE", "redis")
    monkeypatch.delenv("OMNICOREAGENT_BACKGROUND_TASK_STORE_URL", raising=False)

    result = CliRunner().invoke(cli, ["run", "--agent", str(agent_file)])

    assert result.exit_code != 0
    assert "Invalid OmniServe config" in result.output
    assert "OMNICOREAGENT_BACKGROUND_TASK_STORE_URL" in result.output
    assert "Traceback" not in result.output
