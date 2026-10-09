"""Every file the package reads at runtime is in the release.

The wheel is built from the sdist, and the sdist listed only ``*.py`` files,
so 0.5.x shipped without the portable-evidence JSON schema: validating or
importing portable evidence from an installed release raised
``FileNotFoundError``. The source-tree suites passed, because the file is
there; the 0.6.0 gate ran the suite against the installed wheel and found it.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _sdist_patterns() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]


def _regex(pattern: str) -> re.Pattern[str]:
    # Hatch include patterns are gitignore-style: a leading "/" anchors at the
    # root, "**/" crosses any number of folders and "*" stays inside one.
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)


def _included(path: str, patterns: list[str]) -> bool:
    return any(_regex(pattern).fullmatch("/" + path) for pattern in patterns)


def test_the_pattern_reading_is_right():
    assert _included("src/omnicoreagent/a/b.py", ["/src/omnicoreagent/**/*.py"])
    assert _included("src/omnicoreagent/b.py", ["/src/omnicoreagent/**/*.py"])
    assert not _included("src/omnicoreagent/a/b.json", ["/src/omnicoreagent/**/*.py"])


def test_every_file_in_the_package_is_in_the_sdist():
    tracked = subprocess.run(
        ["git", "ls-files", "src/omnicoreagent"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    patterns = _sdist_patterns()
    left_out = [path for path in tracked if not _included(path, patterns)]
    assert not left_out, f"in the package but not in the sdist (so not in the wheel): {left_out}"
