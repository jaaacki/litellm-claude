import logging

try:
    from .. import config
    from .base import BaseProvider, Status
except ImportError:
    import config
    from providers.base import BaseProvider, Status

log = logging.getLogger("litellm-cli.zhipu")


class ZhipuProvider(BaseProvider):
    name = "zhipu"
    display_name = "Z.AI (Zhipu)"
    supports_thinking = True
    auth_types = ["api_key"]
    env_vars = {"api_key": ["ZAI_API_KEY"]}
    # OpenAI-compatible — use openai/ prefix so LiteLLM translates
    models = {
        "glm-5.1": "openai/glm-5.1",
        "glm-5": "openai/glm-5",
    }
    model_limits = {
        "glm-5.1": {"context": 204800, "max_output": 131072},
        "glm-5":   {"context": 204800, "max_output": 131072},
    }

    API_BASE = "https://api.z.ai/api/coding/paas/v4"

    def get_extra_params(self):
        """Extra litellm_params for Zhipu models."""
        return {"api_base": self.API_BASE, "api_key": "os.environ/ZAI_API_KEY"}

    def resolve_thinking_contract(self, alias, litellm_model, litellm_params=None):
        if litellm_model.startswith("openai/"):
            return self._openai_reasoning_contract("openai")
        return None

    def validate(self):
        probe_model = next(iter(self.models))
        return self._validate_openai_compatible_api_key(
            env_var="ZAI_API_KEY",
            api_base=self.API_BASE,
            model=probe_model,
            provider_label="Z.AI",
            success_message="Authenticated with Z.AI",
            invalid_message="Invalid ZAI_API_KEY",
        )

    login_prompts = {
        "api_key": {
            "instructions": "Enter your Z.AI API key.\n  Get one at: https://z.ai/manage-apikey/apikey-list",
            "fields": [("ZAI_API_KEY", "ZAI_API_KEY: ")],
        }
    }

    def login(self, auth_type="api_key", credentials=None):
        if credentials is None:
            return Status.INVALID, "No credentials provided. Use login_prompts to collect them."
        key = credentials.get("ZAI_API_KEY", "")
        if not key:
            return Status.INVALID, "No key entered."
        config.set_env("ZAI_API_KEY", key)
        return self.validate()
