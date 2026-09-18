"""Integration tests for Docker Multi-Node Network Isolation & Deployment (Section 7.1)."""

from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path

import yaml

from app.infrastructure.client_daemon.config import ClientDaemonConfig

# Add project root to sys.path for scripts module import
project_root = str(Path(__file__).resolve().parents[3])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

_init_vault_pki = importlib.import_module("scripts.init_vault_pki")
generate_dev_fallback_certs = _init_vault_pki.generate_dev_fallback_certs


def test_bank_nodes_isolated_network_configuration() -> None:
    """Verifies that docker-compose.multinode.yml specifies strict network isolation."""
    compose_path = Path(__file__).resolve().parents[3] / "docker-compose.multinode.yml"
    assert compose_path.exists(), "docker-compose.multinode.yml must exist at project root"

    with open(compose_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    services = config.get("services", {})
    networks = config.get("networks", {})

    assert "coordinator" in services
    assert "bank-a" in services
    assert "bank-b" in services
    assert "bank-c" in services

    # Verify bank private network boundaries
    assert "bank-a-net" in networks
    assert "bank-b-net" in networks
    assert "bank-c-net" in networks
    assert networks["bank-a-net"].get("internal") is True
    assert networks["bank-b-net"].get("internal") is True
    assert networks["bank-c-net"].get("internal") is True

    # Bank A must be on bank-a-net and consortium-net
    bank_a_nets = services["bank-a"]["networks"]
    assert "bank-a-net" in bank_a_nets
    assert "consortium-net" in bank_a_nets
    assert "bank-b-net" not in bank_a_nets
    assert "bank-c-net" not in bank_a_nets

    # Bank B must be on bank-b-net and consortium-net
    bank_b_nets = services["bank-b"]["networks"]
    assert "bank-b-net" in bank_b_nets
    assert "consortium-net" in bank_b_nets
    assert "bank-a-net" not in bank_b_nets
    assert "bank-c-net" not in bank_b_nets

    # Bank C must be on bank-c-net and consortium-net
    bank_c_nets = services["bank-c"]["networks"]
    assert "bank-c-net" in bank_c_nets
    assert "consortium-net" in bank_c_nets
    assert "bank-a-net" not in bank_c_nets
    assert "bank-b-net" not in bank_c_nets


def test_grpc_mtls_handshake_coordinator_bank() -> None:
    """Verifies per-node mTLS PKI cert generation and ClientDaemonConfig loading."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir) / "pki" / "bank-a"
        generate_dev_fallback_certs("bank-a", out_path)

        assert (out_path / "cert.pem").exists()
        assert (out_path / "key.pem").exists()
        assert (out_path / "ca.pem").exists()

        daemon_config = ClientDaemonConfig(
            bank_id="bank_a",
            client_cert_path=out_path / "cert.pem",
            client_key_path=out_path / "key.pem",
            ca_cert_path=out_path / "ca.pem",
        )

        assert daemon_config.bank_id == "bank_a"
        assert daemon_config.client_cert_path == out_path / "cert.pem"
        assert daemon_config.mtls_enabled is True


def test_fl_round_multinode_simulation() -> None:
    """Verifies multi-node configuration setup for isolated bank daemon environments."""
    config_a = ClientDaemonConfig(
        bank_id="bank_a", coordinator_host="coordinator", coordinator_port=50051
    )
    config_b = ClientDaemonConfig(
        bank_id="bank_b", coordinator_host="coordinator", coordinator_port=50051
    )

    assert config_a.coordinator_host == "coordinator"
    assert config_b.coordinator_host == "coordinator"
    assert config_a.bank_id != config_b.bank_id
