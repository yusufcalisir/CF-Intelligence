"""Unit tests for API Gateway WebSocket Proxying and Zombie Task Prevention.

Verifies:
- Route mapping and role-based authorization for /ws/ routes
- Bidirectional frame proxying between client and downstream microservice
- Immediate cancellation of companion forwarder coroutines upon disconnection (FIRST_COMPLETED)
- Elimination of zombie coroutines and socket descriptor leaks
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket, WebSocketDisconnect

from app.presentation.routers.gateway import check_ws_authorization, ws_proxy


class TestGatewayWSAuthorization:
    """Verifies RBAC and path mapping for WebSocket proxy routes."""

    def test_analyst_authorized_for_training_and_streaming(self) -> None:
        assert check_ws_authorization("analyst_1", "analyst", "/ws/training") is True
        assert check_ws_authorization("analyst_1", "analyst", "/ws/streaming/scen_1") is True
        assert check_ws_authorization("analyst_1", "analyst", "/ws/scenarios/stream") is True
        assert check_ws_authorization("analyst_1", "analyst", "/ws/telemetry") is True

    def test_bank_role_forbidden_from_training_ws(self) -> None:
        assert check_ws_authorization("bank_node_a", "bank", "/ws/training") is False
        assert check_ws_authorization("bank_node_a", "bank", "/ws/telemetry") is True

    def test_unauthorized_role_rejected(self) -> None:
        assert check_ws_authorization("unauthorized_user", "unauthorized_role", "/ws/training") is False


class TestGatewayWSProxyLifecycle:
    """Verifies proxy connection establishment, forwarding, and clean cancellation."""

    @pytest.mark.asyncio
    async def test_unauthorized_ws_closes_with_3000(self) -> None:
        mock_ws = AsyncMock(spec=WebSocket)
        mock_ws.client = MagicMock(host="127.0.0.1")
        mock_ws.headers = {}
        mock_ws.query_params = {}

        with patch("app.presentation.routers.gateway.authenticate_request", return_value=(None, None, None, None)):
            await ws_proxy(mock_ws, "training")

        mock_ws.accept.assert_awaited_once()
        mock_ws.close.assert_awaited_once_with(code=3000, reason="Gateway Error: Unauthorized key")

    @pytest.mark.asyncio
    async def test_unmapped_ws_path_closes_with_4004(self) -> None:
        mock_ws = AsyncMock(spec=WebSocket)
        mock_ws.client = MagicMock(host="127.0.0.1")
        mock_ws.headers = {}
        mock_ws.query_params = {}

        with (
            patch("app.presentation.routers.gateway.authenticate_request", return_value=("usr1", "analyst", "k1", None)),
            patch("app.presentation.routers.gateway.check_rate_limit", return_value=(True, 100, 100, 60)),
            patch("app.presentation.routers.gateway.check_ws_authorization", return_value=True),
        ):
            await ws_proxy(mock_ws, "unknown/unmapped/service")

        mock_ws.accept.assert_awaited_once()
        mock_ws.close.assert_awaited_once_with(code=4004, reason="Gateway: WS path not mapped")

    @pytest.mark.asyncio
    async def test_downstream_disconnect_cancels_client_forwarder_immediately(self) -> None:
        """When downstream closes, forward_to_client finishes and client_task is cancelled

        without hanging indefinitely on iter_text(), preventing zombie tasks.
        """
        mock_client_ws = AsyncMock(spec=WebSocket)
        mock_client_ws.client = MagicMock(host="127.0.0.1")
        mock_client_ws.headers = {}
        mock_client_ws.query_params = {}

        # Simulates client stuck waiting for incoming messages indefinitely
        async def mock_iter_text():
            try:
                while True:
                    await asyncio.sleep(100)
                    yield "client_heartbeat"
            except asyncio.CancelledError:
                raise

        mock_client_ws.iter_text = mock_iter_text

        # Downstream yields one message then finishes immediately (disconnect)
        mock_downstream_ws = AsyncMock()

        async def mock_downstream_iter():
            yield '{"event": "sim_update", "round": 1}'

        mock_downstream_ws.__aiter__ = lambda self: mock_downstream_iter()

        mock_connect_cm = AsyncMock()
        mock_connect_cm.__aenter__.return_value = mock_downstream_ws
        mock_connect_cm.__aexit__.return_value = False

        with (
            patch("app.presentation.routers.gateway.authenticate_request", return_value=("usr1", "analyst", "k1", None)),
            patch("app.presentation.routers.gateway.check_rate_limit", return_value=(True, 100, 100, 60)),
            patch("app.presentation.routers.gateway.check_ws_authorization", return_value=True),
            patch("websockets.connect", return_value=mock_connect_cm),
        ):
            # Must complete promptly in < 2 seconds rather than hanging on mock_iter_text's 100s sleep
            await asyncio.wait_for(ws_proxy(mock_client_ws, "training"), timeout=2.0)

        # Downstream message was sent to client
        mock_client_ws.send_text.assert_awaited_with('{"event": "sim_update", "round": 1}')
        # Client websocket is closed cleanly
        mock_client_ws.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_client_disconnect_cancels_downstream_forwarder_immediately(self) -> None:
        """When client disconnects, forward_to_server finishes and downstream forwarder

        is cancelled immediately, closing downstream connection without leaks.
        """
        mock_client_ws = AsyncMock(spec=WebSocket)
        mock_client_ws.client = MagicMock(host="127.0.0.1")
        mock_client_ws.headers = {}
        mock_client_ws.query_params = {}

        # Simulates client immediately raising WebSocketDisconnect
        async def mock_iter_text_disconnect():
            raise WebSocketDisconnect(code=1000)
            yield ""  # Generator syntax

        mock_client_ws.iter_text = mock_iter_text_disconnect

        # Downstream stream waiting indefinitely
        mock_downstream_ws = AsyncMock()

        async def mock_downstream_iter():
            try:
                while True:
                    await asyncio.sleep(100)
                    yield "downstream_event"
            except asyncio.CancelledError:
                raise

        mock_downstream_ws.__aiter__ = lambda self: mock_downstream_iter()

        mock_connect_cm = AsyncMock()
        mock_connect_cm.__aenter__.return_value = mock_downstream_ws
        mock_connect_cm.__aexit__.return_value = False

        with (
            patch("app.presentation.routers.gateway.authenticate_request", return_value=("usr1", "analyst", "k1", None)),
            patch("app.presentation.routers.gateway.check_rate_limit", return_value=(True, 100, 100, 60)),
            patch("app.presentation.routers.gateway.check_ws_authorization", return_value=True),
            patch("websockets.connect", return_value=mock_connect_cm),
        ):
            # Must complete promptly in < 2 seconds without hanging on downstream's 100s sleep
            await asyncio.wait_for(ws_proxy(mock_client_ws, "streaming/scen_test"), timeout=2.0)

        mock_client_ws.close.assert_awaited()
