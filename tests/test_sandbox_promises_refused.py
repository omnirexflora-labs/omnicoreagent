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
        _build(provider, {"environment": {"secret_refs": ["STRIPE_KEY"]}})


def test_a_host_allowlist_on_daytona_is_refused():
    with pytest.raises(ValueError, match="cannot enforce a network host allowlist"):
        _build("daytona", {"network_policy": {"default": "deny", "allowed_hosts": ["pypi.org"]}})
