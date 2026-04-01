import logging

import requests

try:
    from .. import config
    from .base import BaseProvider, Status
except ImportError:
    import config
    from providers.base import BaseProvider, Status

log = logging.getLogger("litellm-cli.openai")


class OpenAIProvider(BaseProvider):
    name = "openai"
    display_name = "OpenAI"
    supports_thinking = True
    auth_types = ["browser_oauth", "api_key"]
    env_vars = {
        "browser_oauth": [],
        "api_key": ["OPENAI_API_KEY"],
    }
    # ChatGPT subscription models (browser OAuth, chatgpt/ prefix)
    models = {
        "gpt-5.4": "chatgpt/gpt-5.4",
        "gpt-5.4-pro": "chatgpt/gpt-5.4-pro",
        "gpt-5.3-codex": "chatgpt/gpt-5.3-codex",
        "gpt-5.3-codex-spark": "chatgpt/gpt-5.3-codex-spark",
        "gpt-5.3-instant": "chatgpt/gpt-5.3-instant",
        "gpt-5.3-chat-latest": "chatgpt/gpt-5.3-chat-latest",
    }

    model_limits = {
        "gpt-5.4":             {"context": 1000000, "max_output": 128000},
        "gpt-5.4-pro":         {"context": 1000000, "max_output": 128000},
        "gpt-5.3-codex":       {"context": 1000000, "max_output": 32768},
        "gpt-5.3-codex-spark": {"context": 1000000, "max_output": 32768},
        "gpt-5.3-instant":     {"context": 1000000, "max_output": 32768},
        "gpt-5.3-chat-latest": {"context": 1000000, "max_output": 32768},
        "o3":                  {"context": 200000,  "max_output": 100000},
        "o3-pro":              {"context": 200000,  "max_output": 100000},
        "o4-mini":             {"context": 200000,  "max_output": 100000},
    }

    # OpenAI API key models (openai/ prefix)
    _api_key_models = {
        "gpt-5.4": "openai/gpt-5.4",
        "gpt-5.4-pro": "openai/gpt-5.4-pro",
        "gpt-5.3-instant": "openai/gpt-5.3-instant",
        "o3": "openai/o3",
        "o3-pro": "openai/o3-pro",
        "o4-mini": "openai/o4-mini",
    }
    _thinking_levels_by_alias = {
        # User-provided product requirement: gpt-5.4 exposes an extra-high reasoning tier.
        "gpt-5.4": ("low", "medium", "high", "xhigh"),
    }

    def get_model_string(self, alias, auth_type=None):
        if auth_type == "api_key":
            return self._api_key_models.get(alias)
        return self.models.get(alias)

    def get_models_for_auth(self, auth_type):
        """Return the model catalog for a given auth type."""
        if auth_type == "api_key":
            return self._api_key_models
        return self.models

    def resolve_thinking_contract(self, alias, litellm_model, litellm_params=None):
        levels = self._thinking_levels_by_alias.get(alias, self.thinking_levels)
        if litellm_model.startswith("chatgpt/"):
            return self._openai_reasoning_contract("chatgpt", levels=levels)
        if litellm_model.startswith("openai/"):
            return self._openai_reasoning_contract("openai", levels=levels)
        return None

    def validate(self):
        # Check API key first
        api_key = config.get_env("OPENAI_API_KEY")
        if api_key:
            log.debug("OPENAI_API_KEY found, validating via API")
            return self._validate_api_key(api_key)
        # Check browser OAuth via container logs
        log.debug("No API key, checking browser OAuth via container logs")
        return self._validate_browser()

    def _validate_api_key(self, api_key):
        try:
            resp = requests.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
        except requests.RequestException as e:
            return Status.UNREACHABLE, f"Cannot reach OpenAI API: {e}"
        status, msg = self._classify_response(resp)
        if status == Status.OK:
            return status, "Authenticated with OpenAI API key"
        if status == Status.INVALID and resp.status_code == 403:
            return status, "OPENAI_API_KEY lacks required permissions (403 Forbidden)"
        return status, msg

    def _validate_browser(self):
        """Check browser OAuth auth without host-side log access."""
        import container
        cs, _ = container.status()
        if cs != Status.OK:
            return Status.UNREACHABLE, (
                "LiteLLM backend is not yet reachable, so browser OAuth cannot be checked from inside "
                "the gateway. Use './proclaude.sh launch claude' or inspect './proclaude.sh logs litellm' on the host."
            )

        # Check the proxy's model list endpoint (no billing). Host-side device-code
        # discovery belongs in gateway/host_runtime.py, not the provider layer.
        chatgpt_aliases = {m["alias"] for m in config.list_models() if m["model"].startswith("chatgpt/")}
        if chatgpt_aliases:
            found, err = self._check_proxy_models(chatgpt_aliases)
            if found:
                log.debug("Browser OAuth may be active — chatgpt models served by proxy")
                return Status.UNVERIFIED, "Browser OAuth may be active (models configured in proxy, but cannot independently verify upstream auth)"
            if err:
                log.debug("Proxy model check error: %s", err)
                return Status.UNREACHABLE, f"Cannot verify browser auth (proxy check failed: {err})"

        log.debug("No auth evidence found")
        return Status.NOT_CONFIGURED, "Not authenticated — no browser OAuth evidence found. Run './proclaude.sh login openai' to authenticate."

    def _check_proxy_models(self, chatgpt_aliases):
        """Check if chatgpt models are served by the proxy.

        Returns (found: bool, error: str|None).
        found=True means models detected. error is set on transport/parse failures.
        found=False with error=None means "checked successfully, models not present."
        """
        from container import PROXY_PORT
        master_key = config.get_env("LITELLM_MASTER_KEY")
        if not master_key:
            return False, "LITELLM_MASTER_KEY not set. Run './proclaude.sh start' first."
        try:
            resp = requests.get(
                f"http://localhost:{PROXY_PORT}/v1/models",
                headers={"Authorization": f"Bearer {master_key}"},
                timeout=10,
            )
            if resp.status_code != 200:
                log.debug("Proxy /v1/models returned %d", resp.status_code)
                return False, f"proxy returned HTTP {resp.status_code}"
            ct = resp.headers.get("Content-Type", "")
            if "application/json" not in ct:
                log.debug("Proxy /v1/models returned unexpected content-type: %s", ct)
                return False, f"unexpected content-type: {ct}"
            data = resp.json()
            if not isinstance(data, dict):
                log.debug("Proxy /v1/models returned non-dict response")
                return False, "non-dict response"
            items = data.get("data")
            if not isinstance(items, list):
                log.debug("Proxy /v1/models response has no valid 'data' list")
                return False, "no valid 'data' list in response"
            served_ids = {m.get("id", "") for m in items if isinstance(m, dict)}
            if chatgpt_aliases & served_ids:
                return True, None
            log.debug("Proxy /v1/models: no chatgpt models in served set")
            return False, None
        except requests.RequestException as e:
            log.debug("Proxy /v1/models request failed: %s", e)
            return False, f"request failed: {e}"
        except ValueError as e:
            log.debug("Proxy /v1/models JSON parse failed: %s", e)
            return False, f"invalid JSON: {e}"

    def login(self, auth_type="browser_oauth", credentials=None):
        if auth_type == "api_key":
            return self._login_api_key(credentials)
        return self._login_browser()

    # Credentials prompt shown by CLI layer, not here
    login_prompts = {
        "api_key": {
            "instructions": "Enter your OpenAI API key.\n  Get one at: https://platform.openai.com/api-keys",
            "fields": [("OPENAI_API_KEY", "OPENAI_API_KEY: ")],
        }
    }

    def _login_api_key(self, credentials=None):
        """Authenticate with API key. Caller must prompt via login_prompts."""
        if credentials is None:
            return Status.INVALID, "No credentials provided. Use login_prompts to collect them."
        key = credentials.get("OPENAI_API_KEY", "")
        if not key:
            return Status.INVALID, "No key entered."
        config.set_env("OPENAI_API_KEY", key)
        return self.validate()

    def _login_browser(self):
        """Browser OAuth login must run on the host via gateway/host_runtime.py."""
        return Status.INVALID, "OpenAI browser OAuth must be started from './proclaude.sh provider login openai' on the host."
