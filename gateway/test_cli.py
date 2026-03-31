import unittest
from unittest import mock

import cli
from providers.base import Status


class LaunchClaudeModelSelectionTests(unittest.TestCase):
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
            {"alias": "gpt-5", "provider": "openai", "model": "chatgpt/gpt-5"},
            {"alias": "llama3", "provider": "ollama", "model": "ollama/llama3"},
        ]
        ollama_provider = mock.Mock()
        ollama_provider.discover_models.return_value = {}
        openai_provider = mock.Mock()
        openai_provider.supports_thinking = False

        def get_provider_side_effect(name):
            if name == "ollama":
                return ollama_provider
            if name == "openai":
                return openai_provider
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


if __name__ == "__main__":
    unittest.main()
