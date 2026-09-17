"""Unit tests for Zero-Trust Security, Vault KMS, ZK-SNARK & Cryptographic Suite API endpoints."""

from __future__ import annotations

import fastapi
from fastapi.testclient import TestClient

from app.presentation.routers.security import api_router, router

_app = fastapi.FastAPI()
_app.include_router(router)
_app.include_router(api_router)
client = TestClient(_app)


class TestSecurityStatusEndpoints:
    def test_get_security_status_canonical(self) -> None:
        response = client.get("/v1/security/status")
        assert response.status_code == 200
        data = response.json()
        assert "mtls" in data
        assert "oidc" in data
        assert "abac" in data
        assert "vault" in data
        assert "audit_chain" in data

    def test_get_security_status_prefixed(self) -> None:
        response = client.get("/api/v1/security/status")
        assert response.status_code == 200
        data = response.json()
        assert data["audit_chain"]["chain_valid"] is True


class TestABACEvaluationEndpoints:
    def test_evaluate_abac_allowed(self) -> None:
        payload = {
            "user_username": "compliance_officer_1",
            "user_bank_id": "bank_01",
            "user_roles": ["compliance_officer"],
            "user_clearance": 4,
            "action": "view_sar",
            "resource_type": "sar_report",
            "resource_bank_id": "bank_01",
            "hour_override": 14,
        }
        response = client.post("/v1/security/abac/evaluate", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "allowed" in data
        assert "policy_name" in data
        assert "evaluated_at" in data

    def test_evaluate_abac_denied_role(self) -> None:
        payload = {
            "user_username": "intern_01",
            "user_bank_id": "bank_01",
            "user_roles": ["guest"],
            "user_clearance": 1,
            "action": "view_sar",
            "resource_type": "sar_report",
            "resource_bank_id": "bank_01",
            "hour_override": 14,
        }
        response = client.post("/api/v1/security/abac/evaluate", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "allowed" in data
        assert "reason" in data


class TestAuditChainEndpoints:
    def test_get_audit_chain_history(self) -> None:
        response = client.get("/v1/security/audit-chain?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        if len(data) > 0:
            entry = data[0]
            assert "curr_hash" in entry
            assert "event_type" in entry

    def test_verify_audit_chain_integrity(self) -> None:
        response = client.post("/api/v1/security/audit-chain/verify")
        assert response.status_code == 200
        data = response.json()
        assert "is_valid" in data
        assert "total_records" in data
        assert "genesis_hash" in data


class TestZKSNARKEndpoints:
    def test_get_zk_verifier_status(self) -> None:
        response = client.get("/v1/security/zk/status")
        assert response.status_code == 200
        data = response.json()
        assert "proving_scheme" in data
        assert "curve" in data
        assert "hash_algorithm" in data

    def test_get_zk_verifier_status_canonical_path(self) -> None:
        response = client.get("/api/v1/security/zkp/status")
        assert response.status_code == 200
        data = response.json()
        assert data["curve"] == "BN254"

    def test_verify_zk_proof_endpoint(self) -> None:
        payload = {
            "proof_id": "zk-proof-test-001",
            "bank_id": "bank_alpha",
            "round_id": 1,
            "pi_a": ["0x1234", "0x5678"],
            "pi_b": [["0x1111", "0x2222"], ["0x3333", "0x4444"]],
            "pi_c": ["0x5555", "0x6666"],
            "public_weight_hash": "a" * 64,
            "l2_norm_bound": 10.0,
            "vector_dimension": 128,
        }
        response = client.post("/api/v1/security/zk/verify", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "is_valid" in data
        assert data["bank_id"] == "bank_alpha"


class TestFederatedUnlearningEndpoints:
    def test_get_unlearning_status(self) -> None:
        response = client.get("/v1/security/unlearning/status")
        assert response.status_code == 200
        data = response.json()
        assert "engine_status" in data
        assert "supported_methods" in data
        assert isinstance(data["supported_methods"], list)

    def test_trigger_unlearning_endpoint(self) -> None:
        payload = {
            "target_bank_id": "bank_gamma",
            "unlearning_method": "exact_reaggregation",
            "start_round": 1,
            "end_round": 10,
        }
        response = client.post("/api/v1/security/unlearn", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["target_bank_id"] == "bank_gamma"
        assert "unlearned_model_l2_norm" in data
        assert data["erasure_verified"] is True


class TestPQCEndpoints:
    def test_get_pqc_status(self) -> None:
        response = client.get("/v1/security/pqc/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ACTIVE"
        assert "NIST" in data["standard_fips_203"]

    def test_generate_pqc_keypair(self) -> None:
        payload = {
            "kem_algorithm": "kyber_768",
            "signature_algorithm": "dilithium_3",
        }
        response = client.post("/api/v1/security/pqc/keypair", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "kyber_public_key_hex" in data
        assert "dilithium_public_key_hex" in data

    def test_encapsulate_pqc(self) -> None:
        response = client.post("/api/v1/security/pqc/encapsulate")
        assert response.status_code == 200
        data = response.json()
        assert data["encapsulation_status"] == "COMPLETED"
        assert "shared_secret_hash" in data


class TestLayer2BridgeEndpoints:
    def test_get_bridge_status(self) -> None:
        response = client.get("/v1/security/bridge/status")
        assert response.status_code == 200
        data = response.json()
        assert "bridge_status" in data
        assert "supported_networks" in data

    def test_disburse_crosschain_rewards(self) -> None:
        payload = {
            "epoch_id": 42,
            "pool_amount": 50000.0,
            "currency": "wCBDC",
            "allocations": {
                "bank_alpha": 0.5,
                "bank_beta": 0.5,
            },
        }
        response = client.post("/api/v1/security/bridge/disburse", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["epoch_id"] == 42
        assert data["is_fully_finalized"] is True
        assert len(data["routes"]) >= 1


class TestAdaptiveDPEndpoints:
    def test_get_rdp_status(self) -> None:
        response = client.get("/v1/security/rdp/status?node_id=bank_01")
        assert response.status_code == 200
        data = response.json()
        assert data["node_id"] == "bank_01"
        assert "active_sigma" in data

    def test_calibrate_rdp_noise(self) -> None:
        payload = {
            "round_id": 1,
            "current_loss": 0.42,
            "prev_loss": 0.55,
            "batch_size": 256,
            "total_samples": 10000,
            "node_id": "bank_01",
        }
        response = client.post("/api/v1/security/rdp/calibrate", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["node_id"] == "bank_01"
        assert data["calibrated_sigma"] > 0.0


class TestTenantKMSEndpoints:
    def test_rotate_kms_key(self) -> None:
        payload = {"bank_id": "bank_kms_unit_test"}
        response = client.post("/v1/security/kms/rotate", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["bank_id"] == "bank_kms_unit_test"
        assert data["status"] in ("ROTATED", "active")
        assert data["new_version"] >= 1

    def test_get_kms_key_metadata(self) -> None:
        # Rotate first to ensure key exists
        client.post("/api/v1/security/kms/rotate", json={"bank_id": "bank_kms_meta_test"})
        response = client.get("/v1/security/kms/keys/bank_kms_meta_test")
        assert response.status_code == 200
        data = response.json()
        assert data["bank_id"] == "bank_kms_meta_test"
        assert data["key_name"] == "tenant_bank_kms_meta_test"
        assert data["latest_version"] >= 1

    def test_get_kms_key_metadata_nonexistent(self) -> None:
        response = client.get("/api/v1/security/kms/keys/nonexistent_bank_xyz")
        assert response.status_code == 404

    def test_get_vault_seal_status(self) -> None:
        response = client.get("/api/v1/security/vault/seal-status")
        assert response.status_code == 200
        data = response.json()
        assert "sealed" in data
        assert "vault_url" in data
        assert "ha_enabled" in data
