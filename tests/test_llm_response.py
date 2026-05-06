from types import SimpleNamespace

import pytest

from omnicoreagent.core.agents.llm_response import (
    extract_response_content,
    extract_response_usage,
)


def test_extract_response_content_from_choices_object():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="  hello  "),
            )
        ]
    )

    assert extract_response_content(response) == "hello"
    assert extract_response_content(response, strip=False) == "  hello  "


def test_extract_response_content_from_dict_choices():
    response = {"choices": [{"message": {"content": "answer"}}]}

    assert extract_response_content(response) == "answer"


def test_extract_response_content_returns_default_for_unknown_shape():
    assert extract_response_content(object(), default="") == ""


def test_extract_response_content_rejects_unknown_shape_without_default():
    with pytest.raises(ValueError, match="No valid response content"):
        extract_response_content(object())


def test_extract_response_usage_from_object():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )
    )

    usage = extract_response_usage(response)

    assert usage.requests == 1
    assert usage.request_tokens == 10
    assert usage.response_tokens == 5
    assert usage.total_tokens == 15


def test_extract_response_usage_from_dict():
    usage = extract_response_usage(
        {
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
            }
        }
    )

    assert usage.requests == 1
    assert usage.request_tokens == 7
    assert usage.response_tokens == 3
    assert usage.total_tokens == 10
