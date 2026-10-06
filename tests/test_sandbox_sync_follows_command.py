"""The sandbox's own file listing follows the decision for its command (0.5.1, A1).

Found filming the steward (2026-10-05): a strict policy that allowed sandbox
commands but named no rule for ``sandbox.workspace.sync`` denied the runtime's
listing after every command, so nothing a command wrote came back. The listing
is the runtime's reading of the sandbox, so it follows the command's decision;
an explicit rule for it, and every file rule, still decide as before.

Runs on the local provider (real processes, no Docker).
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage
from omnicoreagent.governance import (
    GovernanceEngine,
    PolicyEffect,
    PolicyEnvelope,
    PolicyMode,
    PolicyRule,
    PolicyRuleConditions,
    PolicyRuleSet,
)
from omnicoreagent.governance.errors import GovernanceError
from omnicoreagent.sandbox import SandboxExecutionService, SandboxManifest, build_sandbox_runtime
from omnicoreagent.sandbox.scope import ExecutionScope
from omnicoreagent.sandbox.workspace_bridge import WorkspaceBridge

pytestmark = pytest.mark.asyncio


class Recorder:
    """Keeps the governance events the engine emits."""

    config = None

    def __init__(self):
        self.events = []

    async def emit_event(self, event_type, **kwargs):
        self.events.append((event_type, kwargs.get("metadata") or {}, kwargs.get("output") or {}))


def _rule(rule_id, capability, effect=PolicyEffect.ALLOW, **extra):
    return PolicyRule(rule_id=rule_id, effect=effect, capability=capability, **extra)


# The steward's shape: named sandbox capabilities, no rule for the sync.
BASE = [
    _rule("sandbox", "sandbox.execute"),
    _rule("network", "sandbox.network.configure"),
    _rule("commands", "process.exec", conditions=PolicyRuleConditions(execution_surface="host")),
    _rule("read_files", "workspace.files.read"),
    _rule("write_files", "workspace.files.write"),
]


def _policy(allow=BASE, deny=(), ask=()):
    return PolicyEnvelope(
        name="strict-custom",
        mode=PolicyMode.STRICT,
        rules=PolicyRuleSet(allow=list(allow), deny=list(deny), ask=list(ask)),
    )


def _setup(tmp_path, policy, **bridge):
    work = tmp_path / "work"
    work.mkdir()
    storage = LocalWorkspaceStorage(tmp_path / "files")
    runtime = build_sandbox_runtime({"provider": "local", "options": {}})
    recorder = Recorder()
    engine = GovernanceEngine(policy, sandbox_runtime=runtime, telemetry_recorder=recorder)
    manifest = SandboxManifest(working_dir=str(work), network_policy={"default": "allow"})
    scope = ExecutionScope(
        SandboxExecutionService(engine),
        manifest,
        workspace_bridge=WorkspaceBridge(storage, governance_engine=engine, **bridge),
    )
    return scope, storage, recorder


async def _sh(scope, command):
    return await scope.execute(["sh", "-c", command], timeout_seconds=30)


def _sync_decisions(recorder):
    return [
        (kind, meta, output)
        for kind, meta, output in recorder.events
        if kind.startswith("policy_decision_") and meta.get("capability") == "sandbox.workspace.sync"
    ]


async def test_a_strict_policy_with_no_rule_for_the_sync_still_copies_files_back(tmp_path):
    scope, storage, recorder = _setup(tmp_path, _policy())

    async with scope.active():
        result = await _sh(scope, "mkdir -p out && echo made > out/r.txt")

    assert result.exit_code == 0
    assert storage.read_text("out/r.txt") == "made\n"
    assert result.metadata["workspace"]["written"] == ["out/r.txt"]


async def test_the_sync_is_recorded_as_following_the_command_not_as_a_rule_match(tmp_path):
    scope, _, recorder = _setup(tmp_path, _policy())

    async with scope.active():
        result = await _sh(scope, "echo hi")

    [(kind, meta, output)] = _sync_decisions(recorder)
    command_request = result.metadata["authority"]["authority_request_id"]
    assert kind == "policy_decision_allow"
    assert meta["reason_code"] == "follows_command"
    assert meta["matched_rule_ids"] == []
    assert command_request in output["decision"]["reason"]


async def test_an_explicit_deny_rule_for_the_sync_still_wins(tmp_path):
    deny = _rule("no_sync", "sandbox.workspace.sync", PolicyEffect.DENY)
    scope, storage, recorder = _setup(tmp_path, _policy(deny=[deny]))

    async with scope.active():
        result = await _sh(scope, "mkdir -p out && echo made > out/r.txt")

    # The command ran; its files stay in the sandbox, and the model is told why.
    assert result.exit_code == 0 and not storage.exists("out/r.txt")
    [skipped] = result.metadata["workspace"]["skipped"]
    assert "sandbox.workspace.sync" in skipped["reason"] and "lost" not in skipped["reason"]
    [(kind, meta, _)] = _sync_decisions(recorder)
    assert kind == "policy_decision_deny" and meta["matched_rule_ids"] == ["no_sync"]


async def test_an_explicit_ask_rule_for_the_sync_is_still_asked(tmp_path):
    ask = _rule("ask_sync", "sandbox.workspace.sync", PolicyEffect.ASK)
    scope, _, recorder = _setup(tmp_path, _policy(ask=[ask]))

    async with scope.active():
        await _sh(scope, "echo hi")

    assert any(meta["matched_rule_ids"] == ["ask_sync"] for _, meta, _ in _sync_decisions(recorder))


async def test_a_denied_command_produces_no_sync(tmp_path):
    no_exec = [r for r in BASE if r.rule_id != "commands"]
    scope, storage, recorder = _setup(tmp_path, _policy(allow=no_exec))

    async with scope.active():
        with pytest.raises(GovernanceError):
            await _sh(scope, "echo made > out.txt")

    assert _sync_decisions(recorder) == []
    assert not storage.exists("out.txt")


async def test_the_file_rules_still_skip_a_file_a_workspace_rule_covers(tmp_path):
    deny = _rule("protect", "workspace.files.write", PolicyEffect.DENY, target={"path": "protected/*"})
    scope, storage, _ = _setup(tmp_path, _policy(deny=[deny]))

    async with scope.active():
        result = await _sh(scope, "mkdir -p out protected && echo a > out/a.txt && echo b > protected/b.txt")

    assert storage.exists("out/a.txt") and not storage.exists("protected/b.txt")
    skipped = {item["path"]: item["reason"] for item in result.metadata["workspace"]["skipped"]}
    assert "policy" in skipped["protected/b.txt"]
