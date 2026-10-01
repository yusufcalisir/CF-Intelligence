from __future__ import annotations

import json

import pytest
from starlette.types import Message, Receive, Scope, Send

from app.main import ContentTypeMiddleware


@pytest.mark.asyncio
async def test_pure_asgi_non_http_scope_passthrough():
    """Verify non-HTTP scopes (websocket, lifespan) pass through without validation."""
    call_log: list[str] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        call_log.append(scope["type"])

    mw = ContentTypeMiddleware(mock_app)
    scope: Scope = {"type": "websocket", "path": "/ws/alerts"}
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert call_log == ["websocket"]

    call_log.clear()
    scope_lifespan: Scope = {"type": "lifespan"}
    await mw(scope_lifespan, None, None)  # type: ignore[arg-type]
    assert call_log == ["lifespan"]


@pytest.mark.asyncio
async def test_pure_asgi_get_options_passthrough():
    """Verify unmutating methods (GET, OPTIONS) pass through regardless of Content-Type."""
    call_log: list[str] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        call_log.append("app_executed")

    mw = ContentTypeMiddleware(mock_app)
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "headers": [(b"content-type", b"text/plain")],
    }
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert len(call_log) == 1


@pytest.mark.asyncio
async def test_pure_asgi_post_valid_json_passthrough():
    """Verify POST with application/json or parameterized charset passes through."""
    call_log: list[str] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        call_log.append("app_executed")

    mw = ContentTypeMiddleware(mock_app)

    # Standard application/json
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/score-transaction",
        "headers": [(b"content-type", b"application/json")],
    }
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert len(call_log) == 1

    # Parameterized charset
    call_log.clear()
    scope["headers"] = [(b"content-type", b"application/json; charset=utf-8")]
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert len(call_log) == 1


@pytest.mark.asyncio
async def test_pure_asgi_post_missing_content_type_passthrough():
    """Verify POST without Content-Type header passes through (handled downstream by Pydantic)."""
    call_log: list[str] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        call_log.append("app_executed")

    mw = ContentTypeMiddleware(mock_app)
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/score-transaction",
        "headers": [],
    }
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert len(call_log) == 1


@pytest.mark.asyncio
async def test_pure_asgi_post_exempt_prefix_passthrough():
    """Verify exempt prefixes (/docs, /redoc, /openapi.json, /ws/, /api/v1/banks/upload) pass through."""
    call_log: list[str] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        call_log.append("app_executed")

    mw = ContentTypeMiddleware(mock_app)
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/banks/upload",
        "headers": [(b"content-type", b"multipart/form-data; boundary=---")],
    }
    await mw(scope, None, None)  # type: ignore[arg-type]
    assert len(call_log) == 1


@pytest.mark.asyncio
async def test_pure_asgi_mutating_invalid_content_type_returns_415():
    """Verify mutating operations (POST/PUT/PATCH) with non-JSON Content-Type return 415 problem details."""
    call_log: list[str] = []

    async def mock_app(scope: Scope, receive: Receive, send: Send) -> None:
        call_log.append("inner_called")

    mw = ContentTypeMiddleware(mock_app)

    for method in ("POST", "PUT", "PATCH"):
        call_log.clear()
        sent_messages: list[Message] = []

        def make_sender(msg_list: list[Message]):
            async def _send(message: Message) -> None:
                msg_list.append(message)
            return _send

        mock_send = make_sender(sent_messages)

        scope: Scope = {
            "type": "http",
            "method": method,
            "path": "/api/v1/score-transaction",
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }

        async def mock_receive() -> Message:
            return {"type": "http.request", "body": b"data", "more_body": False}

        await mw(scope, mock_receive, mock_send)
        assert len(call_log) == 0
        assert len(sent_messages) == 2
        start_msg = sent_messages[0]
        body_msg = sent_messages[1]

        assert start_msg["type"] == "http.response.start"
        assert start_msg["status"] == 415

        headers_dict = dict(start_msg["headers"])
        assert b"content-type" in headers_dict
        assert b"application/json" in headers_dict[b"content-type"]

        assert body_msg["type"] == "http.response.body"
        payload = json.loads(body_msg["body"].decode("utf-8"))
        assert payload["status"] == 415
        assert payload["title"] == "Unsupported Media Type"
        assert payload["received"] == "text/plain"
        assert payload["instance"] == "/api/v1/score-transaction"
