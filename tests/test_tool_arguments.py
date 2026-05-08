from omnicoreagent.core.tools.arguments import normalize_tool_args


def test_normalize_tool_args_unwraps_single_dict_list_from_stringified_json():
    raw = '[{"origin": "NYC", "destination": "LON", "date": "2025-09-01"}]'

    normalized = normalize_tool_args(raw)

    assert normalized == {
        "origin": "NYC",
        "destination": "LON",
        "date": "2025-09-01",
    }


def test_normalize_tool_args_keeps_real_scalar_lists():
    normalized = normalize_tool_args({"tags": "billing, urgent"})

    assert normalized == {"tags": ["billing", "urgent"]}
