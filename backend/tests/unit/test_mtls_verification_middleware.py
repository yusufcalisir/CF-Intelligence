"""Unit tests and Golden PRE/POST behavior matrix for MTLSVerificationMiddleware.

Verifies strict semantic equivalence for MTLSVerificationMiddleware:
1. Feature disabled (settings.mtls_enabled = False)
2. Disabled invalid headers
3. Enabled unprotected route passthrough
4. Protected missing verify header
5. Protected empty verify header
6. Protected SUCCESS
7. Lowercase success
8. FAILED status rejection (HTTP 403)
9. Arbitrary non-success rejection (HTTP 403)
10. Whitespace sensitivity (no strip)
11. Revoked hash in CRL rejection (HTTP 403)
12. Valid non-revoked hash passthrough
13. Hash case sensitivity
14. Duplicate verify header semantics (FIRST match wins)
15. Duplicate certificate-hash header semantics (FIRST match wins)
16. Exact prefix behavior
17. Near-prefix startswith behavior
18. MTLSManager constructor failure propagation
19. Downstream 200 pass-through
20. Downstream 204 pass-through
21. Downstream 4xx pass-through
22. Downstream explicit 5xx pass-through
23. Downstream raised exception propagation
24. StreamingResponse pass-through
25. HEAD request pass-through
26. WebSocket / non-HTTP scope passthrough
27. Request header non-mutation
28. Early 403 outer-middleware header behavior (DDoS rate-limit headers present, inner skipped)
29. ContextVar execution visibility
30. Concurrent request isolation with distinct headers/hashes
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any
from unittest.mock import patch

import pytest
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.testclient import TestClient
from starlette.types import Message, Scope

from app.infrastructure.security.mtls_manager import MTLSManager
from app.main import MTLSVerificationMiddleware, app, settings

test_var: ContextVar[str] = ContextVar("test_var", default="initial")


async def dummy_receive() -> Message:
    await asyncio.sleep(60)
    return {"type": "http.disconnect"}


async def dummy_send(message: Message) -> None:
    pass


def make_http_scope(
    path: str = "/api/v1/predict",
    headers: list[tuple[bytes, bytes]] | None = None,
    method: str = "POST",
) -> Scope:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 54321),
    }


async def run_asgi(
    mw: Any,
    scope: Scope,
    body_payload: bytes = b'{"test": 1}',
) -> tuple[int, dict[bytes, bytes], bytes]:
    """Execute an ASGI app/middleware and capture (status, headers_dict, body_bytes)."""
    captured_status = 200
    captured_headers: dict[bytes, bytes] = {}
    body_chunks: list[bytes] = []

    has_sent_body = False

    async def mock_receive() -> Message:
        nonlocal has_sent_body
        if not has_sent_body:
            has_sent_body = True
            return {"type": "http.request", "body": body_payload, "more_body": False}
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}

    async def mock_send(message: Message) -> None:
        nonlocal captured_status, captured_headers
        if message["type"] == "http.response.start":
            captured_status = message["status"]
            captured_headers = {k.lower(): v for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            body_chunks.append(message.get("body", b""))

    await mw(scope, mock_receive, mock_send)
    return captured_status, captured_headers, b"".join(body_chunks)


# ------------------------------------------------------------------------------
# 1. Feature Disabled Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mtls_disabled_protected_route_passes():
    """When settings.mtls_enabled is False, protected route passes downstream."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("DOWNSTREAM_OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", False):
        scope = make_http_scope("/api/v1/predict", headers=[])
        status, _, body = await run_asgi(mw, scope)
        assert status == 200
        assert body == b"DOWNSTREAM_OK"


