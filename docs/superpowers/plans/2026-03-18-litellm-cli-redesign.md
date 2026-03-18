# LiteLLM CLI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bash-only `litellm.sh` with a Python CLI that provides interactive wizards for adding/removing models, provider authentication with live validation, and clear user feedback.

**Architecture:** Thin bash wrapper (`litellm.sh`) manages a `.venv/` and forwards all args to `cli.py`. Python modules handle config management (`config.py`), Docker lifecycle (`container.py`), and a provider registry (`providers/`) where each LLM provider is a self-contained class. No external frameworks — just stdlib + PyYAML + requests.

**Tech Stack:** Python 3, PyYAML, requests, Docker/docker-compose, bash (wrapper only)

**Spec:** `docs/superpowers/specs/2026-03-18-litellm-cli-redesign-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `.gitignore` | Create | Ignore `.venv/`, `*.bak`, `__pycache__/`, `.env` |
| `requirements.txt` | Create | Python dependencies (pyyaml, requests) |
| `litellm.sh` | Rewrite | Thin bash wrapper: venv setup, forward to cli.py |
| `config.py` | Create | Read/write `litellm_config.yaml` and `.env` safely |
| `container.py` | Create | Container lifecycle (up/down/restart/status/logs) via subprocess |
| `providers/base.py` | Create | BaseProvider ABC, AuthStatus enum |
| `providers/ollama.py` | Create | Ollama provider: no auth, dynamic model discovery |
| `providers/alibaba.py` | Create | DashScope provider: API key auth |
| `providers/openai.py` | Create | OpenAI provider: browser OAuth + API key auth |
| `providers/__init__.py` | Create | Provider registry: lookup by name, list all |
| `cli.py` | Create | Main entry point: argument routing, help, wizard flows |

**Notes:**
- `container.py` renamed to `container.py` to avoid shadowing the `docker` pip package
- `providers/__init__.py` is created last since it imports the concrete providers
- Project is not a git repo — Task 1 initializes git

---

### Task 1: Project setup — git, .gitignore, requirements.txt, litellm.sh wrapper

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Rewrite: `litellm.sh`

- [ ] **Step 1: Initialize git repo**

```bash
cd /Users/noonoon/Dev/liteLLM && git init
```

- [ ] **Step 2: Create .gitignore**

```
.venv/
__pycache__/
*.bak
.env
```

- [ ] **Step 3: Create requirements.txt**

```
pyyaml
requests
```

- [ ] **Step 4: Rewrite litellm.sh as thin wrapper**

```bash
#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV="$DIR/.venv"

# Ensure venv exists
if [ ! -d "$VENV" ]; then
    echo "Setting up Python environment..."
    python3 -m venv "$VENV" || { echo "Error: Python 3 is required. Install it and try again."; exit 1; }
    "$VENV/bin/pip" install -q -r "$DIR/requirements.txt" || { echo "Error: Failed to install dependencies."; exit 1; }
fi

# Ensure deps are installed (fast check: import yaml)
"$VENV/bin/python" -c "import yaml, requests" 2>/dev/null || {
    echo "Installing dependencies..."
    "$VENV/bin/pip" install -q -r "$DIR/requirements.txt" || { echo "Error: Failed to install dependencies."; exit 1; }
}

exec "$VENV/bin/python" "$DIR/cli.py" "$@"
```

- [ ] **Step 5: Verify litellm.sh runs and shows an error (cli.py doesn't exist yet)**

Run: `cd /Users/noonoon/Dev/liteLLM && ./litellm.sh`
Expected: Python error about cli.py not found (proves the wrapper works)

- [ ] **Step 6: Commit**

```bash
git add .gitignore requirements.txt litellm.sh
git commit -m "feat: project setup — git init, venv wrapper, dependencies"
```

---

### Task 2: config.py — YAML and .env management

**Files:**
- Create: `config.py`

- [ ] **Step 1: Create config.py with YAML functions**

```python
import os
import shutil
import yaml

DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(DIR, "litellm_config.yaml")
CONFIG_BACKUP = CONFIG_PATH + ".bak"
ENV_PATH = os.path.join(DIR, ".env")
ENV_BACKUP = ENV_PATH + ".bak"
ENV_EXAMPLE = os.path.join(DIR, ".env.example")

# --- YAML helpers ---

def _load_yaml():
    """Load litellm_config.yaml, return full dict."""
    if not os.path.exists(CONFIG_PATH):
        return {"model_list": [], "general_settings": {}}
    with open(CONFIG_PATH, "r") as f:
        data = yaml.safe_load(f) or {}
    if "model_list" not in data:
        data["model_list"] = []
    return data


def _save_yaml(data):
    """Backup then write litellm_config.yaml. Preserves all top-level keys."""
    if os.path.exists(CONFIG_PATH):
        shutil.copy2(CONFIG_PATH, CONFIG_BACKUP)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def list_models():
    """Return list of dicts: {alias, model, provider_name}."""
    data = _load_yaml()
    results = []
    for entry in data.get("model_list", []):
        alias = entry.get("model_name", "")
        params = entry.get("litellm_params", {})
        model = params.get("model", "")
        # Derive provider from model prefix (e.g. "chatgpt/gpt-5" -> "openai")
        provider = _provider_from_model(model)
        results.append({"alias": alias, "model": model, "provider": provider})
    return results


