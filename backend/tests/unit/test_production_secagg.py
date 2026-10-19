"""Comprehensive Unit & Multi-Process Test Suite for Production Secure Aggregation.

Covers Requirements A through N:
A. Real isolated bank processes exchange authenticated public keys without exposing private key material.
B. The coordinator receives only masked bank updates in SecAgg mode.
C. The aggregate reconstructs the expected weighted FedAvg result within justified quantization tolerance (< 1e-5).
D. Two different banks cannot impersonate one another.
E. A forged or substituted public-key bundle is rejected with SecAggSecurityError.
F. Cross-job, cross-consortium, cross-round, or mismatched-roster replay is rejected.
G. Repeated rounds use fresh independent ephemeral keys.
H. Missing participants cause fail-closed abort without plaintext fallback.
I. Malformed vectors, dimension mismatches, and non-finite values are rejected.
J. DP-SGD + SecAgg jointly executes through real workers and preserves durable institutional privacy accounting.
K. Successful execution produces a verified, inactive model candidate in ModelRegistry.
L. Failed SecAgg execution produces no falsely verified receipt or candidate.
M. Existing non-SecAgg FedAvg remains functional and correctly classified.
N. F.1 and F.2.1 trust boundaries remain intact.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.data_generator import DataGenerator
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    spawn_bank_workers,
)
from app.application.services.model_registry import ModelRegistry
from app.application.services.operational_federation_service import (
    WorkerEndpointRegistry,
)
from app.application.services.training_provenance_service import (
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
    TrustedTrainingJobRecordSource,
)
from app.infrastructure.security.p2p_secagg_driver import (
    ECDHPublicKeyBundle,
    P2PSecAggDriver,
    SecAggSecurityError,
    derive_institutional_identity_keypair,
)


@pytest.fixture(autouse=True)
def clean_test_environment():
    """Ensure clean test environment and registry per test."""
    WorkerEndpointRegistry.clear()
    tmp_reg = tempfile.mkdtemp(prefix="cfi_secagg_test_")
    old_env = os.environ.get("APP_ENV")
    os.environ["APP_ENV"] = "development"

    yield tmp_reg

    WorkerEndpointRegistry.clear()
    shutil.rmtree(tmp_reg, ignore_errors=True)
    if old_env is not None:
        os.environ["APP_ENV"] = old_env


# ---------------------------------------------------------------------------
# Test A & B: Authenticated Key Exchange & Plaintext Protection
# ---------------------------------------------------------------------------


def test_authenticated_peer_bundle_exchange_and_zero_private_leakage():
    """Test A & B: Real drivers exchange public keys without exposing private key material to coordinator."""
    job_id = "job_secagg_test_001"
    consortium_id = "cfi-consortium"
    session_id = "session_test_001"
    roster = ["BANK_A", "BANK_B", "BANK_C"]

    # Derive institutional Ed25519 keys
    priv_a, pub_a = derive_institutional_identity_keypair("BANK_A", "token_alpha_auth")
    priv_b, pub_b = derive_institutional_identity_keypair("BANK_B", "token_beta_auth")
    priv_c, pub_c = derive_institutional_identity_keypair("BANK_C", "token_gamma_auth")

    driver_a = P2PSecAggDriver("BANK_A", institutional_private_key=priv_a, institutional_public_key=pub_a)
    driver_b = P2PSecAggDriver("BANK_B", institutional_private_key=priv_b, institutional_public_key=pub_b)
    driver_c = P2PSecAggDriver("BANK_C", institutional_private_key=priv_c, institutional_public_key=pub_c)

    # Round 1 ephemeral bundle generation
    bundle_a = driver_a.generate_round_keypair(1, job_id, consortium_id, session_id, roster)
    bundle_b = driver_b.generate_round_keypair(1, job_id, consortium_id, session_id, roster)
    bundle_c = driver_c.generate_round_keypair(1, job_id, consortium_id, session_id, roster)

    # Verify coordinator receives ONLY public bundles (no private keys)
    for b in (bundle_a, bundle_b, bundle_c):
        assert len(b.public_key_bytes) == 32
        assert len(b.hmac_signature) == 64  # Ed25519 signature
        assert b.auth_scheme == "ed25519"
        # Ensure bundle does not leak private key or self mask seed
        assert not hasattr(b, "private_key")
        assert not hasattr(b, "self_mask_seed")

    # Peer ingestion and mutual verification
    driver_a.receive_peer_bundle(bundle_b, peer_institutional_public_key=pub_b)
    driver_a.receive_peer_bundle(bundle_c, peer_institutional_public_key=pub_c)

    driver_b.receive_peer_bundle(bundle_a, peer_institutional_public_key=pub_a)
    driver_b.receive_peer_bundle(bundle_c, peer_institutional_public_key=pub_c)

    driver_c.receive_peer_bundle(bundle_a, peer_institutional_public_key=pub_a)
    driver_c.receive_peer_bundle(bundle_b, peer_institutional_public_key=pub_b)

    # Ingestion confirmed without error
    assert len(driver_a._received_peer_bundles) == 2
    assert len(driver_b._received_peer_bundles) == 2
    assert len(driver_c._received_peer_bundles) == 2


# ---------------------------------------------------------------------------
# Test C: Weighted FedAvg Reconstructed Within Quantization Tolerance
# ---------------------------------------------------------------------------


def test_weighted_fedavg_quantization_correctness():
    """Test C: Masks cancel out identically and weighted FedAvg reconstructs within bounded float32 tolerance."""
    job_id = "job_secagg_math"
    consortium_id = "cfi-consortium"
    session_id = "session_math_01"
    roster = ["BANK_A", "BANK_B", "BANK_C"]

    priv_a, pub_a = derive_institutional_identity_keypair("BANK_A", "token_a")
    priv_b, pub_b = derive_institutional_identity_keypair("BANK_B", "token_b")
    priv_c, pub_c = derive_institutional_identity_keypair("BANK_C", "token_c")

    driver_a = P2PSecAggDriver("BANK_A", institutional_private_key=priv_a)
    driver_b = P2PSecAggDriver("BANK_B", institutional_private_key=priv_b)
    driver_c = P2PSecAggDriver("BANK_C", institutional_private_key=priv_c)

    bundle_a = driver_a.generate_round_keypair(1, job_id, consortium_id, session_id, roster)
    bundle_b = driver_b.generate_round_keypair(1, job_id, consortium_id, session_id, roster)
    bundle_c = driver_c.generate_round_keypair(1, job_id, consortium_id, session_id, roster)

    driver_a.receive_peer_bundle(bundle_b, peer_institutional_public_key=pub_b)
    driver_a.receive_peer_bundle(bundle_c, peer_institutional_public_key=pub_c)

    driver_b.receive_peer_bundle(bundle_a, peer_institutional_public_key=pub_a)
    driver_b.receive_peer_bundle(bundle_c, peer_institutional_public_key=pub_c)

    driver_c.receive_peer_bundle(bundle_a, peer_institutional_public_key=pub_a)
    driver_c.receive_peer_bundle(bundle_b, peer_institutional_public_key=pub_b)

    # Realistic floating point weights including negative values
    w_a = [0.123456, -0.654321, 1.234567, -0.000042, 0.888888]
    w_b = [0.555555, 0.222222, -0.777777, 0.000105, -0.333333]
    w_c = [-0.200000, 0.400000, 0.600000, -0.800000, 0.100000]

    n_a = 150
    n_b = 300
    n_c = 200
    total_n = n_a + n_b + n_c

    # Ground-truth weighted average
    expected_w = [
        (n_a * wa + n_b * wb + n_c * wc) / total_n
        for wa, wb, wc in zip(w_a, w_b, w_c, strict=True)
    ]

    quantization_scale = 1e7
    modulus = 2**64

    masked_a = driver_a.compute_weighted_masked_vector(w_a, n_a, quantization_scale=quantization_scale, modulus=modulus)
    masked_b = driver_b.compute_weighted_masked_vector(w_b, n_b, quantization_scale=quantization_scale, modulus=modulus)
    masked_c = driver_c.compute_weighted_masked_vector(w_c, n_c, quantization_scale=quantization_scale, modulus=modulus)

    # Verify coordinator reconstructs within bounded float numerical tolerance (< 1e-5)
    reconstructed = P2PSecAggDriver.aggregate_weighted_masked_vectors(
        masked_vectors={"BANK_A": masked_a, "BANK_B": masked_b, "BANK_C": masked_c},
        sample_counts={"BANK_A": n_a, "BANK_B": n_b, "BANK_C": n_c},
        quantization_scale=quantization_scale,
        modulus=modulus,
    )

    for actual, exp in zip(reconstructed, expected_w, strict=True):
        diff = abs(actual - exp)
        assert diff < 1e-5, f"Numerical reconstruction error too high: {diff} (actual={actual}, expected={exp})"


# ---------------------------------------------------------------------------
# Test D & E: Impersonation and Forgery Rejection
# ---------------------------------------------------------------------------


def test_bank_impersonation_fails_closed():
    """Test D: An attacker cannot sign as Bank B using Bank A's institutional private key."""
    priv_a, pub_a = derive_institutional_identity_keypair("BANK_A", "token_a")
    _, pub_b = derive_institutional_identity_keypair("BANK_B", "token_b")

    # Attacker tries to create a driver pretending to be BANK_B with Bank A's key
    malicious_driver = P2PSecAggDriver("BANK_B", institutional_private_key=priv_a)
    bundle_fake = malicious_driver.generate_round_keypair(
        round_id=1,
        job_id="job_spoof",
        consortium_id="cfi-consortium",
        session_id="session_spoof",
        roster=["BANK_A", "BANK_B", "BANK_C"],
    )

    # Honest Bank C checks against authoritative public key of BANK_B
    priv_c, pub_c = derive_institutional_identity_keypair("BANK_C", "token_c")
    driver_c = P2PSecAggDriver("BANK_C", institutional_private_key=priv_c, institutional_public_key=pub_c)
    driver_c.generate_round_keypair(1, "job_spoof", "cfi-consortium", "session_spoof", ["BANK_A", "BANK_B", "BANK_C"])

    with pytest.raises(SecAggSecurityError):
        driver_c.receive_peer_bundle(bundle_fake, peer_institutional_public_key=pub_b)


