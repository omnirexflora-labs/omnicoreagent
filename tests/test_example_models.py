"""Examples and defaults name models a provider still serves (2026-09-26).

`omniserve quickstart` with no arguments used gemini-2.0-flash, which is
retired, and the docs' examples mixed in retired models (gemini-2.0-flash-exp,
Groq's llama-3.1-8b-instant) and ones older than the maintainer's floor for
OpenAI, GPT-5.4. LiteLLM's model list stands in for "still served".
"""

from __future__ import annotations

import re
from pathlib import Path

import litellm

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    ROOT / "README.md",
    *(ROOT / "docs").rglob("*.mdx"),
    *(ROOT / "cookbook").rglob("*.md*"),
    *(ROOT / "cookbook").rglob("*.py"),
]
# Models run on your own machine (Ollama, or a server behind `base_url`) or
# placeholders are not in the list.
LOCAL = {"llama3.1:8b", "llama3", "qwen3-coder-30b", "..."}
OLDER_OPENAI = re.compile(r"^(gpt-4|gpt-3\.5|gpt-4o)(-|$)")


def _served(model: str) -> bool:
    known = litellm.model_cost
    return model in known or any(name.endswith("/" + model) for name in known)


def test_quickstart_defaults_to_a_served_model():
    from omnicoreagent.serve.cli import cli

    params = {p.name: p.default for p in cli.commands["quickstart"].params}
    assert (params["provider"], params["model"]) == ("openai", "gpt-5.4-mini")
    assert _served(params["model"])


def test_every_model_in_the_docs_is_served_and_openai_ones_are_gpt_5_4_or_newer():
    models = {
        (page.relative_to(ROOT).as_posix(), model)
        for page in PAGES
        for model in re.findall(r'"model": "([^"]+)"|--model ([\w.:/-]+)', page.read_text())
        for model in model
        if model
    }
    retired = sorted((page, m) for page, m in models if m not in LOCAL and not _served(m))
    older = sorted((page, m) for page, m in models if OLDER_OPENAI.match(m))
    assert not retired, retired
    assert not older, older