def _provider_from_model(model_str):
    """Map litellm model prefix to provider name."""
    prefix = model_str.split("/")[0] if "/" in model_str else ""
    mapping = {
        "chatgpt": "openai",
        "openai": "openai",
        "dashscope": "alibaba",
        "ollama": "ollama",
    }
    return mapping.get(prefix, prefix)


def add_model(alias, litellm_model, extra_params=None):
    """Add a model to config. Returns (success, message)."""
    data = _load_yaml()
    # Check for duplicate alias
    for entry in data["model_list"]:
        if entry.get("model_name") == alias:
            return False, f"Model alias '{alias}' already exists."
    new_entry = {
        "model_name": alias,
        "litellm_params": {"model": litellm_model},
    }
    if extra_params:
        new_entry["litellm_params"].update(extra_params)
    data["model_list"].append(new_entry)
    _save_yaml(data)
    return True, f"Added '{alias}' -> {litellm_model}"


def remove_model(alias):
    """Remove a model by alias. Returns (success, message, provider_name)."""
    data = _load_yaml()
    original_len = len(data["model_list"])
    removed_provider = None
    for entry in data["model_list"]:
        if entry.get("model_name") == alias:
            model_str = entry.get("litellm_params", {}).get("model", "")
            removed_provider = _provider_from_model(model_str)
    data["model_list"] = [
        e for e in data["model_list"] if e.get("model_name") != alias
    ]
    if len(data["model_list"]) == original_len:
        return False, f"Model '{alias}' not found.", None
    _save_yaml(data)
    return True, f"Removed '{alias}'", removed_provider


def provider_has_models(provider_name):
    """Check if any remaining models use this provider."""
    for m in list_models():
        if m["provider"] == provider_name:
            return True
    return False


# --- .env helpers ---

def _ensure_env():
    """Ensure .env exists."""
    if not os.path.exists(ENV_PATH):
        if os.path.exists(ENV_EXAMPLE):
            shutil.copy2(ENV_EXAMPLE, ENV_PATH)
        else:
            open(ENV_PATH, "w").close()


def _read_env_lines():
    """Read .env as raw lines."""
    _ensure_env()
    with open(ENV_PATH, "r") as f:
        return f.readlines()


def _write_env_lines(lines):
    """Backup then write .env lines."""
    if os.path.exists(ENV_PATH):
        shutil.copy2(ENV_PATH, ENV_BACKUP)
    with open(ENV_PATH, "w") as f:
        f.writelines(lines)


def get_env(key):
    """Get value of an env var from .env. Returns None if not set or commented."""
    for line in _read_env_lines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        k, _, v = stripped.partition("=")
        if k.strip() == key:
            return v.strip()
    return None


def set_env(key, value):
    """Set an env var in .env. Updates existing or appends."""
    lines = _read_env_lines()
    found = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped:
            k, _, _ = stripped.partition("=")
            if k.strip() == key:
                new_lines.append(f"{key}={value}\n")
                found = True
                continue
        new_lines.append(line)
    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={value}\n")
    _write_env_lines(new_lines)


def remove_env(key):
    """Comment out an env var in .env."""
    lines = _read_env_lines()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped:
            k, _, _ = stripped.partition("=")
            if k.strip() == key:
                new_lines.append(f"# REMOVED: {stripped}\n")
                continue
        new_lines.append(line)
    _write_env_lines(new_lines)
```

- [ ] **Step 2: Smoke test config.py in Python REPL**

Run: `cd /Users/noonoon/Dev/liteLLM && .venv/bin/python -c "import config; print(config.list_models())"`
Expected: List of 3 current models (gpt-5, qwen-max, llama3)

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add config.py for YAML and .env management"
```

---

### Task 3: container.py — Container lifecycle

**Files:**
- Create: `container.py`

- [ ] **Step 1: Create container.py**

