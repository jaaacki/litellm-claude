# Proxy Hardening Design

**Date:** 2026-03-22
**Scope:** Upstream trust + resource bounds for local-only dev proxy
**Threat model:** Upstream misbehavior and resource exhaustion from legitimate local sessions. No adversarial client traffic.

## Overview

Harden five files against upstream failures, resource exhaustion, state corruption, and protocol violations. All changes preserve the existing architecture (host proxy + Docker LiteLLM container).

---

## Section 1: proxy.py

### 1.1 Protocol fix
- Set `protocol_version = "HTTP/1.1"` class attribute on `Handler`. This changes the response status line from `HTTP/1.0 200 OK` to `HTTP/1.1 200 OK`, which is required for standards-compliant clients to accept the `Transfer-Encoding: chunked` header. The existing manual chunked framing in `_stream_response` (hex size + CRLF + data + CRLF) is already correct and does not change.

### 1.2 strip_system crash fix
- Guard against `messages` not being a list or containing non-dict entries.
- On any structural mismatch, return original bytes unchanged instead of crashing the handler thread.

### 1.3 Bounded concurrency
- Replace `Threaded` class (unbounded `threading.Thread` per request) with `concurrent.futures.ThreadPoolExecutor`.
- Override `process_request` in the server class: use `threading.Semaphore(max_workers)` with `acquire(blocking=False)`. If the semaphore cannot be acquired (pool is full), immediately return a 503 Service Unavailable response and close the socket. If acquired, submit the request to the `ThreadPoolExecutor` and release the semaphore in the task's `finally` block.
- This avoids blocking on `ThreadPoolExecutor.submit()` (which queues indefinitely) and gives explicit backpressure.

### 1.4 Max request body
- Check `Content-Length` against configurable cap before reading. Return 413 if exceeded.

### 1.5 Max response body
- In `_buffer_response`, read in chunks up to configurable cap. Abort with 502 if upstream exceeds it.

### 1.6 Upstream timeouts
- Split the current 300s blanket timeout into separate connect timeout (10s) and read timeout (300s) via `http.client.HTTPConnection`.

### 1.7 Streaming idle timeout
- In `_stream_response`, enforce configurable idle timeout between chunks using `select.select()` on the socket. Abort stream if upstream goes silent.

### 1.8 Sanitized errors
- Return generic "proxy error" JSON to client. Log actual exception detail to stderr only.

### 1.9 Client socket timeout
- Call `socket.settimeout(N)` on accepted connections (which sets both `SO_RCVTIMEO` and `SO_SNDTIMEO`). This prevents slow/stuck clients from pinning threads indefinitely.

### 1.10 Environment-based configuration
All tunables read from `os.environ` with sane defaults (proxy.py is a standalone process, no config.py dependency). Size values use the human-readable format described in Section 1.11 (e.g. `10MB`, `512KB`):

| Env var | Default | Purpose |
|---|---|---|
| `PROXY_LISTEN_PORT` | `2555` | Proxy listen port |
| `PROXY_LITELLM_HOST` | `localhost` | Upstream LiteLLM host |
| `PROXY_LITELLM_PORT` | `4000` | Upstream LiteLLM port |
| `PROXY_MAX_WORKERS` | `20` | Thread pool size |
| `PROXY_MAX_REQUEST_BODY` | `10MB` | Max request body (accepts KB/MB/GB) |
| `PROXY_MAX_RESPONSE_BODY` | `50MB` | Max response body (accepts KB/MB/GB) |
| `PROXY_CONNECT_TIMEOUT` | `10` | Upstream connect timeout (seconds) |
| `PROXY_READ_TIMEOUT` | `300` | Upstream read timeout (seconds) |
| `PROXY_STREAM_IDLE_TIMEOUT` | `60` | Streaming idle timeout (seconds) |
| `PROXY_SOCKET_TIMEOUT` | `30` | Client socket timeout (seconds) |

### 1.11 Human-readable size parser
`_parse_size(value, default)` helper that accepts `B`, `KB`, `MB`, `GB` suffixes (case-insensitive). Raw integers treated as bytes. Invalid values fall back to default with stderr warning.

