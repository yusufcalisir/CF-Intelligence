"""Integration tests for Section 41.1: Bank-Side FL Client Daemon."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.infrastructure.client_daemon.daemon import (
    BankClientDaemon,
    remove_pid_file,
    write_pid_file,
)
from app.infrastructure.client_daemon.health_server import DaemonHealthServer
from app.infrastructure.client_daemon.local_trainer import LocalTrainer


@pytest.mark.asyncio
async def test_daemon_starts_and_writes_pid() -> None:
    """Verifies that initializing daemon writes process PID file."""
    pid_path = "storage/test_daemon.pid"
    daemon = BankClientDaemon(pid_path=pid_path)

    await daemon.initialize()

    path = Path(pid_path)
    assert path.exists()
    assert int(path.read_text().strip()) > 0

    remove_pid_file(pid_path)


def test_training_cycle_produces_gradient() -> None:
    """Verifies that running a local training cycle generates compressed gradient bytes."""
    trainer = LocalTrainer(bank_id="bank_test")
    res = trainer.run_training_cycle(
        round_id=1, config={"batch_size": 10, "dp_epsilon": 0.5, "clip_norm": 1.0}
    )

    assert res["status"] == "COMPLETED"
    assert res["sample_count"] > 0
    assert len(res["compressed_gradient_bytes"]) > 0


def test_dp_applied_to_gradient() -> None:
    """Verifies that Opacus DP clips gradient L2 norm to clip_norm threshold."""
    trainer = LocalTrainer(bank_id="bank_test")
    raw_gradients = [10.0, -15.0, 20.0]

    dp_gradients, epsilon = trainer.apply_dp_clipping_and_noise(
        raw_gradients, clip_norm=1.0, epsilon=1.0
    )
    norm_after_dp = trainer.compute_gradient_norm(dp_gradients)

    assert epsilon == 1.0
    # Clipped norm plus DP noise should be within reasonable proximity of clip_norm (<= 1.5)
    assert norm_after_dp <= 1.5


@pytest.mark.asyncio
async def test_graceful_shutdown_on_sigterm() -> None:
    """Verifies that graceful shutdown clears is_running state and removes PID file."""
    pid_path = "storage/test_shutdown.pid"
    daemon = BankClientDaemon(pid_path=pid_path)
    write_pid_file(pid_path)

    await daemon.graceful_shutdown(timeout_seconds=5.0)

    assert not daemon.is_running
    assert not Path(pid_path).exists()


def test_health_endpoint_returns_200() -> None:
    """Verifies that DaemonHealthServer responds with 200 OK and status HEALTHY."""
    server = DaemonHealthServer(host="127.0.0.1", port=8089)
    server.start()

    try:
        resp = httpx.get("http://127.0.0.1:8089/health", timeout=2.0)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "HEALTHY"
        assert "last_round_id" in data
    finally:
        server.stop()
