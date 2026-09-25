"""No ignore rule leaves the package's own source out of the wheel.

The wheel is built from what git does not ignore. 0.3.9 was published with
`core/runtime/config.py` importing `omnicoreagent.core.workspace`, but an
unanchored `workspace/` rule kept that package out of the wheel: every
`OmniCoreAgent(...)` on 0.3.9 fails with ModuleNotFoundError.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="needs the git checkout",
)
def test_no_source_file_of_the_package_is_ignored():
    sources = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "src" / "omnicoreagent").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=ROOT,
        input="\n".join(sources),
        capture_output=True,
        text=True,
    ).stdout.split()
    assert not ignored, f"left out of the wheel: {ignored}"
