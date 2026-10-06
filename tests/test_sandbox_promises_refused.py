"""A sandbox setting no provider keeps is refused when the agent is built.

Found writing the sandbox pages (D6), each silently ignored until now:
- `denied_hosts`: no built-in provider blocks named hosts (only `http` passes
  the list on to your service); a policy of "allow, except these" ran with
  the network fully open.
- `environment.secret_refs`: authorized as `secret.use`, delivered by no
  provider, so the command ran without the secret and nothing said why.
- Daytona's `allowed_hosts`: its API takes IP ranges, not host names, and the
  sandbox failed to start with a validation error from its SDK.
Like a host allowlist on a provider that cannot keep one, each is refused
before a person is asked to approve a rule the sandbox would not enforce.
"""

from __future__ import annotations

import pytest

from omnicoreagent import OmniCoreAgent

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


def _build(provider: str, manifest: dict) -> None:
    OmniCoreAgent(
        name="sandboxed",
        system_instruction="x",
        model_config=MODEL,
        agent_config={
            "governance_config": {
                "enabled": True,
                "profile": "interactive-dev",
                "sandbox_config": {"provider": provider},
                "sandbox_manifest": manifest,
            }
        },
    )


@pytest.mark.parametrize("provider", ["docker", "e2b", "daytona", "modal", "vercel", "local"])
def test_denied_hosts_are_refused_where_no_provider_blocks_them(provider):
    with pytest.raises(ValueError, match="denied_hosts"):
        _build(provider, {"network_policy": {"default": "allow", "denied_hosts": ["example.com"]}})


@pytest.mark.parametrize("provider", ["docker", "e2b", "daytona", "modal", "vercel", "local", "http"])
def test_secret_refs_are_refused_where_no_provider_delivers_them(provider):
    with pytest.raises(ValueError, match="secret_refs"):
        # `local` also needs a network of allow, or it is refused for that first.
        _build(
            provider,
            {"network_policy": {"default": "allow"}, "environment": {"secret_refs": ["STRIPE_KEY"]}},
        )


def test_a_host_allowlist_on_daytona_is_refused():
    with pytest.raises(ValueError, match="does not enforce a network host allowlist"):
        _build("daytona", {"network_policy": {"default": "deny", "allowed_hosts": ["pypi.org"]}})


# SP1 (simple-policy plan): every setting a provider does not apply is
# refused, from one table (sandbox/contract.py). A survey on 2026-10-01 found
# a workspace mount approved and then ignored by e2b, daytona, modal and
# vercel, and a sandbox lifetime and a GPU ignored by every provider.


@pytest.mark.parametrize("provider", ["e2b", "daytona", "modal", "vercel", "http"])
def test_a_workspace_mount_is_refused_where_it_is_not_applied(provider):
    with pytest.raises(ValueError, match="workspace mount"):
        _build(provider, {"workspace_mount": {"source": "/srv/data", "target": "/data"}})


def test_docker_applies_a_workspace_mount():
    _build("docker", {"workspace_mount": {"source": "/srv/data", "target": "/data"}})


@pytest.mark.parametrize("provider", ["docker", "e2b", "daytona", "modal", "vercel", "http"])
def test_a_sandbox_lifetime_is_refused_everywhere(provider):
    with pytest.raises(ValueError, match="timeout_seconds"):
        _build(provider, {"resources": {"timeout_seconds": 600}})


@pytest.mark.parametrize("provider", ["docker", "e2b", "daytona", "modal", "vercel"])
def test_a_gpu_is_refused_where_it_is_not_applied(provider):
    with pytest.raises(ValueError, match="GPU"):
        _build(provider, {"resources": {"gpu": True}})


def test_e2b_applies_no_cpu_or_memory_limit():
    with pytest.raises(ValueError, match="CPU"):
        _build("e2b", {"resources": {"cpu": "2"}})


@pytest.mark.parametrize("provider", ["docker", "daytona", "modal", "vercel", "http"])
def test_cpu_and_memory_are_kept_where_applied(provider):
    _build(provider, {"resources": {"cpu": "1", "memory": "1g"}})


