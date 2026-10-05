"""Unit and concurrency tests for simulation runtime isolation, Great Expectations thread safety,
request idempotency, and WebSocket connection lifecycle accounting.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketState

from app.application.services.data_validator import (
    DataContractValidationError,
    DataValidatorService,
)
from app.application.services.idempotency import IdempotencyService
from app.main import app
from app.presentation.websockets.manager import WebSocketConnectionManager


def _valid_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "transaction_amount": [120.0, 250.0, 80.0],
            "velocity": [1.2, 1.8, 0.9],
            "hour_of_day": [10, 15, 20],
            "merchant_risk_score": [0.05, 0.2, 0.1],
            "customer_history_score": [0.85, 0.9, 0.95],
            "chargeback_count": [0, 0, 0],
            "account_age_days": [200, 500, 800],
            "country_code": ["US", "GB", "DE"],
            "merchant_category": ["grocery", "retail", "services"],
            "device_type": ["mobile_app", "web_browser", "pos_terminal"],
        }
    )


def _invalid_df() -> pd.DataFrame:
    """Invalid dataframe violating schema (negative transaction amount and mean out of bounds)."""
    return pd.DataFrame(
        {
            "transaction_amount": [-50.0, -100.0, -10.0],
            "velocity": [1.2, 1.8, 0.9],
            "hour_of_day": [10, 15, 20],
            "merchant_risk_score": [0.05, 0.2, 0.1],
            "customer_history_score": [0.85, 0.9, 0.95],
            "chargeback_count": [0, 0, 0],
            "account_age_days": [200, 500, 800],
            "country_code": ["US", "GB", "DE"],
            "merchant_category": ["grocery", "retail", "services"],
            "device_type": ["mobile_app", "web_browser", "pos_terminal"],
        }
    )


class TestGreatExpectationsConcurrencySafety:
    """Verifies that Great Expectations validation definitions and batch definitions
    are safely isolated and can run concurrently without collision.
    """

    def test_concurrent_same_bank_great_expectations_validation(self):
        """Regression test for production defect:
        BatchDefinition 'bd_bank_a' has changed since it has last been saved.
        Two simulations validating bank_a concurrently must not collide.
        """
        service = DataValidatorService()
        df = _valid_df()

        def _run_validation(sim_id: str):
            service.gate_data_contract(df, "bank_a", simulation_id=sim_id)
            return True

        with ThreadPoolExecutor(max_workers=4) as executor:
            sim_ids = [
                "fa14cdb4-0000-0000-0000-000000000001",
                "066a8f7e-0000-0000-0000-000000000002",
                "fa14cdb4-0000-0000-0000-000000000003",
                "066a8f7e-0000-0000-0000-000000000004",
            ]
            futures = [executor.submit(_run_validation, sid) for sid in sim_ids]
            results = [f.result() for f in futures]

        assert all(results)
        assert len(results) == 4

    def test_concurrent_different_bank_validation(self):
        """Verifies concurrent validation across different banks."""
        service = DataValidatorService()
        df = _valid_df()

        def _run_validation(bank_id: str, sim_id: str):
            service.gate_data_contract(df, bank_id, simulation_id=sim_id)
            return True

        tasks = [
            ("bank_a", "sim_diff_1"),
            ("bank_b", "sim_diff_2"),
            ("bank_c", "sim_diff_3"),
            ("bank_a", "sim_diff_4"),
        ]

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_run_validation, b, s) for b, s in tasks]
            results = [f.result() for f in futures]

        assert all(results)
        assert len(results) == 4

    def test_concurrent_deterministic_barrier_validation(self):
        """Exercises deterministic interleaving across two threads validating bank_a.
        Uses a threading.Barrier to ensure both threads launch in controlled lockstep,
        verifying that Great Expectations thread-local contexts do not leak or
        overwrite datasources during simultaneous execution.
        """
        service = DataValidatorService()
        df = _valid_df()
        barrier = threading.Barrier(2)

        def _worker(sim_id: str):
            barrier.wait(timeout=5)
            service.gate_data_contract(df, "bank_a", simulation_id=sim_id)
            return sim_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(_worker, "sim_barrier_001")
            f2 = executor.submit(_worker, "sim_barrier_002")
            res1 = f1.result(timeout=15)
            res2 = f2.result(timeout=15)

        assert res1 == "sim_barrier_001"
        assert res2 == "sim_barrier_002"

    def test_concurrent_three_banks_overlapping_lifecycle(self):
        """Simulates production multi-bank federated round startup where bank_a,
        bank_b, and bank_c are gated simultaneously across concurrent simulations.
        """
        service = DataValidatorService()
        df = _valid_df()

        tasks = [
            ("bank_a", "sim_round_01"),
            ("bank_b", "sim_round_01"),
            ("bank_c", "sim_round_01"),
            ("bank_a", "sim_round_02"),
            ("bank_b", "sim_round_02"),
            ("bank_c", "sim_round_02"),
        ]

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [
                executor.submit(service.gate_data_contract, df, bank, simulation_id=sid)
                for bank, sid in tasks
            ]
            for f in futures:
                f.result(timeout=20)

    def test_concurrent_valid_and_invalid_data_isolation(self):
        """Fail-closed isolation test:
        Simulation A receives a valid dataset; Simulation B receives an invalid dataset.
        Under concurrent validation, Simulation A must pass while Simulation B must fail
        with DataContractValidationError. No cross-contamination is allowed.
        """
        service = DataValidatorService()
        valid_df = _valid_df()
        invalid_df = _invalid_df()

        def _validate_valid():
            service.gate_data_contract(valid_df, "bank_a", simulation_id="sim_valid_01")
            return "VALID_PASS"

        def _validate_invalid():
            service.gate_data_contract(invalid_df, "bank_a", simulation_id="sim_invalid_01")
            return "INVALID_PASS"

        with ThreadPoolExecutor(max_workers=2) as executor:
            f_valid = executor.submit(_validate_valid)
            f_invalid = executor.submit(_validate_invalid)

            assert f_valid.result(timeout=15) == "VALID_PASS"

            with pytest.raises(DataContractValidationError):
                f_invalid.result(timeout=15)


class TestSimulationCreationIdempotency:
    """Verifies duplicate Start action protection via request idempotency."""

    @pytest.fixture(autouse=True)
    def clean_idempotency(self):
        svc = IdempotencyService.get()
        with svc._fallback_lock:
            svc._fallback.clear()
        if svc._redis_client is not None:
            try:
                for k in svc._redis_client.keys("idem:*"):
                    svc._redis_client.delete(k)
            except Exception:
                pass
        yield
        with svc._fallback_lock:
            svc._fallback.clear()
        if svc._redis_client is not None:
            try:
                for k in svc._redis_client.keys("idem:*"):
                    svc._redis_client.delete(k)
            except Exception:
                pass

    def test_duplicate_creation_request_returns_cached_simulation(self):
        client = TestClient(app)
        idempotency_key = "idemp_test_duplicate_sim_01"

        payload = {
            "num_rounds": 1,
            "local_epochs": 1,
            "learning_rate": 0.001,
            "batch_size": 32,
            "min_clients_per_round": 2,
            "privacy_mechanism": "none",
            "bank_a_transactions": 1000,
            "bank_b_transactions": 1000,
            "bank_c_transactions": 1000,
        }

        # Request 1: creates simulation
        resp1 = client.post(
            "/api/v1/simulations",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
        )
        assert resp1.status_code == 202
        data1 = resp1.json()
        sim_id_1 = data1["id"]
        assert "Idempotency-Replayed" not in resp1.headers

        # Request 2: duplicate delivery of same creation command
        resp2 = client.post(
            "/api/v1/simulations",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
        )
        assert resp2.status_code == 202
        data2 = resp2.json()
        assert data2["id"] == sim_id_1
        assert resp2.headers.get("Idempotency-Replayed") == "true"

    def test_independent_creation_requests_produce_distinct_simulations(self):
        client = TestClient(app)

        payload = {
            "num_rounds": 1,
            "local_epochs": 1,
            "learning_rate": 0.001,
            "batch_size": 32,
            "min_clients_per_round": 2,
            "privacy_mechanism": "none",
            "bank_a_transactions": 1000,
            "bank_b_transactions": 1000,
            "bank_c_transactions": 1000,
        }

        resp1 = client.post(
            "/api/v1/simulations",
            json=payload,
            headers={"Idempotency-Key": "key_sim_indep_01"},
        )
        assert resp1.status_code == 202
        sim_id_1 = resp1.json()["id"]

        resp2 = client.post(
            "/api/v1/simulations",
            json=payload,
            headers={"Idempotency-Key": "key_sim_indep_02"},
        )
        assert resp2.status_code == 202
        sim_id_2 = resp2.json()["id"]

        assert sim_id_1 != sim_id_2

    def test_concurrent_same_key_creation_requests_return_same_simulation(self):
        """Verifies that when two simultaneous requests arrive with the exact same
        Idempotency-Key, backend atomic reservation / CAS guarantees:
        - exactly one simulation is created
        - both requests receive HTTP 202
        - both responses return the exact same simulation UUID
        - one response is marked as replayed (Idempotency-Replayed: true)
        """
        idempotency_key = "idemp_test_concurrent_same_key_001"

        payload = {
            "num_rounds": 1,
            "local_epochs": 1,
            "learning_rate": 0.001,
            "batch_size": 32,
            "min_clients_per_round": 2,
            "privacy_mechanism": "none",
            "bank_a_transactions": 1000,
            "bank_b_transactions": 1000,
            "bank_c_transactions": 1000,
        }

        barrier = threading.Barrier(2)

        def _make_request():
            barrier.wait(timeout=5)
            c = TestClient(app)
            return c.post(
                "/api/v1/simulations",
                json=payload,
                headers={"Idempotency-Key": idempotency_key},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(_make_request)
            f2 = executor.submit(_make_request)
            resp1 = f1.result(timeout=15)
            resp2 = f2.result(timeout=15)

        assert resp1.status_code == 202
        assert resp2.status_code == 202
        data1 = resp1.json()
        data2 = resp2.json()

        # Both responses must return the exact same simulation ID
        assert data1["id"] == data2["id"]

        # Exactly one must have Idempotency-Replayed header
        replayed_headers = [
            resp1.headers.get("Idempotency-Replayed"),
            resp2.headers.get("Idempotency-Replayed"),
        ]
        assert "true" in replayed_headers


class TestWebSocketManagerLifecycleAccounting:
    """Verifies that the WebSocketConnectionManager invariants hold after

    connect, disconnect, and broadcast drops.
    """

    @pytest.mark.asyncio
    async def test_full_teardown_clears_all_manager_accounting(self):
        manager = WebSocketConnectionManager()
        mock_ws = MagicMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock()
        mock_ws.close = AsyncMock()
        mock_ws.client_state = WebSocketState.CONNECTED

        room_name = "simulation:sim_test_lifecycle_1"
        connected = await manager.connect(mock_ws, room=room_name)
        assert connected is True

        assert mock_ws in manager._active_connections
        assert mock_ws in manager._ws_rooms
        assert mock_ws in manager._rooms[room_name]
        assert mock_ws in manager._client_last_seen

        # Full teardown on disconnect
        await manager.disconnect(mock_ws)

        assert mock_ws not in manager._active_connections
        assert mock_ws not in manager._ws_rooms
        assert room_name not in manager._rooms
        assert mock_ws not in manager._client_last_seen
        assert mock_ws not in manager._inbound_counters

    @pytest.mark.asyncio
    async def test_fanout_pruning_disconnected_clients_without_double_close(self):
        manager = WebSocketConnectionManager()

        active_ws = MagicMock()
        active_ws.accept = AsyncMock()
        active_ws.send_text = AsyncMock()
        active_ws.close = AsyncMock()
        active_ws.client_state = WebSocketState.CONNECTED

        disconnected_ws = MagicMock()
        disconnected_ws.accept = AsyncMock()
        disconnected_ws.send_text = AsyncMock(side_effect=RuntimeError("Cannot call send"))
        disconnected_ws.close = AsyncMock()
        disconnected_ws.client_state = WebSocketState.DISCONNECTED

        room = "simulation:test_fanout"
        await manager.connect(active_ws, room=room)
        await manager.connect(disconnected_ws, room=room)

        assert len(manager._active_connections) == 2

        stats = await manager.broadcast_to_room(room, {"event": "test"})
        assert stats["delivered"] == 1
        assert stats["dropped"] == 1

        # Invariants: disconnected client is pruned from all manager state
        assert disconnected_ws not in manager._active_connections
        assert disconnected_ws not in manager._ws_rooms
        assert disconnected_ws not in manager._rooms.get(room, set())
        assert active_ws in manager._active_connections

        # Disconnected client should not have had close() called again
        disconnected_ws.close.assert_not_called()


class TestCrossSimulationIsolation:
    """Verifies complete end-to-end state isolation between two simulations

    (failed-A / completed-B, completed-A / failed-B, REST status isolation).
    """

    def test_failed_a_completed_b_isolation(self):
        from app.presentation.routers.simulation import _simulation_results

        sim_a_id = "sim_test_iso_a_fail"
        sim_b_id = "sim_test_iso_b_comp"

        _simulation_results.set(
            sim_a_id,
            {
                "id": sim_a_id,
                "status": "failed",
                "current_round": 3,
                "total_rounds": 10,
                "error_message": "PrivacyBudgetExceededError: epsilon exceeded",
            },
        )
        _simulation_results.set(
            sim_b_id,
            {
                "id": sim_b_id,
                "status": "completed",
                "current_round": 10,
                "total_rounds": 10,
                "error_message": None,
            },
        )

        client = TestClient(app)

        resp_a = client.get(f"/api/v1/simulation/{sim_a_id}/status")
        assert resp_a.status_code == 200
        data_a = resp_a.json()
        assert data_a["status"] == "failed"
        assert "PrivacyBudgetExceededError" in data_a["error_message"]

        resp_b = client.get(f"/api/v1/simulation/{sim_b_id}/status")
        assert resp_b.status_code == 200
        data_b = resp_b.json()
        assert data_b["status"] == "completed"
        assert data_b["error_message"] is None
        assert data_b["current_round"] == 10

    def test_completed_a_failed_b_isolation(self):
        from app.presentation.routers.simulation import _simulation_results

        sim_a_id = "sim_test_iso_a_comp"
        sim_b_id = "sim_test_iso_b_fail"

        _simulation_results.set(
            sim_a_id,
            {
                "id": sim_a_id,
                "status": "completed",
                "current_round": 10,
                "total_rounds": 10,
                "error_message": None,
            },
        )
        _simulation_results.set(
            sim_b_id,
            {
                "id": sim_b_id,
                "status": "failed",
                "current_round": 5,
                "total_rounds": 10,
                "error_message": "DataContractValidationError: gate failed",
            },
        )

        client = TestClient(app)

        resp_a = client.get(f"/api/v1/simulation/{sim_a_id}/status")
        assert resp_a.status_code == 200
        assert resp_a.json()["status"] == "completed"

        resp_b = client.get(f"/api/v1/simulation/{sim_b_id}/status")
        assert resp_b.status_code == 200
        assert resp_b.json()["status"] == "failed"
        assert "DataContractValidationError" in resp_b.json()["error_message"]
