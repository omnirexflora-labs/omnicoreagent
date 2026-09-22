"""The workspace bridge: workspace files in the sandbox, outputs back as governed writes.

The workspace stays the source of truth. Before each command, workspace files
that changed are copied into the sandbox's working directory (each read
authorized as `workspace.files.read`); after it, files the command created or
changed are copied back (each authorized as `workspace.files.write`, with the
workspace privacy filter applied). Links, hidden paths, oversized and non-text
files never come back. Deletions are not propagated in either direction.

Uses the real Docker backend (`alpine:3.20`); skipped only where Docker is
unavailable.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage
from omnicoreagent.governance import (
    GovernanceEngine,
    PolicyEffect,
    PolicyRule,
    build_default_policy,
)
from omnicoreagent.governance.hashing import attach_policy_hash
from test_execute_tool import ScriptedModel, _containers, _MODEL, needs_docker

pytestmark = [needs_docker, pytest.mark.asyncio]


def _engine(policy=None):
    from omnicoreagent.sandbox import build_sandbox_runtime

    runtime = build_sandbox_runtime({"provider": "docker", "options": {"image": "alpine:3.20"}})
    return GovernanceEngine(policy or build_default_policy("interactive-dev"), sandbox_runtime=runtime)


def _scope(storage, *, policy=None, privacy_filter=None, **limits):
    from omnicoreagent.sandbox import SandboxExecutionService
    from omnicoreagent.sandbox.scope import ExecutionScope
    from omnicoreagent.sandbox.workspace_bridge import WorkspaceBridge

    engine = _engine(policy)
    bridge = WorkspaceBridge(storage, governance_engine=engine, privacy_filter=privacy_filter, **limits)
    return ExecutionScope(SandboxExecutionService(engine), workspace_bridge=bridge)


async def _sh(scope, command: str):
    return await scope.execute(["sh", "-c", command], timeout_seconds=30)


async def test_workspace_files_are_in_the_sandbox_and_outputs_come_back(tmp_path):
    storage = LocalWorkspaceStorage(tmp_path / "files")
    storage.write_text("data/input.txt", "hello from the workspace")

    async with _scope(storage).active() as scope:
        result = await _sh(scope, "cat data/input.txt && mkdir -p out && echo result > out/answer.txt")

    assert result.stdout == "hello from the workspace"
    assert storage.read_text("out/answer.txt") == "result\n"
    assert result.metadata["workspace"] == {"written": ["out/answer.txt"], "skipped": []}


async def test_a_file_the_policy_protects_is_not_written_back(tmp_path):
    policy = build_default_policy("interactive-dev")
    policy.rules.deny.append(
        PolicyRule(
            rule_id="deny_protected_writes",
            effect=PolicyEffect.DENY,
            capability="workspace.files.write",
            target={"path": "protected/*"},
        )
    )
    storage = LocalWorkspaceStorage(tmp_path / "files")

    async with _scope(storage, policy=attach_policy_hash(policy)).active() as scope:
        result = await _sh(scope, "mkdir protected && echo x > protected/x.txt && echo ok > ok.txt")

    assert storage.exists("ok.txt") and not storage.exists("protected/x.txt")
    skipped = result.metadata["workspace"]["skipped"]
    assert [item["path"] for item in skipped] == ["protected/x.txt"]
    assert "not permitted" in skipped[0]["reason"]


async def test_policy_sees_the_exact_path_written_even_with_a_tool_path_prefix(tmp_path):
    # The workspace tools strip prefixes such as "files/"; the bridge must not,
    # or a rule for "files/*" would be checked against a different path.
    policy = build_default_policy("interactive-dev")
    policy.rules.deny.append(
        PolicyRule(
            rule_id="deny_files_dir",
            effect=PolicyEffect.DENY,
            capability="workspace.files.write",
            target={"path": "files/*"},
        )
    )
    storage = LocalWorkspaceStorage(tmp_path / "files")

    async with _scope(storage, policy=attach_policy_hash(policy)).active() as scope:
        result = await _sh(scope, "mkdir files && echo x > files/x.txt")

    assert not (tmp_path / "files" / "files" / "x.txt").exists()
    assert [item["path"] for item in result.metadata["workspace"]["skipped"]] == ["files/x.txt"]


async def test_a_file_the_policy_protects_is_not_copied_in(tmp_path):
    policy = build_default_policy("interactive-dev")
    policy.rules.deny.append(
        PolicyRule(
            rule_id="deny_secret_reads",
            effect=PolicyEffect.DENY,
            capability="workspace.files.read",
            target={"path": "secret/*"},
        )
    )
    storage = LocalWorkspaceStorage(tmp_path / "files")
    storage.write_text("secret/key.txt", "do not share")
    storage.write_text("public.txt", "fine")

    async with _scope(storage, policy=attach_policy_hash(policy)).active() as scope:
        result = await _sh(scope, "cat public.txt; ls secret 2>&1 || true")

    assert "fine" in result.stdout
    assert "key.txt" not in result.stdout


async def test_links_hidden_paths_and_oversized_or_binary_files_do_not_come_back(tmp_path):
    storage = LocalWorkspaceStorage(tmp_path / "files")

    async with _scope(storage, max_file_bytes=1000).active() as scope:
        result = await _sh(
            scope,
            "ln -s /etc/hostname link.txt; ln -s / rootlink; "
            "mkdir .hidden && echo s > .hidden/s.txt; "
            "head -c 3000 /dev/zero | tr '\\0' a > big.txt; "
            "printf '\\377\\376\\000' > bin.dat; echo fine > fine.txt",
        )

    names = {item.path for item in storage.list_files()}
    assert names == {"fine.txt"}
    reasons = {item["path"]: item["reason"] for item in result.metadata["workspace"]["skipped"]}
    assert set(reasons) == {"big.txt", "bin.dat"}
    assert "too large" in reasons["big.txt"] and "not text" in reasons["bin.dat"]


async def test_changes_on_either_side_reach_the_other_between_commands(tmp_path):
    storage = LocalWorkspaceStorage(tmp_path / "files")
    storage.write_text("a.txt", "v1")

    async with _scope(storage).active() as scope:
        first = await _sh(scope, "cat a.txt")
        storage.write_text("a.txt", "v2")
        second = await _sh(scope, "cat a.txt && echo v3 > a.txt")
        third = await _sh(scope, "cat a.txt")

    assert (first.stdout, second.stdout, third.stdout) == ("v1", "v2", "v3\n")
    assert first.metadata["workspace"]["written"] == []
    assert second.metadata["workspace"]["written"] == ["a.txt"]
    # Nothing changed in the third command, so nothing is written again.
    assert third.metadata["workspace"]["written"] == []
    assert storage.read_text("a.txt") == "v3\n"


async def test_outputs_come_back_as_written(tmp_path):
    """A file the sandbox produced is the agent's work: a pyproject.toml
    whose author email came back as "[REDACTED_EMAIL]" was what the
    repository steward once pushed."""
    from omnicoreagent.core.privacy import PrivacyFilter

    storage = LocalWorkspaceStorage(tmp_path / "files")

    async with _scope(storage, privacy_filter=PrivacyFilter()).active() as scope:
        await _sh(scope, "printf 'authors = [{ email = \"alice@example.com\" }]\\n' > pyproject.toml")

    assert "alice@example.com" in storage.read_text("pyproject.toml")


async def test_outputs_pass_the_workspace_privacy_filter_when_asked(tmp_path):
    from omnicoreagent.core.privacy import PrivacyConfig, PrivacyFilter

    storage = LocalWorkspaceStorage(tmp_path / "files")
    privacy = PrivacyFilter(PrivacyConfig(redact_workspace=True))

    async with _scope(storage, privacy_filter=privacy).active() as scope:
        await _sh(scope, "echo 'mail me at someone@example.com' > note.txt")

    assert "someone@example.com" not in storage.read_text("note.txt")


async def test_the_agent_execute_tool_works_on_its_workspace_files(tmp_path):
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

    before = _containers()
    model = ScriptedModel(
        [("c1", "write_file", json.dumps({"path": "numbers.txt", "content": "3\n4\n5"}))],
        [("c2", "execute", json.dumps({"command": "awk '{s+=$1} END {print s}' numbers.txt > total.txt"}))],
        [("c3", "read_file", json.dumps({"path": "total.txt"}))],
        "done",
    )
    agent = OmniCoreAgent(
        name="bridge-agent",
        system_instruction="Use the workspace and execute.",
        model_config=_MODEL,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": True,
            "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
            "governance_config": {
                "enabled": True,
                "profile": "interactive-dev",
                "sandbox_config": {"provider": "docker", "options": {"image": "alpine:3.20"}},
            },
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model

    result = await agent.run("add the numbers", session_id="bridge")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])

    outputs = [e.output for e in trace.events if e.event_type == "tool_result"]
    executed = next(o for o in outputs if "exit_code" in json.dumps(o))
    assert executed["data"]["workspace_files"]["written"] == ["total.txt"]
    observed = next(
        e.output for e in trace.events if e.event_type == "tool_observation" and e.output["tool_call_id"] == "c3"
    )
    assert "12" in json.dumps(observed)
    assert (tmp_path / "ws" / "files" / "total.txt").read_text() == "12\n"
    writes = [
        e
        for e in trace.events
        if e.event_type.startswith("policy_decision_")
        and e.metadata.get("capability") == "workspace.files.write"
    ]
    assert writes, "the copy back must be authorized like any workspace write"
    assert _containers() == before


async def test_the_bridge_records_one_summary_of_its_checks_not_one_per_file(tmp_path):
    """Telemetry storage plan, T3: 8,812 of the steward's 10,831 policy
    requests were this bridge checking workspace files one by one, each
    recorded as a request and an allow. A copy records one summary; a file
    it refuses is still recorded on its own."""
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

    policy = build_default_policy("interactive-dev")
    policy.rules.deny.insert(
        0,
        PolicyRule(
            rule_id="deny_secret_reads",
            effect=PolicyEffect.DENY,
            capability="workspace.files.read",
            target={"path": "secret/*"},
        ),
    )
    files = LocalWorkspaceStorage(tmp_path / "ws" / "files")
    for number in range(30):
        files.write_text(f"notes/{number}.txt", f"note {number}")
    files.write_text("secret/key.txt", "do not share")
    model = ScriptedModel([("c1", "execute", json.dumps({"command": "ls notes | wc -l"}))], "done")
    agent = OmniCoreAgent(
        name="bridge-summary",
        system_instruction="Count the notes.",
        model_config=_MODEL,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": True,
            "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
            "governance_config": {
                "enabled": True,
                "policy": attach_policy_hash(policy),
                "sandbox_config": {"provider": "docker", "options": {"image": "alpine:3.20"}},
            },
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    result = await agent.run("count", session_id="bridge-summary")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])

    bridge_requests = [
        e for e in trace.events
        if e.event_type == "policy_request_created"
        and ((e.input or {}).get("request") or {}).get("metadata", {}).get("purpose") == "sandbox workspace bridge"
    ]
    assert [r.input["request"]["target"]["path"] for r in bridge_requests] == ["secret/key.txt"]
    (summary,) = [e for e in trace.events if e.event_type == "policy_decisions_summarized"]
    assert summary.output["allowed"] >= 30 and summary.output["denied"] == ["secret/key.txt"], summary.output
    assert summary.output["purpose"] == "sandbox workspace bridge"