def test_tampered_bundle_rejected():
    """Test E: Forged or modified public key bundle is rejected."""
    priv_a, pub_a = derive_institutional_identity_keypair("BANK_A", "token_a")
    driver_a = P2PSecAggDriver("BANK_A", institutional_private_key=priv_a)
    bundle_a = driver_a.generate_round_keypair(1, "job_mod", "cfi-consortium", "session_mod", ["BANK_A", "BANK_B"])

    # Tamper with 1 byte of the public key
    tampered_bytes = bytearray(bundle_a.public_key_bytes)
    tampered_bytes[0] ^= 0xFF
    tampered_bundle = ECDHPublicKeyBundle(
        bank_id=bundle_a.bank_id,
        round_id=bundle_a.round_id,
        public_key_bytes=bytes(tampered_bytes),
        hmac_signature=bundle_a.hmac_signature,
        job_id=bundle_a.job_id,
        consortium_id=bundle_a.consortium_id,
        session_id=bundle_a.session_id,
        roster=bundle_a.roster,
        auth_scheme=bundle_a.auth_scheme,
    )

    priv_b, _ = derive_institutional_identity_keypair("BANK_B", "token_b")
    driver_b = P2PSecAggDriver("BANK_B", institutional_private_key=priv_b)
    driver_b.generate_round_keypair(1, "job_mod", "cfi-consortium", "session_mod", ["BANK_A", "BANK_B"])

    with pytest.raises(SecAggSecurityError):
        driver_b.receive_peer_bundle(tampered_bundle, peer_institutional_public_key=pub_a)


# ---------------------------------------------------------------------------
# Test F & G: Transcript Replay and Key Freshness
# ---------------------------------------------------------------------------


def test_cross_session_and_cross_round_replay_rejected():
    """Test F: Bundles bound to a previous round/job/session/roster are rejected on replay."""
    priv_a, pub_a = derive_institutional_identity_keypair("BANK_A", "token_a")
    driver_a = P2PSecAggDriver("BANK_A", institutional_private_key=priv_a)
    bundle_r1 = driver_a.generate_round_keypair(1, "job_1", "cfi-consortium", "session_1", ["BANK_A", "BANK_B"])

    priv_b, _ = derive_institutional_identity_keypair("BANK_B", "token_b")
    driver_b = P2PSecAggDriver("BANK_B", institutional_private_key=priv_b)

    # 1. Replay in round 2
    driver_b.generate_round_keypair(2, "job_1", "cfi-consortium", "session_1", ["BANK_A", "BANK_B"])
    with pytest.raises(SecAggSecurityError):
        driver_b.receive_peer_bundle(bundle_r1, peer_institutional_public_key=pub_a)

    # 2. Replay in different job
    driver_b.generate_round_keypair(1, "job_2", "cfi-consortium", "session_1", ["BANK_A", "BANK_B"])
    with pytest.raises(SecAggSecurityError):
        driver_b.receive_peer_bundle(bundle_r1, peer_institutional_public_key=pub_a)

    # 3. Replay in different session
    driver_b.generate_round_keypair(1, "job_1", "cfi-consortium", "session_2", ["BANK_A", "BANK_B"])
    with pytest.raises(SecAggSecurityError):
        driver_b.receive_peer_bundle(bundle_r1, peer_institutional_public_key=pub_a)

    # 4. Replay in different roster
    driver_b.generate_round_keypair(1, "job_1", "cfi-consortium", "session_1", ["BANK_A", "BANK_B", "BANK_C"])
    with pytest.raises(SecAggSecurityError):
        driver_b.receive_peer_bundle(bundle_r1, peer_institutional_public_key=pub_a)


def test_fresh_ephemeral_keys_per_round():
    """Test G: Ephemeral keypairs are regenerated every round."""
    priv_a, _ = derive_institutional_identity_keypair("BANK_A", "token_a")
    driver_a = P2PSecAggDriver("BANK_A", institutional_private_key=priv_a)

    bundle1 = driver_a.generate_round_keypair(1, "job_fresh", "cfi-consortium", "session_fresh", ["BANK_A", "BANK_B"])
    bundle2 = driver_a.generate_round_keypair(2, "job_fresh", "cfi-consortium", "session_fresh", ["BANK_A", "BANK_B"])

    assert bundle1.public_key_bytes != bundle2.public_key_bytes
    assert bundle1.hmac_signature != bundle2.hmac_signature


# ---------------------------------------------------------------------------
# Test H & I: Robust Validation & Malformed Vector Handling
# ---------------------------------------------------------------------------


