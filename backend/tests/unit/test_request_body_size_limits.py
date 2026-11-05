"""Unit tests for request body size limits and payload abuse mitigation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import RequestBodySizeLimitMiddleware, app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_request_within_size_limit_accepted(client: TestClient) -> None:
    """Normal payload well within the 10 MB limit is accepted and processed."""
    response = client.get("/health")
    assert response.status_code == 200


def test_request_exceeding_content_length_rejected_with_413(client: TestClient) -> None:
    """Request with Content-Length exceeding 10 MB limit is rejected with HTTP 413 immediately."""
    oversized_length = 15 * 1024 * 1024  # 15 MB
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(oversized_length),
    }
    response = client.post(
        "/api/v1/auth/login",
        headers=headers,
        content=b'{"username":"test"}',
    )
    assert response.status_code == 413
    data = response.json()
    assert data["status"] == 413
    assert "Payload Too Large" in data["title"]
    assert "exceeds maximum allowed limit" in data["detail"]


def test_multipart_payload_rejected_by_content_type_middleware(client: TestClient) -> None:
    """Non-JSON multipart payload to mutating endpoint is rejected with HTTP 415."""
    headers = {
        "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundary7MA4YWxkTrZu0gW",
    }
    body = b"------WebKitFormBoundary7MA4YWxkTrZu0gW\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\ntest\r\n------WebKitFormBoundary7MA4YWxkTrZu0gW--\r\n"
    response = client.post("/api/v1/auth/login", headers=headers, content=body)
    assert response.status_code == 415
    data = response.json()
    assert data["status"] == 415
    assert "Unsupported Media Type" in data["title"]


def test_oversized_payload_on_exempt_route_rejected_with_413(client: TestClient) -> None:
    """Exempt prefix routes (such as /api/v1/banks/upload) still enforce the 10 MB size limit."""
    oversized_length = 20 * 1024 * 1024  # 20 MB
    headers = {
        "Content-Length": str(oversized_length),
    }
    response = client.post(
        "/api/v1/banks/upload",
        headers=headers,
        content=b"oversized data",
    )
    assert response.status_code == 413
    data = response.json()
    assert data["status"] == 413
    assert "Payload Too Large" in data["title"]


def test_custom_size_limit_via_middleware_constructor() -> None:
    """RequestBodySizeLimitMiddleware respects custom max_bytes configuration."""
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse

    async def dummy_app(scope, receive, send):
        request = Request(scope, receive)
        body = await request.body()
        response = PlainTextResponse(f"OK: {len(body)}")
        await response(scope, receive, send)

    middleware = RequestBodySizeLimitMiddleware(dummy_app, max_bytes=100)
    test_client = TestClient(middleware, raise_server_exceptions=False)

    # Within 100 bytes
    ok_resp = test_client.post("/", content=b"a" * 50)
    assert ok_resp.status_code == 200
    assert ok_resp.text == "OK: 50"

    # Exceeding 100 bytes via Content-Length header
    headers = {"Content-Length": "150"}
    too_large_resp = test_client.post("/", headers=headers, content=b"a" * 50)
    assert too_large_resp.status_code == 413
    assert "Payload Too Large" in too_large_resp.json()["title"]

