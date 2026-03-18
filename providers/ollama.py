import json
import logging
import os
import shutil
import subprocess
from urllib.parse import urlparse
import requests
from providers.base import BaseProvider, AuthStatus

logger = logging.getLogger(__name__)


class OllamaProvider(BaseProvider):
    name = "ollama"
    display_name = "Ollama"
    auth_types = []
    env_vars = {}
    models = {}  # Dynamic — discovered at runtime

    DEFAULT_HOST = "http://localhost:11434"
    DEFAULT_DOCKER_HOST = "http://host.docker.internal:11434"

    @property
    def OLLAMA_HOST(self):
        """Host URL used for local API calls (respects OLLAMA_HOST env var)."""
        return os.environ.get("OLLAMA_HOST", self.DEFAULT_HOST)

    @property
    def DOCKER_HOST(self):
        """Host URL used inside Docker containers (respects OLLAMA_HOST env var).

        If OLLAMA_HOST points to localhost/127.0.0.1, replace with
        host.docker.internal but keep the port.  If it points elsewhere,
        use it as-is (likely a remote Ollama instance).
        """
        env_host = os.environ.get("OLLAMA_HOST")
        if not env_host:
            return self.DEFAULT_DOCKER_HOST

        try:
            parsed = urlparse(env_host)
            hostname = parsed.hostname or ""
            if hostname in ("localhost", "127.0.0.1", "::1"):
                port = parsed.port or 11434
                scheme = parsed.scheme or "http"
                return f"{scheme}://host.docker.internal:{port}"
            # Non-localhost — use as-is (remote Ollama)
            return env_host
        except Exception:
            return self.DEFAULT_DOCKER_HOST

    def validate(self):
        host = self.OLLAMA_HOST
        try:
            resp = requests.get(f"{host}/api/tags", timeout=3)
            if resp.status_code == 200:
                return AuthStatus.OK, f"Ollama is reachable at {host}"
            return AuthStatus.UNREACHABLE, f"Ollama returned status {resp.status_code}"
        except requests.ConnectionError:
            return AuthStatus.UNREACHABLE, f"Ollama is not running at {host}"
        except requests.Timeout:
            return AuthStatus.UNREACHABLE, "Ollama connection timed out"

    def login(self, auth_type=None):
        status, msg = self.validate()
        if status != AuthStatus.OK:
            return False, msg

        print(f"  ✓ {msg}")

        # Offer ollama login for cloud model access
        ollama_bin = shutil.which("ollama")
        if not ollama_bin:
            print("  ⚠ ollama CLI not found — install it to login for cloud models")
        else:
            choice = input("\n  Login to ollama.com for cloud models? [y/N]: ").strip()
            if choice.lower() == "y":
                print()
                result = subprocess.run([ollama_bin, "login"])
                if result.returncode != 0:
                    return False, "ollama login failed"
                print("  ✓ Logged in to ollama.com")

        # Show available models
        models = self.discover_models()
        if models:
            print(f"\n  Available models ({len(models)}):\n")
            for alias in models:
                print(f"    • {alias}")
        else:
            print("\n  No models found.")

        # Offer to pull
        pull = input("\n  Pull a model? Enter name (or Enter to skip): ").strip()
        if pull:
            print()
            ok, pull_msg = self.pull_model(pull)
            if ok:
                print(f"  ✓ {pull_msg}")
            else:
                print(f"  ✗ {pull_msg}")

        return True, "Ollama ready"

    def discover_models(self):
        """Fetch available models from Ollama. Returns dict of alias -> litellm model string."""
        host = self.OLLAMA_HOST
        try:
            resp = requests.get(f"{host}/api/tags", timeout=5)
            if resp.status_code != 200:
                logger.warning(
                    "Ollama at %s returned HTTP %d — cannot discover models",
                    host, resp.status_code,
                )
                return {}
            data = resp.json()
            models = {}
            for m in data.get("models", []):
                name = m.get("name", "")
                if name:
                    alias = name.replace(":latest", "")
                    models[alias] = f"ollama/{name}"
            return models
        except requests.ConnectionError:
            logger.warning(
                "Could not connect to Ollama at %s — is it running?", host
            )
            return {}
        except requests.Timeout:
            logger.warning(
                "Connection to Ollama at %s timed out", host
            )
            return {}

    def pull_model(self, model_name):
        """Pull a model via Ollama REST API. Returns (success, message)."""
        try:
            resp = requests.post(
                f"{self.OLLAMA_HOST}/api/pull",
                json={"name": model_name},
                stream=True,
                timeout=600,
            )
            if resp.status_code != 200:
                return False, f"Pull failed with status {resp.status_code}"

            last_status = ""
            for line in resp.iter_lines():
                if line:
                    data = json.loads(line)
                    status = data.get("status", "")
                    if "completed" in data and "total" in data:
                        total = data["total"]
                        pct = int(data["completed"] / total * 100) if total > 0 else 0
                        print(f"\r  {status}: {pct}%    ", end="", flush=True)
                    elif status != last_status:
                        print(f"\r  {status}              ", end="", flush=True)
                    last_status = status
            print()
            return True, f"Pulled {model_name}"
        except requests.ConnectionError:
            return False, "Ollama is not running — cannot pull"
        except requests.Timeout:
            return False, "Pull timed out"

    def get_model_string(self, alias, auth_type=None):
        return f"ollama/{alias}"

    def get_extra_params(self):
        """Return extra litellm_params for Ollama models."""
        return {"api_base": self.DOCKER_HOST}
