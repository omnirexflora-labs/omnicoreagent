"""Fake Anthropic, Gemini, and OpenRouter servers for continuation tests.

Each speaks its provider's wire format at the path LiteLLM calls, streaming and
not. The first turn asks for one tool call and returns continuation data the
provider requires back on the next request:

- Anthropic ``/v1/messages``: a signed ``thinking`` block and a
  ``redacted_thinking`` block before the ``tool_use``.
- Gemini ``/models/<model>:generateContent``: a ``thoughtSignature`` on the
  ``functionCall`` part.
- OpenRouter ``/api/v1/chat/completions``: ``reasoning_details`` with a
  signature on the assistant message.

Like the real APIs, the follow-up request is rejected with the provider's own
error when that data is missing or changed. Reach them through LiteLLM by
setting ``ANTHROPIC_API_BASE``, ``GEMINI_API_BASE``, and
``OPENROUTER_API_BASE`` (``<base>/api/v1``).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

THINKING = "The user wants order A-17; I should call lookup_order."
SIGNATURE = "EqQBCkYIBxABGAIiQL6yTb0Xk2Vy8cT4mAnthropicSignature+/=="
REDACTED = "EmwKAhgBEgy3va3pzix/LafPsn4aDFIT2Xlxh0L5L8rLVyIw9redacted+/=="
GEMINI_SIGNATURE = "CiQB0e2Kb7geminiThoughtSignature+/abc=="
OPENROUTER_SIGNATURE = "ErUBCkYIBhABGAIiQOpenRouterClaudeSignature+/=="
TOOL = "lookup_order"
ARGS = {"order_id": "A-17"}
ANSWER = "Order A-17 has shipped."


class ContinuationProviders:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.rejections: list[str] = []
        providers = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):  # noqa: N802 - http.server API
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                providers.requests.append({"path": self.path, "body": body})
                if self.path.startswith("/v1/messages"):
                    status, payload, events = providers._anthropic(body)
                elif self.path.startswith("/models/"):
                    status, payload, events = providers._gemini(body)
                elif self.path.startswith("/api/v1/chat/completions"):
                    status, payload, events = providers._openrouter(body)
                else:
                    status, payload, events = 404, {"error": "unknown path"}, None
                streaming = body.get("stream") is True or "alt=sse" in self.path
                if status == 200 and streaming and events is not None:
                    self._sse(events)
                else:
                    self._json(status, payload)

            def _json(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _sse(self, events):
                data = "".join(
                    (f"event: {name}\n" if name else "")
                    + f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n"
                    for name, event in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _reject(self, status: int, payload: dict[str, Any], message: str):
        self.rejections.append(message)
        return status, payload, None

    # --- Anthropic ----------------------------------------------------------

    def _anthropic(self, body):
        messages = body["messages"]
        followup = any(
            isinstance(m.get("content"), list)
            and any(block.get("type") == "tool_result" for block in m["content"])
            for m in messages
        )
        if followup:
            assistant = next(m for m in messages if m["role"] == "assistant")
            blocks = assistant["content"] if isinstance(assistant["content"], list) else []
            kinds = [block.get("type") for block in blocks]
            expected = [
                {"type": "thinking", "thinking": THINKING, "signature": SIGNATURE},
                {"type": "redacted_thinking", "data": REDACTED},
            ]
            received = [
                {k: block.get(k) for k in expected[i] if k in block}
                for i, block in enumerate(blocks[:2])
            ]
            if received != expected:
                message = (
                    "messages.1.content.0.type: Expected `thinking` or `redacted_thinking`, "
                    f"but found `{kinds[0] if kinds else 'nothing'}`. When `thinking` is enabled, "
                    "a final `assistant` message must start with a thinking block."
                )
                return self._reject(
                    400,
                    {"type": "error", "error": {"type": "invalid_request_error", "message": message}},
                    f"anthropic: {message}",
                )
            content = [{"type": "text", "text": ANSWER}]
            stop = "end_turn"
        else:
            content = [
                {"type": "thinking", "thinking": THINKING, "signature": SIGNATURE},
                {"type": "redacted_thinking", "data": REDACTED},
                {"type": "tool_use", "id": "toolu_01A", "name": TOOL, "input": ARGS},
            ]
            stop = "tool_use"
        message = {
            "id": "msg_01" + ("B" if followup else "A"),
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {"input_tokens": 50, "output_tokens": 20},
        }
        return 200, message, self._anthropic_events(message)

    @staticmethod
    def _anthropic_events(message):
        start = {**message, "content": [], "stop_reason": None}
        events = [("message_start", {"type": "message_start", "message": start})]
        for index, block in enumerate(message["content"]):
            if block["type"] == "thinking":
                opening = {"type": "thinking", "thinking": "", "signature": ""}
                deltas = [
                    {"type": "thinking_delta", "thinking": block["thinking"][:20]},
                    {"type": "thinking_delta", "thinking": block["thinking"][20:]},
                    {"type": "signature_delta", "signature": block["signature"]},
                ]
            elif block["type"] == "tool_use":
                opening = {**block, "input": {}}
                deltas = [{"type": "input_json_delta", "partial_json": json.dumps(block["input"])}]
            elif block["type"] == "text":
                opening = {"type": "text", "text": ""}
                deltas = [{"type": "text_delta", "text": block["text"]}]
            else:
                opening, deltas = block, []
            events.append(
                ("content_block_start", {"type": "content_block_start", "index": index, "content_block": opening})
            )
            for delta in deltas:
                events.append(
                    ("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
                )
            events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
        events.append(
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                    "usage": {"output_tokens": 20},
                },
            )
        )
        events.append(("message_stop", {"type": "message_stop"}))
        return events

    # --- Gemini -------------------------------------------------------------

    def _gemini(self, body):
        contents = body["contents"]
        # The API accepts both spellings; LiteLLM sends snake_case.
        followup = any(
            "functionResponse" in part or "function_response" in part
            for content in contents
            for part in content.get("parts", [])
        )
        if followup:
            calls = [
                part
                for content in contents
                if content.get("role") == "model"
                for part in content.get("parts", [])
                if "functionCall" in part or "function_call" in part
            ]
            if not calls or any(part.get("thoughtSignature") != GEMINI_SIGNATURE for part in calls):
                message = (
                    "Function call is missing a thought_signature in functionCall parts. "
                    "This is required for tools to work correctly."
                )
                return self._reject(
                    400,
                    {"error": {"code": 400, "message": message, "status": "INVALID_ARGUMENT"}},
                    f"gemini: {message}",
                )
            parts = [{"text": ANSWER}]
        else:
            parts = [
                {"functionCall": {"name": TOOL, "args": ARGS}, "thoughtSignature": GEMINI_SIGNATURE}
            ]
        response = {
            "candidates": [
                {"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}
            ],
            "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 10, "totalTokenCount": 50},
            "modelVersion": "gemini-3-pro-preview",
            "responseId": "gemini-" + ("b" if followup else "a"),
        }
        return 200, response, [(None, response)]

    # --- OpenRouter ---------------------------------------------------------

    def _openrouter(self, body):
        messages = body["messages"]
        followup = any(m.get("role") == "tool" for m in messages)
        details = [
            {
                "type": "reasoning.text",
                "text": THINKING,
                "signature": OPENROUTER_SIGNATURE,
                "format": "anthropic-claude-v1",
                "index": 0,
            }
        ]
        if followup:
            assistant = next(m for m in messages if m["role"] == "assistant")
            if assistant.get("reasoning_details") != details:
                message = (
                    "Provider returned error: messages.1.content.0.type: Expected `thinking` or "
                    "`redacted_thinking`, but found `tool_use`."
                )
                return self._reject(
                    400, {"error": {"code": 400, "message": message}}, f"openrouter: {message}"
                )
            message_out = {"role": "assistant", "content": ANSWER}
            finish = "stop"
        else:
            message_out = {
                "role": "assistant",
                "content": None,
                "reasoning": THINKING,
                "reasoning_details": details,
                "tool_calls": [
                    {
                        "id": "toolu_01A",
                        "type": "function",
                        "function": {"name": TOOL, "arguments": json.dumps(ARGS)},
                    }
                ],
            }
            finish = "tool_calls"
        response = {
            "id": "gen-" + ("b" if followup else "a"),
            "object": "chat.completion",
            "created": 1,
            "model": body["model"],
            "choices": [{"index": 0, "message": message_out, "finish_reason": finish}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
        }
        delta = dict(message_out)
        if delta.get("tool_calls"):
            delta["tool_calls"] = [{**call, "index": i} for i, call in enumerate(delta["tool_calls"])]
        chunk = {
            "id": response["id"],
            "object": "chat.completion.chunk",
            "created": 1,
            "model": body["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        final = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": response["usage"]}
        return 200, response, [(None, chunk), (None, final), (None, "[DONE]")]
