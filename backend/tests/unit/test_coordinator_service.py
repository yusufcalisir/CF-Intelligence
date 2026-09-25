"""Unit tests for CoordinatorService — Item 18: Enterprise Federated Coordinator Suite."""

from __future__ import annotations

import time

from app.application.services.coordinator_service import CoordinatorService


class TestCoordinatorServiceRegistration:
    """Tests for dynamic client registration and handshake API."""

    def test_successful_registration_compatible_environment(self):
        """A bank with PyTorch >= 2.x and Python >= 3.10 is registered."""
        svc = CoordinatorService()
        res = svc.register_client(
            bank_id="bank_a",
            pytorch_version="2.4.0",
            python_version="3.12.0",
            hardware_type="cuda",
            ram_gb=32.0,
        )
        assert res["registered"] is True
        assert res["status"] == "COMPATIBLE"
        assert "bank_a" in svc.registry
        assert svc.registry["bank_a"].hardware_type == "cuda"

    def test_registration_rejected_old_pytorch(self):
        """A bank with PyTorch < 2.x is rejected as incompatible."""
        svc = CoordinatorService()
        res = svc.register_client(
            bank_id="bank_b",
            pytorch_version="1.13.0",
            python_version="3.12.0",
            hardware_type="cpu",
            ram_gb=8.0,
        )
        assert res["registered"] is False
        assert res["status"] == "INCOMPATIBLE"
        assert "bank_b" not in svc.registry

    def test_registration_rejected_old_python(self):
        """A bank with Python < 3.10 is rejected as incompatible."""
        svc = CoordinatorService()
        res = svc.register_client(
            bank_id="bank_c",
            pytorch_version="2.4.0",
            python_version="3.9.7",
            hardware_type="cpu",
            ram_gb=8.0,
        )
        assert res["registered"] is False
        assert res["status"] == "INCOMPATIBLE"
        assert "bank_c" not in svc.registry


class TestCoordinatorHeartbeat:
    """Tests for client heartbeat monitoring and connection drop detection."""

    def test_valid_heartbeat_updates_timestamp(self):
        """Recording a heartbeat updates the client's last_heartbeat timestamp."""
        svc = CoordinatorService(heartbeat_timeout_seconds=30.0)
        svc.register_client(
            bank_id="bank_a",
            pytorch_version="2.4.0",
            python_version="3.12.0",
            hardware_type="cpu",
            ram_gb=8.0,
        )
        old_ts = svc.registry["bank_a"].last_heartbeat
        time.sleep(0.01)
        result = svc.record_heartbeat("bank_a")
        assert result is True
        assert svc.registry["bank_a"].last_heartbeat >= old_ts

    def test_heartbeat_from_unregistered_client_returns_false(self):
        """Heartbeat from unknown client returns False."""
        svc = CoordinatorService()
        result = svc.record_heartbeat("unknown_bank")
        assert result is False

    def test_client_marked_offline_after_timeout(self):
        """Client is marked OFFLINE when heartbeat times out."""
        svc = CoordinatorService(heartbeat_timeout_seconds=0.01)
        svc.register_client(
            bank_id="bank_a",
            pytorch_version="2.4.0",
            python_version="3.12.0",
            hardware_type="cpu",
            ram_gb=8.0,
        )
        time.sleep(0.05)
        active = svc.get_active_clients()
        assert len(active) == 0
        assert svc.registry["bank_a"].status == "OFFLINE"

    def test_get_active_clients_returns_online_only(self):
        """Only ONLINE clients appear in get_active_clients()."""
        svc = CoordinatorService(heartbeat_timeout_seconds=60.0)
        svc.register_client(
            bank_id="bank_a",
            pytorch_version="2.4.0",
            python_version="3.12.0",
            hardware_type="cuda",
            ram_gb=32.0,
        )
        svc.register_client(
            bank_id="bank_b",
            pytorch_version="2.1.0",
            python_version="3.11.0",
            hardware_type="cpu",
            ram_gb=4.0,
        )
        active = svc.get_active_clients()
        assert len(active) == 2
        bank_ids = [c.bank_id for c in active]
        assert "bank_a" in bank_ids
        assert "bank_b" in bank_ids


