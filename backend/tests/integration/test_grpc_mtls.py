"""Unit tests for Phase 37.1 — Mandatory mTLS gRPC Server Hardening.

All tests are pure unit tests (no live gRPC socket, no real DB).
BankCertificateInterceptor internals are tested by mocking _lookup_bank_active_sync
and simulating handler_call_details with injected metadata.
ProtocolVersionInterceptor is tested via the existing domain layer.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import grpc
import pytest

from app.infrastructure.grpc.server import (
    _CN_PATTERN,
    BankCertificateInterceptor,
)
from app.infrastructure.grpc.version_interceptor import ProtocolVersionInterceptor

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_call_details(metadata: dict[str, str]) -> grpc.HandlerCallDetails:
    """Build a fake HandlerCallDetails with the given metadata dict."""
    details = MagicMock(spec=grpc.HandlerCallDetails)
    details.invocation_metadata = list(metadata.items())
    return details


def _make_context() -> MagicMock:
    ctx = MagicMock(spec=grpc.ServicerContext)
    ctx.abort = MagicMock()
    return ctx


def _run_interceptor(interceptor, metadata: dict[str, str]) -> tuple[bool, grpc.StatusCode | None]:
    """Run interceptor and return (was_allowed, abort_code).

    Returns (True, None) if the continuation was called.
    Returns (False, <code>) if abort was triggered.
    """
    continuation_called = []

    def _continuation(details):
        continuation_called.append(True)
        return MagicMock()

    details = _make_call_details(metadata)
    result = interceptor.intercept_service(_continuation, details)

    if continuation_called:
        return True, None

    # The interceptor returned an abort handler — extract the status code by calling it
    ctx = _make_context()
    if callable(result):
        result(None, ctx)
    elif hasattr(result, "unary_unary") and callable(result.unary_unary):
        result.unary_unary(None, ctx)

    abort_code = ctx.abort.call_args[0][0] if ctx.abort.called else None
    return False, abort_code


# ── Tests: BankCertificateInterceptor ─────────────────────────────────────────


@patch("app.infrastructure.grpc.server._lookup_bank_active_sync", return_value=True)
def test_valid_cert_connection_succeeds(mock_lookup) -> None:
    """Valid CN for a registered ACTIVE bank must pass the interceptor."""
    interceptor = BankCertificateInterceptor()
    allowed, code = _run_interceptor(
        interceptor,
        {"x-cfi-client-cn": "bank_alpha.client.cf-intelligence.io"},
    )
    assert allowed is True, "Interceptor must allow valid ACTIVE bank"
    assert code is None
    mock_lookup.assert_called_once_with("bank_alpha")


@patch("app.infrastructure.grpc.server._lookup_bank_active_sync", return_value=False)
def test_invalid_cert_rejected(mock_lookup) -> None:
    """Unknown/invalid CN format must be rejected with PERMISSION_DENIED."""
    interceptor = BankCertificateInterceptor()
    # CN does not match the expected pattern
    allowed, code = _run_interceptor(
        interceptor,
        {"x-cfi-client-cn": "not-a-valid-cn"},
    )
    assert allowed is False
    assert code == grpc.StatusCode.PERMISSION_DENIED


@patch("app.infrastructure.grpc.server._lookup_bank_active_sync", return_value=False)
def test_expired_cert_rejected(mock_lookup) -> None:
    """A bank whose DB record is SUSPENDED/OFFBOARDED must be rejected with PERMISSION_DENIED.

    This simulates the case where the cert CN is parseable but the DB says the bank
    is no longer ACTIVE (e.g. expired/offboarded — _lookup_bank_active_sync returns False).
    """
    interceptor = BankCertificateInterceptor()
    allowed, code = _run_interceptor(
        interceptor,
        {"x-cfi-client-cn": "bank_suspended.client.cf-intelligence.io"},
    )
    assert allowed is False
    assert code == grpc.StatusCode.PERMISSION_DENIED
    mock_lookup.assert_called_once_with("bank_suspended")


@patch("app.infrastructure.grpc.server._lookup_bank_active_sync", return_value=False)
def test_unknown_bank_cert_rejected(mock_lookup) -> None:
    """Valid CN format but bank_id not in DB must be rejected with PERMISSION_DENIED."""
    interceptor = BankCertificateInterceptor()
    allowed, code = _run_interceptor(
        interceptor,
        {"x-cfi-client-cn": "bank_unknown.client.cf-intelligence.io"},
    )
    assert allowed is False
    assert code == grpc.StatusCode.PERMISSION_DENIED
    mock_lookup.assert_called_once_with("bank_unknown")


# ── Tests: ProtocolVersionInterceptor ─────────────────────────────────────────


def test_version_too_old_rejected() -> None:
    """Client version 0.1.0 (below minimum 1.0.0) must be rejected with FAILED_PRECONDITION."""
    interceptor = ProtocolVersionInterceptor()

    continuation_called = []

    def _continuation(details):
        continuation_called.append(True)
        return MagicMock()

    details = _make_call_details({"x-cfi-protocol-version": "0.1.0"})
    handler = interceptor.intercept_service(_continuation, details)

    assert not continuation_called, "Continuation must NOT be called for version 0.1.0"

    # The interceptor returns a grpc.RpcMethodHandler — extract the unary_unary callable
    ctx = _make_context()
    if hasattr(handler, "unary_unary") and handler.unary_unary is not None:
        handler.unary_unary(None, ctx)
    elif callable(handler):
        handler(None, ctx)
    else:
        pytest.fail(f"Unexpected handler type: {type(handler)}")

    assert ctx.abort.called, "context.abort must be called"
    abort_code = ctx.abort.call_args[0][0]
    assert abort_code == grpc.StatusCode.FAILED_PRECONDITION, (
        f"Expected FAILED_PRECONDITION, got {abort_code}"
    )

    abort_msg = ctx.abort.call_args[0][1]
    assert "https://docs.cf-intelligence.io/upgrade" in abort_msg, (
        "Abort message must contain upgrade URL"
    )


# ── Tests: CN pattern ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cn, expected_bank_id",
    [
        ("bank_alpha.client.cf-intelligence.io", "bank_alpha"),
        ("bank-beta.client.cf-intelligence.io", "bank-beta"),
        ("BIG123.client.cf-intelligence.io", "BIG123"),
    ],
)
def test_cn_pattern_valid(cn: str, expected_bank_id: str) -> None:
    match = _CN_PATTERN.match(cn)
    assert match is not None
    assert match.group("bank_id") == expected_bank_id


@pytest.mark.parametrize(
    "cn",
    [
        "bank_alpha",
        "bank alpha.client.cf-intelligence.io",
        ".client.cf-intelligence.io",
        "bank_alpha.other-domain.io",
        "",
    ],
)
def test_cn_pattern_invalid(cn: str) -> None:
    assert _CN_PATTERN.match(cn) is None
