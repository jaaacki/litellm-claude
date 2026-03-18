# LiteLLM CLI Redesign — Design Spec

## Overview

Rewrite `litellm.sh` from a basic lifecycle wrapper into a full control plane for the LiteLLM proxy. Python-based CLI behind a thin bash wrapper. Interactive wizards for adding/removing models, provider authentication with live validation, and clear user feedback at every step.

## File Structure

```
liteLLM/
├── litellm.sh              # Thin wrapper: ensures .venv, forwards to cli.py
├── cli.py                  # Main CLI entry point (argument routing)
├── providers/
│   ├── __init__.py         # Provider registry (lookup by name)
│   ├── base.py             # BaseProvider ABC
│   ├── openai.py           # OpenAI: browser OAuth + API key
│   ├── alibaba.py          # DashScope: API key
│   └── ollama.py           # Ollama: no auth, dynamic model discovery
├── config.py               # Read/write litellm_config.yaml and .env safely
├── docker.py               # Container lifecycle via docker-compose
├── requirements.txt        # pyyaml, requests
├── litellm_config.yaml     # Managed by CLI
├── docker-compose.yml      # Unchanged
├── .env                    # Managed by CLI
└── data/                   # Unchanged
```

## Python Environment

`litellm.sh` manages a local `.venv/` directory:
1. If `.venv/` doesn't exist, create it: `python3 -m venv .venv`
2. If deps not installed, run: `.venv/bin/pip install -r requirements.txt`
3. Exec: `.venv/bin/python cli.py "$@"`

This avoids the macOS `externally-managed-environment` error and keeps deps isolated.

## Command Interface

```
./litellm.sh                     # No args: show help
./litellm.sh help / -h / --help  # Show help
./litellm.sh up                  # Start container
./litellm.sh down                # Stop container
./litellm.sh restart             # Restart container
./litellm.sh status              # Container status + per-model auth status
./litellm.sh logs                # Stream container logs

./litellm.sh add                 # Interactive wizard (provider-first or model-first)
./litellm.sh remove              # Interactive: pick model to remove, confirm, restart
./litellm.sh models              # List configured models with auth status

./litellm.sh login <provider>    # Authenticate with a provider (checks existing first)
./litellm.sh login               # No arg: show auth status for all configured providers
```

Non-interactive/CI usage is out of scope. This is a local dev tool.

### `status` vs `login` (no arg)

- `status` = container health + model listing (quick, shows everything)
- `login` (no arg) = runs live `validate()` against each provider (slower, actually tests credentials)

## Provider Registry

Each provider is a Python class inheriting from `BaseProvider`:

```python
class AuthStatus(Enum):
    OK = "ok"                    # Authenticated and working
    NOT_CONFIGURED = "not_configured"  # No credentials set
    INVALID = "invalid"          # Credentials set but not working
    UNREACHABLE = "unreachable"  # Can't reach the service

class BaseProvider(ABC):
    name: str                          # e.g. "openai"
    display_name: str                  # e.g. "OpenAI"
    auth_types: list[str]             # e.g. ["browser_oauth", "api_key"]
    env_vars: dict[str, list[str]]    # auth_type -> list of env var names
    models: dict[str, str]            # alias -> litellm model string

    @abstractmethod
    def validate(self) -> tuple[AuthStatus, str]: ...

    @abstractmethod
    def login(self, auth_type: str) -> tuple[bool, str]: ...
```

### OpenAI

Two auth modes with different LiteLLM provider prefixes:

- **`browser_oauth`**: Uses `chatgpt/` prefix (e.g. `chatgpt/gpt-5`). No env vars written — session is managed inside the container by LiteLLM. `validate()` checks docker logs for auth state.
- **`api_key`**: Uses `openai/` prefix (e.g. `openai/gpt-4o`). Writes `OPENAI_API_KEY` to `.env`. `validate()` makes a lightweight API call (list models endpoint).

`env_vars = {"browser_oauth": [], "api_key": ["OPENAI_API_KEY"]}`

Models catalog maps aliases to the appropriate prefix based on the auth type chosen during onboarding.

### Alibaba/DashScope

`auth_types = ["api_key"]`
`env_vars = {"api_key": ["DASHSCOPE_API_KEY"]}`
`validate()` makes a test API call.

### Ollama

`auth_types = []` (no auth needed)
`validate()` checks if `localhost:11434` is reachable.
Models discovered dynamically from `localhost:11434/api/tags`. If Ollama isn't running, the wizard prints "Ollama is not running. Start it and try again." and exits gracefully.

Note: CLI hits `localhost:11434` (host network), container config uses `host.docker.internal:11434`. Both reach the same Ollama instance.

## Wizard Flows

