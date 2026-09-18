"""Runs the end-to-end trajectory acceptance as part of the test suite."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "engineering"
    / "validation"
    / "trajectory_acceptance.py"
)


def _acceptance():
    spec = importlib.util.spec_from_file_location("trajectory_acceptance", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_checklist_item_holds_directly_served_and_under_default_capture(
    monkeypatch, capsys
):
    # The script points the workspace at its own temporary directory.
    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", "unset-by-script")
    _acceptance().run_all_scripted()

    output = capsys.readouterr().out
    assert output.count("10 checklist items passed") == 3, output


def test_committed_fixture_satisfies_the_checklist(capsys):
    _acceptance().check_fixture()

    assert "10 checklist items" in capsys.readouterr().out