def test_modal_keeps_a_host_allowlist():
    _build("modal", {"network_policy": {"default": "deny", "allowed_hosts": ["pypi.org"]}})


@pytest.mark.asyncio
async def test_a_runtime_refuses_at_session_open_what_it_does_not_apply():
    # The same check where a session opens: a runtime used directly skips
    # the agent's config check.
    from omnicoreagent.governance import GovernanceEngine, build_default_policy
    from omnicoreagent.sandbox.contract import ENFORCES
    from omnicoreagent.sandbox.execution import SandboxExecutionService
    from omnicoreagent.sandbox.local import LocalTestSandboxRuntime
    from omnicoreagent.sandbox.models import SandboxManifest

    runtime = LocalTestSandboxRuntime(commands={})
    runtime.enforces = ENFORCES["e2b"]
    engine = GovernanceEngine(
        build_default_policy("permissive-dev"), sandbox_runtime=runtime, allow_test_sandbox_runtime=True
    )
    manifest = SandboxManifest(workspace_mount={"source": "/srv", "target": "/data"})
    with pytest.raises(Exception, match="workspace mount"):
        await SandboxExecutionService(engine).open_session(manifest)


# SP3 (simple-policy plan): the sandbox's path lists were approved by a person
# and applied by no provider, so they are gone; a manifest naming them is
# refused with what to use instead.


def test_a_filesystem_policy_is_refused_with_what_to_use_instead():
    from omnicoreagent.sandbox.models import SandboxManifest

    with pytest.raises(ValueError, match="filesystem_policy was removed.*workspace bridge"):
        SandboxManifest(filesystem_policy={"default": "deny", "readable_paths": ["/workspace/in"]})
    with pytest.raises(ValueError, match="filesystem_policy was removed"):
        _build("docker", {"filesystem_policy": {"default": "allow"}})


def test_the_local_provider_needs_no_filesystem_setting():
    _build("local", {"network_policy": {"default": "allow"}})


# B6 (0.5.1 plan): `local` refused a network of `deny` and an image only at
# the first command, after a person had been asked to approve it; the other
# providers refuse what they do not apply when the agent is built.


def test_local_refuses_network_deny_when_the_agent_is_built():
    with pytest.raises(ValueError, match="local sandbox does not enforce network off.*allow"):
        _build("local", {"network_policy": {"default": "deny"}})
    # A manifest that says nothing about the network gets the default, deny.
    with pytest.raises(ValueError, match="local sandbox does not enforce network off"):
        _build("local", {"working_dir": "/tmp"})


def test_local_refuses_an_image_when_the_agent_is_built():
    with pytest.raises(ValueError, match="local sandbox does not enforce an image"):
        _build("local", {"network_policy": {"default": "allow"}, "image": "python:3.12"})


@pytest.mark.parametrize("provider", ["docker", "e2b", "daytona", "modal", "vercel", "http"])
def test_an_image_is_still_accepted_where_the_provider_applies_one(provider):
    _build(provider, {"network_policy": {"default": "deny"}, "image": "python:3.12"})


# 0.5.1 follow-up: with no `sandbox_manifest` at all the runtime uses the
# default one, whose network is `deny`, so `local` failed at the first command
# though a manifest that said so was refused when the agent was built.


def _build_without_manifest(provider: str) -> None:
    OmniCoreAgent(
        name="sandboxed",
        system_instruction="x",
        model_config=MODEL,
        agent_config={
            "governance_config": {
                "enabled": True,
                "profile": "interactive-dev",
                "sandbox_config": {"provider": provider},
            }
        },
    )


def test_local_with_no_manifest_is_refused_when_the_agent_is_built():
    with pytest.raises(ValueError, match="local sandbox does not enforce network off.*allow"):
        _build_without_manifest("local")


@pytest.mark.parametrize("provider", ["docker", "e2b", "daytona", "modal", "vercel", "http"])
def test_no_manifest_is_still_accepted_where_the_provider_cuts_the_network(provider):
    _build_without_manifest(provider)
