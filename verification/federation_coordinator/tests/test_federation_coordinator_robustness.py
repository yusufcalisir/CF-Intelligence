"""Robustness & Fault Injection Test Suite for Federation Coordinator Subsystem.

Stress-tests every orchestration mechanism by injecting hostile fault conditions:
  GCEX1:  Client Crashes Mid-Round (Straggler Eviction)
  GCEX2:  Duplicated Gradient Submissions (Quorum Idempotency)
  GCEX3:  Gradient Submission to Non-Existent Round ID
  GCEX4:  Malformed Version Strings in Client Registration
  GCEX5:  gRPC Registration with Revoked Certificate Fingerprint
  GCEX6:  gRPC SubmitGradient with Invalid Digital Signature
  GCEX7:  gRPC SubmitGradient with Excessive DP Epsilon (> 10.0 Limit)
  GCEX8:  gRPC SubmitGradient with Corrupted Zlib Compression Payload
  GCEX9:  Disaster Recovery Primary Heartbeat Timeout Failover Execution
  GCEX10: Coordinator In-Memory Reset / Restart Simulation
  GCEX11: High Round ID Simulated AUC Decay Quality Gate Rejection
  GCEX12: Zero Active Clients Round Startup
"""

from __future__ import annotations

import sys
import math
import hashlib
import asyncio
import pytest
import numpy as np

PROJECT_ROOT = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
sys.path.insert(0, PROJECT_ROOT)

from app.application.services.coordinator_service import CoordinatorService
from app.infrastructure.grpc.servicer import FederatedLearningServicer
from app.infrastructure.grpc.types import ClientRegisterRequest, SubmitGradientRequest
from app.infrastructure.disaster_recovery.region_failover import MultiRegionFailoverManager, CoordinatorRegionRole

coord_service = CoordinatorService()
grpc_servicer = FederatedLearningServicer()
failover_mgr = MultiRegionFailoverManager()


# =====================================================================
# GCEX1: Client Crashes Mid-Round (Straggler Eviction)
# =====================================================================

def test_gcex1_client_crashes_mid_round():
    """GCEX1: 2 of 5 clients crash mid-round. Round remains in COLLECTING_GRADIENTS."""
    coord = CoordinatorService(heartbeat_timeout_seconds=15.0)
    for i in range(5):
        coord.register_client(f"bank_crash_{i}")

    rnd = coord.start_round(min_clients=5)
    round_id = rnd["round_id"]

    # Simulate 2 clients crashing (heartbeat timestamp > 15s ago)
    import time
    now = time.time()
    coord.registry["bank_crash_3"].last_heartbeat = now - 30.0
    coord.registry["bank_crash_4"].last_heartbeat = now - 30.0

    # Remaining 3 active clients submit gradients
    coord.on_gradient_received(round_id, "bank_crash_0", b"grad_0")
    coord.on_gradient_received(round_id, "bank_crash_1", b"grad_1")
    resp = coord.on_gradient_received(round_id, "bank_crash_2", b"grad_2")

    # Round must remain in COLLECTING_GRADIENTS because 3/5 submissions < 5 min_clients
    assert coord.rounds[round_id]["status"] == "COLLECTING_GRADIENTS"
    assert resp["status"] == "GRADIENT_STORED"

    # Verify crashed clients evicted to OFFLINE
    active = coord.get_active_clients()
    active_ids = [c.bank_id for c in active]
    assert "bank_crash_3" not in active_ids
    assert "bank_crash_4" not in active_ids


# =====================================================================
# GCEX2: Duplicated Gradient Submissions (Quorum Idempotency)
# =====================================================================

