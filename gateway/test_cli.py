import os
import subprocess
import tempfile
import unittest
from unittest import mock

import cli
import config
from providers.base import Status


class ThinkingContractTests(unittest.TestCase):
    def test_resolve_thinking_contract_for_chatgpt_model(self):
        model = {
            "alias": "gpt-5.4",
            "provider": "openai",
            "model": "chatgpt/gpt-5.4",
            "litellm_params": {"model": "chatgpt/gpt-5.4"},
        }

        contract = config.resolve_thinking_contract(model)

        self.assertIsNotNone(contract)
        self.assertEqual("openai_chat_reasoning_effort", contract["strategy"])
        self.assertEqual("chatgpt", contract["route_family"])
        self.assertEqual(("low", "medium", "high"), contract["levels"])

    def test_resolve_thinking_contract_for_openai_compatible_model(self):
        model = {
            "alias": "MiniMax-M2.7",
            "provider": "minimax",
            "model": "openai/MiniMax-M2.7",
            "litellm_params": {
                "model": "openai/MiniMax-M2.7",
                "api_base": "https://api.minimax.io/v1",
            },
        }

        contract = config.resolve_thinking_contract(model)

        self.assertIsNotNone(contract)
        self.assertEqual("openai_chat_reasoning_effort", contract["strategy"])
        self.assertEqual("openai", contract["route_family"])
        self.assertEqual("minimax", contract["provider"])


class LaunchClaudeModelSelectionTests(unittest.TestCase):
    def test_proclaude_launcher_exists(self):
        self.assertTrue(os.path.exists("/Users/noonoon/Dev/liteLLM/proclaude.sh"))

    def test_proclaude_runs_without_changing_calling_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            zsh_command = (
                'source /Users/noonoon/.zshrc >/dev/null 2>&1; '
                'proclaude --emit-env /tmp/proclaude-test-env >/tmp/proclaude.out 2>/tmp/proclaude.err || true; '
                'pwd'
            )
            result = subprocess.run(
                ["zsh", "-ic", zsh_command],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(os.path.realpath(tmpdir), os.path.realpath(result.stdout.strip()))

    @mock.patch("cli.input", side_effect=["1", "n"])
    @mock.patch("cli.os.execvp", side_effect=SystemExit(0))
    @mock.patch("shutil.which", return_value="/usr/local/bin/claude")
    @mock.patch("cli._eprint")
    @mock.patch("builtins.print")
    @mock.patch("providers.get_provider")
    @mock.patch("config.list_models")
    @mock.patch("config.ensure_master_key", return_value="sk-test")
    @mock.patch("container.status", return_value=(Status.OK, "ok"))
    def test_launch_skips_configured_ollama_model_when_not_available(
        self,
        _container_status,
        _ensure_master_key,
        list_models,
        get_provider,
        _eprint,
        _print,
        _which,
        _execvp,
        _input,
    ):
        list_models.return_value = [
            {
                "alias": "gpt-5",
                "provider": "openai",
                "model": "chatgpt/gpt-5",
                "litellm_params": {"model": "chatgpt/gpt-5"},
            },
            {
                "alias": "llama3",
                "provider": "ollama",
                "model": "ollama/llama3",
                "litellm_params": {"model": "ollama/llama3"},
            },
        ]
        ollama_provider = mock.Mock()
        ollama_provider.discover_models.return_value = {}

        def get_provider_side_effect(name):
            if name == "ollama":
                return ollama_provider
            return None

        get_provider.side_effect = get_provider_side_effect

        cli._init_registry()

        with self.assertRaises(SystemExit):
            cli.cmd_launch_claude()

        output = "\n".join(" ".join(str(arg) for arg in call.args) for call in _print.call_args_list)
        choices = "\n".join(" ".join(str(arg) for arg in call.args) for call in _eprint.call_args_list)

        self.assertNotIn("llama3 (ollama)", output + choices)
        _execvp.assert_called_once()
        args = _execvp.call_args.args[1]
        self.assertIn("--dangerously-skip-permissions", args)
        self.assertEqual("gpt-5", cli.os.environ["ANTHROPIC_MODEL"])

    @mock.patch("cli.os.execvp", side_effect=SystemExit(0))
    @mock.patch("shutil.which", return_value="/usr/local/bin/claude")
    @mock.patch("builtins.print")
    @mock.patch("providers.get_provider")
    @mock.patch("config.list_models")
    @mock.patch("config.ensure_master_key", return_value="sk-test")
    @mock.patch("container.status", return_value=(Status.OK, "ok"))
    def test_launch_hard_fails_when_thinking_is_requested_without_verified_contract(
        self,
        _container_status,
        _ensure_master_key,
        list_models,
        get_provider,
        _print,
        _which,
        _execvp,
    ):
        list_models.return_value = [
            {
                "alias": "llama3",
                "provider": "ollama",
                "model": "ollama/llama3",
                "litellm_params": {"model": "ollama/llama3"},
            },
        ]
        ollama_provider = mock.Mock()
        ollama_provider.discover_models.return_value = {"llama3": "ollama/llama3"}
        get_provider.return_value = ollama_provider

        with self.assertRaises(SystemExit):
            cli.cmd_launch_claude(thinking="high", telegram=False)

        output = "\n".join(" ".join(str(arg) for arg in call.args) for call in _print.call_args_list)
        self.assertIn("Thinking effort is not supported", output)
        _execvp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
