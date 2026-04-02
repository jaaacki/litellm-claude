"""Semantic state machine for V2 translated streams."""

from dataclasses import dataclass
import json
import logging

try:
    from gateway.proxy_v2.contracts import map_openai_finish_reason
    from gateway.proxy_v2.events import (
        Abort,
        IncompleteMessageError,
        MessageStart,
        MessageStop,
        OpenAIChunk,
        TextDelta,
        ToolCallArgsDelta,
        ToolCallComplete,
        ToolCallStart,
        ToolUseArgsDelta,
        ToolUseStart,
        TransportAbortError,
        UsageDelta,
    )
except ImportError:
    from proxy_v2.contracts import map_openai_finish_reason
    from proxy_v2.events import (
        Abort,
        IncompleteMessageError,
        MessageStart,
        MessageStop,
        OpenAIChunk,
        TextDelta,
        ToolCallArgsDelta,
        ToolCallComplete,
        ToolCallStart,
        ToolUseArgsDelta,
        ToolUseStart,
        TransportAbortError,
        UsageDelta,
    )

log = logging.getLogger("litellm-proxy.v2.state")


@dataclass
class _ToolCallBuffer:
    index: int
    tool_call_id: str = ""
    name: str = ""
    arguments_buffer: str = ""
    started: bool = False
    completed: bool = False


