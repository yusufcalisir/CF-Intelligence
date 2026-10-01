from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from app.main import APIVersionLifecycleMiddleware, app


@pytest.mark.asyncio
async def test_api_version_non_http_scope_passthrough():
    """Verify non-HTTP scopes (websocket, lifespan) delegate directly without intervention."""
    call_log: list[str] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        call_log.append(scope["type"])

    mw = APIVersionLifecycleMiddleware(mock_app)
    scope_ws: Scope = {"type": "websocket", "path": "/ws/alerts"}
    await mw(scope_ws, None, None)  # type: ignore[arg-type]
    assert call_log == ["websocket"]

    call_log.clear()
    scope_lifespan: Scope = {"type": "lifespan"}
    await mw(scope_lifespan, None, None)  # type: ignore[arg-type]
    assert call_log == ["lifespan"]


@pytest.mark.asyncio
async def test_api_version_normal_200_json_response():
    """Verify normal HTTP 200 JSON response receives X-API-Version header while preserving downstream headers."""
    messages_sent: list[Message] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-custom-metric", b"42"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"status":"ok"}',
            "more_body": False,
        })

    async def mock_send(message: Message) -> None:
        messages_sent.append(message)

    mw = APIVersionLifecycleMiddleware(mock_app)
    scope: Scope = {"type": "http", "method": "GET", "path": "/api/v1/ping"}
    await mw(scope, None, mock_send)  # type: ignore[arg-type]

    assert len(messages_sent) == 2
    start_msg = messages_sent[0]
    assert start_msg["type"] == "http.response.start"
    assert start_msg["status"] == 200

    headers_dict = {k.lower(): v for k, v in start_msg["headers"]}
    assert headers_dict[b"x-api-version"] == b"v1"
    assert headers_dict[b"content-type"] == b"application/json"
    assert headers_dict[b"x-custom-metric"] == b"42"

    body_msg = messages_sent[1]
    assert body_msg["type"] == "http.response.body"
    assert body_msg["body"] == b'{"status":"ok"}'


@pytest.mark.asyncio
async def test_api_version_4xx_downstream_response():
    """Verify downstream 4xx (e.g. 404 Not Found) receives X-API-Version header."""
    messages_sent: list[Message] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 404,
            "headers": [(b"content-type", b"application/problem+json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"detail":"Not Found"}',
            "more_body": False,
        })

    async def mock_send(message: Message) -> None:
        messages_sent.append(message)

    mw = APIVersionLifecycleMiddleware(mock_app)
    scope: Scope = {"type": "http", "method": "GET", "path": "/api/v1/nonexistent"}
    await mw(scope, None, mock_send)  # type: ignore[arg-type]

    start_msg = messages_sent[0]
    headers_dict = {k.lower(): v for k, v in start_msg["headers"]}
    assert headers_dict[b"x-api-version"] == b"v1"
    assert start_msg["status"] == 404


@pytest.mark.asyncio
async def test_api_version_5xx_exception_propagation():
    """Verify unhandled downstream exceptions bubble up cleanly without swallowing."""
    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("Simulated unhandled downstream crash")

    mw = APIVersionLifecycleMiddleware(mock_app)
    scope: Scope = {"type": "http", "method": "GET", "path": "/api/v1/crash"}

    with pytest.raises(RuntimeError, match="Simulated unhandled downstream crash"):
        await mw(scope, None, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_api_version_overrides_existing_header():
    """Verify existing X-API-Version from downstream is replaced with authoritative platform version."""
    messages_sent: list[Message] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"x-api-version", b"v0-legacy"),
                (b"X-API-VERSION", b"v0-dupe"),
                (b"content-type", b"application/json"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": b"{}",
            "more_body": False,
        })

    async def mock_send(message: Message) -> None:
        messages_sent.append(message)

    mw = APIVersionLifecycleMiddleware(mock_app)
    scope: Scope = {"type": "http", "method": "GET", "path": "/api/v1/test"}
    await mw(scope, None, mock_send)  # type: ignore[arg-type]

    start_msg = messages_sent[0]
    # Verify exactly one x-api-version header remains and its value is b"v1"
    api_version_headers = [v for k, v in start_msg["headers"] if k.lower() == b"x-api-version"]
    assert len(api_version_headers) == 1
    assert api_version_headers[0] == b"v1"


