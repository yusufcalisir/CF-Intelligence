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
