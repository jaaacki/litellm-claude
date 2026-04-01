import unittest

try:
    from gateway.proxy_v2.errors import ProxyError
    from gateway.proxy_v2.request_translate import translate_anthropic_request
except ImportError:
    from proxy_v2.errors import ProxyError
    from proxy_v2.request_translate import translate_anthropic_request


class ProxyV2RequestTranslateTests(unittest.TestCase):
    def test_translate_anthropic_request_injects_hidden_feedback_for_tool_validation_errors(self):
        translated = translate_anthropic_request(
            {
                "model": "glm-5.1",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_send",
                                "is_error": True,
                                "content": "Error: summary is required when message is a string",
                            }
                        ],
                    }
                ],
            },
            thinking_effort=None,
            thinking_contract=None,
        )
        self.assertEqual("system", translated["messages"][0]["role"])
        self.assertIn("summary is required", translated["messages"][0]["content"])
        self.assertIn("Reissue SendMessage", translated["messages"][0]["content"])

    def test_translate_anthropic_request_preserves_required_tool_fields(self):
        translated = translate_anthropic_request(
            {
                "model": "glm-5.1",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "name": "SendMessage",
                        "description": "Send a teammate message",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "to": {"type": "string"},
                                "message": {"type": "string"},
                                "summary": {"type": "string"},
                            },
                            "required": ["to", "message", "summary"],
                        },
                    }
                ],
            },
            thinking_effort=None,
            thinking_contract=None,
        )
        params = translated["tools"][0]["function"]["parameters"]
        self.assertEqual(["to", "message", "summary"], params["required"])

    def test_translate_anthropic_request_forced_tool_choice_filters_tools_and_uses_required(self):
        translated = translate_anthropic_request(
            {
                "model": "demo-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "tools": [
                    {
                        "name": "echo_tool",
                        "description": "Echo text",
                        "input_schema": {"type": "object"},
                    },
                    {
                        "name": "other_tool",
                        "description": "Other text",
                        "input_schema": {"type": "object"},
                    },
                ],
                "tool_choice": {"type": "tool", "name": "echo_tool"},
            },
            thinking_effort=None,
            thinking_contract=None,
        )
        self.assertEqual("required", translated["tool_choice"])
        self.assertEqual(1, len(translated["tools"]))
        self.assertEqual("echo_tool", translated["tools"][0]["function"]["name"])

    def test_translate_anthropic_request_rejects_unknown_forced_tool_name(self):
        with self.assertRaises(ProxyError):
            translate_anthropic_request(
                {
                    "model": "demo-model",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "tools": [
                        {
                            "name": "echo_tool",
                            "description": "Echo text",
                            "input_schema": {"type": "object"},
                        },
                    ],
                    "tool_choice": {"type": "tool", "name": "missing_tool"},
                },
                thinking_effort=None,
                thinking_contract=None,
            )

    def test_translate_anthropic_request_rejects_tool_choice_without_tools(self):
        with self.assertRaises(ProxyError):
            translate_anthropic_request(
                {
                    "model": "demo-model",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "tool_choice": {"type": "any"},
                },
                thinking_effort=None,
                thinking_contract=None,
            )

    def test_translate_anthropic_request_supports_verified_thinking_contract(self):
        translated = translate_anthropic_request(
            {
                "model": "demo-model",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            thinking_effort="high",
            thinking_contract={
                "provider": "openai",
                "strategy": "openai_chat_reasoning_effort",
                "levels": ("low", "medium", "high"),
            },
        )
        self.assertEqual("high", translated["reasoning_effort"])

    def test_translate_anthropic_request_rejects_invalid_payload_with_proxy_error(self):
        with self.assertRaises(ProxyError):
            translate_anthropic_request({"model": "demo-model"}, thinking_effort=None, thinking_contract=None)

    def test_translate_anthropic_request_rejects_unsupported_thinking_strategy(self):
        with self.assertRaises(ProxyError):
            translate_anthropic_request(
                {
                    "model": "demo-model",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
                thinking_effort="high",
                thinking_contract={
                    "provider": "demo",
                    "strategy": "unsupported_strategy",
                    "levels": ("high",),
                },
            )


if __name__ == "__main__":
    unittest.main()