### `add` wizard

1. "What would you like to add?" → `[1] A provider` / `[2] A specific model`
2. **Provider-first**: pick provider → pick auth method (if multiple) → authenticate → show model catalog → user picks models → user confirms or customizes alias (default = short name like "gpt-5") → write config → restart → confirm live
3. **Model-first**: show combined catalog from all registered providers (Ollama only contributes if running, otherwise skipped with a note) → user picks → resolve provider → authenticate if needed → write config → restart → confirm live
4. **Alias collision**: if alias already exists in config, warn and ask user to pick a different alias.
5. **Combined catalog conflicts**: if two providers offer the same model name, show both with provider prefix (e.g. `gpt-4o (openai)`, `gpt-4o (azure)`).

### `remove` flow

1. Show numbered list of configured models
2. User picks one or more
3. Confirm: "Remove gpt-5 (openai)? This will restart the container. [y/N]"
4. Remove from YAML
5. If no remaining models use that provider, ask: "No models left for OpenAI. Remove OPENAI_API_KEY from .env? [y/N]"
6. Restart container, confirm running

### `login` flow

1. Call `validate()` first
2. If `AuthStatus.OK`: print "Already authenticated with OpenAI. Verified just now." — done
3. If `NOT_CONFIGURED` or `INVALID`: run auth flow (browser OAuth or prompt for API key)
4. After auth, call `validate()` again to confirm
5. Clear success/failure message with next steps on failure

### "Confirm live" means:

After restart, wait for container to be healthy (poll `docker-compose ps` for "Up" status, max 30s), then call `validate()` on the newly added provider. Report success or failure.

## OpenAI Browser OAuth

1. Pre-check via `validate()` — skip if already `OK`
2. Capture current time as RFC3339 timestamp
3. Use `docker logs --since <timestamp>` to read only new log lines
4. Parse login URL with regex pattern for the OAuth device code URL
5. Display URL clearly to user with instructions
6. Poll every 3s with live status update, 5-minute hard timeout
7. Terminal states — always clear:
   - Success: "Authenticated with OpenAI. Verified with a test call."
   - Timeout: "Login timed out after 5 minutes. Run './litellm.sh login openai' to try again."
   - Error: "Login failed: <reason>. Run './litellm.sh login openai' to try again."

## Config Management

### YAML (`litellm_config.yaml`)

- PyYAML for load/dump with `yaml.dump(..., default_flow_style=False, sort_keys=False)` to preserve key ordering
- Quoted strings (URLs, keys) preserved via `yaml.SafeDumper` with explicit string representation
- `add_model(alias, litellm_model, extra_params={})` — appends to `model_list`
- `remove_model(alias)` — removes from `model_list` by `model_name`
- `list_models()` — returns list of configured models with provider info
- **`general_settings` and any non-model-list sections are preserved untouched.** The CLI only reads/writes the `model_list` key.
- Comments will be lost (PyYAML limitation, acceptable trade-off)

### Env (`.env`)

- If `.env` doesn't exist, copy from `.env.example`. If `.env.example` doesn't exist either, create empty `.env`.
- Key=value parsing preserving comments and order
- `set_env(key, value)` — adds or updates
- `get_env(key)` — reads current value
- `remove_env(key)` — comments out the line (preserves as `# REMOVED: KEY=value`)
- **Never touches `LITELLM_MASTER_KEY`, `LITELLM_LOG`, or any non-provider env vars** — these are outside the CLI's scope

### Safety

- Before writing, backup to `litellm_config.yaml.bak` and `.env.bak` (single backup, overwritten each time)
- Auto-restart container after config changes

### Error Handling

All commands check prerequisites and exit with clear messages:
- Docker not installed or daemon not running → "Docker is not running. Start Docker and try again."
- docker-compose not found → "docker-compose is required. Install it and try again."
- Container fails to start after config change → "Container failed to start. Check './litellm.sh logs' for details. Your previous config was backed up to litellm_config.yaml.bak"
- Exit code 1 on all errors.

## `status` Output

```
Container:  litellm-proxy  [running]
Port:       localhost:2555

Models:
  gpt-5      openai     ✓ authenticated
  qwen-max   alibaba    ✗ invalid key
  llama3     ollama     ✓ reachable
```

Auth status per model is derived from the provider's `validate()`. Models sharing a provider share the same auth status.

## Dependencies

- Python 3 (already on macOS)
- `.venv/` with `requirements.txt`: pyyaml, requests
- Docker + docker-compose (already required)

## Out of Scope

- Token auto-refresh (noted for future)
- Providers beyond OpenAI, Alibaba, Ollama (extensible by adding provider classes)
- Non-interactive / CI usage
- CI/CD, tests, version control setup
