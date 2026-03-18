"""
Reverse proxy: strips system messages from Anthropic /v1/messages requests
before forwarding to LiteLLM. Supports SSE streaming pass-through so
Claude Code sees tokens incrementally. Runs on the host at port 2555.

Required because chatgpt/ provider rejects system messages and LiteLLM
doesn't strip them in the Anthropic-to-Responses translation path.
"""

import json
import os
import sys
import http.client
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

LITELLM_HOST = "localhost"
LITELLM_PORT = 4000
LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 2555


def strip_system(body_bytes):
    """Remove 'system' field, merge into first user message."""
    try:
        data = json.loads(body_bytes)
    except Exception:
        return body_bytes

    system = data.pop("system", None)
    if not system:
        # No modification needed — return original bytes unchanged to
        # preserve key ordering, whitespace, and numeric formatting.
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

    if text and data.get("messages"):
        msg = data["messages"][0]
        if msg.get("role") == "user":
            c = msg.get("content", "")
            if isinstance(c, str):
                msg["content"] = text + "\n\n" + c
            elif isinstance(c, list):
                msg["content"] = [{"type": "text", "text": text + "\n\n"}] + c
        else:
            data["messages"].insert(0, {"role": "user", "content": text})

    return json.dumps(data).encode()


def _is_streaming(resp):
    """Return True if the upstream response should be streamed to the client."""
    ct = resp.getheader("Content-Type", "")
    te = resp.getheader("Transfer-Encoding", "")
    return "text/event-stream" in ct or "chunked" in te


class Handler(BaseHTTPRequestHandler):
    def _proxy(self, method):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if method == "POST" and "/v1/messages" in self.path:
            body = strip_system(body)

        conn = http.client.HTTPConnection(LITELLM_HOST, LITELLM_PORT, timeout=300)
        try:
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "content-length", "transfer-encoding")}
            headers["Content-Length"] = str(len(body))
            headers["Host"] = f"{LITELLM_HOST}:{LITELLM_PORT}"

            conn.request(method, self.path, body=body if method == "POST" else None, headers=headers)
            resp = conn.getresponse()

            if _is_streaming(resp):
                self._stream_response(resp, conn)
            else:
                self._buffer_response(resp, conn)
        except Exception as e:
            error_msg = json.dumps({"error": {"message": f"Proxy error: {e}", "type": "proxy_error"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_msg)))
            self.end_headers()
            self.wfile.write(error_msg)
        finally:
            conn.close()

    def _buffer_response(self, resp, conn):
        """Forward a non-streaming response after fully buffering it."""
        # Content-Encoding (e.g. gzip) is intentionally passed through as-is.
        # We do not decompress — the raw bytes and the header stay consistent.
        resp_body = resp.read()

        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "keep-alive", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

    def _stream_response(self, resp, conn):
        """Forward an SSE / chunked response incrementally."""
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "keep-alive", "content-length"):
                self.send_header(k, v)
        # Use chunked transfer-encoding so the client can consume data as it
        # arrives without needing a Content-Length up front.
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        # Read from upstream in small pieces and flush each one immediately.
        # Content-Encoding (e.g. gzip) is intentionally NOT decoded here —
        # we pass the raw compressed bytes through together with the header,
        # so the client handles decompression itself.
        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                # Write HTTP chunked framing: hex size + CRLF + data + CRLF
                self.wfile.write(f"{len(chunk):x}\r\n".encode())
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            # Chunked terminator
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected mid-stream — nothing to do.
            pass

    def do_POST(self):
        self._proxy("POST")

    def do_GET(self):
        self._proxy("GET")

    def log_message(self, fmt, *args):
        pass


class Threaded(HTTPServer):
    def process_request(self, req, addr):
        t = threading.Thread(target=self._do, args=(req, addr), daemon=True)
        t.start()

    def _do(self, req, addr):
        try:
            self.finish_request(req, addr)
        except Exception as e:
            import traceback
            print(f"Proxy request error: {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
        finally:
            self.shutdown_request(req)


if __name__ == "__main__":
    print(f"Proxy :{LISTEN_PORT} -> LiteLLM :{LITELLM_PORT}", flush=True)
    Threaded(("127.0.0.1", LISTEN_PORT), Handler).serve_forever()
