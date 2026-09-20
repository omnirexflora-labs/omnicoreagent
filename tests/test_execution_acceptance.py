"""Runs the governed execution acceptance as part of the test suite."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "engineering" / "validation" / "execution_acceptance.py"
)


def _acceptance():
    spec = importlib.util.spec_from_file_location("execution_acceptance", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_records_satisfy_the_checklist(capsys):
    _acceptance().check_fixture()

    assert "8 checklist items passed" in capsys.readouterr().out


def test_the_scenario_satisfies_the_checklist_for_real(capsys, monkeypatch):
    pytest.importorskip("pydantic_monty", reason="code mode needs omnicoreagent[codemode]")
    from test_execute_tool import _docker_available

    if not _docker_available():
        pytest.skip("the Docker daemon is not reachable")
    # The script points the workspace at its own temporary directory.
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", "unset-by-script")

    _acceptance().run_scripted()

    assert "8 checklist items passed" in capsys.readouterr().out
