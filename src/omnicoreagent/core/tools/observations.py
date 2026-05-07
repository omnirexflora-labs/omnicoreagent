import json
from collections import defaultdict
from html import escape


def build_xml_observations_block(tools_results):
    if not tools_results:
        return "<observations></observations>"

    lines = ["<observations>"]
    tool_counter = defaultdict(int)

    for result in tools_results:
        tool_name = str(result.get("tool_name", "unknown_tool"))
        tool_counter[tool_name] += 1
        unique_id = f"{tool_name}#{tool_counter[tool_name]}"

        output_value = result.get("data") or result.get("message") or "No output"
        if isinstance(output_value, (dict, list)):
            output_str = json.dumps(output_value, separators=(",", ":"))
        else:
            output_str = str(output_value)

        safe_output = escape(output_str, quote=False)
        lines.append(
            f'  <observation tool_name="{unique_id}">{safe_output}</observation>'
        )

    lines.append("</observations>")
    return "\n".join(lines)