class TestParameterNegotiation:
    """Tests for heterogeneous hardware parameter negotiation."""

    def test_cuda_high_ram_gets_full_parameters(self):
        """High-end CUDA node receives base parameters unchanged."""
        svc = CoordinatorService()
        svc.register_client(
            bank_id="bank_gpu",
            pytorch_version="2.4.0",
            python_version="3.12.0",
            hardware_type="cuda",
            ram_gb=32.0,
        )
        neg = svc.negotiate_parameters("bank_gpu", base_batch_size=64, base_epochs=5)
        assert neg.batch_size == 64
        assert neg.local_epochs == 5
        assert neg.use_cuda is True
        assert neg.status == "COMPATIBLE"
        assert neg.gradient_accumulation_steps == 1

    def test_cpu_low_ram_gets_reduced_parameters(self):
        """Low-RAM CPU node gets reduced batch size, epochs, and increased gradient accumulation."""
        svc = CoordinatorService()
        svc.register_client(
            bank_id="bank_cpu_low",
            pytorch_version="2.4.0",
            python_version="3.12.0",
            hardware_type="cpu",
            ram_gb=2.0,
        )
        neg = svc.negotiate_parameters("bank_cpu_low", base_batch_size=64, base_epochs=5)
        assert neg.batch_size == 16
        assert neg.local_epochs <= 3
        assert neg.use_cuda is False
        assert neg.gradient_accumulation_steps == 4
        assert neg.status == "DEGRADED"

    def test_unregistered_client_gets_safe_degraded_defaults(self):
        """Unregistered bank falls back to safe CPU defaults."""
        svc = CoordinatorService()
        neg = svc.negotiate_parameters("unknown_bank", base_batch_size=64, base_epochs=5)
        assert neg.batch_size == 16
        assert neg.use_cuda is False
        assert neg.status == "DEGRADED"


class TestConsortiumNodesTelemetry:
    """Tests for authentic consortium bank nodes seeding, alias resolution, and institutional telemetry."""

    def test_seed_consortium_nodes(self):
        svc = CoordinatorService(auto_seed=True)
        assert len(svc.registry) == 5
        assert svc.registry["bank_alpha"].bank_name == "Garanti BBVA"
        assert svc.registry["bank_beta"].bank_name == "İş Bankası"
        assert svc.registry["bank_gamma"].bank_name == "Akbank"
        assert svc.registry["bank_a"].bank_name == "Meridian National"
        assert svc.registry["bank_b"].bank_name == "Nexus Digital"
        assert svc.registry["bank_alpha"].pytorch_version == "2.4.0+cu124"
        assert svc.registry["bank_alpha"].hardware_type == "cuda"
        assert svc.registry["bank_alpha"].ram_gb == 128.0
        assert svc.registry["bank_b"].hardware_type == "cpu"
        assert svc.registry["bank_b"].device_count == 0

    def test_heartbeat_and_negotiation_alias_resolution(self):
        svc = CoordinatorService(auto_seed=True)
        # Using alias bank_meridian should resolve to bank_a
        assert svc.record_heartbeat("bank_meridian") is True
        assert svc.record_heartbeat("bank_nexus") is True
        neg = svc.negotiate_parameters("bank_meridian", base_batch_size=64, base_epochs=5)
        assert neg.use_cuda is True
        assert neg.status == "COMPATIBLE"

    def test_explicit_bank_name_registration(self):
        svc = CoordinatorService()
        res = svc.register_client(
            bank_id="bank_custom",
            pytorch_version="2.4.0",
            python_version="3.12.0",
            hardware_type="cuda",
            ram_gb=32.0,
            bank_name="Custom Federal Bank",
        )
        assert res["registered"] is True
        assert svc.registry["bank_custom"].bank_name == "Custom Federal Bank"