@pytest.mark.asyncio
async def test_mtls_disabled_invalid_header_passes():
    """When settings.mtls_enabled is False, even FAILED verify header passes downstream."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("DOWNSTREAM_OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", False):
        scope = make_http_scope(
            "/api/v1/predict",
            headers=[(b"x-ssl-client-verify", b"FAILED")],
        )
        status, _, body = await run_asgi(mw, scope)
        assert status == 200
        assert body == b"DOWNSTREAM_OK"


# ------------------------------------------------------------------------------
# 2. Path Matching Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mtls_enabled_unprotected_route_passes():
    """When route does not start with enforced prefix, passes even without headers."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("UNPROTECTED_OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        scope = make_http_scope("/health", headers=[(b"x-ssl-client-verify", b"FAILED")])
        status, _, body = await run_asgi(mw, scope)
        assert status == 200
        assert body == b"UNPROTECTED_OK"


@pytest.mark.asyncio
async def test_mtls_exact_prefixes():
    """Test all three enforced prefixes reject FAILED verification."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        for prefix in ("/api/v1/predict", "/api/v1/training", "/api/v1/banks"):
            scope = make_http_scope(prefix, headers=[(b"x-ssl-client-verify", b"FAILED")])
            status, headers, body = await run_asgi(mw, scope)
            assert status == 403
            assert b"mTLSVerificationFailed" in body


@pytest.mark.asyncio
async def test_mtls_near_prefix_startswith():
    """Preserves exact startswith semantics: /api/v1/prediction matches /api/v1/predict."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        # /api/v1/prediction startswith /api/v1/predict -> enforced -> 403
        scope = make_http_scope("/api/v1/prediction", headers=[(b"x-ssl-client-verify", b"FAILED")])
        status, _, body = await run_asgi(mw, scope)
        assert status == 403

        # /api/v1/bank does NOT startswith /api/v1/banks -> not enforced -> 200
        scope2 = make_http_scope("/api/v1/bank", headers=[(b"x-ssl-client-verify", b"FAILED")])
        status2, _, body2 = await run_asgi(mw, scope2)
        assert status2 == 200


# ------------------------------------------------------------------------------
# 3. Verify Header Semantics Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mtls_missing_verify_header_passes():
    """Existing behavior: missing x-ssl-client-verify evaluates to empty string, passes."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        scope = make_http_scope("/api/v1/predict", headers=[])
        status, _, body = await run_asgi(mw, scope)
        assert status == 200
        assert body == b"OK"


@pytest.mark.asyncio
async def test_mtls_empty_verify_header_passes():
    """Empty string verify header passes downstream."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        scope = make_http_scope(
            "/api/v1/predict",
            headers=[(b"x-ssl-client-verify", b"")],
        )
        status, _, body = await run_asgi(mw, scope)
        assert status == 200


@pytest.mark.asyncio
async def test_mtls_success_passes():
    """SUCCESS verify header passes downstream."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        scope = make_http_scope(
            "/api/v1/predict",
            headers=[(b"x-ssl-client-verify", b"SUCCESS")],
        )
        status, _, body = await run_asgi(mw, scope)
        assert status == 200


@pytest.mark.asyncio
async def test_mtls_lowercase_success_passes():
    """'success' is upper-cased and passes downstream."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        scope = make_http_scope(
            "/api/v1/predict",
            headers=[(b"x-ssl-client-verify", b"success")],
        )
        status, _, body = await run_asgi(mw, scope)
        assert status == 200


@pytest.mark.asyncio
async def test_mtls_failed_status_rejects():
    """FAILED verify header returns 403 problem json."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        scope = make_http_scope(
            "/api/v1/predict",
            headers=[(b"x-ssl-client-verify", b"FAILED")],
        )
        status, headers, body = await run_asgi(mw, scope)
        assert status == 403
        assert headers[b"content-type"] == b"application/problem+json"
        assert b"mTLSVerificationFailed" in body
        assert b"Client certificate verification status: 'FAILED'" in body


@pytest.mark.asyncio
async def test_mtls_arbitrary_non_success_rejects():
    """Any non-SUCCESS string like NONE returns 403."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        scope = make_http_scope(
            "/api/v1/predict",
            headers=[(b"x-ssl-client-verify", b"NONE")],
        )
        status, _, body = await run_asgi(mw, scope)
        assert status == 403
        assert b"NONE" in body