```python
import os
import subprocess
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))


def _compose_cmd():
    """Return the docker compose command as a list. Tries 'docker compose' (v2) first."""
    try:
        result = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True
        )
        if result.returncode == 0:
            return ["docker", "compose"]
    except FileNotFoundError:
        pass
    # Fall back to docker-compose v1
    return ["docker-compose"]


def _run(args, capture=False, stream=False):
    """Run a docker compose command from the project directory."""
    cmd = _compose_cmd() + args
    try:
        if stream:
            proc = subprocess.Popen(cmd, cwd=DIR)
            proc.wait()
            return proc.returncode == 0, ""
        result = subprocess.run(
            cmd, cwd=DIR, capture_output=capture, text=True
        )
        if capture:
            return result.returncode == 0, result.stdout
        return result.returncode == 0, ""
    except FileNotFoundError:
        print("Error: docker compose is required. Install Docker Desktop or docker-compose.")
        sys.exit(1)


def _docker_running():
    """Check if Docker daemon is running."""
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _check_docker():
    """Exit with message if Docker isn't available."""
    if not _docker_running():
        print("Error: Docker is not running. Start Docker and try again.")
        sys.exit(1)


def up():
    _check_docker()
    ok, _ = _run(["up", "-d"])
    if ok:
        print("Service started on http://localhost:2555")
    return ok


def down():
    _check_docker()
    ok, _ = _run(["down"])
    return ok


def restart():
    _check_docker()
    ok, _ = _run(["restart"])
    return ok


def status():
    """Return (is_running: bool, output: str)."""
    _check_docker()
    ok, output = _run(["ps"], capture=True)
    is_running = "Up" in output or "running" in output.lower()
    return is_running, output


def logs(follow=True):
    _check_docker()
    args = ["logs"]
    if follow:
        args.append("-f")
    _run(args, stream=True)


def get_logs_since(timestamp):
    """Get container logs since a timestamp (RFC3339 format). Returns log text."""
    _check_docker()
    result = subprocess.run(
        ["docker", "logs", "litellm-proxy", "--since", timestamp],
        capture_output=True, text=True, cwd=DIR,
    )
    return result.stdout + result.stderr


def wait_healthy(timeout=30):
    """Poll until container is up or timeout. Returns True if healthy."""
    for _ in range(timeout):
        running, _ = status()
        if running:
            return True
        time.sleep(1)
    return False
```

- [ ] **Step 2: Smoke test container.py**

Run: `cd /Users/noonoon/Dev/liteLLM && .venv/bin/python -c "import docker; running, out = docker.status(); print(f'Running: {running}')"`
Expected: Shows whether container is running (True/False)

- [ ] **Step 3: Commit**

```bash
git add container.py
git commit -m "feat: add container.py for container lifecycle management"
```

---

### Task 4: Provider base class

**Files:**
- Create: `providers/base.py`

- [ ] **Step 1: Create providers directory**

```bash
mkdir -p /Users/noonoon/Dev/liteLLM/providers
touch /Users/noonoon/Dev/liteLLM/providers/__init__.py
```

- [ ] **Step 2: Create providers/base.py**

```python
from abc import ABC, abstractmethod
from enum import Enum


class AuthStatus(Enum):
    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    INVALID = "invalid"
    UNREACHABLE = "unreachable"


class BaseProvider(ABC):
    name: str = ""
    display_name: str = ""
    auth_types: list = []
    env_vars: dict = {}  # auth_type -> list of env var names
    models: dict = {}    # alias -> litellm model string

    @abstractmethod
    def validate(self) -> tuple:
        """Returns (AuthStatus, message_string)."""
        pass

    @abstractmethod
    def login(self, auth_type: str) -> tuple:
        """Returns (success: bool, message_string)."""
        pass

    def get_model_string(self, alias, auth_type=None):
        """Get the litellm model string for an alias."""
        return self.models.get(alias)

    def get_env_vars_for_auth(self, auth_type):
        """Get list of env var names needed for an auth type."""
        return self.env_vars.get(auth_type, [])
```

- [ ] **Step 3: Commit**

```bash
git add providers/
git commit -m "feat: add provider base class with AuthStatus enum"
```

---

### Task 5: Ollama provider

**Files:**
- Create: `providers/ollama.py`

- [ ] **Step 1: Create providers/ollama.py**

```python
import requests
from providers.base import BaseProvider, AuthStatus


class OllamaProvider(BaseProvider):
    name = "ollama"
    display_name = "Ollama (Local)"
    auth_types = []
    env_vars = {}
    models = {}  # Dynamic — discovered at runtime

    OLLAMA_HOST = "http://localhost:11434"
    DOCKER_HOST = "http://host.docker.internal:11434"

    def validate(self):
        try:
            resp = requests.get(f"{self.OLLAMA_HOST}/api/tags", timeout=3)
            if resp.status_code == 200:
                return AuthStatus.OK, "Ollama is reachable"
            return AuthStatus.UNREACHABLE, f"Ollama returned status {resp.status_code}"
        except requests.ConnectionError:
            return AuthStatus.UNREACHABLE, "Ollama is not running at localhost:11434"
        except requests.Timeout:
            return AuthStatus.UNREACHABLE, "Ollama connection timed out"

    def login(self, auth_type=None):
        # No auth needed — just check reachability
        status, msg = self.validate()
        if status == AuthStatus.OK:
            return True, msg
        return False, msg

    def discover_models(self):
        """Fetch available models from Ollama. Returns dict of alias -> litellm model string."""
        try:
            resp = requests.get(f"{self.OLLAMA_HOST}/api/tags", timeout=5)
            if resp.status_code != 200:
                return {}
            data = resp.json()
            models = {}
            for m in data.get("models", []):
                name = m.get("name", "")
                if name:
                    # Strip :latest tag for cleaner alias
                    alias = name.replace(":latest", "")
                    models[alias] = f"ollama/{name}"
            return models
        except (requests.ConnectionError, requests.Timeout):
            return {}

    def get_model_string(self, alias, auth_type=None):
        return f"ollama/{alias}"

    def get_extra_params(self):
        """Return extra litellm_params for Ollama models."""
        return {"api_base": self.DOCKER_HOST}
```

