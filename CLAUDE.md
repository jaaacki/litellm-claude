# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A configuration-only deployment repo for a [LiteLLM](https://github.com/BerryAI/litellm) proxy server, providing a unified API gateway to multiple LLM providers. The CLI (`litellm.sh`) is a Python-based control plane behind a thin bash wrapper.

## Architecture

```
Client → localhost:2555 (proxy.py) → localhost:4000 (Docker: litellm-proxy) → LLM providers
```

There are two layers of proxying:
1. **proxy.py** — host-side reverse proxy on port 2555 that strips `system` messages from Anthropic `/v1/messages` requests (needed because `chatgpt/` provider rejects system messages and LiteLLM doesn't strip them in the Anthropic-to-Responses translation)
2. **Docker container** — LiteLLM on port 4000, handles actual API translation and routing

Key files:
- **litellm.sh** — thin bash wrapper: manages `.venv/`, forwards to `cli.py`
- **cli.py** — main CLI entry point (argument routing, interactive wizards)
- **config.py** — reads/writes `litellm_config.yaml` and `.env` (atomic writes with backups)
- **container.py** — Docker container lifecycle + proxy.py process management
- **proxy.py** — system message rewriter proxy (threaded HTTP server)
- **providers/** — provider registry with `BaseProvider` ABC
- **setup-alias.sh** — interactive script to create shell functions for Claude Code aliases
- **docker-compose.yml** — single-service definition, maps port 4000 (not 2555; proxy.py handles 2555)
- **litellm_config.yaml** — model registry (managed by CLI, uses litellm model string format like `chatgpt/gpt-5.4`, `dashscope/qwen-max`, `ollama/llama3`)
- **.env** — API keys and master key (managed by CLI, chmod 600)
- **auth/** — mounted into container at `/root/.config/litellm` for browser OAuth persistence

## Commands

All operations go through the CLI wrapper:

```bash
./litellm.sh up              # Start proxy (port 2555)
./litellm.sh down            # Stop and remove container
./litellm.sh restart         # Restart container (force-recreate to pick up .env/config changes)
./litellm.sh status          # Container status + per-model auth status
./litellm.sh logs            # Stream container logs (follow mode)
./litellm.sh models          # List configured models with providers

./litellm.sh add             # Interactive wizard to add models/providers
./litellm.sh remove          # Interactive wizard to remove models

./litellm.sh login           # Show auth status for all providers
./litellm.sh login openai    # Authenticate with OpenAI (browser OAuth or API key)
./litellm.sh login alibaba   # Authenticate with DashScope (API key)
./litellm.sh login ollama    # Check Ollama connectivity, cloud login, pull models

./litellm.sh claude [args]   # Launch Claude Code through the proxy
./setup-alias.sh             # Create persistent shell alias for Claude Code
```

## Provider System

Providers inherit from `BaseProvider` (in `providers/base.py`) and must implement `validate() -> (AuthStatus, msg)` and `login(auth_type) -> (bool, msg)`. Registered in `providers/__init__.py` via `_register()` — order matters for display.

**Provider-specific details:**
- **OpenAI** — two auth paths: `browser_oauth` (chatgpt/ prefix, uses container logs to find OAuth URL) and `api_key` (openai/ prefix, stored in `.env`). `get_models_for_auth()` returns different model catalogs per auth type.
- **Alibaba** — single `api_key` auth via `DASHSCOPE_API_KEY` env var. Static model catalog.
- **Ollama** — no auth_types (manages own auth). Dynamic model discovery via `/api/tags` API. Models use `api_base: http://host.docker.internal:11434` to reach host Ollama from container.

### Adding a New Provider

1. Create `providers/yourprovider.py` inheriting from `BaseProvider`
2. Implement `validate()` and `login()`, define `auth_types`, `env_vars`, `models`
3. For dynamic catalogs, implement `discover_models()` (see Ollama)
4. Register in `providers/__init__.py`

## Key Details

- Container image: `ghcr.io/berriai/litellm:main-v1.82.4-nightly`
- Master key for proxy auth: set via `LITELLM_MASTER_KEY` in `.env` (default: `sk-1234`)
- Python deps: `pyyaml`, `requests` (managed in `.venv/`, auto-created by litellm.sh)
- Config writes are atomic (temp file + rename) with `.bak` backups
- `container.restart()` uses `--force-recreate` to pick up `.env`/config changes
- `container.up()` also starts proxy.py; `container.down()` stops it (PID file at `.proxy.pid`)
- Ollama models need `extra_hosts: host.docker.internal:host-gateway` in docker-compose to reach host
- No tests, no CI/CD
