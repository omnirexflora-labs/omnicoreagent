import hashlib
import json
import logging
import platform
import re
import subprocess
import sys
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from types import SimpleNamespace
from rich.console import Console, Group
from rich.panel import Panel
from rich.pretty import Pretty
from rich.text import Text
from datetime import datetime, timezone
from decouple import config as decouple_config
import asyncio
from typing import Callable
from html import escape
import ast
import inspect

console = Console()
logger = logging.getLogger("omnicoreagent")
logger.setLevel(logging.INFO)


for handler in logger.handlers[:]:
    logger.removeHandler(handler)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)

log_file = Path("omnicoreagent.log")
file_handler = logging.FileHandler(log_file, mode="a")
file_handler.setLevel(logging.INFO)

console_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

file_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

console_handler.setFormatter(console_formatter)
file_handler.setFormatter(file_formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

console_handler.flush = sys.stdout.flush
file_handler.flush = lambda: file_handler.stream.flush()
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Coroutine


class BackgroundTaskManager:
    """Unified helper for running background, async, or blocking tasks safely."""

    def __init__(self, max_workers: int = 4):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.tasks = set()

    def run_background(self, func: Callable[..., Any], *args, **kwargs):
        """
        Run a synchronous function in a background thread (fire-and-forget).
        Use this for non-async I/O or CPU-bound functions.
        """

        def wrapper():
            try:
                func(*args, **kwargs)
            except Exception:
                traceback.print_exc()

        asyncio.create_task(asyncio.to_thread(wrapper))

    def run_background_async(self, coro: Coroutine):
        """
        Run an async coroutine in the same event loop (fire-and-forget).
        Use only for lightweight, non-blocking coroutines.
        """

        async def runner():
            try:
                await coro
            except Exception:
                traceback.print_exc()

        asyncio.create_task(runner())

    def run_background_strict(self, coro):
        """Fire and forget a coroutine safely, with internal error handling."""
        if asyncio.iscoroutine(coro):
            task = asyncio.create_task(self._run_safe(coro))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        else:
            logger.warning(f"Tried to run non-coroutine task: {coro}")

    async def _run_safe(self, coro):
        """Wrap background coroutine in safety net."""
        try:
            await coro
        except asyncio.CancelledError:
            logger.debug("Background task cancelled.")
        except Exception as e:
            logger.exception(f"Background task failed: {e}")

    def run_in_executor(
        self, func: Callable[..., Any], *args, **kwargs
    ) -> asyncio.Task:
        """
        Run a blocking function in the threadpool and return an awaitable task.
        Use this when you need the result (not fire-and-forget).
        """
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(self.executor, lambda: func(*args, **kwargs))


def clean_json_response(json_response):
    """Clean and extract JSON from the response."""
    try:
        json.loads(json_response)
        return json_response
    except json.JSONDecodeError:
        try:
            if "```" in json_response:
                start = json_response.find("```") + 3
                end = json_response.rfind("```")
                if json_response[start : start + 4].lower() == "json":
                    start += 4
                json_response = json_response[start:end].strip()

            start = json_response.find("{")
            end = json_response.rfind("}") + 1
            if start >= 0 and end > start:
                json_response = json_response[start:end]

            json.loads(json_response)
            return json_response
        except (json.JSONDecodeError, ValueError) as e:
            raise json.JSONDecodeError(
                f"Could not extract valid JSON from response: {str(e)}",
                json_response,
                0,
            )


def hash_text(text: str) -> str:
    """Generate a simple hash for a string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RobustLoopDetector:
    def __init__(
        self,
        maxlen: int = 20,
        consecutive_threshold: int = 7,
        pattern_detection: bool = True,
        max_pattern_length: int = 5,
        pattern_repetition_threshold: int = 4,
        debug: bool = True,
    ):
        """
        Initialize a robust loop detector.

        - maxlen: number of past interactions to track
        - consecutive_threshold: number of consecutive IDENTICAL calls to detect loop
        - pattern_detection: enable repeating pattern detection
        - max_pattern_length: max pattern length for pattern detection
        - pattern_repetition_threshold: how many times a pattern must repeat to be considered a loop (default: 4)
        - debug: enable debug logging
        """
        self.global_interactions = deque(maxlen=maxlen)
        self.tool_interactions: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=maxlen)
        )
        self.consecutive_threshold = max(1, consecutive_threshold)
        self.pattern_detection = pattern_detection
        self.max_pattern_length = max(1, max_pattern_length)
        self.pattern_repetition_threshold = max(4, pattern_repetition_threshold)

        self._last_signature = None
        self._consecutive_count = 0
        self.debug = debug

    def record_tool_call(
        self, tool_name: str, tool_input: str, tool_output: str
    ) -> None:
        """Record a new tool call interaction."""
        tool_name = tool_name or "unknown_tool"
        tool_input = tool_input if tool_input is not None else ""
        tool_output = tool_output if tool_output is not None else ""

        signature = (
            tool_name,
            hash_text(tool_input),
            hash_text(tool_output),
        )

        self.global_interactions.append(signature)
        self.tool_interactions[tool_name].append(signature)

        if signature == self._last_signature:
            self._consecutive_count += 1
        else:
            self._last_signature = signature
            self._consecutive_count = 1

        if self.debug:
            logger.info(
                f"[LoopDetector] Tool '{tool_name}' called. "
                f"Consecutive count: {self._consecutive_count} "
                f"(signature: {tool_name}, input_hash={signature[1][:8]}..., output_hash={signature[2][:8]}...)"
            )

    def reset(self, tool_name: str | None = None) -> None:
        """
        Reset loop memory.

        Edge cases handled:
        - Empty/whitespace tool_name (treated as global reset)
        - Resetting a tool that doesn't exist (no error)
        - Multiple rapid resets (idempotent)
        """
        if tool_name and tool_name.strip():
            self.tool_interactions.pop(tool_name, None)

            if self._last_signature and self._last_signature[0] == tool_name:
                self._last_signature = None
                self._consecutive_count = 0
        else:
            self.global_interactions.clear()
            self.tool_interactions.clear()
            self._last_signature = None
            self._consecutive_count = 0

        if self.debug:
            reset_target = (
                f"tool '{tool_name}'"
                if tool_name and tool_name.strip()
                else "all tools"
            )
            logger.info(f"[LoopDetector] Reset performed for {reset_target}.")

    def _is_tool_stuck_consecutive(self, tool_name: str) -> bool:
        """Check if a tool has been called consecutively with SAME input/output."""

        if not tool_name:
            return False

        tool_history = self.tool_interactions.get(tool_name, [])
        if not tool_history:
            return False

        last_tool_signature = tool_history[-1]

        if self._last_signature is None:
            return False

        stuck = (
            last_tool_signature == self._last_signature
            and self._consecutive_count >= self.consecutive_threshold
        )

        if self.debug and stuck:
            logger.info(
                f"[LoopDetector] Tool '{tool_name}' is stuck due to "
                f"{self._consecutive_count} consecutive identical calls."
            )
        return stuck

    def _has_tool_pattern_loop(self, tool_name: str) -> bool:
        """
        Detect repeating patterns for a tool.

        A pattern is considered a loop only if it repeats pattern_repetition_threshold times.
        For example, with threshold=2 and pattern_length=2:
        - [A, B, A, B, A, B] is a loop (pattern [A,B] repeats 3 times >= 2)
        - [A, B, A, B] is NOT a loop (pattern [A,B] repeats only 2 times, need 3+ for threshold 2)
        """

        if not tool_name or not self.pattern_detection:
            return False

        interactions = list(self.tool_interactions.get(tool_name, []))

        min_required = 4
        if len(interactions) < min_required:
            return False

        max_checkable_pattern = min(
            self.max_pattern_length,
            len(interactions) // (self.pattern_repetition_threshold + 1),
        )

        if max_checkable_pattern < 1:
            return False

        for pattern_len in range(1, max_checkable_pattern + 1):
            required_length = pattern_len * (self.pattern_repetition_threshold + 1)

            if len(interactions) < required_length:
                continue

            pattern = interactions[-pattern_len:]
            is_loop = True

            for i in range(1, self.pattern_repetition_threshold + 1):
                start_idx = -(i + 1) * pattern_len
                end_idx = -i * pattern_len if i > 0 else None
                prev_pattern = interactions[start_idx:end_idx]

                if len(prev_pattern) != pattern_len or prev_pattern != pattern:
                    is_loop = False
                    break

            if is_loop:
                if self.debug:
                    logger.info(
                        f"[LoopDetector] Tool '{tool_name}' has repeating pattern: "
                        f"{pattern_len} steps repeated {self.pattern_repetition_threshold + 1} times."
                    )
                return True

        return False

    def is_looping(self, tool_name: str | None = None) -> bool:
        """
        Check if a tool or global state is looping.

        Edge cases handled:
        - Empty/None tool_name when checking specific tool
        - No interactions recorded yet
        - Empty tool_interactions dict
        """
        if tool_name is not None:
            if not tool_name or not tool_name.strip():
                return False
            return self._is_tool_stuck_consecutive(
                tool_name
            ) or self._has_tool_pattern_loop(tool_name)

        if not self.tool_interactions:
            return False

        return any(
            self._is_tool_stuck_consecutive(name) or self._has_tool_pattern_loop(name)
            for name in self.tool_interactions.keys()
        )

    def get_loop_type(self, tool_name: str | None = None) -> list[str]:
        """
        Get detailed loop type for a tool.

        Edge cases handled:
        - None/empty tool_name
        - No loops detected (returns empty list)
        - Tools with no history
        """
        types = []

        if tool_name is not None:
            if not tool_name or not tool_name.strip():
                return types

            if self._is_tool_stuck_consecutive(tool_name):
                types.append("consecutive_calls")
            if self._has_tool_pattern_loop(tool_name):
                types.append("repeating_pattern")
        else:
            if not self.tool_interactions:
                return types

            for name in self.tool_interactions.keys():
                if self._is_tool_stuck_consecutive(name):
                    types.append(f"{name}: consecutive_calls")
                if self._has_tool_pattern_loop(name):
                    types.append(f"{name}: repeating_pattern")

        return types


def normalize_content(content: any) -> str:
    """Ensure message content is always a string."""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def strip_comprehensive_narrative(text):
    """
    Removes <comprehensive_narrative> tags. Returns original text if any error occurs.
    """
    try:
        if not isinstance(text, str):
            return str(text)
        return re.sub(r"</?comprehensive_narrative>", "", text).strip()
    except (TypeError, re.error):
        return str(text)


def json_to_smooth_text(content):
    """
    Converts LLM content (string or JSON string) into smooth, human-readable text.
    - If content is JSON in string form, parse and flatten it.
    - If content is plain text, return as-is.
    - Safe fallback: returns original content if anything fails.
    """
    try:
        if isinstance(content, str):
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return content
        else:
            data = content

        def _flatten(obj):
            if isinstance(obj, dict):
                sentences = []
                for k, v in obj.items():
                    pretty_key = k.replace("_", " ").capitalize()
                    sentences.append(f"{pretty_key}: {_flatten(v)}")
                return " ".join(sentences)
            elif isinstance(obj, list):
                items = [_flatten(v) for v in obj]
                if len(items) == 1:
                    return items[0]
                return ", ".join(items[:-1]) + " and " + items[-1]
            else:
                return str(obj)

        return _flatten(data)

    except Exception:
        return str(content)


def normalize_enriched_tool(enriched: str) -> str:
    """
    Normalize and clean the enriched tool string for better BM25 retrieval.

    This function performs the following operations:
    1. Converts to lowercase for case-insensitive matching
    2. Removes JSON structural characters (braces, brackets, quotes, colons)
    3. Tokenizes parameter names from camelCase/snake_case
    4. Removes special characters while preserving word boundaries
    5. Normalizes whitespace

    Args:
        enriched: Raw enriched string containing tool name, description, and parameters

    Returns:
        Normalized string optimized for BM25 indexing and retrieval
    """
    if not enriched:
        return ""

    text = enriched.lower()

    text = re.sub(r'[{}\[\]":\',]', " ", text)

    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"_", " ", text)

    text = re.sub(r"[^a-z0-9\s]", " ", text)

    text = re.sub(r"\s+", " ", text)

    normalized = text.strip()

    return normalized


def resolve_agent(agent_name: str, sub_agents: list):
    for agent in sub_agents:
        if agent.name == agent_name:
            return agent
    raise ValueError(f"Sub-agent '{agent_name}' not found")


def build_kwargs(agent, provided_params: dict):
    sig = inspect.signature(agent.run)
    kwargs = {}

    for name, param in sig.parameters.items():
        if name == "self":
            continue

        if name in provided_params:
            kwargs[name] = provided_params[name]
            continue

        if param.default is inspect.Parameter.empty:
            raise ValueError(
                f"Missing required parameter '{name}' for agent '{agent.name}'"
            )

    for extra in provided_params:
        if extra not in sig.parameters:
            del provided_params[extra]

    return kwargs


def build_sub_agents_observation_xml(observations: list[dict]) -> str:
    """
    Build properly formatted XML observation block for sub-agent results.

    Args:
        observations: List of dicts with keys: agent_name, status, output

    Returns:
        Formatted XML string for LLM consumption
    """
    xml_lines = [
        "OBSERVATION RESULT FROM SUB-AGENTS",
        "<observations>",
    ]

    for obs in observations:
        agent_name = obs.get("agent_name", "unknown")
        status = obs.get("status", "unknown")
        output = obs.get("output", "")

        xml_lines.append("  <observation>")
        xml_lines.append(f"    <agent_name>{agent_name}</agent_name>")
        xml_lines.append(f"    <status>{status}</status>")

        if status == "error":
            xml_lines.append(f"    <e>{output}</e>")
        else:
            xml_lines.append(f"    <o>{output}</o>")

        xml_lines.append("  </observation>")

    xml_lines.append("</observations>")
    xml_lines.append("END OF OBSERVATIONS")

    return "\n".join(xml_lines)


def handle_stuck_state(original_system_prompt: str, message_stuck_prompt: bool = False):
    """
    Creates a modified system prompt that includes stuck detection guidance.

    Parameters:
    - original_system_prompt: The normal system prompt you use
    - message_stuck_prompt: If True, use a shorter version of the stuck prompt

    Returns:
    - Modified system prompt with stuck guidance prepended
    """
    if message_stuck_prompt:
        stuck_prompt = (
            "⚠️ You are stuck in a loop. This must be addressed immediately.\n\n"
            "REQUIRED ACTIONS:\n"
            "1. **STOP** the current approach\n"
            "2. **ANALYZE** why the previous attempts failed\n"
            "3. **TRY** a completely different method\n"
            "4. **IF** the issue cannot be resolved:\n"
            "   - Explain clearly why not\n"
            "   - Provide alternative solutions\n"
            "   - DO NOT repeat the same failed action\n\n"
            "   - DO NOT try again. immediately stop and do not try again.\n\n"
            "   - Tell user your last known good state, error message and the current state of the conversation.\n\n"
            "❗ CONTINUING THE SAME APPROACH WILL RESULT IN FURTHER FAILURES"
        )
    else:
        stuck_prompt = (
            "⚠️ It looks like you're stuck or repeating an ineffective approach.\n"
            "Take a moment to do the following:\n"
            "1. **Reflect**: Analyze why the previous step didn't work (e.g., tool call failure, irrelevant observation).\n"
            "2. **Try Again Differently**: Use a different tool, change the inputs, or attempt a new strategy.\n"
            "3. **If Still Unsolvable**:\n"
            "   - **Clearly explain** to the user *why* the issue cannot be solved.\n"
            "   - Provide any relevant reasoning or constraints.\n"
            "   - Offer one or more alternative solutions or next steps.\n"
            "   - DO NOT try again. immediately stop and do not try again.\n\n"
            "   - Tell user your last known good state, error message and the current state of the conversation.\n\n"
            "❗ Do not repeat the same failed strategy or go silent."
        )

    modified_system_prompt = (
        f"{stuck_prompt}\n\n"
        f"Your previous approaches to solve this problem have failed. You need to try something completely different.\n\n"
    )

    return modified_system_prompt


def normalize_metadata(obj):
    if isinstance(obj, dict):
        return {k: normalize_metadata(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [normalize_metadata(i) for i in obj]
    elif isinstance(obj, uuid.UUID):
        return str(obj)
    return obj


def dict_to_namespace(d):
    return json.loads(json.dumps(d), object_hook=lambda x: SimpleNamespace(**x))


def utc_now_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_timestamp(ts) -> str:
    if not isinstance(ts, datetime):
        ts = datetime.fromisoformat(ts)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def strip_json_comments(text: str) -> str:
    """
    Removes // and /* */ style comments from JSON-like text,
    but only if they're outside of double-quoted strings.
    """

    def replacer(match):
        s = match.group(0)
        if s.startswith('"'):
            return s
        return ""

    pattern = r'"(?:\\.|[^"\\])*"' + r"|//.*?$|/\*.*?\*/"
    return re.sub(pattern, replacer, text, flags=re.DOTALL | re.MULTILINE)


def show_tool_response(agent_name, tool_name, tool_args, observation):
    content = Group(
        Text(agent_name.upper(), style="bold magenta"),
        Text(f"→ Calling tool: {tool_name}", style="bold blue"),
        Text("→ Tool input:", style="bold yellow"),
        Pretty(tool_args),
        Text("→ Tool response:", style="bold green"),
        Pretty(observation),
    )

    panel = Panel.fit(content, title="🔧 TOOL CALL LOG", border_style="bright_black")
    console.print(panel)


def show_sub_agent_call_result(agent_call_result):
    blocks = []

    parent_agent = agent_call_result.get("agent_name", "unknown_agent")
    agent_calls = agent_call_result.get("agent_calls", [])
    outputs = agent_call_result.get("output", [])

    blocks.append(Text(f"PARENT AGENT: {parent_agent.upper()}", style="bold magenta"))

    output_map = {o["agent_name"]: o for o in outputs}

    for call in agent_calls:
        agent_name = call.get("agent", "unknown_agent")
        params = call.get("parameters", {})

        result = output_map.get(agent_name, {})
        status = result.get("status", "unknown")
        output = result.get("output")

        blocks.append(Text(""))
        blocks.append(Text(f"→ Sub-agent: {agent_name}", style="bold blue"))
        blocks.append(Text("→ Parameters:", style="bold yellow"))
        blocks.append(Pretty(params))

        blocks.append(
            Text(
                f"→ Status: {status}",
                style="bold green" if status == "success" else "bold red",
            )
        )

        blocks.append(Text("→ Output:", style="bold cyan"))
        blocks.append(Pretty(output))

    panel = Panel.fit(
        Group(*blocks),
        title="🤖 SUB-AGENT EXECUTION TRACE",
        border_style="bright_black",
    )

    console.print(panel)


def normalize_tool_args(value: Any) -> Any:
    """
    Deeply normalize tool arguments.
    - If value is a list with single dict, unwrap it
    - Converts stringified booleans to bool
    - Converts stringified numbers to int/float
    - Converts "null"/"none" to None
    - Converts stringified JSON or Python literals to Python objects
    - Handles nested dicts, lists, tuples
    - Preserves strings with XML/multi-line content
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
                parts = [p.strip() for p in val.split(",") if p.strip()]
                if len(parts) > 1:
                    return [_normalize(p) for p in parts]
            return v
        elif isinstance(v, dict):
            return {k: _normalize(val) for k, val in v.items()}
        elif isinstance(v, list):
            return [_normalize(i) for i in v]
        elif isinstance(v, tuple):
            return tuple(_normalize(i) for i in v)
        return v

    return _normalize(value)


def get_mac_address() -> str:
    """Get the MAC address of the client machine.

    Returns:
        str: The MAC address as a string, or a fallback UUID if MAC address cannot be determined.
    """
    try:
        if platform.system() == "Linux":
            for interface in ["eth0", "wlan0", "en0"]:
                try:
                    with open(f"/sys/class/net/{interface}/address") as f:
                        mac = f.read().strip()
                        if mac:
                            return mac
                except FileNotFoundError:
                    continue

            result = subprocess.run(
                ["ip", "link", "show"], capture_output=True, text=True
            )
            for line in result.stdout.split("\n"):
                if "link/ether" in line:
                    return line.split("link/ether")[1].split()[0]

        elif platform.system() == "Darwin":
            result = subprocess.run(["ifconfig"], capture_output=True, text=True)
            for line in result.stdout.split("\n"):
                if "ether" in line:
                    return line.split("ether")[1].split()[0]

        elif platform.system() == "Windows":
            result = subprocess.run(["getmac"], capture_output=True, text=True)
            for line in result.stdout.split("\n"):
                if ":" in line and "-" in line:
                    return line.split()[0]

    except Exception as e:
        logger.warning(f"Could not get MAC address: {e}")

    return str(uuid.uuid4())


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


CLIENT_MAC_ADDRESS = get_mac_address()

OPIK_AVAILABLE = False
track = None

try:
    api_key = decouple_config("OPIK_API_KEY", default=None)
    workspace = decouple_config("OPIK_WORKSPACE", default=None)

    if api_key and workspace:
        from opik import track as opik_track

        OPIK_AVAILABLE = True
        track = opik_track
        logger.debug("Opik imported successfully with valid credentials")
    else:
        logger.debug("Opik available but no valid credentials - using fake decorator")

        def track(name_or_func=None):
            if callable(name_or_func):
                return name_or_func
            else:

                def decorator(func):
                    return func

                return decorator

            return decorator

            return decorator
except ImportError:

    def track(name_or_func=None):
        if callable(name_or_func):
            return name_or_func
        else:

            def decorator(func):
                return func

            return decorator

    logger.debug("Opik not available, using no-op decorator")