- [ ] **Step 2: Test Ollama provider in isolation**

Run: `cd /Users/noonoon/Dev/liteLLM && .venv/bin/python -c "from providers.ollama import OllamaProvider; p = OllamaProvider(); print(p.validate())"`
Expected: Either `(AuthStatus.OK, 'Ollama is reachable')` or `(AuthStatus.UNREACHABLE, ...)`

---

### Task 6: Alibaba/DashScope provider

**Files:**
- Create: `providers/alibaba.py`

- [ ] **Step 1: Create providers/alibaba.py**

```python
import requests
import config
from providers.base import BaseProvider, AuthStatus


class AlibabaProvider(BaseProvider):
    name = "alibaba"
    display_name = "Alibaba (DashScope)"
    auth_types = ["api_key"]
    env_vars = {"api_key": ["DASHSCOPE_API_KEY"]}
    models = {
        "qwen-max": "dashscope/qwen-max",
        "qwen-plus": "dashscope/qwen-plus",
        "qwen-turbo": "dashscope/qwen-turbo",
    }

    API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/models"

    def validate(self):
        api_key = config.get_env("DASHSCOPE_API_KEY")
        if not api_key or api_key == "your-alibaba-key-here":
            return AuthStatus.NOT_CONFIGURED, "DASHSCOPE_API_KEY not set"
        try:
            resp = requests.get(
                self.API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return AuthStatus.OK, "Authenticated with DashScope"
            if resp.status_code == 401:
                return AuthStatus.INVALID, "Invalid DASHSCOPE_API_KEY"
            return AuthStatus.INVALID, f"DashScope returned status {resp.status_code}"
        except requests.ConnectionError:
            return AuthStatus.UNREACHABLE, "Cannot reach DashScope API"
        except requests.Timeout:
            return AuthStatus.UNREACHABLE, "DashScope API timed out"

    def login(self, auth_type="api_key"):
        print(f"\n  Enter your DashScope API key.")
        print(f"  Get one at: https://dashscope.console.aliyun.com/\n")
        key = input("  DASHSCOPE_API_KEY: ").strip()
        if not key:
            return False, "No key entered."
        config.set_env("DASHSCOPE_API_KEY", key)
        # Validate the key
        status, msg = self.validate()
        if status == AuthStatus.OK:
            return True, msg
        return False, msg
```

---

### Task 7: OpenAI provider

**Files:**
- Create: `providers/openai.py`

- [ ] **Step 1: Create providers/openai.py**

