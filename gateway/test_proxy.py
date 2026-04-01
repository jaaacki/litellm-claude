import sys
import os
import tempfile
import unittest
import types
import json

sys.argv = [sys.argv[0]]

# Stub yaml before any module imports it (proxy imports config which imports yaml)
yaml_stub = types.ModuleType("yaml")
yaml_stub.safe_load = lambda *a, **k: {}
sys.modules["yaml"] = yaml_stub

try:
    from gateway import proxy
except ImportError:
    import proxy


class ProxyErrorMappingTests(unittest.TestCase):
    """Tests for canonical error-mapping infrastructure."""

    def test_map_upstream_status_auth_errors(self):
        for status in (401, 403):
            code, msg, err_type = proxy._map_upstream_status(status)
            self.assertEqual(code, 502)
            self.assertEqual(err_type, "auth_error")

    def test_map_upstream_status_rate_limit(self):
        code, msg, err_type = proxy._map_upstream_status(429)
        self.assertEqual(code, 429)
        self.assertEqual(err_type, "upstream_error")
        self.assertIn("rate limited", msg.lower())

    def test_map_upstream_status_server_error(self):
        for status in (500, 502, 503, 504):
            code, msg, err_type = proxy._map_upstream_status(status)
            self.assertEqual(code, 502)
            self.assertEqual(err_type, "upstream_error")

    def test_map_upstream_status_other_4xx(self):
        code, msg, err_type = proxy._map_upstream_status(400)
        self.assertEqual(code, 502)
        self.assertEqual(err_type, "upstream_error")

    def test_error_response_format(self):
        code, body = proxy._error_response(502, "test error", "upstream_error")
        self.assertEqual(code, 502)
        import json
        parsed = json.loads(body)
        self.assertEqual(parsed["error"]["message"], "test error")
        self.assertEqual(parsed["error"]["type"], "upstream_error")


class ProxyValidationTests(unittest.TestCase):
    """Tests for request validation."""

    def test_validate_messages_rejects_missing_model(self):
        err = proxy._validate_messages({"messages": [{"role": "user", "content": "hi"}]})
        self.assertIsNotNone(err)
        self.assertIn("model", err.lower())

    def test_validate_messages_rejects_non_list_messages(self):
        err = proxy._validate_messages({"model": "gpt-5.4", "messages": "not a list"})
        self.assertIsNotNone(err)

    def test_validate_messages_rejects_empty_messages(self):
        err = proxy._validate_messages({"model": "gpt-5.4", "messages": []})
        self.assertIsNotNone(err)

    def test_validate_messages_accepts_valid_request(self):
        err = proxy._validate_messages({
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
        })
        self.assertIsNone(err)


class ProxyThinkingContractTests(unittest.TestCase):
    def test_build_route_state_handles_empty_entries(self):
        """_build_route_state must not fail on empty input."""
        try:
            from gateway import providers as p_module
        except ImportError:
            import providers as p_module
        orig_all = p_module.all_providers
        p_module.all_providers = lambda: []
        try:
            route_state = proxy._build_route_state([])
            self.assertEqual(route_state["translated"], set())
            self.assertEqual(route_state["all_models"], [])
            self.assertEqual(route_state["native"], {})
            self.assertEqual(route_state["thinking_contracts"], {})
        finally:
            p_module.all_providers = orig_all

    def test_apply_verified_thinking_contract_injects_reasoning_effort(self):
        openai_body = {"model": "gpt-5.4", "messages": []}
        thinking_contract = {
            "strategy": "openai_chat_reasoning_effort",
            "route_family": "chatgpt",
            "provider": "openai",
            "levels": ("low", "medium", "high"),
        }

        proxy._apply_verified_thinking_contract(openai_body, thinking_contract, "high")

        self.assertEqual("high", openai_body["reasoning_effort"])

    def test_require_verified_thinking_contract_rejects_unverified_model(self):
        with self.assertRaisesRegex(ValueError, "Thinking effort is not supported"):
            proxy._require_verified_thinking_contract(
                "llama3",
                "high",
                thinking_contracts={},
            )

    def test_upstream_error_stop_reason_is_distinct(self):
        self.assertEqual(proxy._UPSTREAM_ERROR_STOP, "upstream_error")
        # Must be distinct from normal finish reasons
        self.assertNotEqual(proxy._UPSTREAM_ERROR_STOP, proxy._map_finish_reason("stop"))


class ProxyTranslationEngineTests(unittest.TestCase):
    def test_normalize_translation_engine_defaults_to_v2(self):
        self.assertEqual("v2", proxy._normalize_translation_engine(None))
        self.assertEqual("v2", proxy._normalize_translation_engine("bogus"))

    def test_normalize_translation_engine_accepts_v1_and_v2(self):
        self.assertEqual("v1", proxy._normalize_translation_engine("v1"))
        self.assertEqual("v2", proxy._normalize_translation_engine("v2"))


class ProxySocketClassificationTests(unittest.TestCase):
    def test_classifies_client_disconnect_errors(self):
        self.assertTrue(proxy._is_client_disconnect_error(ConnectionResetError(104, "Connection reset by peer")))
        self.assertTrue(proxy._is_client_disconnect_error(BrokenPipeError(32, "Broken pipe")))

    def test_classifies_timeout_errors(self):
        self.assertTrue(proxy._is_socket_timeout_error(TimeoutError("timed out")))
        self.assertFalse(proxy._is_client_disconnect_error(TimeoutError("timed out")))