class TranslationState:
    def __init__(self):
        self._started = False
        self._stopped = False
        self._aborted = False
        self._message_id = ""
        self._model = ""
        self._input_tokens = 0
        self._output_tokens = 0
        self._visible_text = []
        self._final_stop_reason = None
        self._tool_calls = {}
        self._noop_chunk_count = 0
        self._logged_noop_warning = False
        self._visible_non_whitespace_text = False
        self._emitted_semantic_content = False
        self._pending_whitespace_text = ""
        # Think-tag state machine: suppress <think>...</think> blocks
        self._in_think = False
        self._think_buf = ""
        self._past_think = False

    def apply_chunk(self, chunk):
        if self._stopped or self._aborted:
            return []

        if chunk.error is not None:
            return self.abort("upstream_error", message=_error_message(chunk.error))

        events = []
        self._prime_message_metadata(chunk)
        usage_changed = self._update_usage(chunk.usage or {})
        text_events = self._apply_text(chunk.delta or {})

        tool_events, tool_abort = self._apply_tool_calls(chunk.delta or {})
        if text_events or tool_events:
            self._emitted_semantic_content = True
        should_start = bool(text_events or tool_events)
        if should_start and not self._started:
            events.extend(self._start_message())
        if self._started and usage_changed:
            events.append(UsageDelta(input_tokens=self._input_tokens, output_tokens=self._output_tokens))
        events.extend(text_events)
        events.extend(tool_events)
        if tool_abort is not None:
            return events + self.abort(tool_abort)

        if self._is_noop_chunk(chunk):
            self._noop_chunk_count += 1
            if not self._logged_noop_warning:
                self._logged_noop_warning = True
                log.warning(
                    "Observed upstream no-op translated chunk; upstream event metadata may have been dropped "
                    "(chunk_id=%s model=%s)",
                    chunk.chunk_id or "unknown",
                    chunk.model or "unknown",
                )

        if chunk.finish_reason:
            if self._has_incomplete_tool_calls():
                log.error("finish_reason=%s arrived before tool arguments completed", chunk.finish_reason)
                return events + self.abort("incomplete_tool_args")
            self._stopped = True
            self._final_stop_reason = map_openai_finish_reason(chunk.finish_reason)
            # Always emit MessageStart + MessageStop so the client sees a
            # complete message lifecycle, even if think-tag filtering ate
            # all visible content.
            if not self._started:
                events.extend(self._start_message())
            events.append(MessageStop(
                stop_reason=self._final_stop_reason,
                output_tokens=self._output_tokens,
            ))
        return events

    def finish_eof(self):
        if self._stopped or self._aborted:
            return []
        if self._noop_chunk_count:
            return self.abort(
                "upstream_eof_no_finish",
                message=(
                    "Upstream stream ended without a finish reason after empty translated chunks; "
                    "upstream event metadata may have been dropped"
                ),
            )
        return self.abort("upstream_eof_no_finish")

    def abort(self, reason, *, message=None):
        if self._stopped or self._aborted:
            return []
        self._aborted = True
        return [Abort(reason=reason, message=message)]

    def _start_message(self):
        if self._started:
            return []
        self._started = True
        return [MessageStart(
            message_id=self._message_id,
            model=self._model,
            input_tokens=self._input_tokens,
        )]

    def _prime_message_metadata(self, chunk):
        self._message_id = chunk.chunk_id or "msg_translated"
        self._model = chunk.model or ""
        if not self._started:
            self._input_tokens = int((chunk.usage or {}).get("prompt_tokens", self._input_tokens) or 0)

    def _update_usage(self, usage):
        if not usage:
            return False
        input_tokens = int(usage.get("prompt_tokens", self._input_tokens) or 0)
        output_tokens = int(usage.get("completion_tokens", self._output_tokens) or 0)
        changed = not (input_tokens == self._input_tokens and output_tokens == self._output_tokens)
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        return changed

    def _apply_text(self, delta):
        raw = ""
        if "content" in delta:
            raw = delta.get("content") or ""
        if not raw:
            return []
        text = self._filter_think_tags(raw)
        if not text:
            return []
        if not text.strip():
            self._pending_whitespace_text += text
            return []

        if self._visible_non_whitespace_text:
            emit_text = _normalize_pending_whitespace(self._pending_whitespace_text) + text
        else:
            emit_text = text.lstrip()
        self._pending_whitespace_text = ""
        if not emit_text:
            return []
        self._visible_non_whitespace_text = True
        self._visible_text.append(emit_text)
        return [TextDelta(text=emit_text)]

    def _filter_think_tags(self, text):
        """Strip <think>...</think> blocks incrementally across chunks."""
        if self._past_think:
            return text
        self._think_buf += text
        result = []
        while self._think_buf:
            if self._in_think:
                end = self._think_buf.find("</think>")
                if end >= 0:
                    self._think_buf = self._think_buf[end + 8:]
                    self._in_think = False
                    self._past_think = True
                    remaining = self._think_buf
                    self._think_buf = ""
                    if remaining:
                        result.append(remaining)
                    return "".join(result)
                else:
                    self._think_buf = ""
                    return "".join(result)
            else:
                start = self._think_buf.find("<think>")
                if start >= 0:
                    before = self._think_buf[:start]
                    if before.strip():
                        result.append(before)
                    self._think_buf = self._think_buf[start + 7:]
                    self._in_think = True
                elif "<" in self._think_buf and len(self._think_buf) < 7:
                    return "".join(result)
                else:
                    self._past_think = True
                    result.append(self._think_buf)
                    self._think_buf = ""
                    return "".join(result)
        return "".join(result)

    def _apply_tool_calls(self, delta):
        events = []
        for tool_call in delta.get("tool_calls", []) or []:
            if not isinstance(tool_call, dict):
                return events, "malformed_tool_call_delta"
            index = int(tool_call.get("index", 0) or 0)
            function = tool_call.get("function") or {}
            if not isinstance(function, dict):
                return events, "malformed_tool_call_delta"

            buffer = self._tool_calls.get(index)
            if buffer is None:
                buffer = _ToolCallBuffer(index=index)
                self._tool_calls[index] = buffer

            if tool_call.get("id"):
                buffer.tool_call_id = tool_call["id"]
            if function.get("name"):
                buffer.name = function["name"]
            if not buffer.started and (buffer.tool_call_id or buffer.name):
                buffer.started = True
                events.append(ToolCallStart(index=index, tool_call_id=buffer.tool_call_id, name=buffer.name))
                if buffer.arguments_buffer:
                    events.append(ToolCallArgsDelta(index=index, partial_json=buffer.arguments_buffer))

            arguments_delta = function.get("arguments")
            if arguments_delta:
                buffer.arguments_buffer += arguments_delta
                if buffer.started:
                    events.append(ToolCallArgsDelta(index=index, partial_json=arguments_delta))
                parsed_input, complete = _parse_tool_arguments(buffer.arguments_buffer)
                if complete:
                    buffer.completed = True
                    events.append(ToolCallComplete(index=index, input=parsed_input))
        return events, None

    def _has_incomplete_tool_calls(self):
        return any(
            (buf.started or buf.arguments_buffer) and not buf.completed
            for buf in self._tool_calls.values()
        )

    def _is_noop_chunk(self, chunk):
        if chunk.finish_reason or chunk.error:
            return False
        if chunk.usage:
            return False
        return not (chunk.delta or {})

    def to_anthropic_message(self):
        if self._aborted:
            raise IncompleteMessageError("stream aborted before an explicit message stop")
        if not self._stopped:
            raise IncompleteMessageError("stream ended without an explicit message stop")

        content = []
        if self._visible_text:
            content.append({"type": "text", "text": "".join(self._visible_text)})
        for index in sorted(self._tool_calls):
            tool_state = self._tool_calls[index]
            if not tool_state.completed:
                continue
            content.append({
                "type": "tool_use",
                "id": tool_state.tool_call_id,
                "name": tool_state.name,
                "input": json.loads(tool_state.arguments_buffer),
            })
        return {
            "id": self._message_id,
            "type": "message",
            "role": "assistant",
            "content": content,
            "model": self._model,
            "stop_reason": self._final_stop_reason,
            "stop_sequence": None,
        }


