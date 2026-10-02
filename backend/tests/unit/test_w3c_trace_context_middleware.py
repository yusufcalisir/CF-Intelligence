"""Unit tests for W3CTraceContextMiddleware (Pure ASGI).

Verifies strict semantic equivalence after converting from Starlette BaseHTTPMiddleware
to pure ASGI:
- W3C traceparent preservation for valid incoming headers
- Automatic traceparent generation when missing or malformed
- Starlette duplicate incoming header compatibility (first-match extraction)
- Outgoing response header overwrite behavior and casing normalization
- HTTP 200, 4xx, 5xx, 204, HEAD, and StreamingResponse handling
- Downstream unhandled exception propagation
- Non-HTTP ASGI scope passthrough (websocket, lifespan)
- Strict request-isolation under concurrent execution
- Zero incoming request header mutation
- Live FastAPI application integration & middleware ordering
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from app.main import W3CTraceContextMiddleware, app


async def dummy_receive() -> Message:
    await asyncio.sleep(60)
    return {"type": "http.disconnect"}


async def dummy_send(message: Message) -> None:
    pass


def make_scope(
    headers: list[tuple[bytes, bytes]],
    method: str = "GET",
    path: str = "/test",
) -> Scope:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
    }


async def run_middleware(
    mw: W3CTraceContextMiddleware,
    scope: Scope,
) -> tuple[int, dict[bytes, bytes], list[bytes]]:
    status_code = 0
    headers: dict[bytes, bytes] = {}
    body_chunks: list[bytes] = []

    async def send(message: Message) -> None:
        nonlocal status_code, headers
        if message["type"] == "http.response.start":
            status_code = message["status"]
            for k, v in message.get("headers", []):
                headers[k.lower()] = v
        elif message["type"] == "http.response.body":
            body_chunks.append(message.get("body", b""))

    await mw(scope, dummy_receive, send)
    return status_code, headers, body_chunks


@pytest.mark.asyncio
async def test_w3c_preserves_valid_incoming_traceparent():
    """Verify syntactically valid W3C traceparent header is preserved exactly."""
    valid_tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        resp = Response("ok", status_code=200)
        await resp(scope, receive, send)

    mw = W3CTraceContextMiddleware(mock_app)
    scope = make_scope([(b"traceparent", valid_tp.encode("latin-1"))])
    status, headers, _ = await run_middleware(mw, scope)

    assert status == 200
    assert headers.get(b"traceparent") == valid_tp.encode("latin-1")


@pytest.mark.asyncio
async def test_w3c_generates_when_traceparent_missing():
    """Verify valid W3C traceparent (00-{32hex}-{16hex}-01) is generated when missing."""
    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        resp = Response("ok", status_code=200)
        await resp(scope, receive, send)

    mw = W3CTraceContextMiddleware(mock_app)
    scope = make_scope([])
    status, headers, _ = await run_middleware(mw, scope)

    assert status == 200
    tp = headers.get(b"traceparent", b"").decode("latin-1")
    assert tp.startswith("00-")
    parts = tp.split("-")
    assert len(parts) == 4
    assert len(parts[1]) == 32
    assert len(parts[2]) == 16
    assert parts[3] == "01"


@pytest.mark.asyncio
async def test_w3c_replaces_malformed_traceparent():
    """Verify malformed traceparent headers are replaced with newly generated valid headers."""
    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        resp = Response("ok", status_code=200)
        await resp(scope, receive, send)

    mw = W3CTraceContextMiddleware(mock_app)

    # 1. Invalid garbage
    scope_garbage = make_scope([(b"traceparent", b"invalid-garbage-value")])
    _, h_garbage, _ = await run_middleware(mw, scope_garbage)
    tp_garbage = h_garbage.get(b"traceparent", b"").decode("latin-1")
    assert tp_garbage != "invalid-garbage-value"
    assert tp_garbage.startswith("00-")
    assert len(tp_garbage.split("-")) == 4

    # 2. Wrong version
    scope_v1 = make_scope([(b"traceparent", b"01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")])
    _, h_v1, _ = await run_middleware(mw, scope_v1)
    tp_v1 = h_v1.get(b"traceparent", b"").decode("latin-1")
    assert tp_v1.startswith("00-")


@pytest.mark.asyncio
async def test_w3c_preserves_existing_defects_consistently():
    """Verify existing validation behavior (all-zero, uppercase, non-hex 4-part) remains unchanged."""
    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        resp = Response("ok", status_code=200)
        await resp(scope, receive, send)

    mw = W3CTraceContextMiddleware(mock_app)

    # All-zero trace ID (existing defect preserved)
    zero_tp = "00-00000000000000000000000000000000-00f067aa0ba902b7-01"
    _, h_zero, _ = await run_middleware(mw, make_scope([(b"traceparent", zero_tp.encode())]))
    assert h_zero.get(b"traceparent", b"").decode() == zero_tp

    # Uppercase hex (existing defect preserved)
    upper_tp = "00-4BF92F3577B34DA6A3CE929D0E0E4736-00F067AA0BA902B7-01"
    _, h_upper, _ = await run_middleware(mw, make_scope([(b"traceparent", upper_tp.encode())]))
    assert h_upper.get(b"traceparent", b"").decode() == upper_tp


@pytest.mark.asyncio
async def test_w3c_duplicate_incoming_headers():
    """Verify first-match extraction on duplicate incoming headers matching Starlette semantics."""
    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        resp = Response("ok", status_code=200)
        await resp(scope, receive, send)

    mw = W3CTraceContextMiddleware(mock_app)

    # Valid + Valid -> First wins
    scope_vv = make_scope([
        (b"traceparent", b"00-1111-2222-01"),
        (b"traceparent", b"00-3333-4444-01"),
    ])
    _, h_vv, _ = await run_middleware(mw, scope_vv)
    assert h_vv.get(b"traceparent") == b"00-1111-2222-01"

    # Valid + Invalid -> First wins (valid preserved)
    scope_vi = make_scope([
        (b"traceparent", b"00-1111-2222-01"),
        (b"traceparent", b"invalid"),
    ])
    _, h_vi, _ = await run_middleware(mw, scope_vi)
    assert h_vi.get(b"traceparent") == b"00-1111-2222-01"

    # Invalid + Valid -> First wins (invalid causes new generation)
    scope_iv = make_scope([
        (b"traceparent", b"invalid"),
        (b"traceparent", b"00-1111-2222-01"),
    ])
    _, h_iv, _ = await run_middleware(mw, scope_iv)
    tp_iv = h_iv.get(b"traceparent", b"").decode()
    assert tp_iv.startswith("00-")
    assert tp_iv != "00-1111-2222-01"


@pytest.mark.asyncio
async def test_w3c_overwrites_downstream_response_traceparent():
    """Verify middleware unconditionally overwrites any pre-existing downstream traceparent response header."""
    valid_tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        resp = Response("ok", status_code=200)
        resp.headers["traceparent"] = "00-customdownstream12345678-customspan01-01"
        await resp(scope, receive, send)

    mw = W3CTraceContextMiddleware(mock_app)
    scope = make_scope([(b"traceparent", valid_tp.encode())])
    _, headers, _ = await run_middleware(mw, scope)

    assert headers.get(b"traceparent") == valid_tp.encode()


@pytest.mark.asyncio
async def test_w3c_handles_various_status_codes_and_verbs():
    """Verify traceparent is attached across 200, 4xx, 5xx, 204, and HEAD responses."""
    valid_tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    # 404 response
    async def app_404(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)
    mw_404 = W3CTraceContextMiddleware(app_404)
    s_404, h_404, _ = await run_middleware(mw_404, make_scope([(b"traceparent", valid_tp.encode())]))
    assert s_404 == 404
    assert h_404.get(b"traceparent") == valid_tp.encode()

    # 500 response
    async def app_500(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse({"error": "internal error"}, status_code=500)(scope, receive, send)
    mw_500 = W3CTraceContextMiddleware(app_500)
    s_500, h_500, _ = await run_middleware(mw_500, make_scope([(b"traceparent", valid_tp.encode())]))
    assert s_500 == 500
    assert h_500.get(b"traceparent") == valid_tp.encode()

    # 204 No Content
    async def app_204(scope: Scope, receive: Receive, send: Send) -> None:
        await Response(status_code=204)(scope, receive, send)
    mw_204 = W3CTraceContextMiddleware(app_204)
    s_204, h_204, _ = await run_middleware(mw_204, make_scope([(b"traceparent", valid_tp.encode())]))
    assert s_204 == 204
    assert h_204.get(b"traceparent") == valid_tp.encode()

    # HEAD request
    async def app_head(scope: Scope, receive: Receive, send: Send) -> None:
        await Response("ok", status_code=200)(scope, receive, send)
    mw_head = W3CTraceContextMiddleware(app_head)
    s_head, h_head, _ = await run_middleware(mw_head, make_scope([(b"traceparent", valid_tp.encode())], method="HEAD"))
    assert s_head == 200
    assert h_head.get(b"traceparent") == valid_tp.encode()


@pytest.mark.asyncio
async def test_w3c_exception_propagation():
    """Verify unhandled downstream exceptions bubble up cleanly through the middleware."""
    async def app_raises(scope: Scope, receive: Receive, send: Send) -> None:
        raise ValueError("simulated downstream failure")

    mw = W3CTraceContextMiddleware(app_raises)
    scope = make_scope([])
    with pytest.raises(ValueError, match="simulated downstream failure"):
        await run_middleware(mw, scope)


@pytest.mark.asyncio
async def test_w3c_streaming_response():
    """Verify StreamingResponse receives traceparent header prior to body chunks."""
    valid_tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    async def chunk_gen() -> AsyncGenerator[bytes, None]:
        yield b"chunk1"
        yield b"chunk2"

    async def app_stream(scope: Scope, receive: Receive, send: Send) -> None:
        resp = StreamingResponse(chunk_gen(), media_type="text/plain")
        await resp(scope, receive, send)

    mw = W3CTraceContextMiddleware(app_stream)
    scope = make_scope([(b"traceparent", valid_tp.encode())])
    status, headers, chunks = await run_middleware(mw, scope)

    assert status == 200
    assert headers.get(b"traceparent") == valid_tp.encode()
    assert b"".join(chunks) == b"chunk1chunk2"


@pytest.mark.asyncio
async def test_w3c_non_http_scope_passthrough():
    """Verify non-HTTP scopes (websocket, lifespan) are passed through without modification."""
    call_log: list[str] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        call_log.append(scope["type"])

    mw = W3CTraceContextMiddleware(mock_app)

    await mw({"type": "websocket", "path": "/ws"}, dummy_receive, dummy_send)
    assert call_log == ["websocket"]

    call_log.clear()
    await mw({"type": "lifespan"}, dummy_receive, dummy_send)
    assert call_log == ["lifespan"]


@pytest.mark.asyncio
async def test_w3c_concurrency_isolation():
    """Verify 50 concurrent requests with unique traceparents maintain 100% correlation without leakage."""
    async def delay_app(scope: Scope, receive: Receive, send: Send) -> None:
        await asyncio.sleep(0.005)
        await Response("ok")(scope, receive, send)

    mw = W3CTraceContextMiddleware(delay_app)

    async def make_request_task(i: int) -> tuple[str, str]:
        u_tp = f"00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01"
        scope = make_scope([(b"traceparent", u_tp.encode())])
        _, headers, _ = await run_middleware(mw, scope)
        resp_tp = headers.get(b"traceparent", b"").decode()
        return u_tp, resp_tp

    results = await asyncio.gather(*(make_request_task(i) for i in range(50)))
    for sent_tp, recv_tp in results:
        assert sent_tp == recv_tp, f"Cross-request mismatch: sent {sent_tp} != recv {recv_tp}"


@pytest.mark.asyncio
async def test_w3c_no_incoming_request_header_mutation():
    """Verify middleware does not mutate incoming request headers or scope headers."""
    saw_header_in_app: str | None = None

    async def inspect_app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal saw_header_in_app
        req = Request(scope)
        saw_header_in_app = req.headers.get("traceparent")
        await Response("ok")(scope, receive, send)

    mw = W3CTraceContextMiddleware(inspect_app)
    scope = make_scope([])
    _, headers, _ = await run_middleware(mw, scope)

    # Downstream sees no header
    assert saw_header_in_app is None
    # Outgoing response has generated header
    assert b"traceparent" in headers


def test_w3c_live_endpoint_golden_matrix():
    """Verify live FastAPI application returns expected traceparent across standard endpoints."""
    client = TestClient(app)

    # 1. GET /health
    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert "traceparent" in r_health.headers
    tp_health = r_health.headers["traceparent"]
    assert tp_health.startswith("00-")

    # 2. Custom incoming traceparent to /health
    custom_tp = "00-12345678901234567890123456789012-1234567890123456-01"
    r_custom = client.get("/health", headers={"traceparent": custom_tp})
    assert r_custom.status_code == 200
    assert r_custom.headers["traceparent"] == custom_tp

    # 3. ContentType early 415 rejection also gets traceparent (W3C wraps ContentType)
    r_415 = client.post(
        "/api/v1/score-transaction",
        data="invalid payload",
        headers={"Content-Type": "text/plain"},
    )
    assert r_415.status_code == 415
    assert "traceparent" in r_415.headers
