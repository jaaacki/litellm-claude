"""
Reverse proxy: strips system messages from Anthropic /v1/messages requests
before forwarding to LiteLLM. Runs on the host at port 2555.

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
        return json.dumps(data).encode()

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
            resp_body = resp.read()

            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding", "connection", "keep-alive"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        except Exception as e:
            error_msg = json.dumps({"error": {"message": f"Proxy error: {e}", "type": "proxy_error"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_msg)))
            self.end_headers()
            self.wfile.write(error_msg)
        finally:
            conn.close()

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
        except Exception:
            pass
        finally:
            self.shutdown_request(req)


if __name__ == "__main__":
    print(f"Proxy :{LISTEN_PORT} -> LiteLLM :{LITELLM_PORT}", flush=True)
    Threaded(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()
