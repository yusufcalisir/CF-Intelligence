"""Lightweight HTTP Health Status Server — Section 41.1."""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger(__name__)

# Shared global health state
health_state: dict[str, Any] = {
    "status": "HEALTHY",
    "last_round_id": 1,
    "last_round_completed_at": datetime.now(UTC).isoformat(),
    "dp_epsilon_remaining": 9.0,
}


class HealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP request handler for daemon health endpoints."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response_data = json.dumps(health_state).encode("utf-8")
            self.wfile.write(response_data)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Suppress default HTTP request logging to stderr."""
        pass


class DaemonHealthServer:
    """Runs lightweight HTTP server in a background thread."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        self.host = host
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start health server on background thread."""
        try:
            self._server = HTTPServer((self.host, self.port), HealthCheckHandler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            logger.info("Daemon health server started on http://%s:%d/health", self.host, self.port)
        except Exception as exc:
            logger.warning("Could not start health server on port %d: %s", self.port, exc)

    def stop(self) -> None:
        """Stop background health server."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            logger.info("Daemon health server stopped.")


def update_health_state(last_round_id: int, dp_epsilon_spent: float = 1.0) -> None:
    """Updates daemon health state metrics."""
    health_state["last_round_id"] = last_round_id
    health_state["last_round_completed_at"] = datetime.now(UTC).isoformat()
    health_state["dp_epsilon_remaining"] = max(
        0.0, health_state["dp_epsilon_remaining"] - dp_epsilon_spent
    )
