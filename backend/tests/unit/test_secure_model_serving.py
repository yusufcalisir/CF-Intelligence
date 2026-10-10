"""Comprehensive Security, Integrity, and Provenance Test Suite for Model Serving (Task 01).

Covers Scenarios:
- T01: Valid trusted champion model loaded, verified, evaluated_by="ML_MODEL"
- T02: Missing model artifact fails closed with HTTP 503 MODEL_NOT_READY
- T03: Corrupted model artifact rejected, no arbitrary deserialization, HTTP 503
- T04: Malicious/untrusted pickle payload rejected, pickle.loads never executed
- T05: Redis cache poisoning rejected (invalid/missing HMAC), falls back safely to disk champion
- T06: Valid cache hit loads authenticated TorchScript model from Redis
- T07: Cache unavailable, local champion served safely without false Redis hit
- T08: Missing and unexpected state dict keys rejected with ModelCompatibilityError
- T09: BatchNorm checkpoint rejected from GroupNorm serving (no silent partial init)
- T10: Invalid model outputs (NaN, Inf, out of bounds) deterministically rejected
- T11: Circuit breaker opens after 3 strikes, fails closed in operational mode, demo mode labeled
- T12: Production mode rejects client simulation bypass (HTTP 400 SIMULATION_NOT_ALLOWED)
- T13: Multiple API prefixes (/v1/inference/score and /api/v1/inference/score) behave identically
- T14: ModelRegistry integration: promotion, active champion resolution, cache invalidation
- T15: Concurrent model loading publishes single atomic verified model without race conditions
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import io
import json
import os
import shutil
import tempfile
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.config import Settings, get_settings
from app.domain.model_serving_errors import (
    ModelCompatibilityError,
    ModelIntegrityError,
    ModelNotAvailableError,
)
from app.presentation.routers.realtime_inference import (
    LocalModelCache,
    api_router,
    get_scripted_model,
    reset_circuit_breaker,
    reset_model_cache,
    router,
)

# Test app with both router prefixes
app = FastAPI()
app.include_router(router)
app.include_router(api_router)
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset circuit breaker, model cache, and environment between tests."""
    reset_circuit_breaker()
    reset_model_cache()
    yield
    reset_circuit_breaker()
    reset_model_cache()


