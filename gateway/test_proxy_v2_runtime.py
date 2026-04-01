import unittest

try:
    from gateway.proxy_v2.anthropic_sse import AnthropicSSEWriter
    from gateway.proxy_v2.events import TextDelta, ToolCallArgsDelta, decode_openai_chunk
    from gateway.proxy_v2.errors import ProxyError
    from gateway.proxy_v2.runtime import translate_buffered_response, translate_stream
except ImportError:
    from proxy_v2.anthropic_sse import AnthropicSSEWriter
    from proxy_v2.events import TextDelta, ToolCallArgsDelta, decode_openai_chunk
    from proxy_v2.errors import ProxyError
    from proxy_v2.runtime import translate_buffered_response, translate_stream


class ProxyV2RuntimeTests(unittest.TestCase):
    def test_translate_buffered_response_translates_json_bytes(self):
        output = translate_buffered_response(
            b'{"id":"resp_1","model":"demo","choices":[{"finish_reason":"stop","message":{"content":"Hello"}}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'
        )
        self.assertIn(b'"stop_reason": "end_turn"', output)

    def test_translate_stream_emits_anthropic_events_for_text_stream(self):
        upstream_chunks = [
            b'data: {"id":"chatcmpl_1","model":"demo","choices":[{"delta":{"content":"Hello"},"finish_reason":null}],"usage":{"prompt_tokens":1,"completion_tokens":0}}\n\n',
            b'data: {"id":"chatcmpl_1","model":"demo","choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n',
            b"data: [DONE]\n\n",
        ]
        output = b"".join(translate_stream(upstream_chunks, abort_signal=lambda: False, logger=None))
        self.assertIn(b"event: message_start", output)
        self.assertIn(b"event: content_block_delta", output)
        self.assertIn(b"event: message_stop", output)

    def test_translate_stream_ignores_leading_whitespace_only_text_deltas(self):
        upstream_chunks = [
            b'data: {"id":"chatcmpl_ws","model":"demo","choices":[{"delta":{"content":"\\n\\n  "},"finish_reason":null}],"usage":{"prompt_tokens":1,"completion_tokens":0}}\n\n',
            b'data: {"id":"chatcmpl_ws","model":"demo","choices":[{"delta":{"content":"Hello"},"finish_reason":null}],"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n',
            b'data: {"id":"chatcmpl_ws","model":"demo","choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2}}\n\n',
            b"data: [DONE]\n\n",
        ]
        output = b"".join(translate_stream(upstream_chunks, abort_signal=lambda: False, logger=None))
        self.assertEqual(1, output.count(b"event: content_block_start"))
        self.assertIn(b'"text": "Hello"', output)

    def test_translate_stream_treats_done_without_finish_as_abort(self):
        upstream_chunks = [
            b'data: {"id":"chatcmpl_1","model":"demo","choices":[{"delta":{"content":"Hello"},"finish_reason":null}],"usage":{"prompt_tokens":1,"completion_tokens":0}}\n\n',
            b"data: [DONE]\n\n",
        ]
        output = b"".join(translate_stream(upstream_chunks, abort_signal=lambda: False, logger=None))
        self.assertIn(b"event: error", output)
        self.assertNotIn(b"event: message_stop", output)

    def test_translate_stream_surfaces_noop_chunk_gap_as_abort_message(self):
        upstream_chunks = [
            b'data: {"id":"chatcmpl_noop","model":"demo","choices":[{"delta":{},"finish_reason":null}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        output = b"".join(translate_stream(upstream_chunks, abort_signal=lambda: False, logger=None))
        self.assertIn(b"event: error", output)
        self.assertIn(b"dropped", output)

    def test_translate_stream_handles_tool_args_before_tool_metadata(self):
        upstream_chunks = [
            b'data: {"id":"chatcmpl_tool","model":"demo","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"city\\""}}]},"finish_reason":null}],"usage":{"prompt_tokens":1,"completion_tokens":0}}\n\n',
            b'data: {"id":"chatcmpl_tool","model":"demo","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"lookup_weather","arguments":": \\"Singapore\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n',
            b"data: [DONE]\n\n",
        ]
        output = b"".join(translate_stream(upstream_chunks, abort_signal=lambda: False, logger=None))
        self.assertIn(b"lookup_weather", output)
        self.assertIn(b"event: message_stop", output)

    def test_translate_stream_batches_message_start_with_first_tool_event(self):
        upstream_chunks = [
            b'data: {"id":"chatcmpl_tool_batch","model":"demo","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"lookup_weather","arguments":"{\\"city\\": \\"Singapore\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n',
            b"data: [DONE]\n\n",
        ]
        payloads = list(translate_stream(upstream_chunks, abort_signal=lambda: False, logger=None))
        self.assertGreaterEqual(len(payloads), 1)
        self.assertIn(b"event: message_start", payloads[0])
        self.assertIn(b"event: content_block_start", payloads[0])

    def test_translate_stream_repairs_send_message_missing_summary(self):
        upstream_chunks = [
            b'data: {"id":"chatcmpl_send","model":"demo","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"SendMessage","arguments":"{\\"to\\":\\"agent-1\\",\\"message\\":\\"{\\\\\\"type\\\\\\":\\\\\\"shutdown_request\\\\\\",\\\\\\"reason\\\\\\":\\\\\\"done\\\\\\"}\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n',
            b"data: [DONE]\n\n",
        ]
        output = b"".join(translate_stream(upstream_chunks, abort_signal=lambda: False, logger=None))
        self.assertIn(b"Shutdown now", output)

    def test_decode_openai_chunk_rejects_missing_choices(self):
        with self.assertRaises(ProxyError):
            decode_openai_chunk('{"id":"chunk_1","model":"demo"}')

    def test_decode_openai_chunk_accepts_usage_only_frame(self):
        chunk = decode_openai_chunk(
            '{"id":"chunk_1","model":"demo","choices":[],"usage":{"prompt_tokens":7,"completion_tokens":0}}'
        )
        self.assertEqual("chunk_1", chunk.chunk_id)
        self.assertEqual("demo", chunk.model)
        self.assertEqual({"prompt_tokens": 7, "completion_tokens": 0}, chunk.usage)
        self.assertEqual({}, chunk.delta)
        self.assertIsNone(chunk.finish_reason)

    def test_decode_openai_chunk_rejects_non_object_delta(self):
        with self.assertRaises(ProxyError):
            decode_openai_chunk(
                '{"id":"chunk_1","model":"demo","choices":[{"delta":"oops","finish_reason":null}]}'
            )

    def test_writer_rejects_text_before_message_start(self):
        writer = AnthropicSSEWriter()
        with self.assertRaises(ProxyError):
            writer.write([TextDelta(text="Hello")])


if __name__ == "__main__":
    unittest.main()
