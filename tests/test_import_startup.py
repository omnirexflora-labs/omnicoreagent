import json
import os
import subprocess
import sys


def _run_import_probe(tmp_path, code: str) -> dict:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("OMNISERVE_"):
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
    (tmp_path / ".env").write_text("OMNISERVE_AUTH_ENABLED=false\n")

    result = _run_import_probe(
        tmp_path,
        """
import json
import os
import sys

import omnicoreagent

print(json.dumps({
    "omniserve_auth": os.environ.get("OMNISERVE_AUTH_ENABLED"),
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
