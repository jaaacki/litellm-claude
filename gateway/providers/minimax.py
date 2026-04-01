import logging
try:
    from .base import BaseProvider, Status
except ImportError:
    from providers.base import BaseProvider, Status

log = logging.getLogger("litellm-cli.minimax")


class MiniMaxProvider(BaseProvider):
    name = "minimax"
    display_name = "MiniMax"
    supports_thinking = True
    auth_types = ["api_key"]
    env_vars = {"api_key": ["MINIMAX_API_KEY"]}
    # Use openai/ prefix so LiteLLM translates via OpenAI-compatible endpoint
    # (minimax/ prefix tries the Anthropic-native path which requires a paid plan)
    models = {
        "MiniMax-M2.7": "openai/MiniMax-M2.7",
        "MiniMax-M2.5": "openai/MiniMax-M2.5",
        "MiniMax-Text-01": "openai/MiniMax-Text-01",
    }
    model_limits = {
        "MiniMax-M2.7":    {"context": 1000000, "max_output": 131072},
        "MiniMax-M2.5":    {"context": 1000000, "max_output": 131072},
        "MiniMax-Text-01": {"context": 1000000, "max_output": 131072},
    }

    def get_extra_params(self):
        """Extra litellm_params for MiniMax models."""
        return {"api_base": f"{self.API_BASE}/v1", "api_key": "os.environ/MINIMAX_API_KEY"}

    def resolve_thinking_contract(self, alias, litellm_model, litellm_params=None):
        if litellm_model.startswith("openai/"):
            return self._openai_reasoning_contract("openai")
        return None

    # LiteLLM appends /v1/ internally, so no trailing /v1 here
    API_BASE = "https://api.minimax.io"

    def validate(self):
        probe_model = next(iter(self.models))
        return self._validate_openai_compatible_api_key(
            env_var="MINIMAX_API_KEY",
            api_base=f"{self.API_BASE}/v1",
            model=probe_model,
            provider_label="MiniMax",
            success_message="Authenticated with MiniMax",
            invalid_message="Invalid MINIMAX_API_KEY",
        )

    login_prompts = {
        "api_key": {
            "instructions": "Enter your MiniMax API key.\n  Get one at: https://platform.minimaxi.com/",
            "fields": [("MINIMAX_API_KEY", "MINIMAX_API_KEY: ")],
        }
    }

    def login(self, auth_type="api_key", credentials=None):
        """Authenticate with provided credentials. Caller must prompt via login_prompts."""
        if credentials is None:
            return Status.INVALID, "No credentials provided. Use login_prompts to collect them."
        key = credentials.get("MINIMAX_API_KEY", "")
        if not key:
            return Status.INVALID, "No key entered."
        config.set_env("MINIMAX_API_KEY", key)
        return self.validate()