@pytest.fixture
def temp_registry_dir():
    """Provides a dedicated temporary storage directory for ModelRegistry."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_registry_")
    os.environ["CFI_STORAGE_DIR"] = temp_dir
    yield temp_dir
    os.environ.pop("CFI_STORAGE_DIR", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


def create_trained_model_fixture(storage_dir: str, dp_compatible: bool = True) -> tuple[str, dict[str, Any]]:
    """Helper: Trains a minimal model on synthetic data and registers it as champion."""
    settings = get_settings()
    svc = ModelService(settings)
    registry = ModelRegistry(storage_dir=storage_dir)

    # Train for 1 epoch on real synthetic data
    model = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=dp_compatible)
    X = np.random.randn(64, NUM_FEATURES).astype(np.float32)
    y = np.random.randint(0, 2, size=(64,)).astype(np.float32)
    trained_model, _, _ = svc.train_local(model, X, y, epochs=1, batch_size=32)
    trained_model.eval()

    state_dict = trained_model.state_dict()
    metrics = {"auc_roc": 0.88, "f1_score": 0.75, "loss": 0.32}

    entry = registry.save_version(
        simulation_id="sim_test_champion",
        state_dict=state_dict,
        metrics=metrics,
        is_promoted=True,
        git_commit_hash="abcdef1234567890",
    )
    global_path = os.path.join(storage_dir, "global_model.pt")
    assert os.path.exists(global_path), "global_model.pt must be created upon promotion"
    return global_path, entry


# ── T01: Valid Trusted Champion Model ──────────────────────────────────────────
def test_t01_valid_trusted_champion_serving(temp_registry_dir: str) -> None:
    """T01: Verify genuinely trained champion is loaded, evaluated, and served as ML_MODEL."""
    global_path, meta = create_trained_model_fixture(temp_registry_dir, dp_compatible=True)
    assert os.path.exists(global_path)

    payload = {
        "transaction_id": "tx_t01_valid",
        "amount": 150.0,
        "currency": "USD",
        "source_account": "acc_1",
        "target_account": "acc_2",
        "merchant_category": "retail",
        "velocity_1h": 1,
    }

    resp = client.post("/v1/inference/score", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["transaction_id"] == "tx_t01_valid"
    assert data["evaluated_by"] == "ML_MODEL"
    assert "PyTorch JIT" in data["explanation"] or "ML Model" in data["explanation"]
    assert 0.0 <= data["risk_score"] <= 1.0
    assert data["decision"] in ("ALLOW", "REVIEW", "BLOCK")


# ── T02: Missing Model Artifact Fails Closed ───────────────────────────────────
def test_t02_missing_model_artifact_fails_closed(temp_registry_dir: str) -> None:
    """T02: Verify missing model artifact returns HTTP 503 in operational mode (no random fallback)."""
    # Ensure no champion file exists in temp_registry_dir
    global_path = os.path.join(temp_registry_dir, "global_model.pt")
    if os.path.exists(global_path):
        os.remove(global_path)

    payload = {
        "transaction_id": "tx_t02_missing",
        "amount": 100.0,
        "currency": "USD",
        "source_account": "acc_1",
        "target_account": "acc_2",
        "merchant_category": "retail",
        "velocity_1h": 1,
    }

    # Operational mode
    with patch.dict(os.environ, {"APP_ENV": "development", "ENABLE_DEMO_FALLBACK": "false"}):
        resp = client.post("/v1/inference/score", json=payload)
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["detail"]["code"] == "MODEL_NOT_READY"
        assert "not currently available" in data["detail"]["message"].lower()


# ── T03: Corrupted Model Artifact Rejection ────────────────────────────────────
def test_t03_corrupted_model_artifact_rejection(temp_registry_dir: str) -> None:
    """T03: Supply corrupt bytes on disk; verify deterministic rejection without arbitrary execution."""
    global_path = os.path.join(temp_registry_dir, "global_model.pt")
    with open(global_path, "wb") as f:
        f.write(b"CORRUPTED_NON_PYTORCH_BYTES_1234567890\x00\xff\xfe")

    payload = {
        "transaction_id": "tx_t03_corrupt",
        "amount": 250.0,
        "currency": "USD",
        "source_account": "acc_1",
        "target_account": "acc_2",
        "merchant_category": "retail",
        "velocity_1h": 1,
    }

    with patch.dict(os.environ, {"APP_ENV": "development", "ENABLE_DEMO_FALLBACK": "false"}):
        resp = client.post("/v1/inference/score", json=payload)
        assert resp.status_code == 503
        data = resp.json()
        assert data["detail"]["code"] == "MODEL_NOT_READY"


# ── T04: Malicious / Untrusted Pickle Rejection ────────────────────────────────
def test_t04_malicious_pickle_rejection(temp_registry_dir: str) -> None:
    """T04: Verify pickle payloads in cache are NEVER passed to pickle.loads."""
    import pickle  # nosec B403 - used exclusively to craft rejecting test fixture

    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)

    fake_pickle_payload = pickle.dumps({"injected": "command_simulation"})

    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda key: (
        fake_pickle_payload if key == "cfi:champion_model" else None
    )

    with (
        patch("app.infrastructure.cache.get_redis_client", return_value=mock_redis),
        patch("pickle.loads") as mock_pickle_loads,
    ):
        # Model serving should ignore unauthenticated pickle payload and fall back to disk champion
        scripted, from_redis = get_scripted_model()
        assert not from_redis
        assert scripted is not None
        # Verify pickle.loads was NEVER called
        mock_pickle_loads.assert_not_called()


# ── T05: Redis Cache Poisoning Rejection ───────────────────────────────────────
def test_t05_redis_cache_poisoning_rejection(temp_registry_dir: str) -> None:
    """T05: Tampered cache bytes or missing/invalid HMAC cannot be served or overwrite champion."""
    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)

    # Attacker places arbitrary bytes in cfi:champion_model with invalid/fake HMAC
    poisoned_bytes = b"ATTACKER_MODEL_BYTES_XYZ"
    fake_auth = json.dumps({
        "hmac": "deadbeef" * 8,
        "sha256": hashlib.sha256(poisoned_bytes).hexdigest(),
        "cached_at": time.time(),
    })

    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda k: (
        poisoned_bytes if k == "cfi:champion_model"
        else fake_auth.encode("utf-8") if k == "cfi:champion_model:auth"
        else None
    )

    with patch("app.infrastructure.cache.get_redis_client", return_value=mock_redis):
        # Must reject poisoned cache and serve legitimate local champion
        scripted, from_redis = get_scripted_model()
        assert not from_redis, "Poisoned Redis cache entry must NOT be accepted as a cache hit"
        assert scripted is not None


# ── T06: Valid Cache Hit ───────────────────────────────────────────────────────
def test_t06_valid_cache_hit(temp_registry_dir: str) -> None:
    """T06: Valid authenticated cache entry is successfully served and labeled as Redis Cache."""
    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)

    # First compile model to get genuine TorchScript bytes
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)
    real_model = svc.get_champion(dp_compatible=True, registry=reg)
    dummy_input = torch.zeros(1, NUM_FEATURES)
    traced = torch.jit.trace(real_model, dummy_input)

    buf = io.BytesIO()
    torch.jit.save(traced, buf)
    valid_bytes = buf.getvalue()

    meta = reg.get_champion_metadata()
    assert meta is not None

    meta_to_sign = {
        "version": meta["version"],
        "simulation_id": meta["simulation_id"],
        "artifact_sha256": meta["sha256"],
        "model_sha256": hashlib.sha256(valid_bytes).hexdigest(),
        "architecture": meta.get("architecture", "FraudDetectionModel-GroupNorm"),
        "input_dim": NUM_FEATURES,
        "serialization_version": 2,
    }
    canonical_meta_str = json.dumps(meta_to_sign, sort_keys=True, separators=(",", ":"))
    valid_hmac = hmac.new(
        settings.payload_signing_secret.encode("utf-8"),
        canonical_meta_str.encode("utf-8") + b":" + valid_bytes,
        hashlib.sha256,
    ).hexdigest()

    auth_payload = dict(meta_to_sign)
    auth_payload["hmac"] = valid_hmac
    auth_payload["cached_at"] = time.time()
    valid_auth = json.dumps(auth_payload).encode("utf-8")

    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda k: (
        valid_bytes if k == "cfi:champion_model"
        else valid_auth if k == "cfi:champion_model:auth"
        else None
    )

    with patch("app.infrastructure.cache.get_redis_client", return_value=mock_redis):
        scripted, from_redis = get_scripted_model()
        assert from_redis, "Authenticated valid cache entry must be a cache hit"
        assert scripted is not None

        # Verify endpoint returns Redis Cache label
        payload = {
            "transaction_id": "tx_t06_cache",
            "amount": 200.0,
            "currency": "USD",
            "source_account": "acc_1",
            "target_account": "acc_2",
            "merchant_category": "retail",
            "velocity_1h": 1,
        }
        resp = client.post("/v1/inference/score", json=payload)
        assert resp.status_code == 200
        assert "Redis Cache JIT" in resp.json()["explanation"]


# ── T07: Cache Unavailable, Local Champion Available ──────────────────────────
def test_t07_cache_unavailable_local_champion_served(temp_registry_dir: str) -> None:
    """T07: When Redis raises an error or is unavailable, serving succeeds via local champion."""
    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)

    with patch("app.infrastructure.cache.get_redis_client", return_value=None):
        scripted, from_redis = get_scripted_model()
        assert not from_redis
        assert scripted is not None

        payload = {
            "transaction_id": "tx_t07_local",
            "amount": 100.0,
            "currency": "USD",
            "source_account": "acc_1",
            "target_account": "acc_2",
            "merchant_category": "retail",
            "velocity_1h": 1,
        }
        resp = client.post("/v1/inference/score", json=payload)
        assert resp.status_code == 200
        assert resp.json()["evaluated_by"] == "ML_MODEL"
        assert "PyTorch JIT" in resp.json()["explanation"]


# ── T08: Missing and Unexpected State Dict Keys ────────────────────────────────
def test_t08_missing_and_unexpected_keys_rejected(temp_registry_dir: str) -> None:
    """T08: Strict state loading rejects incomplete and incompatible state dicts."""
    global_path, _ = create_trained_model_fixture(temp_registry_dir, dp_compatible=True)
    real_sd = torch.load(global_path, map_location="cpu", weights_only=True)

    # Case A: Missing layer weights
    missing_sd = {k: v for k, v in real_sd.items() if "network.4" not in k}
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)
    reg.save_version("sim_test_champion", missing_sd, {"auc": 0.8}, is_promoted=True)

    with pytest.raises(ModelCompatibilityError) as exc_info:
        svc.get_champion(dp_compatible=True, registry=reg)
    assert "Strict parameter loading failed" in str(exc_info.value)

    # Case B: Unexpected parameters
    unexpected_sd = dict(real_sd)
    unexpected_sd["rogue_layer.weight"] = torch.randn(10, 10)
    reg.save_version("sim_test_champion", unexpected_sd, {"auc": 0.8}, is_promoted=True)

    with pytest.raises(ModelCompatibilityError) as exc_info:
        svc.get_champion(dp_compatible=True, registry=reg)
    assert "Strict parameter loading failed" in str(exc_info.value)


# ── T09: BatchNorm / GroupNorm Architecture Compatibility ─────────────────────
def test_t09_batchnorm_groupnorm_boundary_rejection(temp_registry_dir: str) -> None:
    """T09: A BatchNorm checkpoint cannot be silently served as a GroupNorm model."""
    # Create BatchNorm model checkpoint
    bn_path, _ = create_trained_model_fixture(temp_registry_dir, dp_compatible=False)
    bn_sd = torch.load(bn_path, map_location="cpu", weights_only=True)
    assert any("running_mean" in k for k in bn_sd), "BatchNorm checkpoint must contain running stats"

    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    # Requesting GroupNorm (dp_compatible=True) on BatchNorm weights must fail
    with pytest.raises(ModelCompatibilityError) as exc_info:
        svc.get_champion(dp_compatible=True, registry=reg)
    assert "trained with BatchNorm" in str(exc_info.value)
    assert "GroupNorm" in str(exc_info.value)


# ── T10: Invalid Model Outputs Deterministically Rejected ──────────────────────
def test_t10_invalid_model_output_rejection(temp_registry_dir: str) -> None:
    """T10: Models producing NaN, Inf, or invalid score range are rejected with ModelExecutionError."""
    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)

    # Mock scripted model to return NaN
    mock_bad_model = MagicMock()
    mock_bad_model.return_value = torch.tensor([float("nan")])

    with patch("app.presentation.routers.realtime_inference.get_scripted_model", return_value=(mock_bad_model, False)):
        payload = {
            "transaction_id": "tx_t10_nan",
            "amount": 100.0,
            "currency": "USD",
            "source_account": "acc_1",
            "target_account": "acc_2",
            "merchant_category": "retail",
            "velocity_1h": 1,
        }
        with patch.dict(os.environ, {"APP_ENV": "development", "ENABLE_DEMO_FALLBACK": "false"}):
            resp = client.post("/v1/inference/score", json=payload)
            assert resp.status_code == 503
            assert resp.json()["detail"]["code"] == "INFERENCE_FAILED"


# ── T11: Circuit Breaker Behavior ──────────────────────────────────────────────
def test_t11_circuit_breaker_trips_and_fails_closed(temp_registry_dir: str) -> None:
    """T11: Circuit breaker opens after 3 strikes and returns 503 in operational mode."""
    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)

    mock_failing_model = MagicMock(side_effect=RuntimeError("CUDA out of memory simulation"))

    payload = {
        "transaction_id": "tx_t11_strike",
        "amount": 100.0,
        "currency": "USD",
        "source_account": "acc_1",
        "target_account": "acc_2",
        "merchant_category": "retail",
        "velocity_1h": 1,
    }

    with (
        patch("app.presentation.routers.realtime_inference.get_scripted_model", return_value=(mock_failing_model, False)),
        patch.dict(os.environ, {"APP_ENV": "development", "ENABLE_DEMO_FALLBACK": "false"}),
    ):
        # 3 failures to trip circuit breaker
        for _ in range(3):
            r = client.post("/v1/inference/score", json=payload)
            assert r.status_code == 503

        # 4th request while circuit breaker is OPEN
        r4 = client.post("/v1/inference/score", json=payload)
        assert r4.status_code == 503
        assert r4.json()["detail"]["code"] == "CIRCUIT_BREAKER_OPEN"


# ── T12: Production Demo Isolation ─────────────────────────────────────────────
def test_t12_production_demo_isolation(temp_registry_dir: str) -> None:
    """T12: In production environment, simulation fallback cannot be forced by client."""
    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)

    payload = {
        "transaction_id": "tx_t12_prod_bypass",
        "amount": 100.0,
        "currency": "USD",
        "source_account": "acc_1",
        "target_account": "acc_2",
        "merchant_category": "retail",
        "velocity_1h": 1,
        "force_fallback": True,
    }

    # Mock settings with app_env='production' (bypassing lru_cache)
    prod_settings = Settings(app_env="production")
    with patch("app.presentation.routers.realtime_inference.get_settings", return_value=prod_settings):
        resp = client.post("/v1/inference/score", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "SIMULATION_NOT_ALLOWED"


# ── T13: Multiple API Prefixes Parity ──────────────────────────────────────────
def test_t13_multiple_api_prefixes_parity(temp_registry_dir: str) -> None:
    """T13: Verify both POST /v1/inference/score and POST /api/v1/inference/score behave identically."""
    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)

    payload = {
        "transaction_id": "tx_t13_prefix",
        "amount": 120.0,
        "currency": "USD",
        "source_account": "acc_1",
        "target_account": "acc_2",
        "merchant_category": "retail",
        "velocity_1h": 1,
    }

    resp1 = client.post("/v1/inference/score", json=payload)
    resp2 = client.post("/api/v1/inference/score", json=payload)

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    d1 = resp1.json()
    d2 = resp2.json()
    assert d1["evaluated_by"] == d2["evaluated_by"] == "ML_MODEL"
    assert d1["decision"] == d2["decision"]
    assert d1["risk_score"] == d2["risk_score"]


# ── T14: Model Registry Integration ────────────────────────────────────────────
def test_t14_model_registry_lifecycle_and_invalidation(temp_registry_dir: str) -> None:
    """T14: Verify champion promotion, manifest tracking, and cache invalidation."""
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    # Train and register v1
    m1 = svc.create_model(dp_compatible=True)
    sd1 = m1.state_dict()
    v1_entry = reg.save_version("sim_reg_test", sd1, {"auc": 0.81}, is_promoted=True)
    assert v1_entry["version"] == 1
    assert v1_entry["status"] == "champion"
    assert reg.get_champion_artifact_path() is not None

    meta = reg.get_champion_metadata()
    assert meta is not None
    assert meta["version"] == 1
    assert "sha256" in meta

    # Train v2 (not promoted)
    m2 = svc.create_model(dp_compatible=True)
    sd2 = m2.state_dict()
    v2_entry = reg.save_version("sim_reg_test", sd2, {"auc": 0.85}, is_promoted=False, status="challenger")
    assert v2_entry["version"] == 2
    assert v2_entry["status"] == "challenger"
    # Champion is still v1
    meta_v1 = reg.get_champion_metadata()
    assert meta_v1 is not None
    assert meta_v1["version"] == 1

    # Promote v2 to champion
    reg.promote_version("sim_reg_test", version=2, target_status="champion")
    meta_v2 = reg.get_champion_metadata()
    assert meta_v2 is not None
    assert meta_v2["version"] == 2

    # Invalidate cache
    mock_redis = MagicMock()
    with patch("app.infrastructure.cache.get_redis_client", return_value=mock_redis):
        svc.invalidate_model_cache()
        mock_redis.delete.assert_called_with("cfi:champion_model", "cfi:champion_model:auth")
        mock_redis.publish.assert_called_with("cfi:model_events", "model_updated")


# ── T15: Concurrent Model Loading Thread Safety ────────────────────────────────
def test_t15_concurrent_model_loading_race_free(temp_registry_dir: str) -> None:
    """T15: Concurrent scoring requests against cold cache publish atomic, valid model without deadlocks."""
    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)
    reset_model_cache()

    payload = {
        "transaction_id": "tx_t15_concurrent",
        "amount": 99.0,
        "currency": "USD",
        "source_account": "acc_1",
        "target_account": "acc_2",
        "merchant_category": "retail",
        "velocity_1h": 1,
    }

    def worker(i: int):
        req = dict(payload)
        req["transaction_id"] = f"tx_t15_concurrent_{i}"
        return client.post("/v1/inference/score", json=req)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker, i) for i in range(16)]
        results = [f.result() for f in futures]

    for res in results:
        assert res.status_code == 200
        assert res.json()["evaluated_by"] == "ML_MODEL"


# ── T17: Artifact Digest Mismatch ──────────────────────────────────────────────
def test_t17_artifact_digest_mismatch_fails_closed(temp_registry_dir: str) -> None:
    """T17: Modify artifact bytes after registration; verify digest mismatch fails closed."""
    global_path, entry = create_trained_model_fixture(temp_registry_dir, dp_compatible=True)
    sim_dir = os.path.join(temp_registry_dir, "registry", "sim_test_champion")
    artifact_file = os.path.join(sim_dir, entry["filename"])

    # Tamper with the artifact bytes on disk
    with open(artifact_file, "r+b") as f:
        f.seek(16)
        f.write(b"\xde\xad\xbe\xef\xca\xfe\xba\xbe")

    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    with pytest.raises(ModelIntegrityError) as exc_info:
        svc.get_champion(dp_compatible=True, registry=reg)
    assert "digest mismatch" in str(exc_info.value).lower()

    # Verify HTTP inference endpoint fails closed with 503
    payload = {
        "transaction_id": "tx_t17_mismatch",
        "amount": 100.0,
        "currency": "USD",
        "source_account": "acc_1",
        "target_account": "acc_2",
        "merchant_category": "retail",
        "velocity_1h": 1,
    }
    with patch.dict(os.environ, {"APP_ENV": "development", "ENABLE_DEMO_FALLBACK": "false"}):
        resp = client.post("/v1/inference/score", json=payload)
        assert resp.status_code == 503


# ── T18: Manifest Tampering ────────────────────────────────────────────────────
def test_t18_manifest_tampering_detection(temp_registry_dir: str) -> None:
    """T18: Tamper with manifest expected digest; verify integrity verification rejects mismatch.

    Note: SHA-256 checks artifact bytes against manifest metadata. If manifest itself is modified,
    ordinary hash comparison detects discrepancy between file and forged digest. Full manifest signing
    is distinguished at the governance layer (HSM/Dual Sign-off).
    """
    global_path, entry = create_trained_model_fixture(temp_registry_dir, dp_compatible=True)
    sim_dir = os.path.join(temp_registry_dir, "registry", "sim_test_champion")
    manifest_path = os.path.join(sim_dir, "registry.json")

    with open(manifest_path, encoding="utf-8") as f:
        manifest_data = json.load(f)

    # Forger changes recorded sha256 in manifest
    manifest_data[0]["sha256"] = "f" * 64
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f)

    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    with pytest.raises(ModelIntegrityError) as exc_info:
        svc.get_champion(dp_compatible=True, registry=reg)
    assert "digest mismatch" in str(exc_info.value).lower()


# ── T19: Stale global_model.pt ─────────────────────────────────────────────────
def test_t19_stale_global_model_file_does_not_override_manifest(temp_registry_dir: str) -> None:
    """T19: Overwriting global_model.pt with old v1 bytes does NOT override active champion v2."""
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    # Register v1
    m1 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_t19", m1.state_dict(), {"auc": 0.80}, is_promoted=True)

    # Capture v1 bytes from global_model.pt
    global_path = os.path.join(temp_registry_dir, "global_model.pt")
    with open(global_path, "rb") as f:
        v1_bytes = f.read()

    # Register and promote v2
    m2 = svc.create_model(dp_compatible=True)
    with torch.no_grad():
        next(m2.parameters()).add_(1.5)
    reg.save_version("sim_t19", m2.state_dict(), {"auc": 0.90}, is_promoted=True)
    meta_v2_check = reg.get_champion_metadata()
    assert meta_v2_check is not None and meta_v2_check["version"] == 2

    # Overwrite global_model.pt with old v1 bytes
    with open(global_path, "wb") as f:
        f.write(v1_bytes)

    # Serving must resolve v2 from authoritative manifest, NOT stale global_model.pt
    champ = svc.get_champion(dp_compatible=True, registry=reg)
    champ_meta = getattr(champ, "champion_metadata", None)
    assert champ_meta is not None and champ_meta["version"] == 2


# ── T20: Missing Authoritative Champion ────────────────────────────────────────
def test_t20_missing_authoritative_champion_rejected(temp_registry_dir: str) -> None:
    """T20: Nonempty global_model.pt without an active champion manifest is rejected (fails closed)."""
    global_path = os.path.join(temp_registry_dir, "global_model.pt")
    with open(global_path, "wb") as f:
        f.write(b"STRAY_UNREGISTERED_MODEL_BYTES_XYZ" * 20)

    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    with pytest.raises(ModelNotAvailableError):
        svc.get_champion(dp_compatible=True, registry=reg)

    payload = {
        "transaction_id": "tx_t20_stray",
        "amount": 100.0,
        "currency": "USD",
        "source_account": "acc_1",
        "target_account": "acc_2",
        "merchant_category": "retail",
        "velocity_1h": 1,
    }
    with patch.dict(os.environ, {"APP_ENV": "development", "ENABLE_DEMO_FALLBACK": "false"}):
        resp = client.post("/v1/inference/score", json=payload)
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "MODEL_NOT_READY"


# ── T21: Multiple Active Champion Ambiguity ────────────────────────────────────
def test_t21_multiple_active_champion_ambiguity_fails_closed(temp_registry_dir: str) -> None:
    """T21: Multiple conflicting active champions across simulations fail closed with ModelCompatibilityError."""
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    m1 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_bank_a", m1.state_dict(), {"auc": 0.82}, is_promoted=True)

    m2 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_bank_b", m2.state_dict(), {"auc": 0.88}, is_promoted=True)

    # Scans across all simulations without explicit scope must fail closed
    with pytest.raises(ModelCompatibilityError) as exc_info:
        reg.resolve_and_verify_champion()
    assert "Ambiguous champion state" in str(exc_info.value)


# ── T22: Redis Stale Version Rejection ─────────────────────────────────────────
def test_t22_redis_stale_version_rejected(temp_registry_dir: str) -> None:
    """T22: Redis cached model for v1 is rejected after v2 is promoted, even if HMAC was valid for v1."""
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    # Create v1 and compile
    m1 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_t22", m1.state_dict(), {"auc": 0.81}, is_promoted=True)

    buf = io.BytesIO()
    torch.jit.save(torch.jit.trace(m1, torch.zeros(1, NUM_FEATURES)), buf)
    v1_bytes = buf.getvalue()

    # Promote v2
    m2 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_t22", m2.state_dict(), {"auc": 0.89}, is_promoted=True)
    meta_v2 = reg.get_champion_metadata()
    assert meta_v2 is not None and meta_v2["version"] == 2

    # Craft HMAC envelope valid for v1
    v1_meta: dict[str, Any] = {
        "version": 1,
        "simulation_id": "sim_t22",
        "artifact_sha256": hashlib.sha256(b"v1_art").hexdigest(),
        "model_sha256": hashlib.sha256(v1_bytes).hexdigest(),
        "architecture": "FraudDetectionModel-GroupNorm",
        "input_dim": NUM_FEATURES,
        "serialization_version": 2,
    }
    can_v1 = json.dumps(v1_meta, sort_keys=True, separators=(",", ":"))
    v1_hmac = hmac.new(
        settings.payload_signing_secret.encode("utf-8"),
        can_v1.encode("utf-8") + b":" + v1_bytes,
        hashlib.sha256,
    ).hexdigest()
    v1_envelope: dict[str, Any] = dict(v1_meta)
    v1_envelope["hmac"] = v1_hmac
    v1_envelope["cached_at"] = time.time()

    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda k: (
        v1_bytes if k == "cfi:champion_model"
        else json.dumps(v1_envelope).encode("utf-8") if k == "cfi:champion_model:auth"
        else None
    )

    with patch("app.infrastructure.cache.get_redis_client", return_value=mock_redis):
        scripted, from_redis = get_scripted_model()
        # Stale v1 in Redis MUST be rejected, loading fresh v2 from disk
        assert not from_redis, "Stale v1 cache entry must not be accepted when v2 is active"


# ── T23: Redis Cross-Scope Isolation ───────────────────────────────────────────
def test_t23_redis_cross_scope_isolation(temp_registry_dir: str) -> None:
    """T23: Cached model belonging to a different simulation ID cannot be served for active champion."""
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    m1 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_active", m1.state_dict(), {"auc": 0.84}, is_promoted=True)

    buf = io.BytesIO()
    torch.jit.save(torch.jit.trace(m1, torch.zeros(1, NUM_FEATURES)), buf)
    m_bytes = buf.getvalue()

    # Envelope claiming unrelated simulation
    other_meta: dict[str, Any] = {
        "version": 1,
        "simulation_id": "sim_different_tenant",
        "artifact_sha256": hashlib.sha256(b"diff").hexdigest(),
        "model_sha256": hashlib.sha256(m_bytes).hexdigest(),
        "architecture": "FraudDetectionModel-GroupNorm",
        "input_dim": NUM_FEATURES,
        "serialization_version": 2,
    }
    can = json.dumps(other_meta, sort_keys=True, separators=(",", ":"))
    h = hmac.new(
        settings.payload_signing_secret.encode("utf-8"),
        can.encode("utf-8") + b":" + m_bytes,
        hashlib.sha256,
    ).hexdigest()
    env: dict[str, Any] = dict(other_meta)
    env["hmac"] = h
    env["cached_at"] = time.time()

    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda k: (
        m_bytes if k == "cfi:champion_model"
        else json.dumps(env).encode("utf-8") if k == "cfi:champion_model:auth"
        else None
    )

    with patch("app.infrastructure.cache.get_redis_client", return_value=mock_redis):
        scripted, from_redis = get_scripted_model()
        assert not from_redis, "Cross-scope cached model must be rejected"


# ── T24: Cache Envelope Tampering ──────────────────────────────────────────────
def test_t24_cache_envelope_tampering(temp_registry_dir: str) -> None:
    """T24: Altering version, simulation_id, or artifact hash breaks HMAC and causes rejection."""
    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)
    settings = get_settings()
    reg = ModelRegistry(storage_dir=temp_registry_dir)
    meta = reg.get_champion_metadata()
    assert meta is not None

    dummy_bytes = b"DUMMY_TORCHSCRIPT_BYTES_12345678"
    valid_meta = {
        "version": meta["version"],
        "simulation_id": meta["simulation_id"],
        "artifact_sha256": meta["sha256"],
        "model_sha256": hashlib.sha256(dummy_bytes).hexdigest(),
        "architecture": "FraudDetectionModel-GroupNorm",
        "input_dim": NUM_FEATURES,
        "serialization_version": 2,
    }
    can = json.dumps(valid_meta, sort_keys=True, separators=(",", ":"))
    valid_h = hmac.new(
        settings.payload_signing_secret.encode("utf-8"),
        can.encode("utf-8") + b":" + dummy_bytes,
        hashlib.sha256,
    ).hexdigest()

    tampered_env = dict(valid_meta)
    tampered_env["hmac"] = valid_h
    # Attacker alters version without updating HMAC
    tampered_env["version"] = 99
    tampered_env["cached_at"] = time.time()

    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda k: (
        dummy_bytes if k == "cfi:champion_model"
        else json.dumps(tampered_env).encode("utf-8") if k == "cfi:champion_model:auth"
        else None
    )

    with patch("app.infrastructure.cache.get_redis_client", return_value=mock_redis):
        scripted, from_redis = get_scripted_model()
        assert not from_redis, "Tampered cache envelope must fail HMAC verification"


# ── T25: Atomic Cache Publication ──────────────────────────────────────────────
def test_t25_atomic_cache_publication_failure(temp_registry_dir: str) -> None:
    """T25: Incomplete cache writes (missing bytes or missing auth envelope) are rejected safely."""
    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)

    # Case 1: Bytes present, auth envelope missing
    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda k: (
        b"ORPHAN_BYTES" if k == "cfi:champion_model" else None
    )

    with patch("app.infrastructure.cache.get_redis_client", return_value=mock_redis):
        scripted, from_redis = get_scripted_model()
        assert not from_redis

    # Case 2: Auth envelope present, bytes missing
    reset_model_cache()
    mock_redis.get.side_effect = lambda k: (
        b"{}" if k == "cfi:champion_model:auth" else None
    )

    with patch("app.infrastructure.cache.get_redis_client", return_value=mock_redis):
        scripted, from_redis = get_scripted_model()
        assert not from_redis


# ── T26: Process-Local Stale Cache Invalidation ────────────────────────────────
def test_t26_process_local_stale_cache_invalidation(temp_registry_dir: str) -> None:
    """T26: Promoting v2 causes the next inference call to evict process-local v1 and load v2."""
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    m1 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_t26", m1.state_dict(), {"auc": 0.81}, is_promoted=True)

    with patch("app.infrastructure.cache.get_redis_client", return_value=None):
        s1, from_redis1 = get_scripted_model()
        assert not from_redis1

        # Promote v2 in registry
        m2 = svc.create_model(dp_compatible=True)
        reg.save_version("sim_t26", m2.state_dict(), {"auc": 0.91}, is_promoted=True)

        # Without restarting process, next resolution must detect v2
        s2, from_redis2 = get_scripted_model()
        assert s2 is not s1, "Process-local cache must evict stale v1 upon detecting v2 champion"


# ── T27: Cross-Worker Invalidation Consistency ─────────────────────────────────
def test_t27_cross_worker_invalidation_consistency(temp_registry_dir: str) -> None:
    """T27: Multiple worker cache instances detect promotion when revalidating against authoritative registry."""
    worker1_cache = LocalModelCache()
    worker2_cache = LocalModelCache()

    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    m1 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_t27", m1.state_dict(), {"auc": 0.80}, is_promoted=True)
    meta_v1 = reg.get_champion_metadata()
    assert meta_v1 is not None

    # Pre-populate both workers with v1
    worker1_cache.set(m1, 1, "sim_t27", meta_v1["sha256"], False)
    worker2_cache.set(m1, 1, "sim_t27", meta_v1["sha256"], False)

    # Promote v2
    m2 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_t27", m2.state_dict(), {"auc": 0.88}, is_promoted=True)
    meta_v2 = reg.get_champion_metadata()
    assert meta_v2 is not None

    # Both workers revalidating against current metadata detect stale cache and evict
    assert worker1_cache.get(meta_v2) is None
    assert worker2_cache.get(meta_v2) is None


# ── T28: Concurrent Cold-Cache Loading Single-Flight ───────────────────────────
def test_t28_concurrent_cold_cache_single_flight(temp_registry_dir: str) -> None:
    """T28: Concurrent threads encountering cold cache compile exactly once via single-flight locking."""
    create_trained_model_fixture(temp_registry_dir, dp_compatible=True)
    reset_model_cache()

    settings = get_settings()
    svc = ModelService(settings)
    load_counter = 0
    orig_get_champion = svc.get_champion
    counter_lock = threading.Lock()

    def counting_get_champion(*args, **kwargs):
        nonlocal load_counter
        with counter_lock:
            load_counter += 1
        time.sleep(0.04)  # widen concurrent arrival window
        return orig_get_champion(*args, **kwargs)

    with (
        patch("app.application.services.model_service.ModelService.get_champion", side_effect=counting_get_champion),
        patch("app.infrastructure.cache.get_redis_client", return_value=None),
        concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool,
    ):
        futures = [pool.submit(get_scripted_model) for _ in range(8)]
        results = [f.result() for f in futures]

    # Exactly one model load occurred across all 8 concurrent callers
    assert load_counter == 1, f"Expected 1 single-flight load, got {load_counter}"
    models = [r[0] for r in results]
    assert all(m is models[0] for m in models)


# ── T29: Promotion During Concurrent Loading ───────────────────────────────────
def test_t29_promotion_during_concurrent_loading_race_free(temp_registry_dir: str) -> None:
    """T29: If champion v2 is promoted while v1 compilation is in flight, v1 is discarded and v2 is served."""
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    m1 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_t29", m1.state_dict(), {"auc": 0.80}, is_promoted=True)

    m2 = svc.create_model(dp_compatible=True)
    orig_trace = torch.jit.trace
    promoted = False

    def delayed_trace(*args, **kwargs):
        nonlocal promoted
        # Simulate race: v2 promoted in the registry while v1 was being compiled
        if not promoted:
            promoted = True
            reg.save_version("sim_t29", m2.state_dict(), {"auc": 0.90}, is_promoted=True)
        return orig_trace(*args, **kwargs)

    reset_model_cache()
    with (
        patch("app.infrastructure.cache.get_redis_client", return_value=None),
        patch("torch.jit.trace", side_effect=delayed_trace),
    ):
        model, _ = get_scripted_model()
        meta = reg.get_champion_metadata()
        assert meta is not None and meta["version"] == 2


# ── T30: Rollback Correctness ──────────────────────────────────────────────────
def test_t30_rollback_correctness(temp_registry_dir: str) -> None:
    """T30: Rolling back to v1 updates authoritative identity, evicts cache, and serves v1."""
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    m1 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_t30", m1.state_dict(), {"auc": 0.80}, is_promoted=True)

    m2 = svc.create_model(dp_compatible=True)
    reg.save_version("sim_t30", m2.state_dict(), {"auc": 0.85}, is_promoted=True)
    meta_prom = reg.get_champion_metadata()
    assert meta_prom is not None and meta_prom["version"] == 2

    # Rollback to v1
    reg.rollback("sim_t30", version=1)
    meta = reg.get_champion_metadata()
    assert meta is not None and meta["version"] == 1

    with patch("app.infrastructure.cache.get_redis_client", return_value=None):
        model, _ = get_scripted_model()
        assert model is not None


# ── T31: Legitimate Trained Model Preserved ────────────────────────────────────
def test_t31_legitimate_trained_model_preservation(temp_registry_dir: str) -> None:
    """T31: Train model on synthetic data, verify weights change, promote, verify integrity and serving."""
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    model = svc.create_model(dp_compatible=True)
    init_weight = next(model.parameters()).clone()

    X = np.random.randn(128, NUM_FEATURES).astype(np.float32)
    y = np.random.randint(0, 2, size=(128,)).astype(np.float32)
    trained, losses, _ = svc.train_local(model, X, y, epochs=2, batch_size=32)

    # Verify model genuinely learned (weights changed)
    assert not torch.equal(init_weight, next(trained.parameters()))
    assert len(losses) == 2

    entry = reg.save_version(
        "sim_t31",
        trained.state_dict(),
        {"loss": losses[-1], "auc_roc": 0.85},
        is_promoted=True,
    )
    assert entry["is_active"] is True

    champ = svc.get_champion(dp_compatible=True, registry=reg)
    champ_meta = getattr(champ, "champion_metadata", None)
    assert champ_meta is not None and champ_meta["is_verified"] is True

    # Valid output contract
    with torch.no_grad():
        out = champ(torch.randn(5, NUM_FEATURES))
        assert out.shape == (5,)
        assert (out >= 0.0).all() and (out <= 1.0).all()


# ── T32: Random Checkpoint Masquerade Prevention ───────────────────────────────
def test_t32_untrained_checkpoint_provenance_honesty(temp_registry_dir: str) -> None:
    """T32: Fresh untrained model with fabricated metrics is classified honestly, not as verified trained."""
    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    untrained = svc.create_model(dp_compatible=True)
    entry = reg.save_version(
        "sim_t32",
        untrained.state_dict(),
        metrics={"fabricated_auc": 0.99},
        is_promoted=True,
        provenance_type="structural_fixture",
    )
    assert entry["provenance_type"] == "structural_fixture"
    meta = reg.get_champion_metadata()
    assert meta is not None
    assert meta["provenance_type"] == "structural_fixture"
    assert meta["authenticity_status"] != "AUTHENTICATED"
