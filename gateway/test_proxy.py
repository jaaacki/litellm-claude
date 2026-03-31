import sys
import unittest

sys.argv = [sys.argv[0]]

import proxy


class ProxyThinkingContractTests(unittest.TestCase):
    def test_build_route_state_marks_verified_chatgpt_and_openai_models_for_translation(self):
        entries = [
            {
                "model_name": "gpt-5.4",
                "litellm_params": {"model": "chatgpt/gpt-5.4"},
            },
            {
                "model_name": "MiniMax-M2.7",
                "litellm_params": {
                    "model": "openai/MiniMax-M2.7",
                    "api_base": "https://api.minimax.io/v1",
                },
            },
        ]

        route_state = proxy._build_route_state(entries)

        self.assertIn("gpt-5.4", route_state["translated"])
        self.assertIn("MiniMax-M2.7", route_state["translated"])
        self.assertEqual("chatgpt", route_state["thinking_contracts"]["gpt-5.4"]["route_family"])
        self.assertEqual("openai", route_state["thinking_contracts"]["MiniMax-M2.7"]["route_family"])

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


if __name__ == "__main__":
    unittest.main()