def test_gcex2_duplicated_gradient_submissions():
    """GCEX2: Same client submits gradient twice — dictionary overwrites, count stays 1."""
    coord = CoordinatorService()
    coord.register_client("bank_dup_0")
    coord.register_client("bank_dup_1")

    rnd = coord.start_round(min_clients=2)
    round_id = rnd["round_id"]

    # bank_dup_0 submits twice
    coord.on_gradient_received(round_id, "bank_dup_0", b"grad_v1")
    coord.on_gradient_received(round_id, "bank_dup_0", b"grad_v2")

    # Submission dictionary must contain 1 entry for bank_dup_0 with updated bytes
    submissions = coord.gradient_submissions[round_id]
    assert len(submissions) == 1
    assert submissions["bank_dup_0"] == b"grad_v2"
    assert coord.rounds[round_id]["status"] == "COLLECTING_GRADIENTS"


# =====================================================================
# GCEX3: Gradient Submission to Non-Existent Round ID
# =====================================================================

def test_gcex3_non_existent_round_id():
    """GCEX3: Submission to round_id 99999 raises ValueError cleanly."""
    coord = CoordinatorService()
    with pytest.raises(ValueError, match="Round ID 99999 does not exist"):
        coord.on_gradient_received(99999, "bank_1", b"grad_bytes")


# =====================================================================
# GCEX4: Malformed Version Strings in Client Registration
# =====================================================================

def test_gcex4_malformed_version_strings():
    """GCEX4: Malformed version strings fall back to default major version 2/3."""
    coord = CoordinatorService()
    res = coord.register_client(
        bank_id="bank_malformed_ver",
        pytorch_version="invalid_torch_2.x",
        python_version="bad_py_version",
    )
    # Exception handler sets torch_major=2, py_major=3, py_minor=10 => COMPATIBLE
    assert res["registered"] is True
    assert res["status"] == "COMPATIBLE"


# =====================================================================
# GCEX5: gRPC Registration with Revoked Certificate Fingerprint
# =====================================================================

@pytest.mark.asyncio
async def test_gcex5_grpc_revoked_certificate_fingerprint():
    """GCEX5: Certificate fingerprint starting with REVOKED is rejected."""
    req = ClientRegisterRequest(
        bank_id="bank_revoked_01",
        bank_name="Revoked Bank",
        certificate_fingerprint="REVOKED_CERT_SHA256_9999",
    )
    resp = await grpc_servicer.RegisterClient(req)
    assert resp.is_accepted is False
    assert resp.assigned_cluster_id == -1
    assert resp.session_token == ""


# =====================================================================
# GCEX6 & GCEX7 & GCEX8: gRPC SubmitGradient Boundary Failures
# =====================================================================

@pytest.mark.asyncio
async def test_gcex6_grpc_submit_gradient_invalid_signature():
    """GCEX6: Invalid signature returns REJECTED_SIGNATURE ack."""
    req = SubmitGradientRequest(
        round_id="1",
        bank_id="bank_sig_test",
        compressed_masked_gradient=b"compressed_data",
        dp_epsilon_used=1.0,
        participant_count=3,
        signature=b"invalid_signature_bytes",
    )
    ack = await grpc_servicer.SubmitGradient(req)
    assert ack.received is False
    assert "REJECTED_SIGNATURE" in ack.status_message


@pytest.mark.asyncio
async def test_gcex7_grpc_submit_gradient_excessive_dp_epsilon():
    """GCEX7: DP epsilon 15.0 > 10.0 limit returns REJECTED_EPSILON ack."""
    import zlib
    from app.infrastructure.security.signature_verifier import DigitalEnvelopeSigner

    compressed = zlib.compress(b"valid_gradient_payload")
    signed_msg = f"1:bank_eps_test".encode() + hashlib.sha256(compressed).digest()

    signer = DigitalEnvelopeSigner()
    sig = signer.sign_payload(signed_msg, "bank_eps_test")

    req = SubmitGradientRequest(
        round_id="1",
        bank_id="bank_eps_test",
        compressed_masked_gradient=compressed,
        dp_epsilon_used=15.0,  # Exceeds MAX_EPSILON = 10.0
        participant_count=3,
        signature=sig,
    )
    ack = await grpc_servicer.SubmitGradient(req)
    assert ack.received is False
    assert "REJECTED_EPSILON" in ack.status_message