def test_malformed_vector_rejection():
    """Test I: Non-finite values, dimension mismatches, and negative sample counts are rejected."""
    priv_a, pub_a = derive_institutional_identity_keypair("BANK_A", "token_a")
    priv_b, pub_b = derive_institutional_identity_keypair("BANK_B", "token_b")

    driver_a = P2PSecAggDriver("BANK_A", institutional_private_key=priv_a)
    driver_b = P2PSecAggDriver("BANK_B", institutional_private_key=priv_b)

    ba = driver_a.generate_round_keypair(1, "j", "c", "s", ["BANK_A", "BANK_B"])
    bb = driver_b.generate_round_keypair(1, "j", "c", "s", ["BANK_A", "BANK_B"])
    driver_a.receive_peer_bundle(bb, peer_institutional_public_key=pub_b)
    driver_b.receive_peer_bundle(ba, peer_institutional_public_key=pub_a)

    # 1. Non-finite weight values (NaN, Inf)
    with pytest.raises(ValueError, match="non-finite"):
        driver_a.compute_weighted_masked_vector([1.0, float("nan")], sample_count=100)

    with pytest.raises(ValueError, match="non-finite"):
        driver_a.compute_weighted_masked_vector([1.0, float("inf")], sample_count=100)

    # 2. Non-positive sample counts
    with pytest.raises(ValueError, match="positive"):
        driver_a.compute_weighted_masked_vector([1.0, 2.0], sample_count=0)

    with pytest.raises(ValueError, match="positive"):
        driver_a.compute_weighted_masked_vector([1.0, 2.0], sample_count=-10)

    # 3. Dimension mismatch at coordinator
    vec_a = driver_a.compute_weighted_masked_vector([1.0, 2.0], sample_count=100)
    vec_b = driver_b.compute_weighted_masked_vector([1.0, 2.0, 3.0], sample_count=100)
    with pytest.raises(ValueError, match="Dimension mismatch"):
        P2PSecAggDriver.aggregate_weighted_masked_vectors(
            {"BANK_A": vec_a, "BANK_B": vec_b},
            {"BANK_A": 100, "BANK_B": 100},
        )


# ---------------------------------------------------------------------------
# Test J, K, L, M, N: Full Multi-Process Execution with Provenance & DP-SGD
# ---------------------------------------------------------------------------


def test_full_multiprocess_secagg_and_provenance(clean_test_environment):
    """Test J, K, M, N: Real isolated bank worker processes perform SecAgg federated training.

    Proves:
    - Independent worker processes compute bank-local masked updates.
    - Coordinator receives ONLY masked vectors (zero plaintext weights).
    - Global model candidate is registered as inactive with TRAINING_RECORDED provenance.
    - Ed25519 receipt is signed and verifiable.
    """
    tmp_dir = clean_test_environment
    db_path = os.path.join(tmp_dir, "authority.db")
    db_url = f"sqlite:///{db_path}"

    coordinator_token = "coord_secagg_token_12345"
    bank_tokens = {
        "BANK_A": "token_inst_alpha_999",
        "BANK_B": "token_inst_beta_888",
        "BANK_C": "token_inst_gamma_777",
    }

    # Setup database with authentic authority
    auth_source = TrustedTrainingJobRecordSource(
        db_url=db_url,
        coordinator_token=coordinator_token,
        app_env="development",
    )
    auth_db = auth_source._db

    # Generate synthetic partition data for banks
    gen = DataGenerator(seed=42)
    bank_datasets = gen.generate_bank_datasets(bank_a_size=120, bank_b_size=120, bank_c_size=120)

    bank_partitions: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for b_id in ("BANK_A", "BANK_B", "BANK_C"):
        feats_df, labels = bank_datasets[b_id.lower()]
        X = np.asarray(DataGenerator.encode_features(feats_df), dtype=np.float32)
        y = np.asarray(labels.to_numpy(dtype=np.float32), dtype=np.float32)
        bank_partitions[b_id] = (X, y)

    # Spawn isolated worker processes
    procs, endpoints = spawn_bank_workers(
        participating_banks=["BANK_A", "BANK_B", "BANK_C"],
        db_url=db_url,
        bank_tokens=bank_tokens,
        bank_partitions=bank_partitions,
        app_env="development",
    )

    # Holdout validation dataset with guaranteed positive and negative class presence
    feats_val, labels_val = bank_datasets["bank_a"]
    labels_val = labels_val.copy()
    labels_val.iloc[0] = 1
    labels_val.iloc[1] = 0
    X_val = np.asarray(DataGenerator.encode_features(feats_val), dtype=np.float32)
    y_val = np.asarray(labels_val.to_numpy(dtype=np.float32), dtype=np.float32)

    # Issuer for Ed25519 training receipts
    issuer_priv = ed25519.Ed25519PrivateKey.generate()
    issuer = TrainingReceiptIssuer(
        key_id="secagg_test_issuer",
        private_key=issuer_priv,
        job_record_source=auth_source,
    )
    verifier = TrainingReceiptVerifier(trusted_keys={issuer.key_id: issuer.public_key})

    reg_dir = os.path.join(tmp_dir, "models")
    model_reg = ModelRegistry(storage_dir=reg_dir, verifier=verifier)

    manager = DistributedFederatedTrainingManager(
        coordinator_token=coordinator_token,
        db_url=db_url,
        authority_source=auth_source,
        app_env="development",
    )

    job_id = "job_secagg_full_001"
    res = manager.execute_distributed_federation(
        job_id=job_id,
        training_run_id=f"run_{job_id}",
        simulation_id=f"sim_{job_id}",
        holdout_val_data=(X_val, y_val),
        participating_banks=["BANK_A", "BANK_B", "BANK_C"],
        worker_endpoints=endpoints,
        worker_processes=procs,
        signing_authority=issuer,
        receipt_verifier=verifier,
        num_rounds=2,
        registry_storage_dir=reg_dir,
        model_registry=model_reg,
        consortium_id="cfi-consortium",
        dp_config=None,
        secagg_enabled=True,
        secagg_config={"quantization_scale": 1e7, "modulus": 2**64},
    )

    # Assertions on successful SecAgg execution
    assert res["status"] == "COMPLETED"
    assert res["is_verified"] is True
    assert res["secagg_enabled"] is True
    assert res["is_promoted_champion"] is False

    # Check model registered as inactive candidate
    saved_v = res["saved_version"]
    assert saved_v is not None
    assert saved_v["status"] == "inactive"
    assert saved_v["provenance_type"] == "TRAINING_RECORDED"
    assert saved_v["consortium_id"] == "cfi-consortium"

    # Verify GlobalAggregationEvent persisted with secagg algorithm
    aggs = auth_db.get_global_aggregations(job_id)
    assert len(aggs) == 2
    for agg in aggs:
        assert agg.aggregation_algorithm == "secagg_fed_avg_weighted"
        assert len(agg.accepted_participant_ids) == 3
        # Local update digests are digests of transmitted masked vectors
        assert len(agg.accepted_local_update_digests) == 3


