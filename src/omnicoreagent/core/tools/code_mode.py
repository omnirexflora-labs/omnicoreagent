"""Code mode: a ``run_code`` tool that runs a Python program in Monty.

The model writes a short program; the chosen tools are functions it can call.
The program runs in Monty (Pydantic's interpreter for a Python subset) in a
separate worker process, with no filesystem, network, or environment access,
under a time limit, a memory limit, and a cap on tool calls. Each tool call
from the program is handed back to the harness, which runs it through the
same governed path as a direct tool call and returns the result (or raises
the error) into the program.

Requires the optional extra: ``pip install "omnicoreagent[codemode]"``.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

RUN_CODE = "run_code"

# A tool function called from a program: (keyword arguments, positional
# arguments) in, the tool's result data out, or an exception.
ToolFunction = Callable[[dict[str, Any], list[Any]], Awaitable[Any]]


@dataclass
class CodeModeConfig:
    enabled: bool = False
    # Tool names a program may call; None means every eligible tool.
    tools: list[str] | None = None
    max_duration_seconds: float = 30.0
    max_memory_bytes: int = 256 * 1024 * 1024
    max_tool_calls: int = 50
    max_output_bytes: int = 64 * 1024
    # Signs a paused program stored on a run record. Without one (or
    # OMNICOREAGENT_CODE_SNAPSHOT_KEY) a random key is used, so a paused
    # program can only continue in the process that paused it.
    snapshot_key: str | None = None
    max_snapshot_bytes: int = 4 * 1024 * 1024

    @classmethod
    def from_value(cls, value: "CodeModeConfig | dict[str, Any] | None") -> "CodeModeConfig":
        if isinstance(value, cls):
            return value
        value = dict(value or {})
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"code_mode has unknown keys: {', '.join(sorted(unknown))}")
        config = cls(**value)
        if config.tools is not None and (
            not isinstance(config.tools, list) or not all(isinstance(t, str) for t in config.tools)
        ):
            raise ValueError("code_mode.tools must be a list of tool names")
        if config.snapshot_key is not None and not isinstance(config.snapshot_key, str):
            raise ValueError("code_mode.snapshot_key must be a string")
        for name in (
            "max_duration_seconds",
            "max_memory_bytes",
            "max_tool_calls",
            "max_output_bytes",
            "max_snapshot_bytes",
        ):
            number = getattr(config, name)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or number <= 0:
                raise ValueError(f"code_mode.{name} must be positive")
        return config

    def allows(self, tool_name: str) -> bool:
        return tool_name != RUN_CODE and (self.tools is None or tool_name in self.tools)


def callable_name(tool_name: str) -> str | None:
    """The function name a program uses for a tool, if the name is usable."""
    return tool_name if tool_name.isidentifier() else None


def function_signature(tool_name: str, schema: dict[str, Any], description: str) -> str:
    properties = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    parameters = [
        f"{name}: {(spec or {}).get('type', 'any')}" + ("" if name in required else " = None")
        for name, spec in properties.items()
    ]
    summary = " ".join((description or "").split())[:160]
    return f"{tool_name}({', '.join(parameters)})  # {summary}"


def build_code_mode_tool(
    registry: ToolRegistry, *, config: CodeModeConfig, functions: list[str]
) -> ToolRegistry:
    """Register ``run_code``; ``functions`` are the signatures shown to the model."""

    listing = "\n".join(f"- {line}" for line in functions) or "- (no tools are available)"

    @registry.register_tool(
        name=RUN_CODE,
        description=(
            "Run a short Python program. Use it to combine several tool calls, "
            "loop, filter, or compute, instead of calling tools one at a time. "
            "Call tools as functions with keyword arguments; each returns the "
            "tool's result data or raises an error you can catch. The value of "
            "the last expression is returned, with anything printed. The program "
            "has no filesystem, network, or environment access, and is stopped "
            f"after {config.max_duration_seconds:g}s or {config.max_tool_calls} tool calls. "
            "It supports a subset of Python (no imports of third-party packages).\n"
            f"Functions:\n{listing}"
        ),
        inputSchema={
            "type": "object",
            "properties": {"code": {"type": "string", "description": "The Python program."}},
            "required": ["code"],
            "additionalProperties": False,
        },
    )
    async def run_code(code: str) -> dict:
        # Dispatched by the agent loop, which supplies the governed tool
        # functions; this body only runs if the tool is called directly.
        return {"status": "error", "message": "run_code must run inside an agent"}

    registry.mark_internal_tool_provider(RUN_CODE, "code")
    return registry


class _TooManyCalls(Exception):
    pass


class ProgramPaused(Exception):
    """A tool call inside a program needs a person's decision.

    Carries the paused program, signed, so the run can store it and continue
    the program later from exactly this call.
    """

    def __init__(self, *, snapshot: bytes, call_id: str, call_number: int, output: str):
        super().__init__(f"Program paused at {call_id}")
        self.snapshot = snapshot
        self.call_id = call_id
        self.call_number = call_number
        self.output = output


class _PauseHere(Exception):
    """Raised by a tool function when its call needs approval."""

    def __init__(self, call_id: str, call_number: int):
        super().__init__(call_id)
        self.call_id = call_id
        self.call_number = call_number


def snapshot_key(config: "CodeModeConfig") -> bytes:
    """The key that signs paused programs."""
    import os

    key = config.snapshot_key or os.environ.get("OMNICOREAGENT_CODE_SNAPSHOT_KEY")
    if key:
        return key.encode("utf-8")
    return _PROCESS_KEY


def sign_snapshot(blob: bytes, config: "CodeModeConfig") -> str:
    import hashlib
    import hmac

    return hmac.new(snapshot_key(config), blob, hashlib.sha256).hexdigest()


def verify_snapshot(blob: bytes, signature: str, config: "CodeModeConfig") -> bool:
    import hmac

    return hmac.compare_digest(sign_snapshot(blob, config), signature or "")


async def run_program(
    code: str,
    *,
    functions: dict[str, ToolFunction],
    config: CodeModeConfig,
    resume_from: bytes | None = None,
) -> dict[str, Any]:
    """Run ``code`` in Monty; tool calls go to ``functions``.

    ``resume_from`` is a paused program (from ``ProgramPaused``): it continues
    at the call it stopped on instead of running the code again.
    """
    from omnicoreagent._optional import load_optional

    monty = load_optional("code mode", "codemode", lambda: __import__("pydantic_monty"))
    if not isinstance(code, str) or not code.strip():
        return {"status": "error", "message": "The program is empty."}
    output = monty.CollectString(max_bytes=config.max_output_bytes)
    limits = {
        # The program's own compute time (Monty 1.0); the clock stops while it
        # waits on a tool.
        "max_feed_duration_secs": float(config.max_duration_seconds),
        "max_memory": int(config.max_memory_bytes),
        # Tool calls plus refused OS calls; the tool cap is enforced below.
        "max_suspensions": int(config.max_tool_calls) + 100,
    }
    calls = 0
    try:
        async with monty.AsyncMonty(max_processes=1) as pool:
            async with pool.checkout(limits=limits) as session:
                if resume_from is not None:
                    snapshot = session.load_snapshot(resume_from)
                    if inspect.isawaitable(snapshot):
                        snapshot = await snapshot
                else:
                    snapshot = await session.feed_start(
                        code,
                        print_callback=output,
                        # Names only: calls come back as snapshots, answered below.
                        external_lookup={name: _placeholder for name in functions},
                    )
                while not isinstance(snapshot, monty.MontyComplete):
                    if getattr(snapshot, "is_os_function", False):
                        # Files, the environment and sleeping come back to the
                        # host as OS calls, and are refused. The clock and random
                        # numbers Monty answers itself.
                        snapshot = await snapshot.resume_not_handled()
                        continue
                    name = getattr(snapshot, "function_name", None)
                    if name not in functions:
                        snapshot = await snapshot.resume_auto()
                        continue
                    calls += 1
                    if calls > config.max_tool_calls:
                        raise _TooManyCalls()
                    try:
                        value = await functions[name](
                            dict(snapshot.kwargs or {}), list(snapshot.args or ())
                        )
                    except _PauseHere as pause:
                        # Waiting for a person: keep the paused program.
                        blob = snapshot.dump()
                        if len(blob) > config.max_snapshot_bytes:
                            snapshot = await snapshot.resume(
                                {
                                    "exception": RuntimeError(
                                        "This call needs approval, but the program is too "
                                        "large to pause; call the tool directly instead."
                                    )
                                }
                            )
                            continue
                        raise ProgramPaused(
                            snapshot=blob,
                            call_id=pause.call_id,
                            call_number=pause.call_number,
                            output=output.output,
                        ) from None
                    except Exception as exc:  # the error goes into the program
                        snapshot = await snapshot.resume({"exception": exc})
                        continue
                    snapshot = await snapshot.resume({"return_value": value})
                result = snapshot.output
    except _TooManyCalls:
        return _error(
            f"The program was stopped: it may make at most {config.max_tool_calls} tool calls.",
            output,
            calls - 1,
        )
    except monty.MontyError as exc:
        return _error(_describe(exc), output, calls)
    return {
        "status": "success",
        "data": {"result": _plain(result), "output": output.output, "tool_calls": calls},
    }


_PROCESS_KEY = __import__("os").urandom(32)


def _placeholder(*args: Any, **kwargs: Any) -> None:  # never called: calls are answered manually
    return None


def _describe(exc: Exception) -> str:
    display = getattr(exc, "display", None)
    if callable(display):
        try:
            return display()
        except Exception:
            pass
    return str(exc)


def _error(message: str, output: Any, calls: int) -> dict[str, Any]:
    return {
        "status": "error",
        "message": message,
        "data": {"output": output.output, "tool_calls": calls},
    }


def _plain(value: Any) -> Any:
    """A JSON-safe copy of a program's result."""
    return json.loads(json.dumps(value, default=str))
