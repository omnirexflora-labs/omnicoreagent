"""An agent must not be able to edit its own policy.

A policy file is refused where the agent can write: inside the agent's
workspace directory (whatever it is called), or inside a host directory
mounted read-write into its sandbox. The existing name check (directories
called `workspace`, `tmp`, `outputs`, ...) stays; these cover the real paths.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.runtime.construction import build_governance_engine
from omnicoreagent.governance import GovernanceEngine, PolicyLoadError, build_default_policy, load_policy
from omnicoreagent.sandbox import LocalTestSandboxRuntime, SandboxExecutionService, SandboxManifest
from omnicoreagent.sandbox.errors import SandboxUnsupportedError
from omnicoreagent.sandbox.models import WorkspaceMount


def _policy_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "app-policy", "rules": {"allow": []}}))
    return path


def _config(tmp_path, policy_path, workspace_dir):
    return {
        "workspace_config": {"workspace_dir": str(workspace_dir)},
        "governance_config": {
            "enabled": True,
            "policy_path": str(policy_path),
            "project_root": str(tmp_path),
        },
    }


def test_a_policy_inside_the_agents_workspace_directory_is_refused(tmp_path):
    workspace = tmp_path / "agentdata"
    policy = _policy_file(workspace / "files" / "policy.json")

    with pytest.raises(PolicyLoadError, match="agent's workspace"):
        build_governance_engine(_config(tmp_path, policy, workspace))


def test_a_policy_beside_the_workspace_loads(tmp_path):
    policy = _policy_file(tmp_path / "config" / "policy.json")

    engine = build_governance_engine(_config(tmp_path, policy, tmp_path / "agentdata"))

    assert engine.policy.name == "app-policy"


def _engine_with_policy_at(path):
    policy = load_policy(explicit_path=_policy_file(path), project_root=path.parent.parent)
    return GovernanceEngine(policy, sandbox_runtime=LocalTestSandboxRuntime(), allow_test_sandbox_runtime=True)


@pytest.mark.asyncio
async def test_a_read_write_mount_containing_the_policy_is_refused(tmp_path):
    engine = _engine_with_policy_at(tmp_path / "project" / "policy.json")
    engine.policy.rules.allow.extend(build_default_policy("permissive-dev").rules.allow)
    manifest = SandboxManifest(
        workspace_mount=WorkspaceMount(source=str(tmp_path / "project"), target="/mnt/p", mode="read_write")
    )

    with pytest.raises(SandboxUnsupportedError, match="policy"):
        await SandboxExecutionService(engine).open_session(manifest)


@pytest.mark.asyncio
async def test_a_read_only_mount_containing_the_policy_is_allowed(tmp_path):
    engine = _engine_with_policy_at(tmp_path / "project" / "policy.json")
    engine.policy.rules.allow.extend(build_default_policy("permissive-dev").rules.allow)
    manifest = SandboxManifest(
        workspace_mount=WorkspaceMount(source=str(tmp_path / "project"), target="/mnt/p", mode="read_only")
    )

    session = await SandboxExecutionService(engine).open_session(manifest)

    assert session.session_id
