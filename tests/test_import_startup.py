import json
import os
import subprocess
import sys


def _run_import_probe(tmp_path, code: str) -> dict:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("OMNICOREAGENT_SERVE_", "OMNICOREAGENT_BACKGROUND_")):
            env.pop(key)

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_root_import_does_not_load_dotenv_or_provider_clients(tmp_path):
    (tmp_path / ".env").write_text("OMNICOREAGENT_SERVE_AUTH_ENABLED=false\n")

    result = _run_import_probe(
        tmp_path,
        """
import json
import os
import sys

import omnicoreagent

print(json.dumps({
    "omniserve_auth": os.environ.get("OMNICOREAGENT_SERVE_AUTH_ENABLED"),
    "dotenv_loaded": "dotenv" in sys.modules,
    "litellm_loaded": "litellm" in sys.modules,
    "openai_loaded": "openai" in sys.modules,
    "has_omnicoreagent": hasattr(omnicoreagent, "__all__"),
}))
""",
    )

    assert result == {
        "omniserve_auth": None,
        "dotenv_loaded": False,
        "litellm_loaded": False,
        "openai_loaded": False,
        "has_omnicoreagent": True,
    }


def test_root_import_exposes_package_version(tmp_path):
    result = _run_import_probe(
        tmp_path,
        """
import json
import omnicoreagent

print(json.dumps({
    "version_type": type(omnicoreagent.__version__).__name__,
    "has_version": bool(omnicoreagent.__version__),
}))
""",
    )

    assert result == {
        "version_type": "str",
        "has_version": True,
    }


def test_public_exports_resolve_without_provider_client_imports(tmp_path):
    result = _run_import_probe(
        tmp_path,
        """
import json
import sys

from omnicoreagent import (
    EventRouter,
    LLMConnection,
    MemoryRouter,
    OmniCoreAgent,
    ToolRegistry,
)

print(json.dumps({
    "exports": [
        EventRouter.__name__,
        LLMConnection.__name__,
        MemoryRouter.__name__,
        OmniCoreAgent.__name__,
        ToolRegistry.__name__,
    ],
    "litellm_loaded": "litellm" in sys.modules,
    "openai_loaded": "openai" in sys.modules,
    "dotenv_loaded": "dotenv" in sys.modules,
}))
""",
    )

    assert result == {
        "exports": [
            "EventRouter",
            "LLMConnection",
            "MemoryRouter",
            "OmniCoreAgent",
            "ToolRegistry",
        ],
        "litellm_loaded": False,
        "openai_loaded": False,
        "dotenv_loaded": False,
    }


def test_omnicoreagent_class_export_does_not_load_runtime_stack(tmp_path):
    result = _run_import_probe(
        tmp_path,
        """
import json
import sys

from omnicoreagent import OmniCoreAgent

runtime_modules = [
    "omnicoreagent.agent",
    "omnicoreagent.core.agents.base",
    "omnicoreagent.core.agents.react_agent",
    "omnicoreagent.core.events.event_router",
    "omnicoreagent.core.guardrails",
    "omnicoreagent.core.llm",
    "omnicoreagent.core.memory_store.memory_router",
    "omnicoreagent.core.subagents",
    "omnicoreagent.core.system_prompts",
    "omnicoreagent.core.tools.advance_tools.advanced_tools_use",
    "omnicoreagent.mcp_clients_connection.client",
]

print(json.dumps({
    "export": OmniCoreAgent.__name__,
    "loaded_runtime_modules": [
        module for module in runtime_modules if module in sys.modules
    ],
}))
""",
    )

    assert result == {
        "export": "OmniCoreAgent",
        "loaded_runtime_modules": [],
    }


def test_tool_registry_export_does_not_load_advanced_tools(tmp_path):
    result = _run_import_probe(
        tmp_path,
        """
import json
import sys

from omnicoreagent import ToolRegistry

print(json.dumps({
    "export": ToolRegistry.__name__,
    "advanced_tools_loaded": (
        "omnicoreagent.core.tools.advance_tools.advanced_tools_use" in sys.modules
    ),
    "legacy_utils_loaded": "omnicoreagent.core.utils" in sys.modules,
    "constants_loaded": "omnicoreagent.core.constants" in sys.modules,
}))
""",
    )

    assert result == {
        "export": "ToolRegistry",
        "advanced_tools_loaded": False,
        "legacy_utils_loaded": False,
        "constants_loaded": False,
    }