@pytest.mark.asyncio
async def test_api_version_multiple_response_headers_and_empty_body():
    """Verify multiple headers are preserved and empty body passes through intact."""
    messages_sent: list[Message] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 204,
            "headers": [
                (b"x-frame-options", b"DENY"),
                (b"x-content-type-options", b"nosniff"),
                (b"referrer-policy", b"strict-origin-when-cross-origin"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": b"",
            "more_body": False,
        })

    async def mock_send(message: Message) -> None:
        messages_sent.append(message)

    mw = APIVersionLifecycleMiddleware(mock_app)
    scope: Scope = {"type": "http", "method": "DELETE", "path": "/api/v1/items/1"}
    await mw(scope, None, mock_send)  # type: ignore[arg-type]

    start_msg = messages_sent[0]
    assert start_msg["status"] == 204
    headers_dict = {k.lower(): v for k, v in start_msg["headers"]}
    assert headers_dict[b"x-api-version"] == b"v1"
    assert headers_dict[b"x-frame-options"] == b"DENY"
    assert headers_dict[b"x-content-type-options"] == b"nosniff"

    body_msg = messages_sent[1]
    assert body_msg["body"] == b""


@pytest.mark.asyncio
async def test_api_version_deprecation_sunset_injection(monkeypatch: pytest.MonkeyPatch):
    """Verify RFC 8594 Deprecation and Sunset headers are injected when configured."""
    monkeypatch.setattr(APIVersionLifecycleMiddleware, "_DEPRECATION_DATE", "Sun, 01 Mar 2026 00:00:00 GMT")
    monkeypatch.setattr(APIVersionLifecycleMiddleware, "_SUNSET_DATE", "Tue, 01 Sep 2026 00:00:00 GMT")

    messages_sent: list[Message] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b"{}",
            "more_body": False,
        })

    async def mock_send(message: Message) -> None:
        messages_sent.append(message)

    mw = APIVersionLifecycleMiddleware(mock_app)
    scope: Scope = {"type": "http", "method": "GET", "path": "/api/v1/deprecated-endpoint"}
    await mw(scope, None, mock_send)  # type: ignore[arg-type]

    start_msg = messages_sent[0]
    headers_dict = {k.lower(): v for k, v in start_msg["headers"]}
    assert headers_dict[b"x-api-version"] == b"v1"
    assert headers_dict[b"deprecation"] == b"Sun, 01 Mar 2026 00:00:00 GMT"
    assert headers_dict[b"sunset"] == b"Tue, 01 Sep 2026 00:00:00 GMT"


def test_api_version_live_endpoint_golden_matrix():
    """Verify live FastAPI application returns expected headers across standard endpoints."""
    client = TestClient(app)

    # 1. GET /health
    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert r_health.headers.get("x-api-version") == "v1"
    assert "traceparent" in r_health.headers
    assert r_health.headers.get("x-content-type-options") == "nosniff"

    # 2. POST /api/v1/score-transaction with valid transaction payload
    payload = {
        "transaction_id": "tx_api_version_golden_1",
        "account_id": "acc_golden_1",
        "amount": 250.0,
        "currency": "EUR",
        "merchant_id": "merch_golden_1",
        "country": "US",
        "device_id": "dev_golden_1",
    }
    r_score = client.post(
        "/api/v1/score-transaction",
        json=payload,
        headers={"X-Bank-ID": "bank_alpha", "Content-Type": "application/json"},
    )
    assert r_score.status_code == 200, f"Expected 200, got {r_score.status_code}: {r_score.text}"
    assert r_score.headers.get("x-api-version") == "v1"
    assert "traceparent" in r_score.headers
    assert r_score.headers.get("content-type") == "application/json"
    assert r_score.headers.get("x-content-type-options") == "nosniff"
    data = r_score.json()
    assert "risk_score" in data
    assert "decision" in data
