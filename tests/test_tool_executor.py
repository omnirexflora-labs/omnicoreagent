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


def test_normalize_dict_with_domain_status_is_data_not_envelope():
    executor = ToolExecutor(tool_handler=None)

    result = executor._normalize_result(
        "create_escalation",
        {"ticket_id": "tck-1042"},
        {
            "ticket_id": "tck-1042",
            "status": "queued_for_specialist",
            "summary": "Delayed shipment needs review.",
        },
    )

    assert result["status"] == "success"
    assert result["data"]["status"] == "queued_for_specialist"
    assert result["data"]["ticket_id"] == "tck-1042"


def test_normalize_dict_with_domain_status_and_message_is_data_not_envelope():
    executor = ToolExecutor(tool_handler=None)

    result = executor._normalize_result(
        "check_order",
        {"order_id": "ord-1002"},
        {
            "order_id": "ord-1002",
            "status": "delayed",
            "message": "Carrier missed the pickup window.",
        },
    )

    assert result["status"] == "success"
    assert result["data"]["status"] == "delayed"
    assert result["data"]["message"] == "Carrier missed the pickup window."


def test_normalize_dict_with_domain_error_field_is_data_not_envelope():
    executor = ToolExecutor(tool_handler=None)

    result = executor._normalize_result(
        "validate_profile",
        {"customer_id": "cust-001"},
        {
            "customer_id": "cust-001",
            "field": "email",
            "error": "minor validation warning",
        },
    )

    assert result["status"] == "success"
    assert result["data"]["error"] == "minor validation warning"
    assert result["data"]["field"] == "email"


def test_normalize_dict_with_domain_status_and_data_is_data_not_envelope():
    executor = ToolExecutor(tool_handler=None)

    result = executor._normalize_result(
        "create_escalation",
        {"ticket_id": "tck-1042"},
        {
            "status": "queued_for_specialist",
            "data": {"ticket_id": "tck-1042"},
        },
    )

    assert result["status"] == "success"
    assert result["data"] == {
        "status": "queued_for_specialist",
        "data": {"ticket_id": "tck-1042"},
    }


def test_normalize_pure_error_dict_is_error_envelope():
    executor = ToolExecutor(tool_handler=None)

    result = executor._normalize_result(
        "search_inventory",
        {"sku": "missing"},
        {"error": "Inventory service unavailable"},
    )

    assert result["status"] == "error"
    assert result["data"] is None
    assert result["message"] == "Inventory service unavailable"