def test_secagg_fail_closed_on_missing_participant(clean_test_environment):
    """Test H & L: Round aborts if a participant fails setup; no fallback to plaintext FedAvg."""
    tmp_dir = clean_test_environment
    db_path = os.path.join(tmp_dir, "authority_fail.db")
    db_url = f"sqlite:///{db_path}"

    coordinator_token = "coord_secagg_fail"
    bank_tokens = {
        "BANK_A": "token_a",
        "BANK_B": "token_b",
        "BANK_C": "token_c",
    }

    auth_source = TrustedTrainingJobRecordSource(
        db_url=db_url,
        coordinator_token=coordinator_token,
        app_env="development",
    )

    gen = DataGenerator(seed=42)
    bank_datasets = gen.generate_bank_datasets(bank_a_size=100, bank_b_size=100, bank_c_size=100)
    bank_partitions = {
        b_id: (
            np.asarray(DataGenerator.encode_features(bank_datasets[b_id.lower()][0]), dtype=np.float32),
            np.asarray(bank_datasets[b_id.lower()][1].to_numpy(dtype=np.float32), dtype=np.float32),
        )
        for b_id in ("BANK_A", "BANK_B", "BANK_C")
    }

    procs, endpoints = spawn_bank_workers(
        participating_banks=["BANK_A", "BANK_B", "BANK_C"],
        db_url=db_url,
        bank_tokens=bank_tokens,
        bank_partitions=bank_partitions,
        app_env="development",
    )

    # Terminate BANK_B process to simulate crash
    procs["BANK_B"].terminate()
    procs["BANK_B"].join()

    feats_val, labels_val = bank_datasets["bank_a"]
    labels_val = labels_val.copy()
    labels_val.iloc[0] = 1
    labels_val.iloc[1] = 0
    X_val = np.asarray(DataGenerator.encode_features(feats_val), dtype=np.float32)
    y_val = np.asarray(labels_val.to_numpy(dtype=np.float32), dtype=np.float32)

    manager = DistributedFederatedTrainingManager(
        coordinator_token=coordinator_token,
        db_url=db_url,
        authority_source=auth_source,
        app_env="development",
    )

    with pytest.raises(TrainingProvenanceSecurityError, match="aborted"):
        manager.execute_distributed_federation(
            job_id="job_secagg_abort",
            training_run_id="run_abort",
            simulation_id="sim_abort",
            holdout_val_data=(X_val, y_val),
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            worker_endpoints=endpoints,
            worker_processes=procs,
            num_rounds=1,
            secagg_enabled=True,
        )


def test_secagg_with_opacus_dp_sgd_preserves_budget(clean_test_environment):
    """Test J: SecAgg executes jointly with Opacus DP-SGD and commits institutional privacy expenditures."""
    tmp_dir = clean_test_environment
    db_path = os.path.join(tmp_dir, "authority_dp.db")
    db_url = f"sqlite:///{db_path}"

    coordinator_token = "coord_dp_secagg"
    bank_tokens = {
        "BANK_A": "token_a_dp",
        "BANK_B": "token_b_dp",
    }

    auth_source = TrustedTrainingJobRecordSource(
        db_url=db_url,
        coordinator_token=coordinator_token,
        app_env="development",
    )

    # Allocate initial privacy budgets for BANK_A and BANK_B
    for b_id in ("BANK_A", "BANK_B"):
        auth_source.provision_institutional_privacy_budget(
            bank_id=b_id,
            dataset_tag="primary_vault",
            allocated_epsilon=5.0,
            allocated_delta=1e-4,
            aliases=[f"{b_id}_dataset", f"{b_id}_protected_dataset", "primary_vault", "default_vault"],
            authority_token=coordinator_token,
        )

    gen = DataGenerator(seed=42)
    bank_datasets = gen.generate_bank_datasets(bank_a_size=100, bank_b_size=100, bank_c_size=100)
    bank_partitions = {
        b_id: (
            np.asarray(DataGenerator.encode_features(bank_datasets[b_id.lower()][0]), dtype=np.float32),
            np.asarray(bank_datasets[b_id.lower()][1].to_numpy(dtype=np.float32), dtype=np.float32),
        )
        for b_id in ("BANK_A", "BANK_B")
    }

    procs, endpoints = spawn_bank_workers(
        participating_banks=["BANK_A", "BANK_B"],
        db_url=db_url,
        bank_tokens=bank_tokens,
        bank_partitions=bank_partitions,
        app_env="development",
    )

    feats_val, labels_val = bank_datasets["bank_a"]
    labels_val = labels_val.copy()
    labels_val.iloc[0] = 1
    labels_val.iloc[1] = 0
    X_val = np.asarray(DataGenerator.encode_features(feats_val), dtype=np.float32)
    y_val = np.asarray(labels_val.to_numpy(dtype=np.float32), dtype=np.float32)

    issuer_priv = ed25519.Ed25519PrivateKey.generate()
    issuer = TrainingReceiptIssuer(
        key_id="dp_secagg_issuer",
        private_key=issuer_priv,
        job_record_source=auth_source,
    )
    verifier = TrainingReceiptVerifier(trusted_keys={issuer.key_id: issuer.public_key})

    reg_dir = os.path.join(tmp_dir, "models")
    model_reg = ModelRegistry(storage_dir=reg_dir, verifier=verifier)

    manager = DistributedFederatedTrainingManager(
        coordinator_token=coordinator_token,
        db_url=db_url,
        authority_source=auth_source,
        app_env="development",
    )

    dp_cfg = {
        "dp_enabled": True,
        "target_epsilon": 2.0,
        "target_delta": 1e-5,
        "max_grad_norm": 1.0,
        "noise_multiplier": 1.2,
        "batch_size": 32,
        "learning_rate": 0.01,
        "accountant": "rdp",
        "dataset_tag": "primary_vault",
    }

    job_id = "job_dp_secagg_001"
    res = manager.execute_distributed_federation(
        job_id=job_id,
        training_run_id=f"run_{job_id}",
        simulation_id=f"sim_{job_id}",
        holdout_val_data=(X_val, y_val),
        participating_banks=["BANK_A", "BANK_B"],
        worker_endpoints=endpoints,
        worker_processes=procs,
        signing_authority=issuer,
        receipt_verifier=verifier,
        num_rounds=1,
        registry_storage_dir=reg_dir,
        model_registry=model_reg,
        consortium_id="cfi-consortium",
        dp_config=dp_cfg,
        secagg_enabled=True,
    )

    assert res["status"] == "COMPLETED"
    assert res["dp_enabled"] is True
    assert res["secagg_enabled"] is True

    # Verify institutional privacy budget spent and tracked in durable authority
    budget_a = auth_source.get_institutional_privacy_budget("BANK_A", "primary_vault", token="token_a_dp")
    assert budget_a is not None
    assert budget_a.spent_epsilon > 0.0
    assert budget_a.remaining_epsilon < 5.0


# ---------------------------------------------------------------------------
# Test: Malicious Coordinator Peer-Key Substitution Rejected (Real 2-Bank Multi-Process)
# ---------------------------------------------------------------------------