def test_agent_construction_does_not_load_prompt_or_runtime_modules(tmp_path):
    result = _run_import_probe(
        tmp_path,
        """
import json
import sys

from omnicoreagent import OmniCoreAgent

agent = OmniCoreAgent(
    name="startup",
    system_instruction="You are fast to create.",
    model_config={"provider": "openai", "model": "gpt-4o", "api_key": "test"},
    agent_config={"guardrail_mode": "off"},
)

watched_modules = [
    "omnicoreagent.core.agents.react_agent",
    "omnicoreagent.core.constants",
    "omnicoreagent.core.events.event_router",
    "omnicoreagent.core.guardrails",
    "omnicoreagent.core.llm",
    "omnicoreagent.core.memory_store.memory_router",
    "omnicoreagent.core.system_prompts",
    "omnicoreagent.core.token_usage",
    "omnicoreagent.core.types",
    "omnicoreagent.core.utils",  # deleted legacy dump-yard module
]

print(json.dumps({
    "agent_name": agent.name,
    "loaded_modules": [module for module in watched_modules if module in sys.modules],
}))
""",
    )

    assert result == {
        "agent_name": "startup",
        "loaded_modules": [],
    }


def test_disabled_tool_offloader_does_not_load_workspace_storage(tmp_path):
    result = _run_import_probe(
        tmp_path,
        """
import json
import sys

from omnicoreagent.core.workspace.artifacts import ToolResponseOffloader

offloader = ToolResponseOffloader(config={"enabled": False})

print(json.dumps({
    "offload_enabled": offloader.config.enabled,
    "workspace_loaded": "omnicoreagent.core.workspace" in sys.modules,
    "workspace_storage_loaded": "omnicoreagent.core.workspace.storage" in sys.modules,
}))
""",
    )

    assert result == {
        "offload_enabled": False,
        "workspace_loaded": True,
        "workspace_storage_loaded": False,
    }


def test_agent_initialize_without_optional_tools_stays_lightweight(tmp_path):
    result = _run_import_probe(
        tmp_path,
        """
import asyncio
import json
import sys

from omnicoreagent import OmniCoreAgent

watched_modules = [
    "decouple",
    "rich",
    "rich.console",
    "litellm",
    "openai",
    "pydantic",
    "omnicoreagent.core.guardrails",
    "omnicoreagent.core.tools.advance_tools.advanced_tools_use",
    "omnicoreagent.core.tools.advance_tools_use",
    "omnicoreagent.core.workspace.artifact_tools",
    "omnicoreagent.core.workspace.tools",
    "omnicoreagent.core.skills.tools",
    "omnicoreagent.core.workspace.storage",
]

async def main():
    agent = OmniCoreAgent(
        name="startup",
        system_instruction="You are fast to initialize.",
        model_config={"provider": "openai", "model": "gpt-4o", "api_key": "test"},
        agent_config={"guardrail_mode": "off"},
    )
    await agent.initialize()

    print(json.dumps({
        "agent_name": agent.name,
        "loaded_modules": [module for module in watched_modules if module in sys.modules],
    }))

asyncio.run(main())
""",
    )

    assert result == {
        "agent_name": "startup",
        "loaded_modules": [],
    }


def test_core_logging_import_has_no_runtime_side_effects(tmp_path):
    result = _run_import_probe(
        tmp_path,
        """
import json
import sys
from pathlib import Path

from omnicoreagent.core.logging import logger

print(json.dumps({
    "logger_name": logger.name,
    "log_file_created": Path("omnicoreagent.log").exists(),
    "decouple_loaded": "decouple" in sys.modules,
    "rich_loaded": any(module == "rich" or module.startswith("rich.") for module in sys.modules),
}))
""",
    )

    assert result == {
        "logger_name": "omnicoreagent",
        "log_file_created": False,
        "decouple_loaded": False,
        "rich_loaded": False,
    }
