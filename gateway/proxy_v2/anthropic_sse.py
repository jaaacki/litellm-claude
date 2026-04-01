"""Anthropic SSE serialization for V2 semantic events."""

import json
import logging

try:
    from gateway.proxy_v2.errors import ProxyError
    from gateway.proxy_v2.events import (
        Abort,
        MessageStart,
        MessageStop,
        TextDelta,
        ToolCallArgsDelta,
        ToolCallComplete,
        ToolCallStart,
        UsageDelta,
    )
    from gateway.proxy_v2.tool_repair import repair_tool_call
except ImportError:
    from proxy_v2.errors import ProxyError
    from proxy_v2.events import (
        Abort,
        MessageStart,
        MessageStop,
        TextDelta,
        ToolCallArgsDelta,
        ToolCallComplete,
        ToolCallStart,
        UsageDelta,
    )
    from proxy_v2.tool_repair import repair_tool_call

log = logging.getLogger("litellm-proxy.v2.anthropic_sse")


class AnthropicSSEWriter:
    def __init__(self):
        self._started = False
        self._terminated = False
        self._text_index = None
        self._next_index = 0
        self._tool_indexes = {}
        self._closed_tool_indexes = set()
        self._tool_buffers = {}
        self._message_id = ""
        self._model = ""
        self._input_tokens = 0
        self._output_tokens = 0

    def write(self, events):
        if self._terminated:
            if events:
                log.error("Invalid semantic event after terminal output")
                raise ProxyError(
                    502,
                    "Invalid semantic event after terminal output",
                    "upstream_error",
                    code="invalid_semantic_event_order",
                )
            return []
        chunks = []
        for event in events:
            if isinstance(event, MessageStart):
                if self._started:
                    log.error("Duplicate MessageStart event")
                    raise ProxyError(
                        502,
                        "Duplicate MessageStart event",
                        "upstream_error",
                        code="duplicate_message_start",
                    )
                self._started = True
                self._message_id = event.message_id
                self._model = event.model
                self._input_tokens = event.input_tokens
                chunks.append(_encode_sse("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": self._message_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": self._model,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": self._input_tokens, "output_tokens": 0},
                    },
                }))
                continue
            if isinstance(event, Abort):
                chunks.extend(self._close_text_block())
                chunks.extend(self._close_open_tool_blocks())
                chunks.append(_encode_sse("error", {
                    "type": "error",
                    "error": {
                        "type": "proxy_error",
                        "message": event.message or event.reason,
                    },
                }))
                self._terminated = True
                continue
            if not self._started:
                log.error("MessageStart missing before %s", type(event).__name__)
                raise ProxyError(
                    502,
                    "MessageStart is required before semantic event %s" % type(event).__name__,
                    "upstream_error",
                    code="missing_message_start",
                )
            if isinstance(event, UsageDelta):
                self._input_tokens = event.input_tokens
                self._output_tokens = event.output_tokens
            elif isinstance(event, TextDelta):
                if self._text_index is None:
                    self._text_index = self._next_index
                    self._next_index += 1
                    chunks.append(_encode_sse("content_block_start", {
                        "type": "content_block_start",
                        "index": self._text_index,
                        "content_block": {"type": "text", "text": ""},
                    }))
                chunks.append(_encode_sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": self._text_index,
                    "delta": {"type": "text_delta", "text": event.text},
                }))
            elif isinstance(event, ToolCallStart):
                block_index = self._tool_indexes.get(event.index)
                if block_index is None:
                    block_index = self._next_index
                    self._next_index += 1
                    self._tool_indexes[event.index] = block_index
                self._tool_buffers[event.index] = {
                    "tool_call_id": event.tool_call_id,
                    "name": event.name,
                    "raw_arguments": "",
                }
            elif isinstance(event, ToolCallArgsDelta):
                buffer = self._require_tool_buffer(event.index)
                buffer["raw_arguments"] += event.partial_json
            elif isinstance(event, ToolCallComplete):
                block_index = self._require_tool_block_index(event.index)
                buffer = self._require_tool_buffer(event.index)
                repaired_input, repaired_raw, _ = repair_tool_call(
                    buffer["name"],
                    event.input,
                    buffer["raw_arguments"],
                )
                raw_arguments = repaired_raw
                if raw_arguments is None:
                    raw_arguments = json.dumps(repaired_input, separators=(",", ":"))
                chunks.extend(self._close_text_block())
                chunks.append(_encode_sse("content_block_start", {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": buffer["tool_call_id"],
                        "name": buffer["name"],
                        "input": {},
                    },
                }))
                chunks.append(_encode_sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "input_json_delta", "partial_json": raw_arguments},
                }))
                if block_index not in self._closed_tool_indexes:
                    chunks.append(_encode_sse("content_block_stop", {
                        "type": "content_block_stop",
                        "index": block_index,
                    }))
                    self._closed_tool_indexes.add(block_index)
            elif isinstance(event, MessageStop):
                chunks.extend(self._close_text_block())
                chunks.extend(self._close_open_tool_blocks())
                chunks.append(_encode_sse("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": event.stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": max(event.output_tokens, self._output_tokens)},
                }))
                chunks.append(_encode_sse("message_stop", {"type": "message_stop"}))
                self._terminated = True
            else:
                log.error("Unsupported semantic event %s", type(event).__name__)
                raise ProxyError(
                    502,
                    "Unsupported semantic event %s" % type(event).__name__,
                    "upstream_error",
                    code="unsupported_semantic_event",
                )
        return chunks

    def finish(self):
        return []

    def _close_text_block(self):
        if self._text_index is None:
            return []
        index = self._text_index
        self._text_index = None
        return [_encode_sse("content_block_stop", {"type": "content_block_stop", "index": index})]

    def _close_open_tool_blocks(self):
        chunks = []
        for block_index in sorted(self._tool_indexes.values()):
            if block_index in self._closed_tool_indexes:
                continue
            chunks.append(_encode_sse("content_block_stop", {
                "type": "content_block_stop",
                "index": block_index,
            }))
            self._closed_tool_indexes.add(block_index)
        return chunks

    def _require_tool_block_index(self, tool_call_index):
        block_index = self._tool_indexes.get(tool_call_index)
        if block_index is None:
            log.error("Tool event ordering invalid for index %s", tool_call_index)
            raise ProxyError(
                502,
                "Invalid tool event ordering for index %s" % tool_call_index,
                "upstream_error",
                code="invalid_tool_event_order",
            )
        return block_index

    def _require_tool_buffer(self, tool_call_index):
        buffer = self._tool_buffers.get(tool_call_index)
        if buffer is None:
            log.error("Tool buffer missing for index %s", tool_call_index)
            raise ProxyError(
                502,
                "Invalid tool event ordering for index %s" % tool_call_index,
                "upstream_error",
                code="invalid_tool_event_order",
            )
        return buffer


def _encode_sse(event_name, payload):
    return ("event: %s\ndata: %s\n\n" % (event_name, json.dumps(payload))).encode("utf-8")
