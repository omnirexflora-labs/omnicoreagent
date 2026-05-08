"""Import helpers for running OmniServe examples as plain scripts."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from cookbook.shared import (  # noqa: E402,F401
    model_config,
    require_llm_api_key,
)