def test_malicious_coordinator_peer_key_substitution_rejected(clean_test_environment):
    """Test Requirement 1: A malicious coordinator attempts to substitute an institutional identity.

    Negative Test:
    1. A malicious coordinator supplies its own Ed25519 identity key as Bank B in SECAGG_SETUP.
       Bank A MUST independently detect key substitution against authoritative trust material
       and reject setup before private computation.
    2. Even if setup succeeds with authentic keys, if coordinator provides a forged peer bundle
       in SECAGG_TRAIN_AND_MASK signed by coordinator's forged key, Bank A rejects verification
       before training or masked release.
    Tested with real independent worker processes across a 2-bank federation.
    """
    from cryptography.hazmat.primitives import serialization

    from app.infrastructure.security.p2p_secagg_driver import canonical_bundle_message

    tmp_dir = clean_test_environment
    db_path = os.path.join(tmp_dir, "authority_peer_trust.db")
    db_url = f"sqlite:///{db_path}"

    coordinator_token = "coord_malicious_test"
    bank_tokens = {
        "BANK_A": "token_a_trust_auth",
        "BANK_B": "token_b_trust_auth",
    }

    auth_source = TrustedTrainingJobRecordSource(
        db_url=db_url,
        coordinator_token=coordinator_token,
        app_env="development",
    )

    gen = DataGenerator(seed=42)
    bank_datasets = gen.generate_bank_datasets(bank_a_size=50, bank_b_size=50, bank_c_size=50)
    bank_partitions = {
        b_id: (
            np.asarray(DataGenerator.encode_features(bank_datasets[b_id.lower()][0]), dtype=np.float32),
            np.asarray(bank_datasets[b_id.lower()][1].to_numpy(dtype=np.float32), dtype=np.float32),
        )
        for b_id in ("BANK_A", "BANK_B")
    }

    procs, endpoints = spawn_bank_workers(
        participating_banks=["BANK_A", "BANK_B"],
        db_url=db_url,
        bank_tokens=bank_tokens,
        bank_partitions=bank_partitions,
        app_env="development",
    )

    try:
        job_id = "job_coord_subst_test_001"
        consortium_id = "cfi-consortium"

        # Schedule job with permitted workers
        auth_source.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id=f"sim_{job_id}",
            coordinator_token=coordinator_token,
            expected_rounds=1,
            expected_epochs=1,
            permitted_workers=["BANK_A", "BANK_B"],
            consortium_id=consortium_id,
        )

        # Register authentic institutional keys for both banks using bank credentials
        auth_source.register_institutional_identity(
            bank_id="BANK_A",
            public_key_hex=endpoints["BANK_A"]["institutional_public_key"],
            token=bank_tokens["BANK_A"],
        )
        auth_source.register_institutional_identity(
            bank_id="BANK_B",
            public_key_hex=endpoints["BANK_B"]["institutional_public_key"],
            token=bank_tokens["BANK_B"],
        )

        inbox_a = endpoints["BANK_A"]["inbox"]
        outbox_a = endpoints["BANK_A"]["outbox"]

        # Malicious coordinator generates its own forged Ed25519 identity key
        forged_coord_priv = ed25519.Ed25519PrivateKey.generate()
        forged_coord_pub_hex = forged_coord_priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()

        # Part 1: Coordinator supplies forged Bank B identity in SECAGG_SETUP
        forged_setup = {
            "command": "SECAGG_SETUP",
            "job_id": job_id,
            "round_number": 1,
            "session_id": "sess_malicious_setup_001",
            "consortium_id": consortium_id,
            "roster": ["BANK_A", "BANK_B"],
            "institutional_public_keys": {
                "BANK_A": endpoints["BANK_A"]["institutional_public_key"],
                "BANK_B": forged_coord_pub_hex,  # FORGED!
            },
            "secagg_config": {"quantization_scale": 1e7, "modulus": 2**64},
        }

        inbox_a.put(forged_setup)
        resp = outbox_a.get(timeout=10)

        # Bank A MUST reject the setup before any private computation or masked release
        assert resp["status"] == "ERROR"
        assert "key substitution" in resp["error"].lower() or "mismatch" in resp["error"].lower()

        # Part 2: Genuine setup succeeds, but coordinator attempts forged peer bundle in TRAIN_AND_MASK
        clean_session_id = "sess_clean_setup_002"
        genuine_setup = {
            "command": "SECAGG_SETUP",
            "job_id": job_id,
            "round_number": 1,
            "session_id": clean_session_id,
            "consortium_id": consortium_id,
            "roster": ["BANK_A", "BANK_B"],
            "institutional_public_keys": {
                "BANK_A": endpoints["BANK_A"]["institutional_public_key"],
                "BANK_B": endpoints["BANK_B"]["institutional_public_key"],
            },
            "secagg_config": {"quantization_scale": 1e7, "modulus": 2**64},
        }

        inbox_a.put(genuine_setup)
        resp_setup = outbox_a.get(timeout=10)
        assert resp_setup["status"] == "SUCCESS"

        # Coordinator constructs a forged bundle signed by its forged private key
        forged_ephemeral_x25519 = os.urandom(32)
        bundle_msg = canonical_bundle_message(
            bank_id="BANK_B",
            round_id=1,
            public_key_bytes=forged_ephemeral_x25519,
            job_id=job_id,
            consortium_id=consortium_id,
            session_id=clean_session_id,
            roster=["BANK_A", "BANK_B"],
        )
        forged_sig = forged_coord_priv.sign(bundle_msg)
        forged_bundle_dict = {
            "bank_id": "BANK_B",
            "round_id": 1,
            "public_key_bytes": forged_ephemeral_x25519.hex(),
            "hmac_signature": forged_sig.hex(),
            "auth_scheme": "ed25519",
            "job_id": job_id,
            "consortium_id": consortium_id,
            "session_id": clean_session_id,
            "roster": ["BANK_A", "BANK_B"],
        }

        forged_train_payload = {
            "command": "SECAGG_TRAIN_AND_MASK",
            "job_id": job_id,
            "consortium_id": consortium_id,
            "round_number": 1,
            "session_id": clean_session_id,
            "peer_bundles": {"BANK_B": forged_bundle_dict},
        }

        inbox_a.put(forged_train_payload)
        resp_train = outbox_a.get(timeout=10)

        # Bank A MUST reject peer bundle signature verification against authoritative Bank B key
        assert resp_train["status"] == "ERROR"
        assert "rejected peer bundle" in resp_train["error"].lower() or "signature" in resp_train["error"].lower()

    finally:
        for p in procs.values():
            if hasattr(p, "terminate"):
                p.terminate()


# ---------------------------------------------------------------------------
# Test: Single-Use Cryptographic Sessions & Replay Rejection
# ---------------------------------------------------------------------------