def _parse_tool_arguments(arguments_buffer):
    try:
        parsed = json.loads(arguments_buffer)
    except (TypeError, ValueError):
        return None, False
    if not isinstance(parsed, dict):
        return None, False
    return parsed, True


def _error_message(error):
    if isinstance(error, dict):
        return error.get("message", "Unknown upstream error")
    return str(error)


def _normalize_pending_whitespace(text):
    if not text:
        return ""
    return text


class OpenAIStreamState:
    """Compatibility semantic accumulator for the earlier V2 event tests."""

    def __init__(self, message_id, model):
        self.message_id = message_id
        self.model = model
        self.visible_text = ""
        self._translation_state = TranslationState()
        self._stop_reason = None
        self._aborted = False

    def consume_chunk(self, chunk):
        if self._aborted:
            raise TransportAbortError("stream already aborted")
        normalized_events = self._translation_state.apply_chunk(_chunk_from_payload(
            chunk,
            message_id=self.message_id,
            model=self.model,
        ))
        return self._convert_events(normalized_events)

    def note_transport_done(self):
        if not self._stop_reason:
            raise IncompleteMessageError("stream ended without an explicit message stop")

    def abort(self, reason):
        self._aborted = True
        self._translation_state.abort(reason)
        raise TransportAbortError(reason)

    def to_anthropic_message(self):
        message = self._translation_state.to_anthropic_message()
        message["id"] = self.message_id
        message["model"] = self.model
        return message

    def _convert_events(self, normalized_events):
        emitted = []
        for event in normalized_events:
            if isinstance(event, TextDelta):
                self.visible_text += event.text
                emitted.append(event)
            elif isinstance(event, ToolCallStart):
                emitted.append(ToolUseStart(index=event.index, tool_id=event.tool_call_id, name=event.name))
            elif isinstance(event, ToolCallArgsDelta):
                emitted.append(ToolUseArgsDelta(index=event.index, partial_json=event.partial_json))
            elif isinstance(event, MessageStop):
                self._stop_reason = event.stop_reason
                emitted.append(MessageStop(event.stop_reason))
            elif isinstance(event, Abort):
                self._aborted = True
                raise TransportAbortError(event.reason)
        return emitted


def _chunk_from_payload(payload, *, message_id, model):
    choice = ((payload.get("choices") or [{}])[0] or {})
    return OpenAIChunk(
        chunk_id=payload.get("id", message_id),
        model=payload.get("model", model),
        usage=payload.get("usage") or {},
        delta=choice.get("delta") or {},
        finish_reason=choice.get("finish_reason"),
        error=payload.get("error"),
    )
