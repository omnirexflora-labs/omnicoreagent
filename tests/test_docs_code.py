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
OUTPUT = list(_blocks({"text"}))


def _tree(body: str):
    return compile(
        body, "<docs>", "exec", flags=ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
    )


@pytest.mark.parametrize("body", OUTPUT)
def test_printed_output_shows_no_markdown_the_reader_would_see_raw(body):
    """A model that answers in markdown prints `**1042**`, and a text block
    shows it as is; the home page did. The examples ask for plain text."""
    assert not re.search(r"\*\*[^*\n]+\*\*|^\s*[-*] \*\*|^#{1,6} ", body, re.M)


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


def test_a_page_whose_code_builds_an_agent_imports_it():
    """In a code block: prose may name the constructor without importing it."""
    missing = []
    for page in PAGES:
        code = "\n".join(m.group(3) for m in FENCE.finditer(page.read_text()) if m.group(2) in ("python", "py"))
        if "OmniCoreAgent(" in code and not re.search(
            r"from omnicoreagent import (\(|[^\n]*\bOmniCoreAgent\b)", code
        ):
            missing.append(str(page.relative_to(ROOT)))
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
        # Every link to a page of the site, not only those under /docs: a link
        # that lost its /docs prefix is broken on the site (found by Mintlify's
        # own checker, 2026-09-25).
        prose = FENCE.sub("", text)
        for target in re.findall(r"\]\((/[^)#\s]+)", prose) + re.findall(r'href="(/[^"#]+)"', prose):
            path = target.lstrip("/").rstrip("/")
            exists = any(
                candidate.exists()
                for candidate in (
                    ROOT / f"{path}.mdx",
                    ROOT / f"{path}.md",
                    ROOT / path / "index.mdx",
                    ROOT / path,
                )
            )
            if not exists:
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


def _nav_groups():
    config = json.loads((ROOT / "docs.json").read_text())
    return config, config["navigation"]["dropdowns"][0]["groups"]


def test_short_paths_redirect_to_real_pages():
    # An outside review (2026-09-28) found /docs/quickstart was a 404: people guess
    # short paths, and posts link them. Each redirect must land on a page in the nav.
    config, groups = _nav_groups()
    pages = {f"/{page}" for page in re.findall(r'"(docs/[^"#]+)"', json.dumps(groups))}
    redirects = {r["source"]: r["destination"] for r in config.get("redirects", [])}
    assert {"/docs/quickstart", "/quickstart", "/docs/install", "/docs/installation"} <= set(redirects)
    assert not [d for d in redirects.values() if d not in pages]
    assert not [s for s in redirects if s in pages]


TAGLINE = "The governed runtime for Python agents you can let act."


def test_the_tagline_is_the_same_everywhere_it_is_shown():
    # One line, not three nouns and a qualifier (the same review).
    config, _ = _nav_groups()
    index = (ROOT / "docs" / "index.mdx").read_text()
    assert TAGLINE in (ROOT / "README.md").read_text()
    assert f"description: '{TAGLINE}'" in index
    assert config["description"] == TAGLINE