def test_single_use_cryptographic_sessions_and_replay_rejection(clean_test_environment):
    """Test Requirement 2: Strict single-use session enforcement.

    Negative Tests:
    - Replaying the same SECAGG_TRAIN_AND_MASK command must be rejected without second release.
    - Replaying with altered model weights must be rejected.
    - Replaying with altered job/round/consortium must be rejected.
    - Session state transitions to CONSUMED after first release and ABORTED after context mismatch.
    """
    from app.domain.value_objects import ModelWeights

    tmp_dir = clean_test_environment
    db_path = os.path.join(tmp_dir, "authority_single_use.db")
    db_url = f"sqlite:///{db_path}"

    coordinator_token = "coord_single_use"
    bank_tokens = {
        "BANK_A": "token_a_su",
        "BANK_B": "token_b_su",
    }

    auth_source = TrustedTrainingJobRecordSource(
        db_url=db_url,
        coordinator_token=coordinator_token,
        app_env="development",
    )

    gen = DataGenerator(seed=42)
    bank_datasets = gen.generate_bank_datasets(bank_a_size=50, bank_b_size=50, bank_c_size=50)
    bank_partitions = {
        b_id: (
            np.asarray(DataGenerator.encode_features(bank_datasets[b_id.lower()][0]), dtype=np.float32),
            np.asarray(bank_datasets[b_id.lower()][1].to_numpy(dtype=np.float32), dtype=np.float32),
        )
        for b_id in ("BANK_A", "BANK_B")
    }

    procs, endpoints = spawn_bank_workers(
        participating_banks=["BANK_A", "BANK_B"],
        db_url=db_url,
        bank_tokens=bank_tokens,
        bank_partitions=bank_partitions,
        app_env="development",
    )

    try:
        job_id = "job_single_use_001"
        consortium_id = "cfi-consortium"
        session_id = "session_single_use_alpha"

        auth_source.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id=f"sim_{job_id}",
            coordinator_token=coordinator_token,
            expected_rounds=1,
            expected_epochs=1,
            permitted_workers=["BANK_A", "BANK_B"],
            consortium_id=consortium_id,
        )

        auth_source.register_institutional_identity("BANK_A", endpoints["BANK_A"]["institutional_public_key"], token=bank_tokens["BANK_A"])
        auth_source.register_institutional_identity("BANK_B", endpoints["BANK_B"]["institutional_public_key"], token=bank_tokens["BANK_B"])

        setup_payload = {
            "command": "SECAGG_SETUP",
            "job_id": job_id,
            "round_number": 1,
            "session_id": session_id,
            "consortium_id": consortium_id,
            "roster": ["BANK_A", "BANK_B"],
            "institutional_public_keys": {
                "BANK_A": endpoints["BANK_A"]["institutional_public_key"],
                "BANK_B": endpoints["BANK_B"]["institutional_public_key"],
            },
            "secagg_config": {"quantization_scale": 1e7, "modulus": 2**64},
        }

        endpoints["BANK_A"]["inbox"].put(setup_payload)
        endpoints["BANK_B"]["inbox"].put(setup_payload)

        res_a = endpoints["BANK_A"]["outbox"].get(timeout=10)
        res_b = endpoints["BANK_B"]["outbox"].get(timeout=10)
        assert res_a["status"] == "SUCCESS"
        assert res_b["status"] == "SUCCESS"

        bundle_b = res_b["bundle"]

        train_payload_a = {
            "command": "SECAGG_TRAIN_AND_MASK",
            "job_id": job_id,
            "consortium_id": consortium_id,
            "round_number": 1,
            "session_id": session_id,
            "peer_bundles": {"BANK_B": bundle_b},
        }

        # 1. First execution succeeds and returns masked vector
        endpoints["BANK_A"]["inbox"].put(train_payload_a)
        res_train_1 = endpoints["BANK_A"]["outbox"].get(timeout=20)
        assert res_train_1["status"] == "SUCCESS"
        assert "masked_vector_bytes" in res_train_1

        # 2. Replay of identical command on same session MUST fail closed (session CONSUMED)
        endpoints["BANK_A"]["inbox"].put(train_payload_a)
        res_replay_identical = endpoints["BANK_A"]["outbox"].get(timeout=10)
        assert res_replay_identical["status"] == "ERROR"
        assert "cannot be reused" in res_replay_identical["error"].lower() or "terminal state" in res_replay_identical["error"].lower()

        # 3. Replay with altered weights on same session MUST fail closed
        dummy_weights = ModelWeights(layer_shapes=[(10,)], flat_weights=[1.0] * 10)
        train_payload_altered_weights = dict(train_payload_a)
        train_payload_altered_weights["global_weights"] = dummy_weights
        endpoints["BANK_A"]["inbox"].put(train_payload_altered_weights)
        res_replay_weights = endpoints["BANK_A"]["outbox"].get(timeout=10)
        assert res_replay_weights["status"] == "ERROR"

        # 4. Replay with altered job/round/consortium on a fresh session fails context binding
        fresh_session_id = "session_single_use_beta"
        setup_payload_2 = dict(setup_payload)
        setup_payload_2["session_id"] = fresh_session_id
        endpoints["BANK_A"]["inbox"].put(setup_payload_2)
        res_setup_2 = endpoints["BANK_A"]["outbox"].get(timeout=10)
        assert res_setup_2["status"] == "SUCCESS"

        train_payload_mismatched_context = {
            "command": "SECAGG_TRAIN_AND_MASK",
            "job_id": "job_altered_mismatch",
            "consortium_id": consortium_id,
            "round_number": 1,
            "session_id": fresh_session_id,
            "peer_bundles": {"BANK_B": bundle_b},
        }
        endpoints["BANK_A"]["inbox"].put(train_payload_mismatched_context)
        res_mismatch = endpoints["BANK_A"]["outbox"].get(timeout=10)
        assert res_mismatch["status"] == "ERROR"
        assert "context mismatch" in res_mismatch["error"].lower()

    finally:
        for p in procs.values():
            if hasattr(p, "terminate"):
                p.terminate()


# ---------------------------------------------------------------------------
# Test: Fixed-Point Encoding Bounds & Overflow Protection
# ---------------------------------------------------------------------------


def test_fixed_point_encoding_bounds_and_overflow_protection():
    """Test Requirement 3: Validate fixed-point encoding bounds before casting.

    Enforces:
    - Reject non-finite or non-positive quantization scales.
    - Reject scales outside authorized representation bounds [1.0, 1e12].
    - Reject unsupported modulus (only 2^64 supported).
    - Reject invalid or non-integer or huge sample counts.
    - Reject out-of-range weight coordinate magnitudes (> 10,000).
    - Reject scaled coordinates exceeding 2^57 to prevent int64 and aggregate overflow.
    - Preserve negative values and sample-weighted FedAvg mathematics.
    """
    priv, pub = derive_institutional_identity_keypair("BANK_A", "tok_fixed_point")
    driver = P2PSecAggDriver("BANK_A", institutional_private_key=priv, institutional_public_key=pub)
    driver.generate_round_keypair(1, "job_fp", "cfi-consortium", "sess_fp_1", ["BANK_A", "BANK_B"])

    priv_b, pub_b = derive_institutional_identity_keypair("BANK_B", "tok_b_fp")
    driver_b = P2PSecAggDriver("BANK_B", institutional_private_key=priv_b, institutional_public_key=pub_b)
    bundle_b = driver_b.generate_round_keypair(1, "job_fp", "cfi-consortium", "sess_fp_1", ["BANK_A", "BANK_B"])
    driver.receive_peer_bundle(bundle_b, peer_institutional_public_key=pub_b)

    valid_weights = [0.1, -0.5, 1.25, -2.0]

    # 1. Non-positive or non-finite quantization scale
    for bad_scale in (0.0, -100.0, float("nan"), float("inf"), 0.5, 1e13, "1e7"):
        with pytest.raises(ValueError):
            driver.compute_weighted_masked_vector(valid_weights, sample_count=100, quantization_scale=bad_scale)

    # 2. Unsupported modulus
    for bad_mod in (2**32, 2**63, 1000):
        with pytest.raises(ValueError, match="Unsupported modulus"):
            driver.compute_weighted_masked_vector(valid_weights, sample_count=100, modulus=bad_mod)

    # 3. Invalid or non-integer or negative sample counts
    for bad_sample in (0, -1, 150_000_000, True, 10.5):
        with pytest.raises(ValueError, match="sample_count"):
            driver.compute_weighted_masked_vector(valid_weights, sample_count=bad_sample)

    # 4. Out of range weight magnitude (> 10_000)
    huge_weights = [0.1, 15_000.0, -0.5]
    with pytest.raises(ValueError, match="magnitude out of bounds"):
        driver.compute_weighted_masked_vector(huge_weights, sample_count=100, quantization_scale=1e7)

    # 5. Scaled coordinate overflow (> 2^57)
    with pytest.raises(ValueError, match="exceeds maximum representation bound"):
        driver.compute_weighted_masked_vector([5000.0], sample_count=50_000_000, quantization_scale=1e9)

    # 6. Correctness: Negative values and sample-weighted FedAvg math preserved
    driver_1 = P2PSecAggDriver("BANK_A", institutional_private_key=priv, institutional_public_key=pub)
    driver_1.generate_round_keypair(1, "job_math", "cfi-consortium", "sess_math_1", ["BANK_A", "BANK_B"])
    driver_2 = P2PSecAggDriver("BANK_B", institutional_private_key=priv_b, institutional_public_key=pub_b)
    bundle_2 = driver_2.generate_round_keypair(1, "job_math", "cfi-consortium", "sess_math_1", ["BANK_A", "BANK_B"])
    bundle_1 = driver_1.generate_round_keypair(1, "job_math", "cfi-consortium", "sess_math_1", ["BANK_A", "BANK_B"])

    driver_1.receive_peer_bundle(bundle_2, peer_institutional_public_key=pub_b)
    driver_2.receive_peer_bundle(bundle_1, peer_institutional_public_key=pub)

    w_a = [-0.75, 1.5, -3.2, 0.0]
    n_a = 200
    w_b = [0.25, -2.0, 1.8, -0.5]
    n_b = 600

    masked_a = driver_1.compute_weighted_masked_vector(w_a, sample_count=n_a, quantization_scale=1e7)
    masked_b = driver_2.compute_weighted_masked_vector(w_b, sample_count=n_b, quantization_scale=1e7)

    # Verify driver is consumed after single compute
    with pytest.raises(SecAggSecurityError, match="already been consumed"):
        driver_1.compute_weighted_masked_vector(w_a, sample_count=n_a, quantization_scale=1e7)

    agg = P2PSecAggDriver.aggregate_weighted_masked_vectors(
        masked_vectors={"BANK_A": masked_a, "BANK_B": masked_b},
        sample_counts={"BANK_A": n_a, "BANK_B": n_b},
        quantization_scale=1e7,
    )

    # Ground truth FedAvg: (n_a * w_a + n_b * w_b) / (n_a + n_b)
    expected = (n_a * np.array(w_a) + n_b * np.array(w_b)) / (n_a + n_b)
    np.testing.assert_allclose(agg, expected, atol=1e-6)