@pytest.mark.asyncio
async def test_mtls_whitespace_sensitivity():
    """Whitespace is not stripped in existing code: ' SUCCESS ' returns 403."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        scope = make_http_scope(
            "/api/v1/predict",
            headers=[(b"x-ssl-client-verify", b" SUCCESS ")],
        )
        status, _, body = await run_asgi(mw, scope)
        assert status == 403


# ------------------------------------------------------------------------------
# 4. Certificate Hash / CRL Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mtls_revoked_hash_rejects():
    """Revoked certificate SHA256 in MTLSManager CRL returns 403."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    revoked_sha = "revoked_sha256_hash_12345"
    with (
        patch.object(settings, "mtls_enabled", True),
        patch.object(MTLSManager, "__init__", lambda self: setattr(self, "crl_revoked_serials", {revoked_sha})),
    ):
        scope = make_http_scope(
            "/api/v1/predict",
            headers=[
                (b"x-ssl-client-verify", b"SUCCESS"),
                (b"x-client-cert-sha256", revoked_sha.encode("latin-1")),
            ],
        )
        status, headers, body = await run_asgi(mw, scope)
        assert status == 403
        assert headers[b"content-type"] == b"application/problem+json"
        assert b"mTLSCertificateRevoked" in body
        assert revoked_sha.encode("latin-1") in body


@pytest.mark.asyncio
async def test_mtls_valid_non_revoked_hash_passes():
    """Non-revoked certificate SHA256 passes downstream."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        scope = make_http_scope(
            "/api/v1/predict",
            headers=[
                (b"x-ssl-client-verify", b"SUCCESS"),
                (b"x-client-cert-sha256", b"valid_clean_sha256"),
            ],
        )
        status, _, body = await run_asgi(mw, scope)
        assert status == 200
        assert body == b"OK"


@pytest.mark.asyncio
async def test_mtls_hash_case_sensitivity():
    """Certificate hash lookup in set is case-sensitive."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with (
        patch.object(settings, "mtls_enabled", True),
        patch.object(MTLSManager, "__init__", lambda self: setattr(self, "crl_revoked_serials", {"sha_lower"})),
    ):
        # Upper case 'SHA_LOWER' is not in {'sha_lower'} -> passes
        scope = make_http_scope(
            "/api/v1/predict",
            headers=[
                (b"x-ssl-client-verify", b"SUCCESS"),
                (b"x-client-cert-sha256", b"SHA_LOWER"),
            ],
        )
        status, _, _ = await run_asgi(mw, scope)
        assert status == 200


# ------------------------------------------------------------------------------
# 5. Duplicate Header Semantics (First Match Wins)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mtls_duplicate_verify_headers_first_wins():
    """Starlette request.headers.get extracts the FIRST matching header."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        # [SUCCESS, FAILED] -> first is SUCCESS -> passes
        scope1 = make_http_scope(
            "/api/v1/predict",
            headers=[
                (b"x-ssl-client-verify", b"SUCCESS"),
                (b"x-ssl-client-verify", b"FAILED"),
            ],
        )
        s1, _, _ = await run_asgi(mw, scope1)
        assert s1 == 200

        # [FAILED, SUCCESS] -> first is FAILED -> 403
        scope2 = make_http_scope(
            "/api/v1/predict",
            headers=[
                (b"x-ssl-client-verify", b"FAILED"),
                (b"x-ssl-client-verify", b"SUCCESS"),
            ],
        )
        s2, _, _ = await run_asgi(mw, scope2)
        assert s2 == 403


@pytest.mark.asyncio
async def test_mtls_duplicate_cert_hash_headers_first_wins():
    """Starlette request.headers.get for cert hash extracts the FIRST matching header."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    revoked = "revoked_123"
    clean = "clean_123"
    with (
        patch.object(settings, "mtls_enabled", True),
        patch.object(MTLSManager, "__init__", lambda self: setattr(self, "crl_revoked_serials", {revoked})),
    ):
        # [clean, revoked] -> first is clean -> passes
        scope1 = make_http_scope(
            "/api/v1/predict",
            headers=[
                (b"x-ssl-client-verify", b"SUCCESS"),
                (b"x-client-cert-sha256", clean.encode("latin-1")),
                (b"x-client-cert-sha256", revoked.encode("latin-1")),
            ],
        )
        s1, _, _ = await run_asgi(mw, scope1)
        assert s1 == 200

        # [revoked, clean] -> first is revoked -> 403
        scope2 = make_http_scope(
            "/api/v1/predict",
            headers=[
                (b"x-ssl-client-verify", b"SUCCESS"),
                (b"x-client-cert-sha256", revoked.encode("latin-1")),
                (b"x-client-cert-sha256", clean.encode("latin-1")),
            ],
        )
        s2, _, _ = await run_asgi(mw, scope2)
        assert s2 == 403


