"""The guard release: pip on Python 3.10 or 3.11 fails loudly instead of quietly.

OmniCoreAgent 0.4 needs Python 3.12. On 3.10 or 3.11 pip did not fail: it
installed 0.3.9, the last release for those versions, which cannot even build
an agent. An outside review (2026-09-28) called that a footgun documented
rather than fixed. The guard is a source-only 0.3.10 for exactly those
versions, whose build stops with one message saying what to do.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "packaging" / "python-guard"


def _project(path: Path) -> dict:
    return tomllib.loads(path.read_text())["project"]


def test_the_guard_covers_exactly_the_pythons_the_package_does_not():
    guard = SpecifierSet(_project(GUARD / "pyproject.toml")["requires-python"])
    package = SpecifierSet(_project(ROOT / "pyproject.toml")["requires-python"])
    for minor in range(8, 16):
        python = Version(f"3.{minor}")
        assert not (python in guard and python in package), python
    assert Version("3.10") in guard and Version("3.11") in guard


def test_the_guard_sorts_above_the_last_old_release_and_below_every_new_one():
    # pip on 3.10/3.11 picks the newest release it may install: the guard, not
    # 0.3.9. Below 0.4, PyPI's latest and the version badge stay on the real one.
    guard = _project(GUARD / "pyproject.toml")
    assert guard["name"] == "omnicoreagent"
    assert Version("0.3.9") < Version(guard["version"]) < Version("0.4.0")


def test_building_the_guard_fails_with_what_to_do():
    built = subprocess.run(
        [sys.executable, "setup.py", "egg_info"], cwd=GUARD, capture_output=True, text=True
    )
    assert built.returncode != 0
    message = built.stderr + built.stdout
    assert "OmniCoreAgent needs Python 3.12 or later" in message
    assert "uv python install 3.12" in message
