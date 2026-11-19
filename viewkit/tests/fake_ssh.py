#!/usr/bin/env python3
"""A stand-in for `ssh -N -L 127.0.0.1:PORT:127.0.0.1:11434 HOST`, the tunnel of claudekit.ssh_tunnel to the Ollama
server of an SSH host. It serves a fake Ollama server on PORT: /api/version, and /api/tags with one model that can use
tools ("remote-test:27b") and one that cannot. It runs until it is stopped. With FAKE_SSH_LOG, each start writes
"PID HOST" to that file, so the test can stop it. Set VIEWKIT_SSH to its path."""
import http.server
import json
import os
import sys

args = sys.argv[1:]
port = int(args[args.index("-L") + 1].split(":")[1])
host = args[-1]
if os.environ.get("FAKE_SSH_LOG"):
    with open(os.environ["FAKE_SSH_LOG"], "a") as f:
        f.write(f"{os.getpid()} {host}\n")


class Ollama(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/version":
            body = {"version": "0.0-test"}
        elif self.path == "/api/tags":
            body = {"models": [{"name": "remote-test:27b", "capabilities": ["completion", "tools"]},
                               {"name": "remote-embed:latest", "capabilities": ["embedding"]}]}
        else:
            self.send_error(404)
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", port), Ollama).serve_forever()
