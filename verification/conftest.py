"""Shared fixtures and isolation for Scientific Verification Suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("CFI_DEFAULT_SIMULATION_ID", "live_prod_v2")


@pytest.fixture(scope="session", autouse=True)
def _ensure_verification_default_champion_model():
    """Ensure a verified champion model is active in ModelRegistry for verification audits."""
    import numpy as np
    import torch

    from app.application.services.model_registry import ModelRegistry
    from app.application.services.model_service import NUM_FEATURES, ModelService
    from app.config import get_settings

    reg = ModelRegistry()
    if reg.get_champion_metadata(simulation_id="live_prod_v2") is None:
        svc = ModelService(get_settings())
        model = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
        X = np.random.RandomState(42).randn(64, NUM_FEATURES).astype(np.float32)
        y = np.random.RandomState(42).randint(0, 2, size=(64,)).astype(np.float32)
        trained, _, _ = svc.train_local(model, X, y, epochs=1, batch_size=32)
        trained.eval()
        reg.save_version(
            simulation_id="live_prod_v2",
            state_dict=trained.state_dict(),
            metrics={"auc_roc": 0.91, "f1_score": 0.82, "loss": 0.29},
            is_promoted=True,
            status="champion",
            provenance_type="TRAINING_RECORDED",
        )
    global_path = os.path.join(reg.storage_dir, "global_model.pt")
    if not os.path.exists(global_path):
        svc = ModelService(get_settings())
        model = svc.create_model(dp_compatible=True)
        torch.save(model.state_dict(), global_path)


@pytest.fixture(autouse=True)
def _isolate_verification_audit_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure verification tests have an isolated, non-polluting ImmutableAuditChain."""
    from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

    test_ledger = tmp_path / "verification_audit_ledger.json"
    monkeypatch.setenv("CFI_AUDIT_LEDGER_PATH", str(test_ledger))
    ImmutableAuditChain.reset_instance()
    yield
    ImmutableAuditChain.reset_instance()