```python
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests

import config
import container
from providers.base import BaseProvider, AuthStatus


class OpenAIProvider(BaseProvider):
    name = "openai"
    display_name = "OpenAI"
    auth_types = ["browser_oauth", "api_key"]
    env_vars = {
        "browser_oauth": [],
        "api_key": ["OPENAI_API_KEY"],
    }
    # Models differ by auth type — browser_oauth uses chatgpt/ prefix,
    # api_key uses openai/ prefix. This catalog is for browser_oauth (default).
    models = {
        "gpt-5": "chatgpt/gpt-5",
        "gpt-4o": "chatgpt/gpt-4o",
        "gpt-4o-mini": "chatgpt/gpt-4o-mini",
    }

    _api_key_models = {
        "gpt-5": "openai/gpt-5",
        "gpt-4o": "openai/gpt-4o",
        "gpt-4o-mini": "openai/gpt-4o-mini",
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

    def validate(self):
        # Check API key first
        api_key = config.get_env("OPENAI_API_KEY")
        if api_key:
            return self._validate_api_key(api_key)
        # Check browser OAuth via container logs
        return self._validate_browser()

    def _validate_api_key(self, api_key):
        try:
            resp = requests.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return AuthStatus.OK, "Authenticated with OpenAI API key"
            if resp.status_code == 401:
                return AuthStatus.INVALID, "Invalid OPENAI_API_KEY"
            return AuthStatus.INVALID, f"OpenAI returned status {resp.status_code}"
        except requests.ConnectionError:
            return AuthStatus.UNREACHABLE, "Cannot reach OpenAI API"
        except requests.Timeout:
            return AuthStatus.UNREACHABLE, "OpenAI API timed out"

    def _validate_browser(self):
        """Check container logs for browser OAuth auth state."""
        running, _ = container.status()
        if not running:
            return AuthStatus.NOT_CONFIGURED, "Container not running — cannot check browser auth"
        # Check recent logs (last 200 lines) for auth state
        result = subprocess.run(
            ["docker", "logs", "litellm-proxy", "--tail", "200"],
            capture_output=True, text=True,
        )
        logs = result.stdout + result.stderr
        if re.search(r"(?i)authenticated", logs):
            return AuthStatus.OK, "Authenticated via browser OAuth"
        return AuthStatus.NOT_CONFIGURED, "Not authenticated with OpenAI"

    def login(self, auth_type="browser_oauth"):
        if auth_type == "api_key":
            return self._login_api_key()
        return self._login_browser()

    def _login_api_key(self):
        print(f"\n  Enter your OpenAI API key.")
        print(f"  Get one at: https://platform.openai.com/api-keys\n")
        key = input("  OPENAI_API_KEY: ").strip()
        if not key:
            return False, "No key entered."
        config.set_env("OPENAI_API_KEY", key)
        status, msg = self.validate()
        if status == AuthStatus.OK:
            return True, msg
        return False, msg

    def _login_browser(self):
        """Drive the browser OAuth flow by reading container logs."""
        # Pre-check
        status, msg = self.validate()
        if status == AuthStatus.OK:
            return True, f"Already authenticated. {msg}"

        # Ensure container is running
        running, _ = container.status()
        if not running:
            print("  Container not running. Starting it...")
            container.up()
            if not container.wait_healthy(30):
                return False, "Container failed to start."

        # Capture timestamp before looking for URL
        since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        print("\n  Waiting for login URL from container...")
        login_url = None
        for attempt in range(30):  # 30 * 2s = 60s to find URL
            logs = container.get_logs_since(since)
            # Look for URLs that contain auth/login/device patterns
            urls = re.findall(r'https?://[^\s"\']+(?:login|auth|device|verify)[^\s"\']*', logs)
            if urls:
                login_url = urls[-1]  # Take the most recent
                break
            time.sleep(2)
            print(".", end="", flush=True)

        if not login_url:
            return False, (
                "Could not find login URL in container logs.\n"
                "  Make sure you have a chatgpt/ model configured and run './litellm.sh logs' to debug."
            )

        print(f"\n")
        print(f"  ┌─────────────────────────────────────────────────────┐")
        print(f"  │  OpenAI Login Required                             │")
        print(f"  │                                                     │")
        print(f"  │  Open this URL in your browser:                     │")
        print(f"  │  {login_url[:50]:<50} │")
        if len(login_url) > 50:
            print(f"  │  {login_url[50:100]:<50} │")
        print(f"  │                                                     │")
        print(f"  │  Waiting for browser login... (timeout: 5 min)      │")
        print(f"  └─────────────────────────────────────────────────────┘")
        print()

        # Poll for auth success
        timeout = 300  # 5 minutes
        start = time.time()
        while time.time() - start < timeout:
            logs = container.get_logs_since(since)
            if re.search(r"(?i)authenticated", logs):
                print("  ✓ Authenticated with OpenAI via browser OAuth!")
                return True, "Authenticated via browser OAuth"
            elapsed = int(time.time() - start)
            remaining = timeout - elapsed
            mins, secs = divmod(remaining, 60)
            print(f"\r  Polling... {mins}:{secs:02d} remaining  ", end="", flush=True)
            time.sleep(3)

        print()
        return False, "Login timed out after 5 minutes. Run './litellm.sh login openai' to try again."
```

- [ ] **Step 2: Create providers/__init__.py with registry**

```python
from providers.ollama import OllamaProvider
from providers.alibaba import AlibabaProvider
from providers.openai import OpenAIProvider

_PROVIDERS = {}


def _register(cls):
    instance = cls()
    _PROVIDERS[instance.name] = instance


def get_provider(name):
    """Look up a provider by name. Returns None if not found."""
    return _PROVIDERS.get(name)


def all_providers():
    """Return list of all registered provider instances."""
    return list(_PROVIDERS.values())


# Register providers (order matters for display)
_register(OpenAIProvider)
_register(AlibabaProvider)
_register(OllamaProvider)
```

- [ ] **Step 3: Commit all provider files**

```bash
git add providers/
git commit -m "feat: add provider registry with OpenAI, Alibaba, and Ollama providers"
```

---

### Task 8: cli.py — Main entry point with all commands

**Files:**
- Create: `cli.py`

- [ ] **Step 1: Create cli.py with help and lifecycle commands**

