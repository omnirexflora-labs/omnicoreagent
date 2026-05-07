import json
import uuid
from datetime import datetime, timezone


def normalize_content(content: object) -> str:
    """Ensure memory content is stored as a string."""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def normalize_metadata(obj):
    if isinstance(obj, dict):
        return {key: normalize_metadata(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [normalize_metadata(item) for item in obj]
    if isinstance(obj, uuid.UUID):
        return str(obj)
    return obj


def utc_now_str() -> str:
    return datetime.now(timezone.utc).isoformat()
