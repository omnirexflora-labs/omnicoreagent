from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from cookbook.shared import (  # noqa: E402,F401
    get_model,
    get_provider,
    load_cookbook_env,
    model_config,
    require_llm_api_key,
    response_text,
)
