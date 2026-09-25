"""The README and the docs cannot drift from the code without a test failing.

A reader copies a block and runs it. Six blocks in the docs were not valid Python
(dictionary excerpts, and `OmniCoreAgent(...)` followed by a keyword with no
comma), and five pages built an agent without ever importing it. Each check
here is something a reader would otherwise find the hard way.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").rglob("*.mdx"))]
FENCE = re.compile(r"^([ \t]*)```(\w*)[^\n]*\n(.*?)^\1```", re.S | re.M)


def _blocks(languages: set[str]):
    for page in PAGES:
        text = page.read_text(encoding="utf-8")
        for match in FENCE.finditer(text):
            indent, language, body = match.groups()
            if language not in languages:
                continue
            body = "\n".join(
                line[len(indent):] if line.startswith(indent) else line
                for line in body.splitlines()
            )
            line = text[: match.start()].count("\n") + 1
            yield pytest.param(body, id=f"{page.relative_to(ROOT)}:{line}")


PYTHON = list(_blocks({"python", "py"}))
SHELL = list(_blocks({"bash", "sh", "shell"}))


def _tree(body: str):
    return compile(
        body, "<docs>", "exec", flags=ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
    )


@pytest.mark.parametrize("body", PYTHON)
def test_every_python_block_is_valid_python(body):
    _tree(body)


@pytest.mark.parametrize("body", PYTHON)
def test_every_import_from_omnicoreagent_exists(body):
    for node in ast.walk(_tree(body)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "omnicoreagent":
            module = importlib.import_module(node.module)
            for alias in node.names:
                if alias.name == "*" or hasattr(module, alias.name):
                    continue
                importlib.import_module(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "omnicoreagent":
                    importlib.import_module(alias.name)


def test_every_extra_named_exists():
    extras = set(
        tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    )
    unknown = []
    for page in PAGES:
        for group in re.findall(r"omnicoreagent\[([a-z0-9,\- ]+)\]", page.read_text()):
            unknown += [
                f"{page.relative_to(ROOT)}: [{name.strip()}]"
                for name in group.split(",")
                if name.strip() not in extras
            ]
    assert not unknown


def test_a_page_that_builds_an_agent_imports_it():
    missing = [
        str(page.relative_to(ROOT))
        for page in PAGES
        if "OmniCoreAgent(" in page.read_text()
        and not re.search(r"from omnicoreagent import (\(|[^\n]*\bOmniCoreAgent\b)", page.read_text())
    ]
    assert not missing


def test_every_page_in_the_nav_exists():
    nav = json.dumps(json.loads((ROOT / "docs.json").read_text()))
    missing = [
        page
        for page in set(re.findall(r'"(docs/[^"#]+)"', nav))
        if not (ROOT / f"{page}.mdx").exists() and not (ROOT / f"{page}.md").exists()
    ]
    assert not missing


def test_every_internal_link_resolves():
    broken = []
    for page in PAGES:
        text = page.read_text()
        for target in re.findall(r"\]\((/docs/[^)#\s]+)", text) + re.findall(r'href="(/docs/[^"#]+)"', text):
            path = target.lstrip("/").rstrip("/")
            if not (ROOT / f"{path}.mdx").exists() and not (ROOT / path / "index.mdx").exists():
                broken.append(f"{page.relative_to(ROOT)} -> {target}")
        for target in re.findall(r"\]\((\./[^)#\s]+)", text):
            if not (page.parent / target).exists():
                broken.append(f"{page.relative_to(ROOT)} -> {target}")
    assert not broken


def _subcommands(entry: str) -> set[str]:
    if entry == "omnicoreagent":
        from omnicoreagent.cli import cli

        return set(cli.commands)
    from omnicoreagent.serve import cli as serve_cli

    group = getattr(serve_cli, "cli", None) or getattr(serve_cli, "main", None)
    return set(getattr(group, "commands", {}) or {})


@pytest.mark.parametrize("body", SHELL)
def test_every_command_a_shell_block_runs_exists(body):
    for entry in ("omnicoreagent", "omniserve"):
        for used in re.findall(rf"(?m)^\s*(?:\$\s*)?{entry}\s+([a-z][\w-]*)", body):
            if used.startswith("-"):
                continue
            known = _subcommands(entry)
            if known:
                assert used in known, f"`{entry} {used}` is not a command ({sorted(known)})"
