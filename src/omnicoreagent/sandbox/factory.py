from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omnicoreagent.sandbox.base import SandboxRuntime
from omnicoreagent.sandbox.local import LocalTestSandboxRuntime
from omnicoreagent.sandbox.models import SandboxProvider
from omnicoreagent.sandbox.none import NoneSandboxRuntime


@dataclass(slots=True)
class SandboxRuntimeConfig:
    provider: SandboxProvider | str = SandboxProvider.NONE

    def __post_init__(self) -> None:
        try:
            self.provider = SandboxProvider(self.provider)
        except ValueError as exc:
            raise ValueError(f"Unsupported sandbox provider: {self.provider}") from exc


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

    if runtime_config.provider == SandboxProvider.NONE:
        return NoneSandboxRuntime()
    if runtime_config.provider == SandboxProvider.LOCAL_TEST:
        return LocalTestSandboxRuntime(telemetry_recorder=telemetry_recorder)
    raise ValueError(f"Unsupported sandbox provider: {runtime_config.provider.value}")
