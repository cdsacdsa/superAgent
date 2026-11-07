"""Minimal exec API for the custom sandbox image (ACA custom-container session pool).

POST /exec {"command": "...", "timeout": 60} -> {"exit_code", "stdout", "stderr"}
Only reachable through the session pool management endpoint (Entra-authenticated).
"""
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX = 200_000


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._send(200, {"status": "ok"})

    def do_POST(self):
        if self.path.split("?")[0] != "/exec":
            return self._send(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        timeout = min(int(body.get("timeout", 60)), 600)
        try:
            r = subprocess.run(body.get("command", ""), shell=True, capture_output=True, text=True, timeout=timeout)
            self._send(200, {"exit_code": r.returncode, "stdout": r.stdout[-MAX:], "stderr": r.stderr[-MAX:]})
        except subprocess.TimeoutExpired:
            self._send(200, {"exit_code": 124, "stdout": "", "stderr": f"timed out after {timeout}s"})

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