```python
#!/usr/bin/env python3
import sys
import os

DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 2555


def show_help():
    print("LiteLLM Gateway CLI")
    print("Usage: ./litellm.sh [COMMAND]")
    print()
    print("Lifecycle:")
    print("  up              Start the proxy container")
    print("  down            Stop and remove the container")
    print("  restart         Restart the container")
    print("  status          Container and model status")
    print("  logs            Stream container logs")
    print()
    print("Models:")
    print("  add             Add a model or provider (interactive wizard)")
    print("  remove          Remove a configured model")
    print("  models          List configured models")
    print()
    print("Auth:")
    print("  login [provider]  Authenticate with a provider")
    print("                    No arg: show auth status for all providers")


def cmd_status():
    import container
    import config
    import providers

    running, output = container.status()
    state = "running" if running else "stopped"
    print(f"Container:  litellm-proxy  [{state}]")
    print(f"Port:       localhost:{PORT}")
    print()

    models = config.list_models()
    if not models:
        print("Models:     (none configured)")
        return

    print("Models:")
    for m in models:
        provider = providers.get_provider(m["provider"])
        if provider and running:
            auth_status, _ = provider.validate()
            if auth_status.value == "ok":
                icon = "✓"
                label = "authenticated" if m["provider"] != "ollama" else "reachable"
            elif auth_status.value == "not_configured":
                icon = "✗"
                label = "not configured"
            elif auth_status.value == "unreachable":
                icon = "✗"
                label = "unreachable"
            else:
                icon = "✗"
                label = "invalid"
        else:
            icon = "-"
            label = "unknown" if not running else "unknown provider"
        print(f"  {m['alias']:<12} {m['provider']:<10} {icon} {label}")


def cmd_models():
    import config
    models = config.list_models()
    if not models:
        print("No models configured.")
        return
    print("Configured models:")
    for m in models:
        print(f"  {m['alias']:<12} {m['provider']:<10} ({m['model']})")


def cmd_login(provider_name=None):
    import providers

    if provider_name is None:
        # Show auth status for all configured providers
        print("Provider auth status:\n")
        for p in providers.all_providers():
            status, msg = p.validate()
            if status.value == "ok":
                print(f"  {p.display_name:<20} ✓ {msg}")
            else:
                print(f"  {p.display_name:<20} ✗ {msg}")
        return

    provider = providers.get_provider(provider_name)
    if not provider:
        print(f"Unknown provider: {provider_name}")
        print(f"Available: {', '.join(p.name for p in providers.all_providers())}")
        sys.exit(1)

    # Check existing auth
    status, msg = provider.validate()
    if status.value == "ok":
        print(f"  ✓ Already authenticated with {provider.display_name}. {msg}")
        return

    # If multiple auth types, ask which one
    auth_type = None
    if len(provider.auth_types) == 0:
        # No auth needed (e.g. Ollama)
        print(f"  {provider.display_name} doesn't require authentication.")
        ok, msg = provider.login()
        print(f"  {'✓' if ok else '✗'} {msg}")
        return
    elif len(provider.auth_types) == 1:
        auth_type = provider.auth_types[0]
    else:
        print(f"\n  {provider.display_name} supports multiple auth methods:\n")
        for i, at in enumerate(provider.auth_types, 1):
            label = at.replace("_", " ").title()
            print(f"    [{i}] {label}")
        print()
        choice = input("  Choose [1]: ").strip() or "1"
        try:
            idx = int(choice) - 1
            auth_type = provider.auth_types[idx]
        except (ValueError, IndexError):
            print("  Invalid choice.")
            sys.exit(1)

    ok, msg = provider.login(auth_type)
    if ok:
        print(f"\n  ✓ {msg}")
    else:
        print(f"\n  ✗ {msg}")
        sys.exit(1)


def cmd_add():
    import config
    import container
    import providers

    print("\n  What would you like to add?\n")
    print("    [1] A provider (then pick models)")
    print("    [2] A specific model")
    print()
    choice = input("  Choose [1]: ").strip() or "1"

    if choice == "1":
        _add_provider_first()
    elif choice == "2":
        _add_model_first()
    else:
        print("  Invalid choice.")
        sys.exit(1)


def _add_provider_first():
    import config
    import container
    import providers

    # Pick provider
    all_provs = providers.all_providers()
    print(f"\n  Select a provider:\n")
    for i, p in enumerate(all_provs, 1):
        print(f"    [{i}] {p.display_name}")
    print()
    choice = input("  Choose: ").strip()
    try:
        provider = all_provs[int(choice) - 1]
    except (ValueError, IndexError):
        print("  Invalid choice.")
        sys.exit(1)

    # Authenticate if needed
    auth_type = None
    if provider.auth_types:
        if len(provider.auth_types) == 1:
            auth_type = provider.auth_types[0]
        else:
            print(f"\n  Auth method for {provider.display_name}:\n")
            for i, at in enumerate(provider.auth_types, 1):
                label = at.replace("_", " ").title()
                print(f"    [{i}] {label}")
            print()
            at_choice = input("  Choose [1]: ").strip() or "1"
            try:
                auth_type = provider.auth_types[int(at_choice) - 1]
            except (ValueError, IndexError):
                print("  Invalid choice.")
                sys.exit(1)

        # Check if already authenticated
        status, msg = provider.validate()
        if status.value != "ok":
            print(f"\n  Need to authenticate with {provider.display_name}.")
            ok, msg = provider.login(auth_type)
            if not ok:
                print(f"\n  ✗ {msg}")
                sys.exit(1)
            print(f"  ✓ {msg}")

    # Pick models
    if provider.name == "ollama":
        catalog = provider.discover_models()
        if not catalog:
            print("\n  ✗ Ollama is not running. Start it and try again.")
            sys.exit(1)
    else:
        if auth_type and hasattr(provider, "get_models_for_auth"):
            catalog = provider.get_models_for_auth(auth_type)
        else:
            catalog = provider.models

    if not catalog:
        print("\n  No models available for this provider.")
        sys.exit(1)

    aliases = list(catalog.keys())
    print(f"\n  Available models for {provider.display_name}:\n")
    for i, alias in enumerate(aliases, 1):
        print(f"    [{i}] {alias}")
    print(f"    [a] All")
    print()
    model_choice = input("  Choose (comma-separated, e.g. 1,3): ").strip()

    selected = []
    if model_choice.lower() == "a":
        selected = aliases
    else:
        for part in model_choice.split(","):
            try:
                idx = int(part.strip()) - 1
                selected.append(aliases[idx])
            except (ValueError, IndexError):
                print(f"  Skipping invalid choice: {part.strip()}")

    if not selected:
        print("  No models selected.")
        sys.exit(1)

    # Add each model
    added = []
    for alias in selected:
        model_str = catalog[alias]
        # Check alias collision
        existing = config.list_models()
        existing_aliases = [m["alias"] for m in existing]
        final_alias = alias
        if alias in existing_aliases:
            print(f"\n  Alias '{alias}' already exists.")
            final_alias = input(f"  Enter a different alias (or Enter to skip): ").strip()
            if not final_alias or final_alias in existing_aliases:
                print(f"  Skipping {alias}.")
                continue

        extra = {}
        if provider.name == "ollama":
            extra = provider.get_extra_params()

        ok, msg = config.add_model(final_alias, model_str, extra)
        if ok:
            added.append(final_alias)
            print(f"  ✓ {msg}")
        else:
            print(f"  ✗ {msg}")

    if not added:
        print("\n  No models added.")
        return

    # Restart container and confirm live
    print(f"\n  Restarting container...")
    container.restart()
    if container.wait_healthy():
        # Validate provider credentials
        status, msg = provider.validate()
        if status.value == "ok":
            print(f"  ✓ Container is running. Added: {', '.join(added)}. {msg}")
        else:
            print(f"  ⚠ Container is running. Added: {', '.join(added)}")
            print(f"    Auth check: {msg}")
    else:
        print(f"  ✗ Container failed to start. Check './litellm.sh logs' for details.")
        print(f"    Your previous config was backed up to litellm_config.yaml.bak")
        sys.exit(1)


def _add_model_first():
    import config
    import container
    import providers

    # Build combined catalog
    combined = {}  # display_name -> (provider, alias, model_str)
    for p in providers.all_providers():
        if p.name == "ollama":
            ollama_models = p.discover_models()
            if not ollama_models:
                print("  (Ollama not running — skipping its models)")
            for alias, model_str in ollama_models.items():
                key = f"{alias} ({p.display_name})"
                combined[key] = (p, alias, model_str)
        else:
            for alias, model_str in p.models.items():
                key = f"{alias} ({p.display_name})"
                combined[key] = (p, alias, model_str)

    if not combined:
        print("  No models available from any provider.")
        sys.exit(1)

    keys = list(combined.keys())
    print(f"\n  Available models:\n")
    for i, key in enumerate(keys, 1):
        print(f"    [{i}] {key}")
    print()
    choice = input("  Choose: ").strip()
    try:
        key = keys[int(choice) - 1]
    except (ValueError, IndexError):
        print("  Invalid choice.")
        sys.exit(1)

    provider, alias, model_str = combined[key]

    # Authenticate if needed
    auth_type = None
    if provider.auth_types:
        status, msg = provider.validate()
        if status.value != "ok":
            if len(provider.auth_types) > 1:
                print(f"\n  Auth method for {provider.display_name}:\n")
                for i, at in enumerate(provider.auth_types, 1):
                    label = at.replace("_", " ").title()
                    print(f"    [{i}] {label}")
                print()
                at_choice = input("  Choose [1]: ").strip() or "1"
                try:
                    auth_type = provider.auth_types[int(at_choice) - 1]
                except (ValueError, IndexError):
                    print("  Invalid choice.")
                    sys.exit(1)
            else:
                auth_type = provider.auth_types[0]
            print(f"\n  Need to authenticate with {provider.display_name}.")
            ok, msg = provider.login(auth_type)
            if not ok:
                print(f"\n  ✗ {msg}")
                sys.exit(1)
            print(f"  ✓ {msg}")

            # Re-resolve model string based on chosen auth type
            if auth_type and hasattr(provider, "get_model_string"):
                new_model_str = provider.get_model_string(alias, auth_type)
                if new_model_str:
                    model_str = new_model_str

    # Confirm alias
    existing_aliases = [m["alias"] for m in config.list_models()]
    final_alias = alias
    if alias in existing_aliases:
        print(f"\n  Alias '{alias}' already exists.")
        final_alias = input(f"  Enter a different alias: ").strip()
        if not final_alias or final_alias in existing_aliases:
            print("  Cancelled.")
            sys.exit(1)
    else:
        custom = input(f"  Alias [{alias}]: ").strip()
        if custom:
            final_alias = custom

    extra = {}
    if provider.name == "ollama":
        extra = provider.get_extra_params()

    ok, msg = config.add_model(final_alias, model_str, extra)
    if not ok:
        print(f"  ✗ {msg}")
        sys.exit(1)
    print(f"  ✓ {msg}")

    # Restart and confirm live
    print(f"\n  Restarting container...")
    container.restart()
    if container.wait_healthy():
        status, msg = provider.validate()
        if status.value == "ok":
            print(f"  ✓ Container is running with '{final_alias}'. {msg}")
        else:
            print(f"  ⚠ Container is running with '{final_alias}'")
            print(f"    Auth check: {msg}")
    else:
        print(f"  ✗ Container failed to start. Check './litellm.sh logs' for details.")
        print(f"    Config backed up to litellm_config.yaml.bak")
        sys.exit(1)


def cmd_remove():
    import config
    import container
    import providers

    models = config.list_models()
    if not models:
        print("  No models configured.")
        return

    print(f"\n  Configured models:\n")
    for i, m in enumerate(models, 1):
        print(f"    [{i}] {m['alias']} ({m['provider']})")
    print()
    choice = input("  Remove which model(s)? (comma-separated, e.g. 1,3): ").strip()

    selected = []
    for part in choice.split(","):
        try:
            idx = int(part.strip()) - 1
            selected.append(models[idx])
        except (ValueError, IndexError):
            print(f"  Skipping invalid choice: {part.strip()}")

    if not selected:
        print("  No models selected.")
        return

    names = ", ".join(f"'{m['alias']}'" for m in selected)
    confirm = input(
        f"  Remove {names}? This will restart the container. [y/N]: "
    ).strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    removed_providers = set()
    for model in selected:
        ok, msg, provider_name = config.remove_model(model["alias"])
        if ok:
            print(f"  ✓ {msg}")
            if provider_name:
                removed_providers.add(provider_name)
        else:
            print(f"  ✗ {msg}")

    # Check if any providers lost all models
    for pname in removed_providers:
        if not config.provider_has_models(pname):
            provider = providers.get_provider(pname)
            if provider:
                all_env_vars = []
                for env_list in provider.env_vars.values():
                    all_env_vars.extend(env_list)
                if all_env_vars:
                    cleanup = input(
                        f"  No models left for {provider.display_name}. "
                        f"Remove {', '.join(all_env_vars)} from .env? [y/N]: "
                    ).strip().lower()
                    if cleanup == "y":
                        for var in all_env_vars:
                            config.remove_env(var)
                        print(f"  ✓ Cleaned up env vars.")

    # Restart
    print(f"\n  Restarting container...")
    container.restart()
    if container.wait_healthy():
        print(f"  ✓ Container is running.")
    else:
        print(f"  ✗ Container failed to start. Check './litellm.sh logs' for details.")
        sys.exit(1)


def main():
    # Ensure .env exists before any command
    import config
    config._ensure_env()

    args = sys.argv[1:]

    if not args or args[0] in ("help", "-h", "--help"):
        show_help()
        return

    cmd = args[0]

    if cmd == "up":
        import container
        container.up()
    elif cmd == "down":
        import container
        container.down()
    elif cmd == "restart":
        import container
        container.restart()
    elif cmd == "status":
        cmd_status()
    elif cmd == "logs":
        import container
        container.logs()
    elif cmd == "models":
        cmd_models()
    elif cmd == "login":
        provider_name = args[1] if len(args) > 1 else None
        cmd_login(provider_name)
    elif cmd == "add":
        cmd_add()
    elif cmd == "remove":
        cmd_remove()
    else:
        print(f"Unknown command: {cmd}")
        show_help()
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        sys.exit(130)
```

