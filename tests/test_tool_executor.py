from omnicoreagent.core.tools.tool_executor import ToolExecutor


def test_normalize_plain_dict_tool_result_is_data():
    executor = ToolExecutor(tool_handler=None)

    result = executor._normalize_result(
        "search_flights",
        {"origin": "NYC"},
        {"flights": [{"flight_id": "F3001"}]},
    )

    assert result["status"] == "success"
    assert result["data"] == {"flights": [{"flight_id": "F3001"}]}
    assert result["message"] is None


def test_normalize_structured_tool_result_envelope_still_supported():
    executor = ToolExecutor(tool_handler=None)

    result = executor._normalize_result(
        "search_products",
        {"query": "headphones"},
        {"status": "success", "data": [{"name": "Wireless Headphones"}]},
    )

    assert result["status"] == "success"
    assert result["data"] == [{"name": "Wireless Headphones"}]