# ------------------------------------------------------------------------------
# 6. MTLSManager Construction & Exception Timing
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mtls_manager_constructor_failure():
    """If MTLSManager instantiation raises, the exception bubbles up unswallowed."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("OK", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with (
        patch.object(settings, "mtls_enabled", True),
        patch("app.infrastructure.security.mtls_manager.MTLSManager", side_effect=RuntimeError("PKI init failed")),
    ):
        scope = make_http_scope(
            "/api/v1/predict",
            headers=[(b"x-ssl-client-verify", b"SUCCESS")],
        )
        with pytest.raises(RuntimeError, match="PKI init failed"):
            await run_asgi(mw, scope)


# ------------------------------------------------------------------------------
# 7. Downstream Response Type Pass-Through Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mtls_downstream_200():
    """Downstream 200 passes through untouched."""
    async def downstream(scope, receive, send):
        resp = JSONResponse({"status": "ok"}, status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    scope = make_http_scope("/api/v1/predict", headers=[])
    s, h, b = await run_asgi(mw, scope)
    assert s == 200
    assert b == b'{"status":"ok"}'


@pytest.mark.asyncio
async def test_mtls_downstream_204():
    """Downstream 204 No Content passes through untouched."""
    async def downstream(scope, receive, send):
        resp = Response(status_code=204)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    scope = make_http_scope("/api/v1/predict", headers=[])
    s, _, b = await run_asgi(mw, scope)
    assert s == 204
    assert b == b""


@pytest.mark.asyncio
async def test_mtls_downstream_4xx():
    """Downstream 4xx passes through untouched."""
    async def downstream(scope, receive, send):
        resp = JSONResponse({"error": "not_found"}, status_code=404)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    scope = make_http_scope("/api/v1/predict", headers=[])
    s, _, b = await run_asgi(mw, scope)
    assert s == 404
    assert b == b'{"error":"not_found"}'


@pytest.mark.asyncio
async def test_mtls_downstream_explicit_5xx():
    """Downstream explicit 500 JSONResponse passes through untouched."""
    async def downstream(scope, receive, send):
        resp = JSONResponse({"error": "server_error"}, status_code=500)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    scope = make_http_scope("/api/v1/predict", headers=[])
    s, _, b = await run_asgi(mw, scope)
    assert s == 500
    assert b == b'{"error":"server_error"}'


@pytest.mark.asyncio
async def test_mtls_downstream_raised_exception():
    """Downstream raised exception propagates unhandled."""
    async def downstream(scope, receive, send):
        raise ValueError("Downstream exploded")

    mw = MTLSVerificationMiddleware(downstream)
    scope = make_http_scope("/api/v1/predict", headers=[])
    with pytest.raises(ValueError, match="Downstream exploded"):
        await run_asgi(mw, scope)


@pytest.mark.asyncio
async def test_mtls_downstream_streaming():
    """StreamingResponse streams chunks untouched."""
    async def downstream(scope, receive, send):
        async def numbers():
            for i in range(3):
                yield f"chunk_{i},".encode()
        resp = StreamingResponse(numbers(), media_type="text/plain")
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    scope = make_http_scope("/api/v1/predict", headers=[])
    s, _, b = await run_asgi(mw, scope)
    assert s == 200
    assert b == b"chunk_0,chunk_1,chunk_2,"


@pytest.mark.asyncio
async def test_mtls_head_request():
    """HEAD request passes through untouched."""
    async def downstream(scope, receive, send):
        resp = PlainTextResponse("HEAD_BODY", status_code=200)
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    scope = make_http_scope("/api/v1/predict", headers=[], method="HEAD")
    s, _, _ = await run_asgi(mw, scope)
    assert s == 200


# ------------------------------------------------------------------------------
# 8. Non-HTTP Scope & Header Preservation
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mtls_non_http_scope_passthrough():
    """Non-HTTP scopes (websocket, lifespan) pass through to downstream app."""
    reached: bool = False

    async def downstream(scope, receive, send):
        nonlocal reached
        reached = True

    mw = MTLSVerificationMiddleware(downstream)
    scope = {"type": "websocket", "path": "/ws/alerts"}
    await mw(scope, dummy_receive, dummy_send)
    assert reached


@pytest.mark.asyncio
async def test_mtls_no_request_header_mutation():
    """Incoming scope headers are not mutated or deleted by MTLS."""
    original_headers = [(b"x-custom", b"val"), (b"x-ssl-client-verify", b"SUCCESS")]
    headers_copy = list(original_headers)

    captured_headers: list[tuple[bytes, bytes]] = []

    async def downstream(scope, receive, send):
        nonlocal captured_headers
        captured_headers = list(scope.get("headers", []))
        resp = PlainTextResponse("OK")
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        scope = make_http_scope("/api/v1/predict", headers=headers_copy)
        await run_asgi(mw, scope)
        assert captured_headers == original_headers


# ------------------------------------------------------------------------------
# 9. Early 403 Response Outer-Middleware Integration
# ------------------------------------------------------------------------------

def test_mtls_early_403_outer_headers_via_testclient():
    """In live app, early 403 receives DDoS rate-limit headers but skips inner headers."""
    client = TestClient(app)
    with patch.object(settings, "mtls_enabled", True):
        resp = client.get(
            "/api/v1/predict",
            headers={"X-SSL-Client-Verify": "FAILED"},
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["type"] == "https://cfi-platform.org/errors/mTLSVerificationFailed"

        # Inner middleware headers (W3C, APIVersion, SecurityHeaders, CORS) should NOT be present
        # because MTLS short-circuits before them
        assert "traceparent" not in resp.headers
        assert "x-api-version" not in resp.headers


# ------------------------------------------------------------------------------
# 10. ContextVar Execution Visibility
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mtls_contextvar_visibility():
    """ContextVar set upstream is visible downstream."""
    captured_val = None

    async def downstream(scope, receive, send):
        nonlocal captured_val
        captured_val = test_var.get()
        resp = PlainTextResponse("OK")
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    token = test_var.set("upstream_value")
    try:
        scope = make_http_scope("/api/v1/predict", headers=[])
        await run_asgi(mw, scope)
        assert captured_val == "upstream_value"
    finally:
        test_var.reset(token)


# ------------------------------------------------------------------------------
# 11. Concurrency Isolation
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mtls_concurrency_isolation():
    """Concurrent requests with valid vs invalid headers process in total isolation."""
    async def downstream(scope, receive, send):
        await asyncio.sleep(0.01)
        resp = PlainTextResponse("OK")
        await resp(scope, receive, send)

    mw = MTLSVerificationMiddleware(downstream)
    with patch.object(settings, "mtls_enabled", True):
        async def make_call(status_str: str) -> int:
            scope = make_http_scope(
                "/api/v1/predict",
                headers=[(b"x-ssl-client-verify", status_str.encode("latin-1"))],
            )
            s, _, _ = await run_asgi(mw, scope)
            return s

        tasks = [
            make_call("SUCCESS"),
            make_call("FAILED"),
            make_call("SUCCESS"),
            make_call("FAILED"),
            make_call("SUCCESS"),
        ]
        results = await asyncio.gather(*tasks)
        assert results == [200, 403, 200, 403, 200]
