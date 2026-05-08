import ast
import json
from typing import Any


def normalize_tool_args(value: Any) -> Any:
    """
    Deeply normalize model-provided tool arguments.

    Handles stringified booleans, numbers, null values, JSON/Python literals,
    comma-separated scalar lists, nested dicts/lists/tuples, and single-dict
    list wrappers.
    """
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        value = value[0]

    def _normalize(v: Any) -> Any:
        if isinstance(v, str):
            val = v.strip()
            if val.lower() in ("null", "none"):
                return None
            if val.lower() == "true":
                return True
            if val.lower() == "false":
                return False
            try:
                if "." in val or "e" in val.lower():
                    return float(val)
                return int(val)
            except ValueError:
                pass
            try:
                parsed_json = json.loads(val)
                return _normalize(parsed_json)
            except (ValueError, json.JSONDecodeError):
                pass
            if val.startswith(("[", "{", "(")) and val.endswith(("]", "}", ")")):
                try:
                    parsed_literal = ast.literal_eval(val)
                    return _normalize(parsed_literal)
                except (ValueError, SyntaxError):
                    pass
            if (
                "," in val
                and not (val.startswith('"') or val.startswith("'"))
                and "<" not in val
            ):
                parts = [part.strip() for part in val.split(",") if part.strip()]
                if len(parts) > 1:
                    return [_normalize(part) for part in parts]
            return v
        if isinstance(v, dict):
            return {key: _normalize(val) for key, val in v.items()}
        if isinstance(v, list):
            normalized_items = [_normalize(item) for item in v]
            if len(normalized_items) == 1 and isinstance(normalized_items[0], dict):
                return normalized_items[0]
            return normalized_items
        if isinstance(v, tuple):
            return tuple(_normalize(item) for item in v)
        return v

    return _normalize(value)
