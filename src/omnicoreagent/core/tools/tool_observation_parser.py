from __future__ import annotations

import json
from typing import Any

from omnicoreagent.core.utils import logger


def parse_tool_observation(raw_output: str | dict[str, Any]) -> dict[str, Any]:
    """
    Normalize tool output into a single observation shape.

    Always returns:
    {
        "status": "success" | "partial" | "error",
        "tools_results": [
            {
                "tool_name": str,
                "args": dict | None,
                "status": "success" | "error",
                "data": dict | str | None,
                "message": str | None,
            },
            ...
        ]
    }
    """
    try:
        parsed = _coerce_raw_output(raw_output)
        raw_results = _extract_raw_results(parsed)
        normalized_results = [_normalize_result(item) for item in raw_results]

        return {
            "status": _global_status(normalized_results),
            "tools_results": normalized_results,
        }

    except Exception as e:
        logger.error(f"Error parsing tool observation: {e}", exc_info=True)
        return _error_observation(f"Observation parsing failed: {str(e)}")


def _coerce_raw_output(raw_output: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw_output, str):
        try:
            return json.loads(raw_output)
        except json.JSONDecodeError:
            logger.warning("Tool observation output is not valid JSON.")
            return _single_error_result(raw_output)
    if isinstance(raw_output, dict):
        return raw_output
    return _single_error_result(str(raw_output))


def _extract_raw_results(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    if "tools_results" in parsed:
        return parsed["tools_results"]

    if "successes" in parsed or "errors" in parsed:
        raw_results = []
        for success in parsed.get("successes", []):
            raw_results.append({**success, "status": "success"})
        for error in parsed.get("errors", []):
            raw_results.append({**error, "status": "error"})
        return raw_results

    return [parsed]


def _normalize_result(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            pass

    return {
        "tool_name": item.get("tool_name") or item.get("tool") or "unknown",
        "args": item.get("args"),
        "status": item.get("status", "success"),
        "data": data,
        "message": item.get("message") or item.get("error"),
    }


def _global_status(normalized_results: list[dict[str, Any]]) -> str:
    success_count = sum(
        1 for result in normalized_results if result["status"] == "success"
    )
    error_count = sum(1 for result in normalized_results if result["status"] == "error")

    if success_count > 0 and error_count == 0:
        return "success"
    if success_count > 0 and error_count > 0:
        return "partial"
    return "error"


def _single_error_result(message: str) -> dict[str, Any]:
    return {
        "tools_results": [
            {
                "tool_name": "unknown",
                "args": None,
                "status": "error",
                "data": None,
                "message": message,
            }
        ]
    }


def _error_observation(message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "tools_results": [
            {
                "tool_name": "unknown",
                "args": None,
                "status": "error",
                "data": None,
                "message": message,
            }
        ],
    }
