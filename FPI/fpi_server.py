#!/usr/bin/env python3
"""
Local helper server for the FPI Dashboard.

Run once in the background (leave the terminal open):

    cd D:\\FPI
    python fpi_server.py

Then the dashboard's "Sync" button (in FPI_Dashboard.html) can hit
http://127.0.0.1:8765/refresh to run fpi_update.py on demand.

Endpoints:
  GET  /            -> health check
  GET  /status      -> health check
  POST /refresh     -> run fpi_update.py, return result as JSON
                       (POST-only and same-origin/loopback only; see guards)

The server binds to 127.0.0.1 only, so it's not reachable from the network.
Requests are additionally restricted to a loopback Host header (anti
DNS-rebinding) and /refresh to same-origin / file:// callers (anti CSRF),
because /refresh starts a subprocess.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))
UPDATER = os.path.join(HERE, "fpi_update.py")
RUN_TIMEOUT_SEC = 300  # 5 min cap

_ALLOWED_HOST_NAMES = {"127.0.0.1", "localhost", "[::1]", "::1"}


class Handler(BaseHTTPRequestHandler):
    # ── Origin / Host guards ─────────────────────────────────────────────
    def _host_ok(self) -> bool:
        """Only serve requests addressed to loopback (anti DNS-rebinding)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip().lower()
        return host in _ALLOWED_HOST_NAMES

    def _origin_ok(self) -> bool:
        """Block cross-site callers from the state-changing /refresh route.

        /refresh spawns a Python subprocess, so a cross-origin "simple" POST
        from any website the user is visiting would otherwise run it via the
        victim's browser (CSRF) even though the browser blocks reading the
        reply. Allow only loopback origins and file:// pages (Origin null /
        absent); reject any real web origin.
        """
        origin = (self.headers.get("Origin") or "").strip()
        if origin in ("", "null"):
            return True   # file:// dashboard or a non-CORS client
        try:
            host = (urlsplit(origin).hostname or "").lower()
        except Exception:  # noqa: BLE001
            return False
        return host in _ALLOWED_HOST_NAMES

    # ── CORS / JSON helpers ──────────────────────────────────────────────
    def _cors(self) -> None:
        # Never a wildcard. The dashboard is usually opened as a file:// page
        # (Origin: null), so echo "null" for it; otherwise echo only an allowed
        # loopback origin. A real web origin (e.g. https://evil.com) gets no
        # CORS header and cannot read responses — and cannot trigger /refresh
        # at all (see _origin_ok + POST-only routing).
        origin = (self.headers.get("Origin") or "").strip()
        if origin == "null":
            self.send_header("Access-Control-Allow-Origin", "null")
        elif origin and self._origin_ok():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    # ── Routing ──────────────────────────────────────────────────────────
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        if not self._host_ok():
            self._json(403, {"ok": False, "error": "forbidden host"})
            return
        if self.path in ("/", "/status"):
            self._json(200, {
                "ok": True,
                "service": "fpi_server",
                "port": PORT,
                "cwd": HERE,
                "updater_exists": os.path.exists(UPDATER),
            })
            return
        # /refresh is intentionally POST-only (it has a side effect) so it can't
        # be fired by an <img> tag or a top-level navigation from another page.
        self._json(404, {"ok": False, "error": f"Not found: {self.path}"})

    def do_POST(self) -> None:
        if not self._host_ok():
            self._json(403, {"ok": False, "error": "forbidden host"})
            return
        if self.path == "/refresh":
            if not self._origin_ok():
                self._json(403, {"ok": False, "error": "cross-origin refresh blocked"})
                return
            self._run_refresh()
            return
        self._json(404, {"ok": False, "error": f"Not found: {self.path}"})

    # ── /refresh implementation ──────────────────────────────────────────
    def _run_refresh(self) -> None:
        if not os.path.exists(UPDATER):
            self._json(500, {"ok": False, "error": f"Updater not found: {UPDATER}"})
            return

        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, UPDATER],
                cwd=HERE,
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            self._json(504, {
                "ok": False,
                "error": f"Updater timed out after {RUN_TIMEOUT_SEC}s",
            })
            return
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": f"Failed to start updater: {e}"})
            return

        elapsed = round(time.time() - t0, 2)
        output = (proc.stdout or "") + (proc.stderr or "")
        lines = output.splitlines()
        tail = "\n".join(lines[-20:])

        # Parse the "Done. New fortnights added this run: N" line
        new_count = None
        for line in lines:
            if "New fortnights added this run:" in line:
                try:
                    new_count = int(line.rsplit(":", 1)[1].strip())
                except ValueError:
                    new_count = None
                break

        ok = proc.returncode == 0 and new_count is not None
        self._json(200 if ok else 500, {
            "ok": ok,
            "returncode": proc.returncode,
            "new_fortnights": new_count,
            "elapsed_sec": elapsed,
            "tail": tail,
        })

    # Quieter access log
    def log_message(self, fmt, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[fpi_server] {self.log_date_time_string()} {fmt % args}\n")


def main() -> None:
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[fpi_server] listening on http://127.0.0.1:{PORT}")
    print(f"[fpi_server] cwd       = {HERE}")
    print(f"[fpi_server] updater   = {UPDATER}")
    print(f"[fpi_server] python    = {sys.executable}")
    print("[fpi_server] press Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[fpi_server] shutting down")
        srv.server_close()


if __name__ == "__main__":
    main()
