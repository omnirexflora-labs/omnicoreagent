import json
import re

from omnicoreagent.core.types import ParsedResponse
from omnicoreagent.core.logging import logger


def extract_thought(response: str) -> str | None:
    match = re.search(r"<thought>(.*?)</thought>", response, re.DOTALL)
    return match.group(1).strip() if match else None


def parse_action_or_answer(response: str, debug: bool = False) -> ParsedResponse:
    """Parse the agent XML response into the next runtime action."""
    try:
        tool_calls = _parse_calls(
            response=response,
            collection_tag="tool_calls",
            item_tag="tool_call",
            name_tags=("tool_name", "name"),
            output_key="tool",
            debug=debug,
        )
        if isinstance(tool_calls, ParsedResponse):
            return tool_calls
        if tool_calls:
            return ParsedResponse(
                action=True,
                data=json.dumps(tool_calls),
                tool_calls=True,
            )

        agent_calls = _parse_calls(
            response=response,
            collection_tag="agent_calls",
            item_tag="agent_call",
            name_tags=("agent_name", "name"),
            output_key="agent",
            debug=debug,
        )
        if isinstance(agent_calls, ParsedResponse):
            return agent_calls
        if agent_calls:
            return ParsedResponse(
                action=True,
                data=json.dumps(agent_calls),
                agent_calls=True,
            )

        final_answer_match = re.search(
            r"<final_answer>(.*?)</final_answer>", response, re.DOTALL
        )
        if final_answer_match:
            return ParsedResponse(answer=final_answer_match.group(1).strip())

        if "<" in response and ">" in response:
            return ParsedResponse(error=_xml_shape_error(response))

        return ParsedResponse(error=_missing_xml_error(response))
    except Exception as e:
        logger.error("Error parsing model response: %s", str(e))
        return ParsedResponse(error=str(e))


def _parse_calls(
    response: str,
    collection_tag: str,
    item_tag: str,
    name_tags: tuple[str, ...],
    output_key: str,
    debug: bool,
) -> list[dict] | ParsedResponse:
    blocks = _extract_call_blocks(response, collection_tag, item_tag, debug)
    calls = []

    for block in blocks:
        name_match = _first_tag_match(block, name_tags)
        args_match = _first_tag_match(block, ("parameters", "args"))
        if not (name_match and args_match):
            return ParsedResponse(
                error=f"Invalid {item_tag.replace('_', ' ')} format - missing name or parameters"
            )

        args = _parse_args(args_match.group(1).strip())
        if isinstance(args, ParsedResponse):
            return args

        calls.append(
            {
                output_key: name_match.group(1).strip(),
                "parameters": args,
            }
        )

    return calls


def _extract_call_blocks(
    response: str,
    collection_tag: str,
    item_tag: str,
    debug: bool,
) -> list[str]:
    if f"<{collection_tag}>" in response and f"</{collection_tag}>" in response:
        if debug:
            logger.info(f"Multiple {item_tag.replace('_', ' ')}s detected.")
        block_match = re.search(
            rf"<{collection_tag}>(.*?)</{collection_tag}>", response, re.DOTALL
        )
        if not block_match:
            return []
        return re.findall(
            rf"<{item_tag}>(.*?)</{item_tag}>", block_match.group(1), re.DOTALL
        )

    if f"<{item_tag}>" in response and f"</{item_tag}>" in response:
        if debug:
            logger.info(f"Single {item_tag.replace('_', ' ')} detected.")
        single_match = re.search(
            rf"<{item_tag}>(.*?)</{item_tag}>", response, re.DOTALL
        )
        return [single_match.group(1)] if single_match else []

    return []


def _first_tag_match(block: str, tags: tuple[str, ...]):
    for tag in tags:
        match = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
        if match:
            return match
    return None


def _parse_args(args_str: str) -> dict | ParsedResponse:
    if args_str.startswith("{") and args_str.endswith("}"):
        try:
            return json.loads(args_str)
        except json.JSONDecodeError as e:
            return ParsedResponse(error=f"Invalid JSON in args: {str(e)}")

    args = {}
    for key, value in re.findall(r"<(\w+)>(.*?)</\1>", args_str, re.DOTALL):
        value = value.strip()
        if (value.startswith("[") and value.endswith("]")) or (
            value.startswith("{") and value.endswith("}")
        ):
            try:
                args[key] = json.loads(value)
            except json.JSONDecodeError:
                args[key] = value
        else:
            args[key] = value
    return args


def _xml_shape_error(response: str) -> str:
    return (
        f"PARSE ERROR: Response contains XML but violates the required format.\n\n"
        f"❌ You used (WRONG):\n"
        f"   {response[:200]}...\n\n"
        f"✓ You Must use one of these structured blocks based on your intent (CORRECT):\n"
        f"   • IF you want to Think/Reason:\n"
        f"     <thought>I will analyze...</thought>\n\n"
        f"   • IF you want to provide the Final Answer:\n"
        f"     <final_answer>The finding is...</final_answer>\n\n"
        f"   • IF you want to Call a Tool:\n"
        f"     <tool_call>\n"
        f"       <tool_name>tool_name</tool_name>\n"
        f"       <parameters><param>value</param></parameters>\n"
        f"     </tool_call>\n\n"
        f"   • IF you want to Call Multiple Independent Tools:\n"
        f"     <tool_calls>\n"
        f"       <tool_call>\n"
        f"         <tool_name>first_tool</tool_name>\n"
        f"         <parameters><param>value</param></parameters>\n"
        f"       </tool_call>\n"
        f"       <tool_call>\n"
        f"         <tool_name>second_tool</tool_name>\n"
        f"         <parameters><param>value</param></parameters>\n"
        f"       </tool_call>\n"
        f"     </tool_calls>\n\n"
        f"ACTION REQUIRED:\n"
        f"- Decide your intent (Reasoning, Answer, or Tool Call).\n"
        f"- Retry using ONLY the specific valid tag for that intent.\n"
        f"- Do not use markdown code blocks ```xml ... ``` around the tags."
    )


def _missing_xml_error(response: str) -> str:
    return (
        f"PARSE ERROR: Response does not use required XML format.\n\n"
        f"❌ You used (WRONG):\n"
        f"   {response[:200]}...\n\n"
        f"✓ You Must use one of these structured blocks based on your intent (CORRECT):\n"
        f"   • IF you want to Think/Reason:\n"
        f"     <thought>I will analyze...</thought>\n\n"
        f"   • IF you want to provide the Final Answer:\n"
        f"     <final_answer>The finding is...</final_answer>\n\n"
        f"   • IF you want to Call a Tool:\n"
        f"     <tool_call>\n"
        f"       <tool_name>tool_name</tool_name>\n"
        f"       <parameters><param>value</param></parameters>\n"
        f"     </tool_call>\n\n"
        f"   • IF you want to Call Multiple Independent Tools:\n"
        f"     <tool_calls>\n"
        f"       <tool_call>\n"
        f"         <tool_name>first_tool</tool_name>\n"
        f"         <parameters><param>value</param></parameters>\n"
        f"       </tool_call>\n"
        f"       <tool_call>\n"
        f"         <tool_name>second_tool</tool_name>\n"
        f"         <parameters><param>value</param></parameters>\n"
        f"       </tool_call>\n"
        f"     </tool_calls>\n\n"
        f"ACTION REQUIRED:\n"
        f"- Decide your intent (Reasoning, Answer, or Tool Call).\n"
        f"- Retry using ONLY the specific valid tag for that intent.\n"
        f"- Do not output plain text outside tags."
    )
