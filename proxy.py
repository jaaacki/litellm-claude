"""
Reverse proxy: strips system messages from Anthropic /v1/messages requests
before forwarding to LiteLLM. Supports SSE streaming pass-through so
Claude Code sees tokens incrementally. Runs on the host at port 2555.

Required because chatgpt/ provider rejects system messages and LiteLLM
doesn't strip them in the Anthropic-to-Responses translation path.
"""

import json
import os
import re
import sys
import http.client
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse


def _parse_size(value, default):
    """Parse a human-readable size string (e.g. '10MB', '512KB', '1GB') to bytes.

    Accepts B, KB, MB, GB suffixes (case-insensitive). Raw integers treated as bytes.
    Returns default with a stderr warning on invalid input.
    """
    if not value:
        return default
    value = str(value).strip()
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
    match = re.match(r'^(\d+(?:\.\d+)?)\s*(GB|MB|KB|B)?$', value, re.IGNORECASE)
    if match:
        num = float(match.group(1))
        unit = (match.group(2) or "B").upper()
        return int(num * multipliers[unit])
    print(f"Warning: invalid size '{value}', using default {default}", file=sys.stderr, flush=True)
    return default


def _env_int(name, default):
    """Read an integer from environment, falling back to default."""
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        print(f"Warning: invalid integer for {name}='{val}', using default {default}", file=sys.stderr, flush=True)
        return default


# --- Configuration (all from environment, no config.py dependency) ---
LITELLM_HOST = os.environ.get("PROXY_LITELLM_HOST", "localhost")
LITELLM_PORT = _env_int("PROXY_LITELLM_PORT", 4000)
LISTEN_PORT = _env_int("PROXY_LISTEN_PORT", int(sys.argv[1]) if len(sys.argv) > 1 else 2555)
MAX_WORKERS = _env_int("PROXY_MAX_WORKERS", 20)
MAX_REQUEST_BODY = _parse_size(os.environ.get("PROXY_MAX_REQUEST_BODY"), 10 * 1024**2)   # 10MB
MAX_RESPONSE_BODY = _parse_size(os.environ.get("PROXY_MAX_RESPONSE_BODY"), 50 * 1024**2)  # 50MB
CONNECT_TIMEOUT = _env_int("PROXY_CONNECT_TIMEOUT", 10)
READ_TIMEOUT = _env_int("PROXY_READ_TIMEOUT", 300)
STREAM_IDLE_TIMEOUT = _env_int("PROXY_STREAM_IDLE_TIMEOUT", 60)
SOCKET_TIMEOUT = _env_int("PROXY_SOCKET_TIMEOUT", 30)


def _error_response(status_code, message):
    """Build a JSON error body and return (status_code, body_bytes)."""
    return status_code, json.dumps(
        {"error": {"message": message, "type": "proxy_error"}}
    ).encode()


def strip_system(body_bytes):
    """Remove 'system' field, merge into first user message."""
    try:
        data = json.loads(body_bytes)
    except Exception:
        return body_bytes

    system = data.pop("system", None)
    if not system:
        return body_bytes

    if isinstance(system, str):
        text = system
    elif isinstance(system, list):
        text = "\n".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in system
        )
    else:
        text = str(system)

    messages = data.get("messages")
    if text and isinstance(messages, list) and len(messages) > 0:
        msg = messages[0]
        if isinstance(msg, dict) and msg.get("role") == "user":
            c = msg.get("content", "")
            if isinstance(c, str):
                msg["content"] = text + "\n\n" + c
            elif isinstance(c, list):
                msg["content"] = [{"type": "text", "text": text + "\n\n"}] + c
            else:
                data["messages"].insert(0, {"role": "user", "content": text})
        else:
            data["messages"].insert(0, {"role": "user", "content": text})

    return json.dumps(data).encode()


