"""Embedded PostgreSQL 16.2 server provider for test suites and development.

Wraps pgserver to provide genuine PostgreSQL relational database instances
with psycopg 3.3.6 client connectivity for test fixtures.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from typing import Any

import pgserver

logger = logging.getLogger(__name__)


class Pg0:
    """Embedded PostgreSQL server instance."""

    def __init__(self, pgdata: str | None = None) -> None:
        self._custom_pgdata = pgdata is not None
        self._pgdata = (
            pgdata
            if pgdata is not None
            else tempfile.mkdtemp(prefix="pg0_data_")
        )
        self._server: Any = None
        self.uri: str | None = None

    def start(self) -> str:
        """Start PostgreSQL daemon and return connection URI."""
        if self._server is None:
            self._server = pgserver.get_server(self._pgdata)
            self.uri = self._server.get_uri()
            logger.info("Pg0 started at %s (data_dir=%s)", self.uri, self._pgdata)
        return self.uri  # type: ignore[return-value]

    def stop(self) -> None:
        """Stop PostgreSQL daemon and clean up temporary storage."""
        if self._server is not None:
            try:
                self._server.cleanup()
            except Exception as exc:
                logger.warning("Error during pgserver cleanup: %s", exc)
            self._server = None
            self.uri = None
            time.sleep(0.3)
        if not self._custom_pgdata and os.path.exists(self._pgdata):
            shutil.rmtree(self._pgdata, ignore_errors=True)
