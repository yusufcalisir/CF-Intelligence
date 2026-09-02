"""Unit tests for Production Error Sanitization & Information Leakage Defense."""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.infrastructure.security.error_handler import format_safe_error_response


@pytest.fixture
def error_app() -> FastAPI:
    """Create a test FastAPI instance with the safe error handler and failing routes."""
    test_app = FastAPI()

    @test_app.exception_handler(Exception)
    async def handle_exc(request: Request, exc: Exception):
        return format_safe_error_response(request, exc, status_code=500)

    @test_app.get("/trigger-db-error")
    async def trigger_db_error():
        # Simulate an internal DB exception with sensitive path/query info
        raise RuntimeError(
            "psycopg2.OperationalError: relation 'users_tbl_secret' does not exist "
            "at /var/www/internal_backend/app/db/session.py line 124 in execute_query"
        )

    @test_app.get("/trigger-file-error")
    async def trigger_file_error():
        # Simulate a filesystem path disclosure exception
        raise FileNotFoundError(
            "No such file or directory: 'C:\\Users\\ServerAdmin\\AppData\\Local\\secret_keys.pem'"
        )

    return test_app


def test_production_error_sanitization_strips_stack_trace_and_paths(error_app: FastAPI):
    """Verify that in production mode, sensitive internal paths and stack traces are NOT leaked to the client."""
    client = TestClient(error_app, raise_server_exceptions=False)

    # Force production mode
    with patch("app.infrastructure.security.error_handler.is_production_mode", return_value=True):
        res = client.get("/trigger-db-error")

        assert res.status_code == 500
        data = res.json()

        # 1. Must contain generic safe message
        assert "Something went wrong" in data["detail"]
        assert data["title"] == "Internal Server Error"

        # 2. Must contain incident ID for support correlation
        assert "incident_id" in data
        assert re.match(r"^inc_[a-f0-9]{12}$", data["incident_id"])
        assert res.headers.get("X-Incident-ID") == data["incident_id"]

        # 3. MUST NOT leak internal file paths, table names, or runtime internals
        body_str = res.text
        assert "psycopg2" not in body_str
        assert "users_tbl_secret" not in body_str
        assert "/var/www" not in body_str
        assert "session.py" not in body_str
        assert "execute_query" not in body_str


def test_production_error_sanitization_strips_windows_paths(error_app: FastAPI):
    """Verify Windows internal filesystem paths are completely stripped in production."""
    client = TestClient(error_app, raise_server_exceptions=False)

    with patch("app.infrastructure.security.error_handler.is_production_mode", return_value=True):
        res = client.get("/trigger-file-error")

        assert res.status_code == 500
        data = res.json()

        assert "Something went wrong" in data["detail"]
        assert "incident_id" in data

        body_str = res.text
        assert "ServerAdmin" not in body_str
        assert "secret_keys.pem" not in body_str
        assert "C:\\Users" not in body_str


def test_development_mode_allows_exception_details(error_app: FastAPI):
    """Verify that in development mode, developers get detailed exception strings for debugging."""
    client = TestClient(error_app, raise_server_exceptions=False)

    with patch("app.infrastructure.security.error_handler.is_production_mode", return_value=False):
        res = client.get("/trigger-db-error")

        assert res.status_code == 500
        data = res.json()
        assert "psycopg2.OperationalError" in data["detail"]
        assert "incident_id" in data
        assert data["exception_type"] == "RuntimeError"
