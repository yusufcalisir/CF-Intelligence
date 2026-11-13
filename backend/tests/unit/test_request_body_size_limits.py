"""ASGI-boundary tests for request body size limits and payload abuse mitigation."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.types import Message

from app.main import (
    ContentTypeMiddleware,
    RequestBodySizeLimitMiddleware,
    app,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


async def _invoke_asgi(
    asgi_app: Any,
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]],
    chunks: list[Message],
) -> tuple[int, dict[str, str], bytes, int, bool]:
    """Drive an ASGI app directly with scripted request frames.

    Returns (status_code, response_headers_lower, body_bytes, frames_pulled, drained_all).
    """
    queue: list[Message] = list(chunks)
    pulled = 0

    async def receive() -> Message:
        nonlocal pulled
        if pulled < len(queue):
            msg = queue[pulled]
            pulled += 1
            return msg
        return {"type": "http.disconnect"}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    await asgi_app(scope, receive, send)

    status_code = 0
    resp_headers: dict[str, str] = {}
    body_parts: list[bytes] = []
    for msg in sent:
        if msg["type"] == "http.response.start":
            status_code = int(msg["status"])
            resp_headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in msg.get("headers", [])
            }
        elif msg["type"] == "http.response.body":
            body_parts.append(msg.get("body", b""))

    return status_code, resp_headers, b"".join(body_parts), pulled, pulled == len(queue)


def test_middleware_installed_outermost_at_intake_boundary() -> None:
    """RequestBodySizeLimitMiddleware executes before auth, rate-limit, content-type, and parsing."""
    middleware_classes = [mw.cls for mw in app.user_middleware]
    assert RequestBodySizeLimitMiddleware in middleware_classes

    # Starlette user_middleware[0] is outermost at execution time (reverse of add_middleware order).
    # Only non-body-consuming outbound decorators may wrap outside the body size intake guard.
    limit_idx = middleware_classes.index(RequestBodySizeLimitMiddleware)
    outer_classes = middleware_classes[:limit_idx]
    assert all(
        cls.__name__ in {"PrometheusInstrumentatorMiddleware", "SecurityHeadersMiddleware"}
        for cls in outer_classes
    ), f"Unexpected middleware wrapping outside RequestBodySizeLimitMiddleware: {outer_classes}"

    content_type_idx = middleware_classes.index(ContentTypeMiddleware)
    assert limit_idx < content_type_idx, (
        "RequestBodySizeLimitMiddleware must execute before ContentTypeMiddleware"
    )
    for idx, cls in enumerate(middleware_classes):
        if dev in {
            "TenantAccessControlMiddleware",
            "DDoSProtectionMiddleware",
            "MTLSVerificationMiddleware",
            "APIVersionLifecycleMiddleware",
            "W3CTraceContextMiddleware",
            "ContentTypeMiddleware",
            "CORSMiddleware",
        }:
            assert limit_idx < idx, (
                f"RequestBodySizeLimitMiddleware (idx={limit_idx}) must run before {cls.__name__} (idx={idx})"
            )


def test_oversized_request_rejected_before_tenant_auth_or_content_type(client: TestClient) -> None:
    """Unauthenticated oversized request to tenant-protected endpoint fails with 413, not 401/415."""
    oversized_length = 11 * 1024 * 1024
    response = client.post(
        "/api/v1/alerts",
        headers={
            "Content-Type": "text/plain",
            "Content-Length": str(oversized_length),
        },
        content=b"oversized-unauth",
    )
    assert response.status_code == 413
    assert response.json()["status"] == 413
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


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
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


@pytest.mark.asyncio
async def test_oversized_content_length_does_not_read_body_or_reach_app() -> None:
    """Content-Length rejection aborts at ASGI intake without polling receive or invoking downstream."""
    downstream_called = False

    async def protected_app(scope, receive, send):
        nonlocal downstream_called
        downstream_called = True

    mw = RequestBodySizeLimitMiddleware(protected_app, max_bytes=128)
    status_code, _, body, pulled, _ = await _invoke_asgi(
        mw,
        method="POST",
        path="/api/v1/alerts",
        headers=[(b"content-length", b"1024"), (b"content-type", b"application/json")],
        chunks=[{"type": "http.request", "body": b'{"a":1}', "more_body": False}],
    )
    assert status_code == 413
    assert downstream_called is False
    assert pulled == 0
    assert json.loads(body)["status"] == 413


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_headers",
    [
        [(b"content-length", b"-1")],
        [(b"content-length", b"abc")],
        [(b"content-length", b"10"), (b"content-length", b"50000000")],
        [(b"content-length", b"10, 50000000")],
    ],
)
async def test_malformed_or_conflicting_content_length_fails_closed(
    bad_headers: list[tuple[bytes, bytes]],
) -> None:
    """Negative, non-ASCII/non-numeric, or conflicting Content-Length headers fail closed with 400."""
    downstream_called = False

    async def protected_app(scope, receive, send):
        nonlocal downstream_called
        downstream_called = True

    mw = RequestBodySizeLimitMiddleware(protected_app, max_bytes=1024)
    status_code, _, body, pulled, _ = await _invoke_asgi(
        mw,
        method="POST",
        path="/api/v1/auth/login",
        headers=bad_headers,
        chunks=[{"type": "http.request", "body": b"{}", "more_body": False}],
    )
    assert status_code == 400
    assert downstream_called is False
    assert pulled == 0
    assert json.loads(body)["status"] == 400


def test_multipart_payload_rejected_by_content_type_middleware(client: TestClient) -> None:
    """Non-JSON small multipart payload to mutating JSON endpoint is rejected with HTTP 415."""
    headers = {
        "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundary7MA4YWxkTrZu0gW",
    }
    body = b"------WebKitFormBoundary7MA4YWxkTrZu0gW\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\ntest\r\n------WebKitFormBoundary7MA4YWxkTrZu0gW--\r\n"
    response = client.post("/api/v1/auth/login", headers=headers, content=body)
    assert response.status_code == 415
    data = response.json()
    assert data["status"] == 415
    assert "Unsupported Media Type" in data["title"]


def test_oversized_multipart_payload_rejected_before_content_type_or_parsing(client: TestClient) -> None:
    """Oversized multipart payload fails with 413 before ContentTypeMiddleware (415) or body parsing."""
    oversized_length = 12 * 1024 * 1024
    response = client.post(
        "/api/v1/auth/login",
        headers={
            "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundary7MA4YWxkTrZu0gW",
            "Content-Length": str(oversized_length),
        },
        content=b"------WebKitFormBoundary7MA4YWxkTrZu0gW\r\n",
    )
    assert response.status_code == 413
    assert response.json()["status"] == 413


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


@pytest.mark.asyncio
async def test_production_app_rejects_chunked_stream_without_content_length() -> None:
    """Production app rejects chunked requests omitting Content-Length once cumulative bytes > 10 MB."""
    one_mb = b"a" * (1024 * 1024)
    # 12 chunks * 1 MB = 12 MB > DEFAULT_MAX_BYTES (10 MB); no Content-Length header sent.
    chunks: list[Message] = [
        {"type": "http.request", "body": one_mb, "more_body": i < 11}
        for i in range(12)
    ]
    status_code, resp_headers, body, pulled, drained_all = await _invoke_asgi(
        app,
        method="POST",
        path="/api/v1/auth/login",
        headers=[(b"content-type", b"application/json")],
        chunks=chunks,
    )
    assert status_code == 413
    data = json.loads(body)
    assert data["status"] == 413
    assert "Payload Too Large" in data["title"]
    assert "X-Content-Type-Options".lower() in resp_headers
    # Stream aborted on the 11th chunk (11 MB > 10 MB), never draining the 12th chunk
    assert pulled == 11
    assert drained_all is False


@pytest.mark.asyncio
async def test_production_app_authenticated_route_rejects_chunked_stream_without_content_length() -> None:
    """Authenticated production POST route rejects chunked streams exceeding 10 MB without Content-Length."""
    from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator

    token = OIDCAuthenticator().create_mock_token(
        username="size_test_user",
        bank_id="bank_a",
        roles=["analyst"],
    )
    one_mb = b"x" * (1024 * 1024)
    chunks: list[Message] = [
        {"type": "http.request", "body": one_mb, "more_body": i < 11}
        for i in range(12)
    ]
    status_code, resp_headers, body, pulled, drained_all = await _invoke_asgi(
        app,
        method="POST",
        path="/api/v1/alerts/triage/evaluate",
        headers=[
            (b"content-type", b"application/json"),
            (b"authorization", f"Bearer {token}".encode("latin-1")),
            (b"x-tenant-id", b"bank_a"),
        ],
        chunks=chunks,
    )
    assert status_code == 413
    assert json.loads(body)["status"] == 413
    assert "x-content-type-options" in resp_headers
    assert pulled == 11
    assert drained_all is False


@pytest.mark.asyncio
async def test_exempt_upload_route_with_full_middleware_stack_rejects_chunked_multipart() -> None:
    """Upload-exempt route wrapped with production middleware rejects chunked multipart > limit."""
    from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator
    from app.infrastructure.security.security_headers import SecurityHeadersMiddleware
    from app.main import TenantAccessControlMiddleware

    token = OIDCAuthenticator().create_mock_token(
        username="upload_user",
        bank_id="bank_a",
        roles=["analyst"],
    )
    parser_completed = False
    upload_app = FastAPI()

    @upload_app.post("/api/v1/banks/upload")
    async def upload_handler(request: Request) -> JSONResponse:
        nonlocal parser_completed
        async for _chunk in request.stream():
            pass
        parser_completed = True
        return JSONResponse({"uploaded": True})

    upload_app.add_middleware(ContentTypeMiddleware)
    upload_app.add_middleware(TenantAccessControlMiddleware)
    upload_app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=300)
    upload_app.add_middleware(SecurityHeadersMiddleware)

    boundary = b"----CFIUploadBoundary"
    chunks: list[Message] = [
        {"type": "http.request", "body": b"--" + boundary + b"\r\n" + b"X" * 150, "more_body": True},
        {"type": "http.request", "body": b"Y" * 200, "more_body": True},  # crosses 300-byte limit
        {"type": "http.request", "body": b"\r\n--" + boundary + b"--\r\n", "more_body": False},
    ]
    status_code, resp_headers, body, pulled, drained_all = await _invoke_asgi(
        upload_app,
        method="POST",
        path="/api/v1/banks/upload",
        headers=[
            (b"content-type", b"multipart/form-data; boundary=" + boundary),
            (b"authorization", f"Bearer {token}".encode("latin-1")),
            (b"x-tenant-id", b"bank_a"),
        ],
        chunks=chunks,
    )
    assert status_code == 413
    assert json.loads(body)["status"] == 413
    assert "x-content-type-options" in resp_headers
    assert parser_completed is False
    assert pulled == 2
    assert drained_all is False


@pytest.mark.asyncio
async def test_fragmented_chunks_and_multipart_stream_cannot_bypass_limit() -> None:
    """Chunked, fragmented multipart requests stop immediately at limit without parser completion."""
    parser_completed = False
    parsed_bytes = 0
    fast_app = FastAPI()

    @fast_app.post("/upload")
    async def upload_endpoint(request: Request) -> JSONResponse:
        nonlocal parser_completed, parsed_bytes
        buf = bytearray()
        try:
            async for chunk in request.stream():
                parsed_bytes += len(chunk)
                buf.extend(chunk)
            parser_completed = True
            return JSONResponse({"ok": True, "bytes": len(buf)})
        except Exception:
            # Even if an endpoint uses broad `except Exception`, BaseException abort still unwinds!
            return JSONResponse({"swallowed": True}, status_code=200)

    fast_app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=256)

    boundary = b"----CFIFormBoundary123"
    prefix = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="payload.bin"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
    )
    suffix = b"\r\n--" + boundary + b"--\r\n"
    full_multipart = prefix + (b"Z" * 600) + suffix

    # Fragment the multipart upload into tiny ASGI frames (plus an empty keepalive frame)
    # WITHOUT a Content-Length header.
    fragment_size = 64
    raw_slices = [
        full_multipart[i : i + fragment_size]
        for i in range(0, len(full_multipart), fragment_size)
    ]
    chunks: list[Message] = [{"type": "http.request", "body": b"", "more_body": True}]
    for idx, piece in enumerate(raw_slices):
        chunks.append(
            {
                "type": "http.request",
                "body": piece,
                "more_body": idx < len(raw_slices) - 1,
            }
        )

    status_code, _, body, pulled, drained_all = await _invoke_asgi(
        fast_app,
        method="POST",
        path="/upload",
        headers=[(b"content-type", b"multipart/form-data; boundary=" + boundary)],
        chunks=chunks,
    )
    assert status_code == 413
    assert json.loads(body)["status"] == 413
    assert parser_completed is False
    # Cumulative buffered bytes downstream strictly never exceeded max_bytes (256)
    assert parsed_bytes <= 256
    # Must stop as soon as cumulative bytes exceed 256 bytes (keepalive + 5 * 64 = 320 bytes -> 6 frames)
    assert pulled == 6
    assert drained_all is False


@pytest.mark.asyncio
async def test_spoofed_small_content_length_cannot_bypass_streaming_limit() -> None:
    """Spoofed small Content-Length with an oversized chunked/fragmented multipart body is rejected."""
    parser_completed = False
    parsed_bytes = 0
    fast_app = FastAPI()

    @fast_app.post("/upload")
    async def upload_endpoint(request: Request) -> JSONResponse:
        nonlocal parser_completed, parsed_bytes
        async for chunk in request.stream():
            parsed_bytes += len(chunk)
        parser_completed = True
        return JSONResponse({"ok": True})

    fast_app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=120)

    chunks: list[Message] = [
        {"type": "http.request", "body": b"A" * 60, "more_body": True},
        {"type": "http.request", "body": b"B" * 60, "more_body": True},
        {"type": "http.request", "body": b"C" * 60, "more_body": True},  # 180 > 120
        {"type": "http.request", "body": b"D" * 60, "more_body": False},
    ]
    status_code, _, body, pulled, drained_all = await _invoke_asgi(
        fast_app,
        method="POST",
        path="/upload",
        headers=[
            (b"content-length", b"10"),
            (b"content-type", b"multipart/form-data; boundary=abc"),
        ],
        chunks=chunks,
    )
    assert status_code == 413
    assert json.loads(body)["status"] == 413
    assert parser_completed is False
    assert parsed_bytes <= 120
    assert pulled == 3
    assert drained_all is False


@pytest.mark.asyncio
async def test_fragmented_body_within_limit_succeeds() -> None:
    """Fragmented ASGI http.request frames totaling <= max_bytes pass cleanly to downstream app."""
    fast_app = FastAPI()

    @fast_app.post("/echo-len")
    async def echo_len(request: Request) -> JSONResponse:
        data = await request.body()
        return JSONResponse({"size": len(data)})

    fast_app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=100)

    chunks: list[Message] = [
        {"type": "http.request", "body": b"", "more_body": True},
        {"type": "http.request", "body": b"x" * 30, "more_body": True},
        {"type": "http.request", "body": b"y" * 40, "more_body": True},
        {"type": "http.request", "body": b"z" * 30, "more_body": False},  # exact 100 bytes
    ]
    status_code, _, body, pulled, drained_all = await _invoke_asgi(
        fast_app,
        method="POST",
        path="/echo-len",
        headers=[(b"content-type", b"application/octet-stream")],
        chunks=chunks,
    )
    assert status_code == 200
    assert json.loads(body) == {"size": 100}
    assert pulled == 4
    assert drained_all is True


def test_custom_size_limit_via_middleware_constructor() -> None:
    """RequestBodySizeLimitMiddleware respects custom max_bytes configuration."""
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import PlainTextResponse

    async def dummy_app(scope, receive, send):
        request = StarletteRequest(scope, receive)
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


@pytest.mark.asyncio
async def test_production_app_allows_within_limit_json_post_through_asgi_stack() -> None:
    """Within-limit JSON POST passes through RequestBodySizeLimitMiddleware to real production app."""
    payload = b'{"username": "nonexistent_user", "password": "wrong_password"}'
    chunks: list[Message] = [
        {"type": "http.request", "body": payload[:20], "more_body": True},
        {"type": "http.request", "body": payload[20:], "more_body": False},
    ]
    status_code, resp_headers, _, _, drained_all = await _invoke_asgi(
        app,
        method="POST",
        path="/api/v1/auth/login",
        headers=[
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ],
        chunks=chunks,
    )
    assert status_code not in {400, 413}
    assert status_code == 401
    assert drained_all is True
    assert resp_headers.get("x-content-type-options") == "nosniff"


@pytest.mark.asyncio
async def test_production_app_rejects_spoofed_small_content_length_stream() -> None:
    """Real production app rejects oversized streamed body even when Content-Length claims 32 bytes."""
    chunk_size = 512 * 1024
    num_chunks = 22  # 11 MB > 10 MB limit
    chunks: list[Message] = [
        {"type": "http.request", "body": b"z" * chunk_size, "more_body": i < num_chunks - 1}
        for i in range(num_chunks)
    ]
    status_code, resp_headers, body, pulled, drained_all = await _invoke_asgi(
        app,
        method="POST",
        path="/api/v1/auth/login",
        headers=[
            (b"content-type", b"application/json"),
            (b"content-length", b"32"),
        ],
        chunks=chunks,
    )
    assert status_code == 413
    assert pulled == 21  # Aborts on chunk #21 (10.5 MB > 10 MB) without draining chunk #22
    assert drained_all is False
    assert json.loads(body)["status"] == 413
    assert resp_headers.get("x-content-type-options") == "nosniff"


