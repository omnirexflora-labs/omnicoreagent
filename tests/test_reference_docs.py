"""The reference pages are the code, and nothing public is left out of them.

`scripts/generate_reference.py` writes `docs/reference/` from the code: every
setting from its dataclass and the comment above it, every method from its
signature and docstring, every CLI command from its definition, the HTTP API
from OmniServe's OpenAPI document. On 2026-09-25, before the reference existed,
7 of `OmniCoreAgent`'s 41 methods and 3 of its 26 settings appeared on no page.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _generator():
    spec = importlib.util.spec_from_file_location(
        "generate_reference", ROOT / "scripts" / "generate_reference.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rendered() -> dict[Path, str]:
    return _generator().render_all()


def test_the_committed_reference_is_what_the_code_generates(rendered):
    stale = [
        str(path.relative_to(ROOT))
        for path, content in rendered.items()
        if not path.exists() or path.read_text() != content
    ]
    assert not stale, f"run `python scripts/generate_reference.py`: {stale}"


def test_every_public_method_is_in_the_reference(rendered):
    import inspect

    from omnicoreagent import OmniCoreAgent

    page = rendered[ROOT / "docs/reference/omnicoreagent.mdx"]
    missing = [
        name
        for name, _ in inspect.getmembers(OmniCoreAgent, predicate=inspect.isfunction)
        if not name.startswith("_") and f"### `{name}`" not in page
    ]
    assert not missing


@pytest.mark.parametrize(
    ("page", "config"),
    [
        ("agent-config.mdx", "omnicoreagent.core.runtime.config:AgentConfig"),
        ("telemetry-config.mdx", "omnicoreagent.core.telemetry.redaction:TelemetryConfig"),
    ],
)
def test_every_setting_is_in_the_reference(rendered, page, config):
    module, name = config.split(":")
    cls = getattr(importlib.import_module(module), name)
    text = rendered[ROOT / "docs/reference" / page]
    missing = [
        field.name
        for field in dataclasses.fields(cls)
        if field.name != "agent_name" and f"| `{field.name}` |" not in text
    ]
    assert not missing


def test_every_cli_command_is_in_the_reference(rendered):
    from omnicoreagent.cli import cli as omnicoreagent_cli
    from omnicoreagent.serve.cli import cli as omniserve_cli

    page = rendered[ROOT / "docs/reference/cli.mdx"]
    commands = [f"omnicoreagent {name}" for name in omnicoreagent_cli.commands] + [
        f"omniserve {name}" for name in omniserve_cli.commands
    ]
    assert not [command for command in commands if f"`{command}`" not in page]


def test_every_http_route_is_in_the_api_document(rendered):
    spec = json.loads(rendered[ROOT / "docs/reference/openapi.json"])
    operations = [
        (method, path) for path, methods in spec["paths"].items() for method in methods
    ]
    assert len(operations) >= 50
    assert all(
        spec["paths"][path][method].get("summary") for method, path in operations
    ), "every route says what it does"


def test_the_api_document_is_in_the_navigation():
    """Mintlify renders it only from the nav — and needs the leading slash (a
    path without one hung its checker)."""
    nav = json.dumps(json.loads((ROOT / "docs.json").read_text()))
    assert '"openapi": "/docs/reference/openapi.json"' in nav