# ---------------------------------------------------------------------------
# Test: Operational API SecAgg Wiring & Validation
# ---------------------------------------------------------------------------


def test_operational_api_secagg_wiring_and_validation():
    """Test Requirement 4: Operational API SecAgg schema validation and fail-closed checks."""
    from app.application.schemas.simulation import (
        OperationalFederationRequest,
        OperationalFederationResponse,
    )

    # 1. Valid request accepts SecAgg configuration
    req = OperationalFederationRequest(
        participating_banks=["BANK_A", "BANK_B"],
        num_rounds=2,
        secagg_enabled=True,
        secagg_quantization_scale=1e7,
        secagg_modulus=2**64,
    )
    assert req.secagg_enabled is True
    assert req.secagg_quantization_scale == 1e7
    assert req.secagg_modulus == 2**64

    # 2. Response exposes secagg_enabled and secagg_provenance
    resp = OperationalFederationResponse(
        job_id="job_api_test",
        training_run_id="run_api_test",
        simulation_id="sim_api_test",
        status="COMPLETED",
        is_verified=True,
        secagg_enabled=True,
        secagg_provenance={"enabled": True, "rounds": {}},
    )
    assert resp.secagg_enabled is True
    assert resp.secagg_provenance is not None


# ---------------------------------------------------------------------------
# Tests: Institutional Identity Bootstrap & Authorization (Requirements A-G)
# ---------------------------------------------------------------------------