---

## Section 2: container.py

### 2.1 Close leaked file descriptor
In `_start_proxy`, wrap `log_fh` in a try/finally block: open before `Popen`, close in `finally` so the FD is released on both success and error paths (e.g. if `Popen` raises because the Python binary is not found). Child process inherits the FD via fork, parent doesn't need it. Prevents EMFILE on repeated restart cycles.

### 2.2 Subprocess timeouts
Add timeouts to all `subprocess.run` calls. Use `timeout=30` for lightweight checks and `timeout=120` for docker compose operations (which can legitimately take over 30s for image pulls and container creation):
- `_compose_cmd` — `timeout=30` (version detection)
- `_run` — `timeout=120` (compose up/down/ps/etc)
- `_docker_running` — `timeout=30` (docker info)
- `_is_proxy_process` — `timeout=10` (ps -p)
- `get_logs_since` — `timeout=30` (docker logs --since)
- `get_logs_tail` — `timeout=30` (docker logs --tail)

Catch `subprocess.TimeoutExpired`, return failure instead of hanging indefinitely.

---

## Section 3: config.py

### 3.1 State corruption guard
When `_load_yaml` encounters malformed YAML:
- Still return the empty fallback for read-only callers (`list_models`, `provider_has_models`).
- Return a `MalformedConfig` class (subclass of `dict`) instead of a plain dict, so read-only callers work transparently.
- `_save_yaml` checks `isinstance(data, MalformedConfig)` and refuses to persist, raising a `ValueError` instead of silently overwriting valid config with empty data. This avoids adding magic keys to the dict that could leak into serialization.

---

## Section 4: providers/openai.py

### 4.1 Response validation in _validate_api_key
- Check `Content-Type` header contains `application/json` before parsing.
- Wrap `resp.json()` in try/except `ValueError`. Return `UNREACHABLE` with descriptive message on decode failure.

### 4.2 Response validation in _validate_browser
- Same JSON validation on `/v1/models` response.
- Replace bare `except Exception` with `except (requests.RequestException, ValueError)` to cover both HTTP errors and JSON decode failures.
- Honest status messages: "Browser OAuth appears configured (based on container logs)" instead of asserting "Authenticated".

### 4.3 General exception handling
- Catch `requests.RequestException` broadly for all HTTP calls instead of separate `ConnectionError`/`Timeout` where both are handled identically.

---

## Section 5: providers/ollama.py

### 5.1 Response validation in validate
- Check content-type on `/api/tags` response.
- Wrap JSON parsing in try/except `ValueError`. Return `UNREACHABLE` on decode failure.

### 5.2 Response validation in discover_models
- Validate `models` key is a list.
- Validate each entry is a dict with a `name` string. Skip malformed entries.
- Catch `requests.RequestException` and `ValueError` (JSON decode).

### 5.3 Hardened pull_model NDJSON parsing
- Wrap `json.loads(line)` in try/except `ValueError` per line — skip malformed frames instead of crashing.
- Add idle timeout between NDJSON lines (60s default) using `response.iter_lines()` combined with a `socket.settimeout()` on the underlying socket. This coexists with the existing 600s overall request timeout: 600s is the outer bound for the entire pull, 60s is the maximum silence between progress updates. Both are needed since large model pulls can legitimately take minutes but should not stall silently.
- Catch `requests.RequestException` instead of only `ConnectionError`/`Timeout`.

---

## .env.example

Update the existing `.env.example` file (do not replace from scratch). Add:
- `OPENAI_API_KEY` (currently missing from `.env.example`)
- `OLLAMA_HOST` (currently missing)
- All proxy tunables from Section 1.10

Preserve existing entries (`DASHSCOPE_API_KEY`, `LITELLM_MASTER_KEY`, `CHATGPT_EMAIL`/`CHATGPT_PASSWORD`, `LITELLM_LOG`). Organize into sections with comments.

---

## Out of scope

- Adversarial client input validation (local-only tool)
- Request JSON schema enforcement beyond crash prevention
- Circuit breakers / jittered retries (complexity not warranted for local dev)
- Tests / CI (project has none per CLAUDE.md)
