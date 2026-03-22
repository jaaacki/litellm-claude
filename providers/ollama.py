import json
import logging
import os
import shutil
import subprocess
from urllib.parse import urlparse
import requests
from providers.base import BaseProvider, AuthStatus

log = logging.getLogger("litellm-cli.ollama")


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
        except (ValueError, AttributeError) as e:
            log.warning("Failed to parse OLLAMA_HOST '%s', using default: %s", env_host, e)
            return self.DEFAULT_DOCKER_HOST

    def validate(self):
        host = self.OLLAMA_HOST
        try:
            resp = requests.get(f"{host}/api/tags", timeout=3)
            if resp.status_code != 200:
                return AuthStatus.UNREACHABLE, f"Ollama returned status {resp.status_code}"
            ct = resp.headers.get("Content-Type", "")
            if "json" not in ct:
                return AuthStatus.UNREACHABLE, f"Ollama returned unexpected Content-Type: {ct}"
            try:
                resp.json()
            except ValueError:
                return AuthStatus.UNREACHABLE, "Ollama returned invalid JSON"
            return AuthStatus.OK, f"Ollama is reachable at {host}"
        except requests.RequestException as e:
            log.warning("Ollama validate failed: %s", e)
            return AuthStatus.UNREACHABLE, f"Cannot reach Ollama at {host}: {e}"

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
                try:
                    result = subprocess.run([ollama_bin, "login"], timeout=120)
                except (OSError, subprocess.TimeoutExpired) as e:
                    return False, f"ollama login failed: {e}"
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
                log.warning(
                    "Ollama at %s returned HTTP %d — cannot discover models",
                    host, resp.status_code,
                )
                return {}
            try:
                data = resp.json()
            except ValueError:
                log.warning("Ollama at %s returned invalid JSON", host)
                return {}
            models_list = data.get("models", [])
            if not isinstance(models_list, list):
                log.warning(
                    "Ollama at %s returned non-list 'models' field: %s",
                    host, type(models_list).__name__,
                )
                return {}
            models = {}
            for m in models_list:
                if not isinstance(m, dict):
                    continue
                name = m.get("name", "")
                if not isinstance(name, str) or not name:
                    continue
                alias = name.replace(":latest", "")
                models[alias] = f"ollama/{name}"
            return models
        except requests.RequestException as e:
            log.warning("Could not reach Ollama at %s: %s", host, e)
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

            # Set socket idle timeout to detect stalled transfers
            try:
                resp.raw._fp.fp.raw._sock.settimeout(60)
            except (AttributeError, TypeError):
                pass

            last_status = ""
            for line in resp.iter_lines():
                if line:
                    try:
                        data = json.loads(line)
                    except ValueError:
                        log.debug("Skipping malformed NDJSON line: %s", line[:100])
                        continue
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
        except requests.RequestException as e:
            return False, f"Pull failed: {e}"

    def get_model_string(self, alias, auth_type=None):
        return f"ollama/{alias}"

    def get_extra_params(self):
        """Return extra litellm_params for Ollama models."""
        return {"api_base": self.DOCKER_HOST}