class ProxyLegacyTranslationTests(unittest.TestCase):
    def test_openai_to_anthropic_repairs_send_message_missing_summary(self):
        translated = json.loads(
            proxy._openai_to_anthropic(
                json.dumps(
                    {
                        "id": "resp_repair",
                        "model": "demo-model",
                        "choices": [
                            {
                                "finish_reason": "tool_calls",
                                "message": {
                                    "tool_calls": [
                                        {
                                            "id": "call_1",
                                            "function": {
                                                "name": "SendMessage",
                                                "arguments": "{\"to\":\"agent-1\",\"message\":\"{\\\"type\\\":\\\"shutdown_request\\\",\\\"reason\\\":\\\"done\\\"}\"}",
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    }
                ).encode("utf-8")
            ).decode("utf-8")
        )
        self.assertEqual("Shutdown now", translated["content"][0]["input"]["summary"])

    def test_anthropic_to_openai_injects_hidden_feedback_for_tool_validation_errors(self):
        translated = json.loads(
            proxy._anthropic_to_openai(
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
                }
            ).decode("utf-8")
        )
        self.assertEqual("system", translated["messages"][0]["role"])
        self.assertIn("summary is required", translated["messages"][0]["content"])
        self.assertIn("Reissue SendMessage", translated["messages"][0]["content"])

    def test_normalize_declared_anthropic_model_maps_all_claude_tiers_to_selected_alias(self):
        original_path = proxy._MODEL_ALIAS_STATE_PATH
        original_state = proxy._MODEL_ALIAS_STATE
        original_all = proxy._ALL_CONFIGURED_MODELS
        original_native = proxy._NATIVE_ANTHROPIC_MODELS
        try:
            with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
                json.dump(
                    {
                        "selected_model": "glm-5.1",
                        "anthropic_defaults": {
                            "haiku": "glm-5.1",
                            "sonnet": "glm-5.1",
                            "opus": "glm-5.1",
                        },
                    },
                    tmp,
                )
                tmp_path = tmp.name
            proxy._MODEL_ALIAS_STATE_PATH = tmp_path
            proxy._MODEL_ALIAS_STATE = {"mtime_ns": None, "selected_model": "", "anthropic_defaults": {}}
            proxy._ALL_CONFIGURED_MODELS = ["gpt-5.4", "MiniMax-M2.7", "glm-5.1"]
            proxy._NATIVE_ANTHROPIC_MODELS = {}

            self.assertEqual(
                "glm-5.1",
                proxy._normalize_declared_anthropic_model({"model": "claude-opus-4-6"})["model"],
            )
            self.assertEqual(
                "glm-5.1",
                proxy._normalize_declared_anthropic_model({"model": "claude-sonnet-4-6"})["model"],
            )
            self.assertEqual(
                "glm-5.1",
                proxy._normalize_declared_anthropic_model({"model": "claude-haiku-4-5"})["model"],
            )
        finally:
            proxy._MODEL_ALIAS_STATE_PATH = original_path
            proxy._MODEL_ALIAS_STATE = original_state
            proxy._ALL_CONFIGURED_MODELS = original_all
            proxy._NATIVE_ANTHROPIC_MODELS = original_native
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_normalize_declared_anthropic_model_preserves_configured_native_models(self):
        original_all = proxy._ALL_CONFIGURED_MODELS
        original_native = proxy._NATIVE_ANTHROPIC_MODELS
        try:
            proxy._ALL_CONFIGURED_MODELS = ["claude-opus-4-6", "glm-5.1"]
            proxy._NATIVE_ANTHROPIC_MODELS = {"claude-opus-4-6": {"host": "demo.example.com"}}
            normalized = proxy._normalize_declared_anthropic_model({"model": "claude-opus-4-6"})
            self.assertEqual("claude-opus-4-6", normalized["model"])
        finally:
            proxy._ALL_CONFIGURED_MODELS = original_all
            proxy._NATIVE_ANTHROPIC_MODELS = original_native

    def test_openai_to_anthropic_does_not_surface_reasoning_content(self):
        translated = json.loads(
            proxy._openai_to_anthropic(
                json.dumps(
                    {
                        "id": "resp_legacy",
                        "model": "demo-model",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "reasoning_content": "<think>private</think>\nVisible answer",
                                },
                            }
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    }
                ).encode("utf-8")
            ).decode("utf-8")
        )
        self.assertEqual([], translated["content"])
        self.assertEqual("end_turn", translated["stop_reason"])

    def test_anthropic_to_openai_forced_tool_choice_filters_tools_and_uses_required(self):
        translated = json.loads(
            proxy._anthropic_to_openai(
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
                }
            ).decode("utf-8")
        )
        self.assertEqual("required", translated["tool_choice"])
        self.assertEqual(1, len(translated["tools"]))
        self.assertEqual("echo_tool", translated["tools"][0]["function"]["name"])


if __name__ == "__main__":
    unittest.main()