def _is_streaming(resp):
    """Return True if the upstream response should be streamed to the client."""
    ct = resp.getheader("Content-Type", "")
    te = resp.getheader("Transfer-Encoding", "")
    return "text/event-stream" in ct or "chunked" in te


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self, method):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_REQUEST_BODY:
            code, error_msg = _error_response(413, "Request body too large")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_msg)))
            self.end_headers()
            self.wfile.write(error_msg)
            return

        body = self.rfile.read(length) if length else b""

        if method == "POST" and "/v1/messages" in self.path:
            body = strip_system(body)

        conn = http.client.HTTPConnection(LITELLM_HOST, LITELLM_PORT, timeout=CONNECT_TIMEOUT)
        try:
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "content-length", "transfer-encoding")}
            headers["Content-Length"] = str(len(body))
            headers["Host"] = f"{LITELLM_HOST}:{LITELLM_PORT}"

            conn.request(method, self.path, body=body if method == "POST" else None, headers=headers)
            conn.sock.settimeout(READ_TIMEOUT)
            resp = conn.getresponse()

            if _is_streaming(resp):
                self._stream_response(resp, conn)
            else:
                self._buffer_response(resp, conn)
        except Exception as e:
            print(f"Proxy upstream error: {e}", file=sys.stderr, flush=True)
            code, error_msg = _error_response(502, "Upstream proxy error")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(error_msg)))
                self.end_headers()
                self.wfile.write(error_msg)
            except Exception:
                pass
        finally:
            conn.close()

    def _buffer_response(self, resp, conn):
        """Forward a non-streaming response after fully buffering it (with size cap)."""
        chunks = []
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BODY:
                print(f"Upstream response exceeded {MAX_RESPONSE_BODY} bytes, aborting", file=sys.stderr, flush=True)
                code, error_msg = _error_response(502, "Upstream response too large")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(error_msg)))
                self.end_headers()
                self.wfile.write(error_msg)
                return
            chunks.append(chunk)
        resp_body = b"".join(chunks)

        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "keep-alive", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

    def _stream_response(self, resp, conn):
        """Forward an SSE / chunked response incrementally with idle timeout."""
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "keep-alive", "content-length"):
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        # Set idle timeout on the upstream socket so resp.read() raises
        # socket.timeout if upstream goes silent. This works correctly with
        # buffered reads (unlike select.select which can false-negative when
        # the BufferedReader has data in its internal buffer).
        try:
            resp.fp.raw._sock.settimeout(STREAM_IDLE_TIMEOUT)
        except (AttributeError, TypeError):
            pass  # Best-effort; READ_TIMEOUT still applies

        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                self.wfile.write(f"{len(chunk):x}\r\n".encode())
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except socket.timeout:
            print(f"Stream idle timeout ({STREAM_IDLE_TIMEOUT}s), aborting", file=sys.stderr, flush=True)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_POST(self):
        self._proxy("POST")

    def do_GET(self):
        self._proxy("GET")

    def log_message(self, fmt, *args):
        pass


class BoundedThreadServer(HTTPServer):
    """HTTPServer with bounded thread pool and client socket timeout."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._semaphore = threading.Semaphore(MAX_WORKERS)

    def process_request(self, req, addr):
        if not self._semaphore.acquire(blocking=False):
            # Pool is full — reject immediately
            try:
                req.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: 62\r\n"
                    b"Connection: close\r\n\r\n"
                    b'{"error":{"message":"Server overloaded","type":"proxy_error"}}'
                )
            except Exception:
                pass
            self.shutdown_request(req)
            return
        self._pool.submit(self._handle, req, addr)

    def _handle(self, req, addr):
        try:
            req.settimeout(SOCKET_TIMEOUT)
            self.finish_request(req, addr)
        except Exception as e:
            print(f"Proxy request error: {e}", file=sys.stderr, flush=True)
        finally:
            self.shutdown_request(req)
            self._semaphore.release()

    def server_close(self):
        super().server_close()
        self._pool.shutdown(wait=False)


if __name__ == "__main__":
    print(f"Proxy :{LISTEN_PORT} -> LiteLLM :{LITELLM_PORT} (workers={MAX_WORKERS})", flush=True)
    BoundedThreadServer(("127.0.0.1", LISTEN_PORT), Handler).serve_forever()
