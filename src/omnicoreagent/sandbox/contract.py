"""What each sandbox provider enforces from a manifest.

The sandbox is the boundary (engineering/architecture/simple-policy-plan.md),
so a manifest may ask only for what the chosen provider applies. A survey of
the providers (2026-10-01) found settings approved by a person and then
applied by none: path lists, a workspace mount on hosted providers, a
sandbox lifetime, a GPU. Each is now refused, naming the provider and the
setting, when the agent is built and again when a session opens; nothing is
approved that will not be kept.
"""

from __future__ import annotations

from typing import Any

NETWORK_OFF = "network off"
ALLOWED_HOSTS = "a network host allowlist (allowed_hosts)"
DENIED_HOSTS = "blocking named hosts (denied_hosts)"
WORKSPACE_MOUNT = "a workspace mount (workspace_mount)"
SECRETS = "delivering secrets (environment.secret_refs)"
CPU = "a CPU limit (resources.cpu)"
MEMORY = "a memory limit (resources.memory)"
IMAGE = "an image (image)"
GPU = "a GPU (resources.gpu)"
LIFETIME = "a sandbox lifetime (resources.timeout_seconds)"

# What each built-in provider applies. `http` forwards to your own service,
# which is trusted to apply what it is sent; it is not sent a mount or a
# lifetime. `local` is not a sandbox: it applies none of them and runs
# commands on the host (sandbox/local_process.py). Until 0.5.1 it refused a
# network of `deny` and an image only at the first command, after a person had
# been asked to approve it; the table now refuses them when the agent is built.
ENFORCES: dict[str, frozenset[str]] = {
    "docker": frozenset({IMAGE, NETWORK_OFF, WORKSPACE_MOUNT, CPU, MEMORY}),
    "e2b": frozenset({IMAGE, NETWORK_OFF}),
    # Daytona's network_allow_list takes IP ranges, not host names.
    "daytona": frozenset({IMAGE, NETWORK_OFF, CPU, MEMORY}),
    "modal": frozenset({IMAGE, NETWORK_OFF, ALLOWED_HOSTS, CPU, MEMORY}),
    "vercel": frozenset({IMAGE, NETWORK_OFF, CPU, MEMORY}),
    "http": frozenset({IMAGE, NETWORK_OFF, ALLOWED_HOSTS, DENIED_HOSTS, CPU, MEMORY, GPU}),
    "local": frozenset(),
}

_ADVICE = {
    NETWORK_OFF: "local runs on the host and cannot cut its network; set network_policy default "
    "'allow' with no host lists, or use an isolating provider such as docker",
    IMAGE: "local runs on the host and has no image; remove it, or use a provider that runs one",
    ALLOWED_HOSTS: "use network_policy default 'deny' or 'allow', or a provider that enforces one (modal, http)",
    DENIED_HOSTS: "use network_policy default 'deny' with allowed_hosts on a provider that enforces them, "
    "or the http provider with a service that does",
    WORKSPACE_MOUNT: "use the workspace bridge, which copies files in and out on every provider, "
    "or the docker provider",
    SECRETS: "put a value the command may see in environment.plain, or keep the secret out of the "
    "sandbox and call the service from a tool instead",
    CPU: "leave it unset, or use a provider that applies it",
    MEMORY: "leave it unset, or use a provider that applies it",
    GPU: "leave it unset, or use the http provider with a service that provides one",
    LIFETIME: "set the provider's own timeout_seconds option in sandbox_config instead",
}


def asked_for(manifest: Any) -> list[str]:
    """The settings a manifest asks a provider to apply."""
    network = manifest.network_policy
    resources = manifest.resources
    wanted: list[str] = []
    if manifest.image:
        wanted.append(IMAGE)
    if network.allowed_hosts:
        wanted.append(ALLOWED_HOSTS)
    if network.denied_hosts:
        wanted.append(DENIED_HOSTS)
    # After the host lists: where both are refused (local), the list is the
    # more specific thing to tell a person.
    if str(getattr(network.default, "value", network.default)) == "deny":
        wanted.append(NETWORK_OFF)
    if manifest.workspace_mount is not None:
        wanted.append(WORKSPACE_MOUNT)
    if manifest.environment.secret_refs:
        wanted.append(SECRETS)
    for setting, value in ((CPU, resources.cpu), (MEMORY, resources.memory),
                           (GPU, resources.gpu), (LIFETIME, resources.timeout_seconds)):
        if value:
            wanted.append(setting)
    return wanted


def check_enforced(provider: str, manifest: Any, enforces: frozenset[str] | None = None) -> None:
    """Refuse a manifest asking for anything the provider does not apply.

    ``enforces`` is the runtime's own declaration; a built-in provider's comes
    from ENFORCES. A custom runtime that declares nothing (None) is trusted,
    as before: what it applies is its author's to say."""
    if enforces is None:
        enforces = ENFORCES.get(provider)
    if enforces is None or manifest is None:
        return
    for setting in asked_for(manifest):
        if setting not in enforces:
            raise ValueError(
                f"The {provider} sandbox does not enforce {setting}: {_ADVICE.get(setting, 'remove it')}"
            )
