"""The injection guardrail, held to a corpus.

Found by the repository steward, which tripped the guardrail three times in
two days on ordinary text. Ordinary developer text — source, docs, tests,
git logs, pytest output, tool results, MCP schemas, the runtime's own
preamble, an issue body — must never be blocked, and the plainest attacks
must be. The corpus is frozen in ``tests/fixtures/guardrail_corpus.json``;
add to it whenever the guardrail is wrong about something real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicoreagent.core.guardrails import PromptInjectionGuard
from omnicoreagent.core.guardrails.models import DetectionConfig

CORPUS = json.loads(Path(__file__).with_name("fixtures").joinpath("guardrail_corpus.json").read_text())
BLOCKING = {"dangerous", "critical"}


def _verdict(text: str, **config) -> tuple[str, list[str]]:
    result = PromptInjectionGuard(DetectionConfig(**config)).check(text)
    return getattr(result.threat_level, "value", str(result.threat_level)), list(result.flags)


@pytest.mark.parametrize("name", sorted(CORPUS["benign"]))
def test_ordinary_developer_text_is_never_blocked(name):
    level, flags = _verdict(CORPUS["benign"][name])
    assert level not in BLOCKING, (name, level, flags)


@pytest.mark.parametrize("name", sorted(CORPUS["benign"]))
def test_ordinary_developer_text_is_not_even_suspicious(name):
    """Structure and vocabulary are not evidence: a diff, a docstring, a
    markdown rule, an identifier, the words "system" and "override"."""
    level, flags = _verdict(CORPUS["benign"][name])
    assert level in {"safe", "low_risk"}, (name, level, flags)


@pytest.mark.parametrize("name", sorted(CORPUS["attacks"]))
def test_an_attack_is_blocked_in_the_default_mode(name):
    level, flags = _verdict(CORPUS["attacks"][name])
    assert level in BLOCKING, (name, level, flags)


@pytest.mark.parametrize("name", sorted(CORPUS["benign"]))
def test_strict_mode_does_not_block_ordinary_text_either(name):
    level, flags = _verdict(CORPUS["benign"][name], strict_mode=True)
    assert level not in BLOCKING, (name, level, flags)
