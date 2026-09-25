"""`pip install "omnicoreagent[all]"` installs every extra an application uses.

It once left out code mode and every sandbox provider, so a reader who installed
"all" found code mode and `execute` unavailable. Harbor is the one extra kept
out: it is evaluation tooling, not part of an application.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

NOT_IN_ALL = {"all", "harbor"}


def _extras() -> dict[str, list[str]]:
    data = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    return data["project"]["optional-dependencies"]


def _name(requirement: str) -> str:
    return re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].lower()


def test_every_application_extra_is_in_all():
    extras = _extras()
    in_all = {_name(dep) for dep in extras["all"]}

    missing = {
        extra: [dep for dep in deps if _name(dep) not in in_all]
        for extra, deps in extras.items()
        if extra not in NOT_IN_ALL
    }

    assert not {k: v for k, v in missing.items() if v}, missing


def test_all_asks_for_the_same_versions_as_each_extra():
    extras = _extras()
    in_all = {_name(dep): dep for dep in extras["all"]}

    for extra, deps in extras.items():
        if extra in NOT_IN_ALL:
            continue
        for dep in deps:
            assert in_all[_name(dep)] == dep, f"{extra}: {dep} vs {in_all[_name(dep)]}"