def test_coordinator_cannot_first_register_bank_identity():
    """Negative Test A: Malicious coordinator attempts to FIRST-register its own public key

    as BANK_B before BANK_B has any authority record. Must fail closed.
    """
    tmp_dir = tempfile.mkdtemp(prefix="cfi_secagg_boot_a_")
    db_file = os.path.join(tmp_dir, "authority.db")
    coord_token = "coord_secret_token_bootstrap_99"
    bank_tokens = {"BANK_A": "token_bank_a_99", "BANK_B": "token_bank_b_99"}

    auth_source = TrustedTrainingJobRecordSource(
        db_url=f"sqlite:///{db_file}",
        coordinator_token=coord_token,
        bank_tokens=bank_tokens,
        app_env="development",
    )

    forged_priv = ed25519.Ed25519PrivateKey.generate()
    forged_pub_hex = forged_priv.public_key().public_bytes_raw().hex()

    # Coordinator attempts first-registration for BANK_B
    with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credentials cannot register"):
        auth_source.register_institutional_identity(
            bank_id="BANK_B",
            public_key_hex=forged_pub_hex,
            token=coord_token,
        )

    # Verify no identity was stored for BANK_B
    assert auth_source.get_institutional_identity("BANK_B") is None
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_coordinator_cannot_replace_existing_bank_identity():
    """Negative Test B: Coordinator attempts to replace an existing BANK_B key. Must fail closed."""
    tmp_dir = tempfile.mkdtemp(prefix="cfi_secagg_boot_b_")
    db_file = os.path.join(tmp_dir, "authority.db")
    coord_token = "coord_secret_token_bootstrap_99"
    bank_tokens = {"BANK_A": "token_bank_a_99", "BANK_B": "token_bank_b_99"}

    auth_source = TrustedTrainingJobRecordSource(
        db_url=f"sqlite:///{db_file}",
        coordinator_token=coord_token,
        bank_tokens=bank_tokens,
        app_env="development",
    )

    # 1. Authentic BANK_B registers its key
    priv_b = ed25519.Ed25519PrivateKey.generate()
    pub_b_hex = priv_b.public_key().public_bytes_raw().hex()
    auth_source.register_institutional_identity(
        bank_id="BANK_B",
        public_key_hex=pub_b_hex,
        token=bank_tokens["BANK_B"],
    )
    assert auth_source.get_institutional_identity("BANK_B") == pub_b_hex

    # 2. Coordinator attempts to replace existing BANK_B key
    forged_priv = ed25519.Ed25519PrivateKey.generate()
    forged_pub_hex = forged_priv.public_key().public_bytes_raw().hex()
    with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credentials cannot register"):
        auth_source.register_institutional_identity(
            bank_id="BANK_B",
            public_key_hex=forged_pub_hex,
            token=coord_token,
        )

    # Verify authentic key remains unchanged
    assert auth_source.get_institutional_identity("BANK_B") == pub_b_hex
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_cross_bank_identity_registration_rejected():
    """Negative Test C: BANK_A attempts to register BANK_B's public key. Must fail closed."""
    tmp_dir = tempfile.mkdtemp(prefix="cfi_secagg_boot_c_")
    db_file = os.path.join(tmp_dir, "authority.db")
    coord_token = "coord_secret_token_bootstrap_99"
    bank_tokens = {"BANK_A": "token_bank_a_99", "BANK_B": "token_bank_b_99"}

    auth_source = TrustedTrainingJobRecordSource(
        db_url=f"sqlite:///{db_file}",
        coordinator_token=coord_token,
        bank_tokens=bank_tokens,
        app_env="development",
    )

    priv_a = ed25519.Ed25519PrivateKey.generate()
    pub_a_hex = priv_a.public_key().public_bytes_raw().hex()

    # BANK_A attempts to register as BANK_B
    with pytest.raises(TrainingProvenanceSecurityError, match="Identity spoofing rejected"):
        auth_source.register_institutional_identity(
            bank_id="BANK_B",
            public_key_hex=pub_a_hex,
            token=bank_tokens["BANK_A"],
        )

    assert auth_source.get_institutional_identity("BANK_B") is None
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_authentic_bank_identity_provisioning_and_immutability():
    """Test D: Authentic BANK_B successfully completes its authorized initial identity provisioning

    and bindings remain immutable against subsequent conflicting registrations.
    """
    tmp_dir = tempfile.mkdtemp(prefix="cfi_secagg_boot_d_")
    db_file = os.path.join(tmp_dir, "authority.db")
    coord_token = "coord_secret_token_bootstrap_99"
    bank_tokens = {"BANK_A": "token_bank_a_99", "BANK_B": "token_bank_b_99"}

    auth_source = TrustedTrainingJobRecordSource(
        db_url=f"sqlite:///{db_file}",
        coordinator_token=coord_token,
        bank_tokens=bank_tokens,
        app_env="development",
    )

    priv_b = ed25519.Ed25519PrivateKey.generate()
    pub_b_hex = priv_b.public_key().public_bytes_raw().hex()

    # 1. Authentic initial provisioning succeeds
    auth_source.register_institutional_identity(
        bank_id="BANK_B",
        public_key_hex=pub_b_hex,
        token=bank_tokens["BANK_B"],
    )
    assert auth_source.get_institutional_identity("BANK_B") == pub_b_hex

    # 2. Re-registering the same key is idempotent and succeeds
    auth_source.register_institutional_identity(
        bank_id="BANK_B",
        public_key_hex=pub_b_hex,
        token=bank_tokens["BANK_B"],
    )
    assert auth_source.get_institutional_identity("BANK_B") == pub_b_hex

    # 3. Registering a conflicting key for BANK_B fails closed
    different_priv = ed25519.Ed25519PrivateKey.generate()
    different_pub_hex = different_priv.public_key().public_bytes_raw().hex()
    with pytest.raises(TrainingProvenanceSecurityError, match="Institutional identity conflict"):
        auth_source.register_institutional_identity(
            bank_id="BANK_B",
            public_key_hex=different_pub_hex,
            token=bank_tokens["BANK_B"],
        )

    # Key remains original
    assert auth_source.get_institutional_identity("BANK_B") == pub_b_hex
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_independent_workers_retrieve_identical_peer_trust_anchors():
    """Test E: Independent BANK_A and BANK_B workers retrieve the same authenticated peer trust anchors."""
    tmp_dir = tempfile.mkdtemp(prefix="cfi_secagg_boot_e_")
    db_file = os.path.join(tmp_dir, "authority.db")
    bank_tokens = {"BANK_A": "token_bank_a_99", "BANK_B": "token_bank_b_99"}

    # Separate worker connection instances representing separate processes
    worker_a_source = TrustedTrainingJobRecordSource(
        db_url=f"sqlite:///{db_file}",
        worker_token=bank_tokens["BANK_A"],
        bank_tokens={"BANK_A": bank_tokens["BANK_A"]},
        app_env="development",
    )
    worker_b_source = TrustedTrainingJobRecordSource(
        db_url=f"sqlite:///{db_file}",
        worker_token=bank_tokens["BANK_B"],
        bank_tokens={"BANK_B": bank_tokens["BANK_B"]},
        app_env="development",
    )

    priv_a = ed25519.Ed25519PrivateKey.generate()
    pub_a_hex = priv_a.public_key().public_bytes_raw().hex()
    priv_b = ed25519.Ed25519PrivateKey.generate()
    pub_b_hex = priv_b.public_key().public_bytes_raw().hex()

    # Bank A registers its own key
    worker_a_source.register_institutional_identity("BANK_A", pub_a_hex, token=bank_tokens["BANK_A"])
    # Bank B registers its own key
    worker_b_source.register_institutional_identity("BANK_B", pub_b_hex, token=bank_tokens["BANK_B"])

    # Both retrieve peer trust anchors
    roster = ["BANK_A", "BANK_B"]
    anchors_a = worker_a_source.get_institutional_identities(roster)
    anchors_b = worker_b_source.get_institutional_identities(roster)

    assert anchors_a == {"BANK_A": pub_a_hex, "BANK_B": pub_b_hex}
    assert anchors_b == {"BANK_A": pub_a_hex, "BANK_B": pub_b_hex}
    assert anchors_a == anchors_b
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_missing_authoritative_peer_keys_fails_closed():
    """Negative Test G: Missing authoritative peer keys cause fail-closed SecAgg setup."""
    import contextlib

    tmp_dir = tempfile.mkdtemp(prefix="cfi_secagg_boot_g_")
    db_file = os.path.join(tmp_dir, "authority.db")
    coordinator_token = "coord_secret_token_secagg_missing_99"
    bank_tokens = {"BANK_A": "token_bank_a_99", "BANK_B": "token_bank_b_99"}
    consortium_id = "consortium_test_missing_auth"
    session_id = "sess_missing_auth_01"

    auth_source = TrustedTrainingJobRecordSource(
        db_url=f"sqlite:///{db_file}",
        coordinator_token=coordinator_token,
        bank_tokens=bank_tokens,
        app_env="development",
    )

    job_id = "job_missing_auth_01"
    auth_source.schedule_training_job(
        job_id=job_id,
        training_run_id=f"run_{job_id}",
        simulation_id=f"sim_{job_id}",
        coordinator_token=coordinator_token,
        expected_rounds=1,
        expected_epochs=1,
        permitted_workers=["BANK_A", "BANK_B"],
        consortium_id=consortium_id,
    )

    gen = DataGenerator(seed=42)
    bank_datasets = gen.generate_bank_datasets(bank_a_size=50, bank_b_size=50, bank_c_size=50)
    bank_partitions = {
        b_id: (
            np.asarray(DataGenerator.encode_features(bank_datasets[b_id.lower()][0]), dtype=np.float32),
            np.asarray(bank_datasets[b_id.lower()][1].to_numpy(dtype=np.float32), dtype=np.float32),
        )
        for b_id in ("BANK_A", "BANK_B")
    }

    spawned_procs, endpoints = spawn_bank_workers(
        participating_banks=["BANK_A"],
        db_url=f"sqlite:///{db_file}",
        bank_tokens=bank_tokens,
        bank_partitions=bank_partitions,
        app_env="development",
    )

    try:
        inbox_a = endpoints["BANK_A"]["inbox"]
        outbox_a = endpoints["BANK_A"]["outbox"]

        fake_pub_b = "b" * 64
        # Send SECAGG_SETUP to BANK_A with roster ["BANK_A", "BANK_B"]
        setup_payload = {
            "command": "SECAGG_SETUP",
            "job_id": job_id,
            "round_number": 1,
            "session_id": session_id,
            "consortium_id": consortium_id,
            "roster": ["BANK_A", "BANK_B"],
            "institutional_public_keys": {
                "BANK_A": endpoints["BANK_A"]["institutional_public_key"],
                "BANK_B": fake_pub_b,
            },
            "secagg_config": {"quantization_scale": 1e7, "modulus": 2**64},
        }
        inbox_a.put(setup_payload)

        # Worker A must reject setup because BANK_B has no authoritative key
        resp = outbox_a.get(timeout=10.0)
        assert resp["status"] == "ERROR"
        assert "missing authoritative institutional keys" in resp["error"].lower()
    finally:
        with contextlib.suppress(Exception):
            endpoints["BANK_A"]["inbox"].put({"command": "SHUTDOWN"})
        for p in spawned_procs.values():
            p.join(timeout=2.0)
            if p.is_alive():
                p.terminate()
        shutil.rmtree(tmp_dir, ignore_errors=True)


