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
import time
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.services.model_registry import ModelRegistry, compute_file_sha256
from app.application.services.model_service import NUM_FEATURES, FraudDetectionModel, ModelService
from app.config import Settings, get_settings
from app.domain.model_serving_errors import (
    ModelCompatibilityError,
    ModelExecutionError,
    ModelIntegrityError,
    ModelNotAvailableError,
)
from app.presentation.routers.realtime_inference import (
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

    with patch("app.infrastructure.cache.get_redis_client", return_value=mock_redis):
        with patch("pickle.loads") as mock_pickle_loads:
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

    # Generate valid HMAC
    valid_hmac = hmac.new(
        settings.payload_signing_secret.encode("utf-8"),
        valid_bytes,
        hashlib.sha256,
    ).hexdigest()
    valid_sha256 = hashlib.sha256(valid_bytes).hexdigest()
    valid_auth = json.dumps({
        "hmac": valid_hmac,
        "sha256": valid_sha256,
        "cached_at": time.time(),
    }).encode("utf-8")

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
    missing_path = os.path.join(temp_registry_dir, "missing_weights.pt")
    torch.save(missing_sd, missing_path)

    settings = get_settings()
    svc = ModelService(settings)
    reg = ModelRegistry(storage_dir=temp_registry_dir)

    with patch.object(reg, "get_champion_artifact_path", return_value=missing_path):
        with pytest.raises(ModelCompatibilityError) as exc_info:
            svc.get_champion(dp_compatible=True, registry=reg)
        assert "Strict parameter loading failed" in str(exc_info.value)

    # Case B: Unexpected parameters
    unexpected_sd = dict(real_sd)
    unexpected_sd["rogue_layer.weight"] = torch.randn(10, 10)
    unexpected_path = os.path.join(temp_registry_dir, "unexpected_weights.pt")
    torch.save(unexpected_sd, unexpected_path)

    with patch.object(reg, "get_champion_artifact_path", return_value=unexpected_path):
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

    with patch("app.presentation.routers.realtime_inference.get_scripted_model", return_value=(mock_failing_model, False)):
        with patch.dict(os.environ, {"APP_ENV": "development", "ENABLE_DEMO_FALLBACK": "false"}):
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
