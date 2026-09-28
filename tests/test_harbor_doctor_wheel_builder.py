"""The Harbor doctor fails a development runtime nothing can build.

Found writing the Harbor page (D8): with no `uv` on PATH, the wheel is built
with `python -m pip`, and a venv made by uv has no pip; the doctor said "ok
runtime" and every trial then errored with "No module named pip".
"""

from __future__ import annotations

import pytest

from omnicoreagent.cli import harbor_doctor


@pytest.fixture
def development_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(harbor_doctor, "host_runtime", lambda: ("0.4.2.dev1", tmp_path))
    monkeypatch.setattr(
        harbor_doctor, "install_source", lambda **kwargs: ("wheel", f"a wheel built from {tmp_path}")
    )


def test_no_uv_and_no_pip_fails_the_runtime_check(development_runtime, monkeypatch, capsys):
    monkeypatch.setattr(harbor_doctor.shutil, "which", lambda name: None)
    monkeypatch.setattr(harbor_doctor.importlib.util, "find_spec", lambda name: None)
    report = harbor_doctor._Report()

    harbor_doctor._check_runtime(report)

    assert report.failed
    assert "uv" in capsys.readouterr().out


def test_uv_on_path_passes(development_runtime, monkeypatch):
    monkeypatch.setattr(harbor_doctor.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    report = harbor_doctor._Report()

    harbor_doctor._check_runtime(report)

    assert not report.failed