- [ ] **Step 2: Test help command**

Run: `cd /Users/noonoon/Dev/liteLLM && ./litellm.sh help`
Expected: Shows full help text with all commands

- [ ] **Step 3: Test status command**

Run: `./litellm.sh status`
Expected: Shows container status and model listing

- [ ] **Step 4: Test models command**

Run: `./litellm.sh models`
Expected: Lists gpt-5, qwen-max, llama3 with providers

- [ ] **Step 5: Test login (no arg)**

Run: `./litellm.sh login`
Expected: Shows auth status for all 3 providers

- [ ] **Step 6: Commit**

```bash
git add cli.py
git commit -m "feat: add cli.py with all commands — lifecycle, add/remove wizards, login"
```

---

### Task 9: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md to reflect new commands**

Update the Commands section to include `add`, `remove`, `login`, and the new `status` behavior. Update the "Adding a New Model" section to reference `./litellm.sh add` instead of manual YAML editing. Add note about `.venv/` directory.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for new CLI commands"
```

---

### Task 10: End-to-end smoke test

- [ ] **Step 1: Test full lifecycle**

```bash
cd /Users/noonoon/Dev/liteLLM
./litellm.sh help
./litellm.sh models
./litellm.sh status
```

- [ ] **Step 2: Test add wizard (provider-first)**

```bash
./litellm.sh add
# Choose [1] provider -> pick Ollama (if running) or Alibaba -> follow prompts
```

- [ ] **Step 3: Test remove wizard**

```bash
./litellm.sh remove
# Pick the model just added -> confirm -> verify it's gone
```

- [ ] **Step 4: Verify config wasn't mangled**

```bash
cat litellm_config.yaml
# Verify general_settings is intact, model_list is correct
```

- [ ] **Step 5: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: adjustments from end-to-end smoke testing"
```
