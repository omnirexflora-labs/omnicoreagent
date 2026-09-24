"""Load an agent from a Python file without importing the serve extra.

The file defines an ``agent`` variable or a ``create_agent()`` function, the
same contract ``omniserve run --agent`` uses. OmniServe's own loader lives in
``omnicoreagent.serve.cli``, whose package imports FastAPI; a headless run must
work with the core install alone.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


class AgentFileError(Exception):
    """The agent file could not be found, imported, or did not define an agent."""


def load_agent(path: str | Path) -> Any:
    file_path = Path(path).resolve()
    if not file_path.is_file():
        raise AgentFileError(f"Agent file not found: {path}")

    spec = importlib.util.spec_from_file_location("omnicoreagent_run_agent", file_path)
    if spec is None or spec.loader is None:
        raise AgentFileError(f"Cannot load a Python module from: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["omnicoreagent_run_agent"] = module
    # The agent file may import modules that sit beside it.
    sys.path.insert(0, str(file_path.parent))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise AgentFileError(
            f"Error loading agent file {path}: {exc.__class__.__name__}: {exc}"
        ) from exc

    if hasattr(module, "agent"):
        return module.agent
    if hasattr(module, "create_agent"):
        try:
            return module.create_agent()
        except Exception as exc:
            raise AgentFileError(
                f"create_agent() in {path} failed: {exc.__class__.__name__}: {exc}"
            ) from exc
    raise AgentFileError(
        f"{path} must define an 'agent' variable or a 'create_agent()' function"
    )
