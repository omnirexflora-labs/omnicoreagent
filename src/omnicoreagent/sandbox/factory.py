from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from omnicoreagent.sandbox.base import SandboxRuntime
from omnicoreagent.sandbox.local import LocalTestSandboxRuntime
from omnicoreagent.sandbox.models import SandboxProvider, sandbox_provider_name
from omnicoreagent.sandbox.none import NoneSandboxRuntime

# Builds a backend from its options: factory(options, telemetry_recorder).
SandboxProviderFactory = Callable[[dict[str, Any], Any], SandboxRuntime]

_PROVIDERS: dict[str, SandboxProviderFactory] = {
    SandboxProvider.NONE.value: lambda options, telemetry_recorder: NoneSandboxRuntime(),
    SandboxProvider.LOCAL_TEST.value: lambda options, telemetry_recorder: LocalTestSandboxRuntime(
        telemetry_recorder=telemetry_recorder
    ),
}


def register_sandbox_provider(
    name: str, factory: SandboxProviderFactory, *, replace: bool = False
) -> None:
    """Make a sandbox provider available by name (bring your own sandbox)."""
    normalized = sandbox_provider_name(name)
    key = str(getattr(normalized, "value", normalized))
    if key in _PROVIDERS and not replace:
        raise ValueError(f"Sandbox provider {key!r} is already registered; pass replace=True")
    _PROVIDERS[key] = factory


def registered_sandbox_providers() -> list[str]:
    return sorted(_PROVIDERS)


@dataclass(slots=True)
class SandboxRuntimeConfig:
    provider: SandboxProvider | str = SandboxProvider.NONE
    # Provider settings (image, credentials reference, region...).
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.provider = sandbox_provider_name(self.provider)
        name = getattr(self.provider, "value", self.provider)
        if name not in _PROVIDERS:
            known = ", ".join(registered_sandbox_providers())
            raise ValueError(f"Unknown sandbox provider {name!r}. Registered: {known}")
        if not isinstance(self.options, dict):
            raise ValueError("sandbox options must be a mapping")

    @property
    def provider_name(self) -> str:
        return str(getattr(self.provider, "value", self.provider))


def build_sandbox_runtime(
    config: SandboxRuntimeConfig | dict[str, Any] | str | SandboxRuntime | None,
    *,
    telemetry_recorder=None,
) -> SandboxRuntime | None:
    if config is None:
        return None
    if isinstance(config, SandboxRuntime):
        return config
    if isinstance(config, str):
        runtime_config = SandboxRuntimeConfig(provider=config)
    elif isinstance(config, SandboxRuntimeConfig):
        runtime_config = config
    elif isinstance(config, dict):
        runtime_config = SandboxRuntimeConfig(**config)
    else:
        raise ValueError("sandbox_config must be a dict, string, or SandboxRuntime")
    return _PROVIDERS[runtime_config.provider_name](
        dict(runtime_config.options), telemetry_recorder
    )
