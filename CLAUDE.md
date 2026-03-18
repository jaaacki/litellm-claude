# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A configuration-only deployment repo for a [LiteLLM](https://github.com/BerryAI/litellm) proxy server, providing a unified API gateway to multiple LLM providers. The CLI (`litellm.sh`) is a Python-based control plane behind a thin bash wrapper.

## Architecture

```
Client → localhost:2555 → Docker (litellm-proxy) :4000 → LLM providers
```

- **litellm.sh** — thin bash wrapper: manages `.venv/`, forwards to `cli.py`
- **cli.py** — main CLI entry point (argument routing, wizards)
- **config.py** — reads/writes `litellm_config.yaml` and `.env` safely
- **container.py** — Docker container lifecycle (up/down/restart/status/logs)
- **providers/** — provider registry (OpenAI, Alibaba/DashScope, Ollama)
- **docker-compose.yml** — single-service definition, maps port 2555→4000
- **litellm_config.yaml** — model registry (managed by CLI)
- **.env** — API keys and master key (managed by CLI)
- **data/** — mounted as /root/.litellm for LiteLLM's persistent cache/state
- **.venv/** — Python virtual environment (auto-created by litellm.sh)

## Commands

All operations go through the CLI wrapper:

```bash
./litellm.sh up              # Start proxy (port 2555)
./litellm.sh down            # Stop and remove container
./litellm.sh restart         # Restart container
./litellm.sh status          # Container status + per-model auth status
./litellm.sh logs            # Stream container logs (follow mode)
./litellm.sh models          # List configured models with providers

./litellm.sh add             # Interactive wizard to add models/providers
./litellm.sh remove          # Interactive wizard to remove models

./litellm.sh login           # Show auth status for all providers
./litellm.sh login openai    # Authenticate with OpenAI (browser OAuth or API key)
./litellm.sh login alibaba   # Authenticate with DashScope (API key)
./litellm.sh login ollama    # Check Ollama connectivity
```

## Adding a New Model

Run `./litellm.sh add` and follow the interactive wizard. It handles provider authentication, config writing, and container restart.

## Adding a New Provider

Create a new file in `providers/` inheriting from `BaseProvider`, implement `validate()` and `login()`, then register it in `providers/__init__.py`.

## Key Details

- Container image: `ghcr.io/berriai/litellm:main-latest`
- Master key for proxy auth: set via `LITELLM_MASTER_KEY` in `.env`
- Python deps managed in `.venv/` (auto-created), defined in `requirements.txt`
- No tests, no CI/CD