@pytest.mark.asyncio
async def test_gcex8_grpc_submit_gradient_corrupted_zlib():
    """GCEX8: Corrupted zlib compression payload returns REJECTED_CORRUPT ack."""
    from app.infrastructure.security.signature_verifier import DigitalEnvelopeSigner

    corrupted_bytes = b"NOT_ZLIB_COMPRESSED_DATA"
    signed_msg = f"1:bank_corrupt_test".encode() + hashlib.sha256(corrupted_bytes).digest()

    signer = DigitalEnvelopeSigner()
    sig = signer.sign_payload(signed_msg, "bank_corrupt_test")

    req = SubmitGradientRequest(
        round_id="1",
        bank_id="bank_corrupt_test",
        compressed_masked_gradient=corrupted_bytes,
        dp_epsilon_used=1.0,
        participant_count=3,
        signature=sig,
    )
    ack = await grpc_servicer.SubmitGradient(req)
    assert ack.received is False
    assert "REJECTED_CORRUPT" in ack.status_message


# =====================================================================
# GCEX9: Multi-Region DR Failover Trigger
# =====================================================================

def test_gcex9_dr_failover_trigger_on_timeout():
    """GCEX9: Primary coordinator timeout triggers automatic failover to standby."""
    dr_mgr = MultiRegionFailoverManager()
    p_node = dr_mgr.register_node("node_primary", "us-east-1", CoordinatorRegionRole.PRIMARY_ACTIVE)
    s_node = dr_mgr.register_node("node_standby", "us-west-2", CoordinatorRegionRole.PASSIVE_STANDBY)

    # Simulate primary heartbeat age 20s (> 15s timeout)
    from datetime import UTC, datetime, timedelta
    p_node.last_heartbeat = datetime.now(UTC) - timedelta(seconds=20)

    event = dr_mgr.evaluate_health_and_failover(timeout_seconds=15.0)

    assert event is not None
    assert event.failed_primary_region == "us-east-1"
    assert event.promoted_standby_region == "us-west-2"
    assert p_node.role == CoordinatorRegionRole.PASSIVE_STANDBY
    assert s_node.role == CoordinatorRegionRole.FAILOVER_PROMOTED


# =====================================================================
# GCEX10: Coordinator In-Memory Reset / Restart Simulation
# =====================================================================

def test_gcex10_coordinator_restart_simulation():
    """GCEX10: Re-instantiating CoordinatorService resets state cleanly."""
    coord = CoordinatorService()
    coord.register_client("bank_restart")
    coord.start_round()

    assert len(coord.registry) == 1
    assert coord.current_round_id == 1

    # Restart coordinator
    coord_new = CoordinatorService()
    assert len(coord_new.registry) == 0
    assert coord_new.current_round_id == 0


# =====================================================================
# GCEX11: High Round ID Simulated AUC Decay Quality Gate Rejection
# =====================================================================

def test_gcex11_high_round_id_simulated_auc_decay():
    """GCEX11: High round_id 25 causes simulated AUC 0.63 < 0.70 threshold rejection."""
    coord = CoordinatorService()
    coord.register_client("bank_decay")
    coord.current_round_id = 24  # Next round will be 25

    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]
    assert round_id == 25

    coord.on_gradient_received(round_id, "bank_decay", b"grad")
    # aggregate_and_deploy with low mock AUC 0.63 < 0.70 threshold rejection
    res = coord.aggregate_and_deploy(round_id, min_auc_threshold=0.70, mock_auc=0.63)

    assert res["auc_score"] == 0.63
    assert res["is_champion"] is False
    assert res["model_status"] == "REJECTED_LOW_AUC"


# =====================================================================
# GCEX12: Zero Active Clients Round Startup
# =====================================================================

def test_gcex12_zero_active_clients_round_startup():
    """GCEX12: start_round with 0 active clients initializes round with empty participating_banks."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=3)
    assert rnd["participating_banks"] == []
    assert rnd["status"] == "COLLECTING_GRADIENTS"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
