"""Unit and integration test suite for 01-F.1 Operational Federated Training Integration.

Verifies Criteria A through I:
A. Authorized initiation reaches real production execution adapter.
B. Unauthorized initiation is rejected before privileged work begins (401/403).
C. Missing or invalid production authority fails closed.
D. Raw bank data and private credentials do not cross forbidden boundaries.
E. Training with legitimate participant identifiers does not require a hardcoded BANK_A participant.
F. Training-start failures cannot silently produce valid completion.
G. Simulation cannot be substituted for production execution.
H. Genuine completion preserves verified evidence and inactive registry status.
I. No valid receipt, completion record, or production candidate is fabricated when execution fails.
"""

import hashlib
import multiprocessing as mp
import os
import shutil
import tempfile
import threading
import time
import unittest.mock as mock
from typing import Any

import numpy as np
import pandas as pd
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from app.application.schemas.simulation import (
    OperationalFederationRequest,
)
from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    IsolatedReceiptSigningAuthority,
    setup_bank_private_storage,
    spawn_bank_workers,
)
from app.application.services.model_registry import (
    ModelAuthorizationError,
    ModelIntegrityError,
    ModelRegistry,
)
from app.application.services.model_service import NUM_FEATURES
from app.application.services.operational_federation_service import (
    OperationalFederationService,
    SingleHostBankWorkerProvisioner,
    TrustedWorkerEndpoint,
    WorkerEndpointRegistry,
)
from app.application.services.simulation_service import SimulationService
from app.application.services.training_provenance_service import (
    TrainingExecutionEvidence,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrustedTrainingJobRecordSource,
    get_default_training_issuer,
    get_default_training_verifier,
)
from app.infrastructure.database.training_authority_backends import (
    GlobalAggregationEvent,
    RoundObservation,
    SQLiteTrainingJobAuthority,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.main import app

client = TestClient(app)


def _get_operator_auth_headers(
    username: str = "test_ml_lead",
    bank_id: str = "bank_a",
    roles: list[str] | None = None,
    clearance_level: int = 2,
    is_active: bool = True,
) -> dict[str, str]:
    """Helper to register and generate authentic Bearer JWT for an operator principal."""
    auth_svc = AuthenticationService.get_instance()
    roles = roles or ["ml_lead"]

    user = auth_svc.get_user(username)
    if not user:
        user = auth_svc.register_user(
            username=username,
            plain_password="SafePassword123!",
            bank_id=bank_id,
            roles=roles,
            clearance_level=clearance_level,
        )
    user.bank_id = bank_id
    user.roles = roles
    user.clearance_level = clearance_level
    user.is_active = is_active
    user.status = "active" if is_active else "suspended"
    auth_svc.identity_store.save_user(user)

    bundle = auth_svc.create_token_bundle(user)
    return {"Authorization": f"Bearer {bundle.access_token}"}


@pytest.fixture(autouse=True)
def clean_test_environment():
    """Ensure clean test environment and registry per test."""
    WorkerEndpointRegistry.clear()
    tmp_reg = tempfile.mkdtemp(prefix="cfi_test_reg_")
    old_env = os.environ.get("APP_ENV")
    os.environ["APP_ENV"] = "development"

    yield tmp_reg

    WorkerEndpointRegistry.clear()
    shutil.rmtree(tmp_reg, ignore_errors=True)
    if old_env is not None:
        os.environ["APP_ENV"] = old_env
    else:
        os.environ.pop("APP_ENV", None)


class TestOperationalFederationIntegration:
    """Targeted verification for 01-F.1 operational federated training integration."""

    def test_criterion_b_unauthorized_initiation_rejected_with_401(self) -> None:
        """Criterion B: Missing or malformed authentication header is rejected before privileged work begins."""
        payload = {
            "participating_banks": ["BANK_A", "BANK_B"],
            "num_rounds": 2,
        }

        # 1. Missing Authorization header
        resp_missing = client.post("/api/v1/training/federation/jobs", json=payload)
        assert resp_missing.status_code == 401
        assert "Missing Authorization header" in resp_missing.json()["detail"]

        # 2. Malformed Authorization header
        resp_malformed = client.post(
            "/api/v1/training/federation/jobs",
            json=payload,
            headers={"Authorization": "Basic invalid_credentials"},
        )
        assert resp_malformed.status_code == 401

        # 3. Invalid JWT token
        resp_invalid = client.post(
            "/api/v1/training/federation/jobs",
            json=payload,
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
        assert resp_invalid.status_code == 401

    def test_criterion_b_insufficient_role_or_clearance_rejected_with_403(self) -> None:
        """Criterion B: Principal lacking required role or minimum clearance level is rejected with 403."""
        payload = {"participating_banks": ["BANK_A", "BANK_B"]}

        # 1. User with unauthorized role (e.g. viewer)
        viewer_headers = _get_operator_auth_headers(
            username="test_viewer_user",
            roles=["viewer"],
            clearance_level=2,
        )
        resp_role = client.post(
            "/api/v1/training/federation/jobs",
            json=payload,
            headers=viewer_headers,
        )
        assert resp_role.status_code == 403
        assert "not authorized" in resp_role.json()["detail"]

        # 2. User with authorized role but clearance level < 2
        low_clearance_headers = _get_operator_auth_headers(
            username="test_low_clearance_lead",
            roles=["ml_lead"],
            clearance_level=1,
        )
        resp_clearance = client.post(
            "/api/v1/training/federation/jobs",
            json=payload,
            headers=low_clearance_headers,
        )
        assert resp_clearance.status_code == 403
        assert "insufficient" in resp_clearance.json()["detail"]

    def test_criterion_a_and_h_authorized_initiation_produces_verified_candidate(
        self, clean_test_environment: str
    ) -> None:
        """Criteria A & H: Authorized operator successfully triggers execution, producing verified inactive candidate."""
        headers = _get_operator_auth_headers(
            username="test_authorized_lead",
            roles=["admin", "ml_lead"],
            clearance_level=3,
        )

        # Mock the server-side executor to simulate canonical execution
        mock_result = {
            "job_id": "job_op_test_101",
            "training_run_id": "run_job_op_test_101",
            "simulation_id": "sim_op_test_101",
            "consortium_id": "cfi-consortium",
            "status": "COMPLETED",
            "is_verified": True,
            "metrics": {"loss": 0.285, "auc_roc": 0.892, "val_loss": 0.291},
            "artifact_sha256": "a" * 64,
            "final_artifact_sha256": "a" * 64,
            "saved_version": {
                "version": 3,
                "status": "inactive",
                "provenance_type": "TRAINING_RECORDED",
                "is_promoted": False,
                "is_production_eligible": True,
                "consortium_id": "cfi-consortium",
            },
            "is_promoted_champion": False,
            "coordinator_pid": 1100,
            "worker_pids": {"BANK_A": 1101, "BANK_B": 1102},
        }

        with mock.patch.object(
            OperationalFederationService,
            "execute_operational_federation",
            return_value=mock_result,
        ) as mock_exec:
            req_body = {
                "job_id": "job_op_test_101",
                "simulation_id": "sim_op_test_101",
                "participating_banks": ["BANK_A", "BANK_B"],
                "num_rounds": 2,
            }
            resp = client.post(
                "/api/v1/training/federation/jobs",
                json=req_body,
                headers=headers,
            )
            assert resp.status_code == 201
            data = resp.json()

            mock_exec.assert_called_once_with(
                job_id="job_op_test_101",
                simulation_id="sim_op_test_101",
                participating_banks=["BANK_A", "BANK_B"],
                num_rounds=2,
                expected_unique_samples=None,
                consortium_id="cfi-consortium",
                byzantine_defense="none",
                byzantine_f_max=1,
                trimmed_mean_trim_count=None,
            )

            assert data["job_id"] == "job_op_test_101"
            assert data["consortium_id"] == "cfi-consortium"
            assert data["status"] == "COMPLETED"
            assert data["is_verified"] is True
            assert data["model_version"] == 3
            assert data["is_promoted_champion"] is False
            assert data["metrics"]["auc_roc"] == 0.892

    def test_criterion_c_missing_production_authority_fails_closed(self) -> None:
        """Criterion C: Missing production authority prerequisites fail closed with explicit configuration error."""
        svc = OperationalFederationService(app_env="production")

        # 1. Missing coordinator token
        with pytest.raises(TrainingProvenanceConfigurationError) as exc_token:
            svc.resolve_coordinator_token(is_prod=True)
        assert "Production training coordinator credential is not configured" in str(exc_token.value)

        # 2. Missing DB URL
        with pytest.raises(TrainingProvenanceConfigurationError) as exc_db:
            svc.resolve_db_url(is_prod=True)
        assert "Production training job authority database URL is not configured" in str(exc_db.value)

        # 3. Insecure SQLite URL in production
        svc_sqlite = OperationalFederationService(db_url="sqlite:///test.db", app_env="production")
        with pytest.raises(TrainingProvenanceConfigurationError) as exc_sqlite:
            svc_sqlite.resolve_db_url(is_prod=True)
        assert "Production training job authority database must be PostgreSQL" in str(exc_sqlite.value)

        # 4. Missing issuer signing key path
        with pytest.raises(TrainingProvenanceConfigurationError) as exc_sign:
            svc.resolve_signing_authority("valid_coord_tok", "postgresql://user:pass@localhost/db", "production", is_prod=True)
        assert "CFI_TRAINING_ISSUER_KEY_PATH" in str(exc_sign.value)

        # 5. Missing completion authority
        with pytest.raises(TrainingProvenanceConfigurationError) as exc_comp:
            svc.resolve_completion_authority("valid_coord_tok", "production", is_prod=True)
        assert "Production isolated completion authority is not configured" in str(exc_comp.value)

        # 6. Missing pre-provisioned worker endpoints in production
        with pytest.raises(TrainingProvenanceSecurityError) as exc_worker:
            svc.resolve_worker_endpoints(["BANK_A", "BANK_B"], is_prod=True)
        assert "Production bank worker endpoints are not provisioned or registered" in str(exc_worker.value)

    def test_criterion_d_forbidden_boundaries_prevent_client_credential_injection(self) -> None:
        """Criterion D: Raw bank data and private credentials cannot be injected via client request."""
        # OperationalFederationRequest accepts only safe domain fields
        req = OperationalFederationRequest(
            job_id="job_sec_01",
            simulation_id="sim_sec_01",
            participating_banks=["BANK_A", "BANK_B"],
            num_rounds=2,
        )
        # Attempting to assign private keys or data paths has no effect on coordinator configuration
        assert not hasattr(req, "signing_key_hex")
        assert not hasattr(req, "bank_tokens")
        assert not hasattr(req, "database_url")
        assert not hasattr(req, "raw_data_path")

    def test_criterion_e_training_without_bank_a_participant_executes_successfully(
        self, clean_test_environment: str
    ) -> None:
        """Criterion E: Training with legitimate participant identifiers does NOT require a hardcoded BANK_A participant.

        Validates that run_bank_worker_node and authority start logic execute cleanly
        for configurations such as ['BANK_B', 'BANK_C'].
        """
        registry_dir = clean_test_environment

        # Instantiate manager with banks B and C (no BANK_A) using shared disk store
        db_file = os.path.join(clean_test_environment, "authority.db")
        manager = DistributedFederatedTrainingManager(
            coordinator_token="test_coord_token_b_c_only",
            db_url=db_file,
            app_env="development",
        )

        val_X = np.random.randn(20, NUM_FEATURES).astype(np.float32)
        val_y = np.random.randint(0, 2, size=20).astype(np.float32)

        # Execute federation with BANK_B and BANK_C only
        X_b = np.random.randn(50, NUM_FEATURES).astype(np.float32)
        y_b = np.random.randint(0, 2, size=50).astype(np.float32)
        X_c = np.random.randn(50, NUM_FEATURES).astype(np.float32)
        y_c = np.random.randint(0, 2, size=50).astype(np.float32)
        partitions = {"BANK_B": (X_b, y_b), "BANK_C": (X_c, y_c)}

        result = manager.execute_distributed_federation(
            job_id="job_non_bank_a_99",
            training_run_id="run_non_bank_a_99",
            simulation_id="sim_non_bank_a_99",
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_B", "BANK_C"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
        )

        assert result["status"] == "COMPLETED"
        assert result["is_verified"] is True
        assert "BANK_B" in result["worker_pids"]
        assert "BANK_C" in result["worker_pids"]
        assert "BANK_A" not in result["worker_pids"]

        # Confirm model candidate registration
        saved_version = result["saved_version"]
        assert saved_version["status"] == "inactive"
        assert saved_version["provenance_type"] == "TRAINING_RECORDED"
        assert result["is_promoted_champion"] is False

    def test_criterion_f_training_start_failure_aborts_fail_closed(
        self, clean_test_environment: str
    ) -> None:
        """Criterion F: Training-start failures cannot silently produce valid completion.

        If a worker fails to initiate or verify job start in PostgreSQL authority,
        the exception is NOT swallowed; the round aborts and fails closed.
        """
        manager = DistributedFederatedTrainingManager(
            coordinator_token="test_coord_token_start_fail",
            db_url="",
            app_env="development",
        )

        val_X = np.random.randn(10, NUM_FEATURES).astype(np.float32)
        val_y = np.random.randint(0, 2, size=10).astype(np.float32)

        X_a = np.random.randn(30, NUM_FEATURES).astype(np.float32)
        y_a = np.random.randint(0, 2, size=30).astype(np.float32)
        X_b = np.random.randn(30, NUM_FEATURES).astype(np.float32)
        y_b = np.random.randint(0, 2, size=30).astype(np.float32)
        partitions = {"BANK_A": (X_a, y_a), "BANK_B": (X_b, y_b)}

        # Patch authority start_training_job to simulate a database start failure
        with mock.patch(
            "app.application.services.training_provenance_service.TrustedTrainingJobRecordSource.start_training_job",
            side_effect=RuntimeError("Database deadlock on job start"),
        ):
            with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
                manager.execute_distributed_federation(
                    job_id="job_start_fail_01",
                    training_run_id="run_start_fail_01",
                    simulation_id="sim_start_fail_01",
                    holdout_val_data=(val_X, val_y),
                    participating_banks=["BANK_A", "BANK_B"],
                    bank_partitions=partitions,
                    num_rounds=1,
                )
            assert "Round 1 aborted" in str(exc_info.value)

    def test_criterion_g_simulation_cannot_be_substituted_for_production(
        self, clean_test_environment: str
    ) -> None:
        """Criterion G: Simulation artifacts carry UNVERIFIED provenance and cannot satisfy production admission.

        Demonstrates that in-process simulations cannot acquire production promotion
        eligibility even if an in-process signature exists.
        """
        import inspect

        import torch

        from app.application.services.model_registry import (
            ModelRegistry,
            validate_production_admission,
        )
        from app.config import get_settings

        # 1. Verify validate_production_admission strictly blocks UNVERIFIED models in production
        unverified_entry = {
            "version": 1,
            "status": "champion",
            "provenance_type": "UNVERIFIED",
            "is_production_eligible": False,
            "sha256": "0" * 64,
        }

        with mock.patch.object(get_settings(), "app_env", "production"):
            with pytest.raises(ModelIntegrityError) as exc_info:
                validate_production_admission(unverified_entry, context="promote_version")
            assert "ineligible for operational champion serving in production environment" in str(exc_info.value)

            # Even if marked active or champion, UNVERIFIED is rejected
            with pytest.raises(ModelIntegrityError):
                validate_production_admission(
                    {"provenance_type": "UNVERIFIED", "status": "active"},
                    context="champion_activation",
                )

        # 2. Verify ModelRegistry saves simulation models as UNVERIFIED with is_production_eligible=False
        reg = ModelRegistry(storage_dir=clean_test_environment)
        saved = reg.save_version(
            simulation_id="sim_research_demo_101",
            state_dict={"layer": torch.tensor([1.0])},
            provenance_type="UNVERIFIED",
            status="inactive",
            is_promoted=False,
        )
        assert saved["provenance_type"] == "UNVERIFIED"
        assert saved["is_production_eligible"] is False
        assert saved["status"] == "inactive"

        # 3. Verify SimulationService source code enforces UNVERIFIED provenance in all saves
        src = inspect.getsource(SimulationService.run_simulation)
        assert 'provenance_type="UNVERIFIED"' in src
        assert 'is_promoted=False' in src
        assert 'status="inactive"' in src

    def test_criterion_i_execution_failure_never_fabricates_receipt_or_candidate(
        self, clean_test_environment: str
    ) -> None:
        """Criterion I: No valid receipt, completion record, or production candidate is fabricated when execution fails."""
        manager = DistributedFederatedTrainingManager(
            coordinator_token="test_coord_token_fail",
            db_url="",
            app_env="development",
        )

        val_X = np.random.randn(10, NUM_FEATURES).astype(np.float32)
        val_y = np.random.randint(0, 2, size=10).astype(np.float32)

        # Force failure in observation collection (e.g. timeout / worker crash)
        with mock.patch.object(
            manager,
            "fl_engine",
            side_effect=RuntimeError("Aggregation engine failure"),
        ):
            with pytest.raises((TrainingProvenanceSecurityError, RuntimeError)):
                manager.execute_distributed_federation(
                    job_id="job_fail_02",
                    training_run_id="run_fail_02",
                    simulation_id="sim_fail_02",
                    holdout_val_data=(val_X, val_y),
                    participating_banks=["BANK_A", "BANK_B"],
                    num_rounds=1,
                )

        # Verify no candidate model was saved in the registry
        reg = ModelRegistry(storage_dir=clean_test_environment)
        assert reg.list_versions("sim_fail_02") == []

    def test_cfi_cli_federate_command(self) -> None:
        """Test cfi-cli federate subcommand parses arguments and invokes API endpoint."""
        from app.presentation.cli.cfi_cli import command_federate, main

        mock_api_resp = {
            "job_id": "job_cli_01",
            "simulation_id": "sim_cli_01",
            "status": "COMPLETED",
            "is_verified": True,
            "metrics": {"auc_roc": 0.88, "loss": 0.32},
            "artifact_sha256": "abcdef" * 10,
            "model_version": 2,
            "is_promoted_champion": False,
        }

        with mock.patch("app.presentation.cli.cfi_cli._api_request", return_value=mock_api_resp) as mock_req:
            res = command_federate(
                coordinator_url="http://localhost:8000",
                token="valid_operator_token",
                banks=["BANK_B", "BANK_C"],
                rounds=3,
                job_id="job_cli_01",
            )
            assert res["status"] == "COMPLETED"
            assert res["is_verified"] is True
            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert call_args[0][0] == "POST"
            assert call_args[0][1] == "http://localhost:8000/api/v1/training/federation/jobs"
            assert call_args[0][2]["participating_banks"] == ["BANK_B", "BANK_C"]
            assert call_args[0][2]["num_rounds"] == 3
            assert call_args[1]["headers"]["Authorization"] == "Bearer valid_operator_token"

        # Test CLI main entrypoint dispatch
        with mock.patch("app.presentation.cli.cfi_cli.command_federate", return_value=mock_api_resp) as mock_cmd:
            ret = main([
                "federate",
                "--coordinator-url",
                "http://localhost:8000",
                "--token",
                "test_tok",
                "--banks",
                "BANK_A",
                "BANK_B",
                "--rounds",
                "2",
            ])
            assert ret == 0
            mock_cmd.assert_called_once()

    def test_institution_scoped_training_authorization(self) -> None:
        """Requirement 2: Enforce authoritative ConsortiumGovernanceService checks and institution-scoped operator authorization."""
        # 1. Non-existent consortium rejected with 404
        bank_a_headers = _get_operator_auth_headers(
            username="operator_bank_a_lead",
            bank_id="bank_a",
            roles=["ml_lead"],
            clearance_level=2,
        )
        resp_unknown_consortium = client.post(
            "/api/v1/training/federation/jobs",
            json={
                "consortium_id": "non_existent_alliance_xyz",
                "participating_banks": ["BANK_A", "BANK_B"],
                "num_rounds": 1,
            },
            headers=bank_a_headers,
        )
        assert resp_unknown_consortium.status_code == 404
        assert "does not exist or has not been provisioned" in resp_unknown_consortium.json()["detail"]

        # 2. Operator bound to BANK_A attempting cross-institution training for BANK_B and BANK_C (clearance 2)
        resp_cross = client.post(
            "/api/v1/training/federation/jobs",
            json={"participating_banks": ["BANK_B", "BANK_C"], "num_rounds": 1},
            headers=bank_a_headers,
        )
        assert resp_cross.status_code == 403
        assert "requires elevated consortium administrator authority" in resp_cross.json()["detail"]

        # 3. Operator bound to BANK_A attempting cross-institution training including BANK_A and BANK_B (clearance 2)
        # Cannot infer cross-institution authority simply because initiator includes their own bank
        resp_cross_with_self = client.post(
            "/api/v1/training/federation/jobs",
            json={"participating_banks": ["BANK_A", "BANK_B"], "num_rounds": 1},
            headers=bank_a_headers,
        )
        assert resp_cross_with_self.status_code == 403
        assert "requires elevated consortium administrator authority" in resp_cross_with_self.json()["detail"]

        # 4. Caller claiming CENTRAL bank identity (not an authorized member of cfi-consortium) is rejected
        central_headers = _get_operator_auth_headers(
            username="fake_central_operator",
            bank_id="CENTRAL",
            roles=["admin"],
            clearance_level=2,
        )
        resp_fake_central = client.post(
            "/api/v1/training/federation/jobs",
            json={"participating_banks": ["BANK_A", "BANK_B"], "num_rounds": 1},
            headers=central_headers,
        )
        assert resp_fake_central.status_code == 403
        assert "not an authorized member of target consortium" in resp_fake_central.json()["detail"]

        # 5. Duplicate participant identities rejected with 400
        resp_dup = client.post(
            "/api/v1/training/federation/jobs",
            json={"participating_banks": ["BANK_A", "BANK_A", "BANK_B"], "num_rounds": 1},
            headers=bank_a_headers,
        )
        assert resp_dup.status_code == 400
        assert "Duplicate participant bank identity" in resp_dup.json()["detail"]

        # 6. Unrecognized or invalid consortium participant rejected with 400
        resp_unrec = client.post(
            "/api/v1/training/federation/jobs",
            json={"participating_banks": ["BANK_A", "BANK_UNREGISTERED"], "num_rounds": 1},
            headers=bank_a_headers,
        )
        assert resp_unrec.status_code == 400
        assert "not an authorized member of consortium" in resp_unrec.json()["detail"]

        # 7. Empty or missing participating_banks rejected with 400
        resp_empty = client.post(
            "/api/v1/training/federation/jobs",
            json={"participating_banks": [], "num_rounds": 1},
            headers=bank_a_headers,
        )
        assert resp_empty.status_code in (400, 422)

        # 8. Consortium Member Admin with elevated authority (Clearance >= 3 and admin role) CAN initiate multi-bank training
        consortium_admin_headers = _get_operator_auth_headers(
            username="bank_a_consortium_admin",
            bank_id="BANK_A",
            roles=["admin"],
            clearance_level=3,
        )
        mock_ok = {
            "job_id": "job_consortium_01",
            "training_run_id": "run_01",
            "simulation_id": "sim_01",
            "consortium_id": "cfi-consortium",
            "status": "COMPLETED",
            "is_verified": True,
            "metrics": {"auc_roc": 0.85},
            "artifact_sha256": "f" * 64,
            "model_version": 4,
            "is_promoted_champion": False,
            "coordinator_pid": 100,
            "worker_pids": {"BANK_B": 101, "BANK_C": 102},
        }
        with mock.patch.object(OperationalFederationService, "execute_operational_federation", return_value=mock_ok):
            resp_consortium = client.post(
                "/api/v1/training/federation/jobs",
                json={"participating_banks": ["BANK_B", "BANK_C"], "num_rounds": 1},
                headers=consortium_admin_headers,
            )
            assert resp_consortium.status_code == 201
            assert resp_consortium.json()["job_id"] == "job_consortium_01"
            assert resp_consortium.json()["consortium_id"] == "cfi-consortium"

    def test_worker_endpoint_lifecycle_and_stale_eviction(self) -> None:
        """Requirement 1: WorkerEndpointRegistry validates capabilities, authenticates tokens, protects overwrites, and evicts dead workers."""
        svc = OperationalFederationService(app_env="development")

        # 1. Registering with dead PID is strictly rejected
        dead_pid = 99999999
        q_in: mp.Queue = mp.Queue()
        q_out: mp.Queue = mp.Queue()
        with pytest.raises(TrainingProvenanceSecurityError) as exc_dead:
            WorkerEndpointRegistry.register_worker_endpoint(
                bank_id="BANK_A",
                endpoint={"inbox": q_in, "outbox": q_out},
                pid=dead_pid,
            )
        assert "dead/terminated" in str(exc_dead.value) or "is not alive" in str(exc_dead.value)

        # 2. Registering with invalid queues is rejected
        current_pid = os.getpid()
        with pytest.raises(TrainingProvenanceConfigurationError) as exc_caps:
            WorkerEndpointRegistry.register_worker_endpoint(
                bank_id="BANK_A",
                endpoint={"inbox": "not_a_queue", "outbox": q_out},
                pid=current_pid,
            )
        assert "put" in str(exc_caps.value)

        # 3. In production, unauthenticated registration is rejected
        with pytest.raises(TrainingProvenanceSecurityError) as exc_no_tok:
            WorkerEndpointRegistry.register_worker_endpoint(
                bank_id="BANK_A",
                endpoint={"inbox": q_in, "outbox": q_out},
                pid=current_pid,
                app_env="production",
            )
        assert "production requires authentic institutional registration token" in str(exc_no_tok.value)

        # 4. In production, registering with invalid/spoofed token is rejected
        with mock.patch("app.application.services.operational_federation_service.get_settings") as mock_set:
            mock_s = mock.MagicMock()
            mock_s.app_env = "production"
            mock_s.training_bank_tokens = {"BANK_A": "valid_secret_token_12345"}
            mock_set.return_value = mock_s
            with pytest.raises(TrainingProvenanceSecurityError) as exc_bad_tok:
                WorkerEndpointRegistry.register_worker_endpoint(
                    bank_id="BANK_A",
                    endpoint={"inbox": q_in, "outbox": q_out},
                    pid=current_pid,
                    registration_token="spoofed_token_wrong",
                    app_env="production",
                )
            assert "invalid institutional token" in str(exc_bad_tok.value)

            # 5. Registering with valid institutional token succeeds
            WorkerEndpointRegistry.register_worker_endpoint(
                bank_id="BANK_A",
                endpoint={"inbox": q_in, "outbox": q_out},
                pid=current_pid,
                registration_token="valid_secret_token_12345",
                app_env="production",
            )
            assert WorkerEndpointRegistry.is_registered("BANK_A")
            ep = WorkerEndpointRegistry.get_trusted_endpoint("BANK_A")
            assert ep is not None
            assert ep.pid == current_pid
            assert ep.attestation_type == "institutional_token"
            assert ep.state == "ACTIVE"

            # 6. Overwrite protection: unauthenticated attempt to overwrite active verified worker is rejected
            with pytest.raises(TrainingProvenanceSecurityError) as exc_overwrite:
                WorkerEndpointRegistry.register_worker_endpoint(
                    bank_id="BANK_A",
                    endpoint={"inbox": q_in, "outbox": q_out},
                    pid=current_pid,
                    app_env="development",
                )
            assert "Cannot overwrite active" in str(exc_overwrite.value)

        # 7. Unauthenticated revocation of institutional worker is rejected
        with pytest.raises(TrainingProvenanceSecurityError) as exc_unauth_rev:
            WorkerEndpointRegistry.revoke_worker_endpoint("BANK_A", reason="Unauthenticated key rotation")
        assert "without valid credential" in str(exc_unauth_rev.value)

        # Explicit authenticated revocation marks state REVOKED and removes from active endpoint retrieval
        WorkerEndpointRegistry.revoke_worker_endpoint(
            "BANK_A",
            reason="Key rotation",
            token="valid_secret_token_12345",
            bank_tokens={"BANK_A": "valid_secret_token_12345"},
        )
        assert not WorkerEndpointRegistry.is_registered("BANK_A")
        assert WorkerEndpointRegistry.get_worker_endpoint("BANK_A") is None

        # 8. If worker dies (simulate by replacing registration with dead PID), resolve_worker_endpoints evicts it
        with WorkerEndpointRegistry._lock:
            WorkerEndpointRegistry._registry["BANK_A"] = TrustedWorkerEndpoint(
                bank_id="BANK_A",
                endpoint={"inbox": q_in, "outbox": q_out},
                pid=dead_pid,
                registered_at=time.time(),
                capabilities=("inbox", "outbox"),
                state="ACTIVE",
            )
        with pytest.raises(TrainingProvenanceSecurityError) as exc_evict:
            svc.resolve_worker_endpoints(["BANK_A"], is_prod=True)
        assert "not provisioned or registered" in str(exc_evict.value)
        assert not WorkerEndpointRegistry.is_registered("BANK_A")

        # 9. Batch registration helper register_provisioned_workers succeeds
        WorkerEndpointRegistry.clear()
        fake_procs = {"BANK_A": mock.MagicMock(pid=current_pid)}
        WorkerEndpointRegistry.register_provisioned_workers(
            endpoints={"BANK_A": {"inbox": q_in, "outbox": q_out}},
            processes=fake_procs,
            bank_tokens={"BANK_A": "tok_batch_123"},
            app_env="development",
        )
        assert WorkerEndpointRegistry.is_registered("BANK_A")

    def test_holdout_evaluation_data_contract(self) -> None:
        """Requirement 3: Enforce FEATURE_NAMES schema ordering, canonical encoding, and non-empty finite evaluation data."""
        svc = OperationalFederationService(app_env="development")

        # 1. Valid (X, y) tuple with exactly NUM_FEATURES and binary labels
        valid_X = np.random.randn(25, NUM_FEATURES).astype(np.float32)
        valid_y = np.array([0] * 15 + [1] * 10, dtype=np.float32)
        resolved_X, resolved_y = svc.resolve_holdout_data(is_prod=False, holdout_data=(valid_X, valid_y))
        assert resolved_X.shape == (25, NUM_FEATURES)
        assert resolved_y.shape == (25,)

        # 2. Valid DataFrame with canonical raw FEATURE_NAMES correctly encoded to NUM_FEATURES
        raw_df = pd.DataFrame({
            "transaction_amount": [100.0, 50.0, 250.0, 15.0],
            "merchant_category": ["dining", "grocery", "electronics", "travel"],
            "country_code": ["US", "US", "GB", "CA"],
            "device_type": ["mobile", "web", "mobile", "pos"],
            "velocity": [1.0, 2.0, 7.0, 1.0],
            "hour_of_day": [12, 14, 2, 8],
            "merchant_risk_score": [0.10, 0.05, 0.88, 0.20],
            "customer_history_score": [0.85, 0.95, 0.12, 0.70],
            "chargeback_count": [0, 0, 2, 0],
            "account_age_days": [365, 720, 30, 180],
            "is_fraud": [0, 0, 1, 0],
        })
        enc_X, enc_y = svc.resolve_holdout_data(is_prod=False, holdout_data=raw_df)
        assert enc_X.shape == (4, NUM_FEATURES)
        assert enc_y.shape == (4,)

        # 3. Incompatible feature dimension (e.g. 7 columns instead of 10) rejected
        invalid_X = np.random.randn(20, 7).astype(np.float32)
        with pytest.raises(TrainingProvenanceConfigurationError) as exc_dim:
            svc.resolve_holdout_data(is_prod=False, holdout_data=(invalid_X, valid_y[:20]))
        assert "dimension" in str(exc_dim.value) or "10" in str(exc_dim.value)

        # 4. Non-finite values (NaN / Inf) rejected
        nan_X = np.random.randn(20, NUM_FEATURES).astype(np.float32)
        nan_X[0, 0] = np.nan
        with pytest.raises(TrainingProvenanceConfigurationError) as exc_nan:
            svc.resolve_holdout_data(is_prod=False, holdout_data=(nan_X, valid_y[:20]))
        assert "finite" in str(exc_nan.value)

        # 5. Single-class labels (all 0s) rejected
        single_class_y = np.zeros(20, dtype=np.float32)
        with pytest.raises(TrainingProvenanceConfigurationError) as exc_class:
            svc.resolve_holdout_data(is_prod=False, holdout_data=(valid_X[:20], single_class_y))
        assert "both positive and negative classes" in str(exc_class.value)

        # 6. Production mode without holdout fails closed (zero synthetic fallback)
        svc_prod = OperationalFederationService(app_env="production")
        with pytest.raises(TrainingProvenanceConfigurationError) as exc_prod_syn:
            svc_prod.resolve_holdout_data(is_prod=True, holdout_data=None)
        assert "Production holdout evaluation dataset is not configured" in str(exc_prod_syn.value)

    def test_authority_process_lifecycle_cleanup(self) -> None:
        """Requirement 5: OperationalFederationService owns and cleans up spawned isolated authorities across all failure paths."""
        svc = OperationalFederationService(app_env="production")

        mock_signing = mock.MagicMock()
        mock_comp = mock.MagicMock()

        # 1. Failure during completion authority resolution terminates already-spawned signing authority
        with mock.patch.object(svc, "resolve_signing_authority", return_value=(mock_signing, True)), \
             mock.patch.object(svc, "resolve_completion_authority", side_effect=TrainingProvenanceConfigurationError("Comp vault missing")), \
             mock.patch.object(svc, "resolve_coordinator_token", return_value="tok"), \
             mock.patch.object(svc, "resolve_db_url", return_value="postgresql://u:p@localhost/db"), \
             mock.patch.object(svc, "resolve_worker_endpoints", return_value=({"BANK_A": {}, "BANK_B": {}}, {"BANK_A": 1, "BANK_B": 2})):
            with pytest.raises(TrainingProvenanceConfigurationError):
                svc.execute_operational_federation(
                    job_id="job_clean_intermediate_01",
                    participating_banks=["BANK_A", "BANK_B"],
                    num_rounds=1,
                )
        mock_signing.terminate.assert_called_once()
        mock_signing.reset_mock()

        # 2. Failure during holdout resolution terminates BOTH spawned signing and completion authorities
        with mock.patch.object(svc, "resolve_signing_authority", return_value=(mock_signing, True)), \
             mock.patch.object(svc, "resolve_completion_authority", return_value=(mock_comp, ("127.0.0.1", 9999), True)), \
             mock.patch.object(svc, "resolve_coordinator_token", return_value="tok"), \
             mock.patch.object(svc, "resolve_db_url", return_value="postgresql://u:p@localhost/db"), \
             mock.patch.object(svc, "resolve_worker_endpoints", return_value=({"BANK_A": {}, "BANK_B": {}}, {"BANK_A": 1, "BANK_B": 2})), \
             mock.patch.object(svc, "resolve_holdout_data", side_effect=TrainingProvenanceConfigurationError("Holdout corrupted")):
            with pytest.raises(TrainingProvenanceConfigurationError):
                svc.execute_operational_federation(
                    job_id="job_clean_intermediate_02",
                    participating_banks=["BANK_A", "BANK_B"],
                    num_rounds=1,
                )
        mock_signing.terminate.assert_called_once()
        mock_comp.terminate.assert_called_once()
        mock_signing.reset_mock()
        mock_comp.reset_mock()

        # 3. Simulate execution failure during runner dispatch in production terminates both
        with mock.patch.object(svc, "resolve_signing_authority", return_value=(mock_signing, True)), \
             mock.patch.object(svc, "resolve_completion_authority", return_value=(mock_comp, ("127.0.0.1", 9999), True)), \
             mock.patch.object(svc, "resolve_coordinator_token", return_value="tok"), \
             mock.patch.object(svc, "resolve_db_url", return_value="postgresql://u:p@localhost/db"), \
             mock.patch.object(svc, "resolve_worker_endpoints", return_value=({"BANK_A": {}, "BANK_B": {}}, {"BANK_A": 1, "BANK_B": 2})), \
             mock.patch.object(svc, "resolve_holdout_data", return_value=(np.zeros((10, NUM_FEATURES)), np.array([0, 1] * 5))), \
             mock.patch("app.application.services.operational_federation_service.run_production_federation", side_effect=RuntimeError("Runner crashed")):
            with pytest.raises(RuntimeError):
                svc.execute_operational_federation(
                    job_id="job_clean_01",
                    participating_banks=["BANK_A", "BANK_B"],
                    num_rounds=1,
                )

        # Both spawned authorities must have been cleanly terminated via finally
        mock_signing.terminate.assert_called_once()
        mock_comp.terminate.assert_called_once()

        # 4. Externally provisioned authorities are NOT terminated
        mock_ext_signing = mock.MagicMock()
        mock_ext_comp = mock.MagicMock()
        svc_ext = OperationalFederationService(
            signing_authority=mock_ext_signing,
            comp_authority=mock_ext_comp,
            app_env="production",
        )
        with mock.patch.object(svc_ext, "resolve_coordinator_token", return_value="tok"), \
             mock.patch.object(svc_ext, "resolve_db_url", return_value="postgresql://u:p@localhost/db"), \
             mock.patch.object(svc_ext, "resolve_worker_endpoints", return_value=({"BANK_A": {}, "BANK_B": {}}, {"BANK_A": 1, "BANK_B": 2})), \
             mock.patch.object(svc_ext, "resolve_holdout_data", return_value=(np.zeros((10, NUM_FEATURES)), np.array([0, 1] * 5))), \
             mock.patch("app.application.services.operational_federation_service.run_production_federation", side_effect=RuntimeError("Runner crashed")):
            with pytest.raises(RuntimeError):
                svc_ext.execute_operational_federation(
                    job_id="job_clean_ext",
                    participating_banks=["BANK_A", "BANK_B"],
                    num_rounds=1,
                )
        mock_ext_signing.terminate.assert_not_called()
        mock_ext_comp.terminate.assert_not_called()

    def test_truthful_execution_reporting_rejections(self) -> None:
        """Requirement 4: Non-completed status, unverified state, or missing/malformed digests are strictly rejected with 500."""
        headers = _get_operator_auth_headers(username="test_lead_truth", bank_id="bank_a", roles=["admin", "ml_lead"], clearance_level=3)

        # 1. Unverified execution result
        unverified_result = {
            "job_id": "job_unverified_01",
            "consortium_id": "cfi-consortium",
            "status": "COMPLETED",
            "is_verified": False,
            "metrics": {},
            "artifact_sha256": "a" * 64,
            "model_version": 1,
        }
        with mock.patch.object(OperationalFederationService, "execute_operational_federation", return_value=unverified_result):
            resp = client.post(
                "/api/v1/training/federation/jobs",
                json={"participating_banks": ["BANK_A", "BANK_B"]},
                headers=headers,
            )
            assert resp.status_code == 500
            assert "unverified execution state" in resp.json()["detail"]

        # 2. Missing artifact SHA-256
        no_sha_result = {
            "job_id": "job_nosha_01",
            "consortium_id": "cfi-consortium",
            "status": "COMPLETED",
            "is_verified": True,
            "metrics": {},
            "artifact_sha256": None,
            "model_version": 1,
        }
        with mock.patch.object(OperationalFederationService, "execute_operational_federation", return_value=no_sha_result):
            resp = client.post(
                "/api/v1/training/federation/jobs",
                json={"participating_banks": ["BANK_A", "BANK_B"]},
                headers=headers,
            )
            assert resp.status_code == 500
            assert "no verified artifact digest" in resp.json()["detail"]

        # 3. Malformed artifact SHA-256 (not 64 hex characters)
        bad_sha_result = {
            "job_id": "job_badsha_01",
            "consortium_id": "cfi-consortium",
            "status": "COMPLETED",
            "is_verified": True,
            "metrics": {},
            "artifact_sha256": "not_a_valid_hex_sha256",
            "model_version": 1,
        }
        with mock.patch.object(OperationalFederationService, "execute_operational_federation", return_value=bad_sha_result):
            resp = client.post(
                "/api/v1/training/federation/jobs",
                json={"participating_banks": ["BANK_A", "BANK_B"]},
                headers=headers,
            )
            assert resp.status_code == 500
            assert "verified artifact digest" in resp.json()["detail"]

    def test_database_authority_concurrency_and_start_job_lifecycle(self, tmp_path: Any) -> None:
        """Requirement 6: Verify database start_job under actual concurrency and multi-worker lifecycle.

        Verification Environment Note:
        - SQLite atomic transactions (INSERT OR IGNORE + SELECT) are tested under multi-threaded concurrency.
        - Production PostgreSQL cluster concurrency uses ON CONFLICT (job_id) DO NOTHING (UNVERIFIED locally due to absent PostgreSQL service).
        """
        db_path = os.path.join(str(tmp_path), "test_authority_concurrency.db")
        auth = SQLiteTrainingJobAuthority(db_target=db_path)

        # Schedule job with permitted workers BANK_A, BANK_B, BANK_C
        auth.schedule_job(
            job_id="job_conc_01",
            training_run_id="run_conc_01",
            simulation_id="sim_conc_01",
            architecture="CFIFraudDetectorMLP",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=FEATURE_NAMES,
            coordinator_id="COORDINATOR_TEST",
            permitted_workers=("BANK_A", "BANK_B", "BANK_C"),
        )

        # 1. Simultaneous start attempts by separate threads (simulating racing workers)
        results: list[Any] = []
        errors: list[Exception] = []

        def worker_start(w_id: str):
            try:
                started = auth.start_job("job_conc_01", worker_id=w_id)
                results.append(started)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker_start, args=("BANK_B",))
        t2 = threading.Thread(target=worker_start, args=("BANK_C",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0, f"Concurrent start_job produced errors: {errors}"
        assert len(results) == 2
        # Both workers received valid StartedTrainingJob records
        for res in results:
            assert res.job_id == "job_conc_01"
            assert res.status == "STARTED"

        # 2. Duplicate start against already-started job returns the same started record
        dup_start = auth.start_job("job_conc_01", worker_id="BANK_A")
        assert dup_start.job_id == "job_conc_01"
        assert dup_start.status == "STARTED"

        # 3. Unauthorized worker identity is rejected with TrainingProvenanceSecurityError
        with pytest.raises(TrainingProvenanceSecurityError) as exc_unauth:
            auth.start_job("job_conc_01", worker_id="BANK_UNAUTHORIZED")
        assert "not authorized to start job" in str(exc_unauth.value)

    def test_operational_federation_service_real_adapter_execution(self, clean_test_environment: str, tmp_path: Any) -> None:
        """Criteria G & H: Real execution through OperationalFederationService without mocking execute_operational_federation."""
        from cryptography.fernet import Fernet

        from app.application.services.distributed_fl_service import (
            setup_bank_private_storage,
        )

        registry_dir = str(tmp_path / "model_registry")
        db_file = str(tmp_path / "authority.db")
        vault_root = str(tmp_path / "bank_vaults")

        val_X = np.random.randn(20, NUM_FEATURES).astype(np.float32)
        val_y = np.random.randint(0, 2, size=20).astype(np.float32)

        bank_data_paths = {}
        bank_enc_keys = {b: Fernet.generate_key() for b in ("BANK_A", "BANK_B")}
        bank_tokens = {b: f"test_worker_token_{b}" for b in ("BANK_A", "BANK_B")}

        np.random.seed(42)
        for b_id, n_samples in (("BANK_A", 40), ("BANK_B", 40)):
            X_part = np.random.randn(n_samples, NUM_FEATURES).astype(np.float32)
            y_part = np.random.randint(0, 2, size=n_samples).astype(np.float32)
            p = setup_bank_private_storage(
                bank_storage_dir=vault_root,
                bank_id=b_id,
                X_partition=X_part,
                y_partition=y_part,
                bank_token=bank_tokens[b_id],
                bank_encryption_key=bank_enc_keys[b_id],
            )
            bank_data_paths[b_id] = p

        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B"],
            bank_tokens=bank_tokens,
            db_url=db_file,
            bank_data_paths=bank_data_paths,
            bank_encryption_keys=bank_enc_keys,
            app_env="development",
        )

        try:
            pids = {b: proc.pid or 0 for b, proc in procs.items()}
            # Legitimate institutional provisioning owner registers authentic endpoints into registry
            WorkerEndpointRegistry.register_provisioned_workers(
                endpoints=endpoints,
                pids=pids,
                app_env="development",
            )

            svc = OperationalFederationService(
                db_url=db_file,
                holdout_val_data=(val_X, val_y),
                registry_storage_dir=registry_dir,
                app_env="development",
            )

            # Execute through the operational service adapter
            result = svc.execute_operational_federation(
                job_id="job_real_adapter_01",
                simulation_id="sim_real_adapter_01",
                participating_banks=["BANK_A", "BANK_B"],
                num_rounds=1,
            )

            assert result["status"] == "COMPLETED"
            assert result["is_verified"] is True
            assert result["consortium_id"] == "cfi-consortium"
            assert len(result["artifact_sha256"]) == 64
            assert result["model_version"] is not None

            # Confirm inactive candidate was recorded in ModelRegistry
            saved_ver = result["saved_version"]
            assert saved_ver["status"] == "inactive"
            assert saved_ver["provenance_type"] == "TRAINING_RECORDED"
            assert saved_ver.get("consortium_id") == "cfi-consortium"
            assert result["is_promoted_champion"] is False
        finally:
            for proc in procs.values():
                try:
                    proc.terminate()
                    proc.join(timeout=2.0)
                except Exception:
                    pass

    def test_boundary_proof_a_cross_bank_token_misuse_rejected(self) -> None:
        """Targeted Proof A: Cross-bank token misuse and generic worker credential rejection."""
        WorkerEndpointRegistry.clear_for_testing()
        q_in: mp.Queue[Any] = mp.Queue()
        q_out: mp.Queue[Any] = mp.Queue()
        tokens = {
            "BANK_A": "auth_token_bank_a_secret_111",
            "BANK_B": "auth_token_bank_b_secret_222",
        }

        with mock.patch("app.application.services.operational_federation_service.get_settings") as mock_set:
            mock_s = mock.MagicMock()
            mock_s.app_env = "production"
            mock_s.training_bank_tokens = tokens
            mock_s.training_worker_token = "generic_shared_training_worker_credential_999"
            mock_set.return_value = mock_s

            # 1. Bank A registration credentials cannot register Bank B
            with pytest.raises(TrainingProvenanceSecurityError) as exc_cross:
                WorkerEndpointRegistry.register_worker_endpoint(
                    bank_id="BANK_B",
                    endpoint={"inbox": q_in, "outbox": q_out},
                    pid=os.getpid(),
                    registration_token="auth_token_bank_a_secret_111",
                    app_env="production",
                    bank_tokens=tokens,
                )
            assert "invalid institutional token" in str(exc_cross.value)

            # 2. Missing Bank C authorization fails closed
            with pytest.raises(TrainingProvenanceSecurityError) as exc_missing:
                WorkerEndpointRegistry.register_worker_endpoint(
                    bank_id="BANK_C",
                    endpoint={"inbox": q_in, "outbox": q_out},
                    pid=os.getpid(),
                    registration_token="some_unconfigured_token",
                    app_env="production",
                    bank_tokens=tokens,
                )
            assert "no authorized institutional credential configured" in str(exc_missing.value)

            # 3. Generic shared worker credentials are insufficient for institution-scoped registration
            with pytest.raises(TrainingProvenanceSecurityError) as exc_generic:
                WorkerEndpointRegistry.register_worker_endpoint(
                    bank_id="BANK_A",
                    endpoint={"inbox": q_in, "outbox": q_out},
                    pid=os.getpid(),
                    registration_token="generic_shared_training_worker_credential_999",
                    app_env="production",
                    bank_tokens=tokens,
                )
            assert "generic shared worker credentials are insufficient" in str(exc_generic.value)

            # 4. Authentic Bank B provisioner CAN register Bank B with legitimate dedicated token
            WorkerEndpointRegistry.register_worker_endpoint(
                bank_id="BANK_B",
                endpoint={"inbox": q_in, "outbox": q_out},
                pid=os.getpid(),
                registration_token="auth_token_bank_b_secret_222",
                app_env="production",
                bank_tokens=tokens,
            )
            assert WorkerEndpointRegistry.is_registered("BANK_B")
            ep_b = WorkerEndpointRegistry.get_trusted_endpoint("BANK_B")
            assert ep_b is not None
            assert ep_b.attestation_type == "institutional_token"
            assert ep_b.state == "ACTIVE"

    def test_boundary_proof_b_unauthorized_live_worker_replacement_rejected(self) -> None:
        """Targeted Proof B: Unauthorized live-worker replacement rejected; verified ownership preserved."""
        WorkerEndpointRegistry.clear_for_testing()
        q_in1: mp.Queue[Any] = mp.Queue()
        q_out1: mp.Queue[Any] = mp.Queue()
        q_in2: mp.Queue[Any] = mp.Queue()
        q_out2: mp.Queue[Any] = mp.Queue()
        tokens = {"BANK_A": "bank_a_active_token_1234"}

        with mock.patch("app.application.services.operational_federation_service.get_settings") as mock_set:
            mock_s = mock.MagicMock()
            mock_s.app_env = "production"
            mock_s.training_bank_tokens = tokens
            mock_s.training_worker_token = "generic_shared_training_worker_credential_999"
            mock_set.return_value = mock_s

            # 1. Register active worker with live PID
            WorkerEndpointRegistry.register_worker_endpoint(
                bank_id="BANK_A",
                endpoint={"inbox": q_in1, "outbox": q_out1},
                pid=os.getpid(),
                registration_token="bank_a_active_token_1234",
                app_env="production",
                bank_tokens=tokens,
            )
            assert WorkerEndpointRegistry.is_registered("BANK_A")

            # 2. Overwrite attempt on active live worker without allow_replacement=True fails closed
            with pytest.raises(TrainingProvenanceSecurityError) as exc_no_replace:
                WorkerEndpointRegistry.register_worker_endpoint(
                    bank_id="BANK_A",
                    endpoint={"inbox": q_in2, "outbox": q_out2},
                    pid=os.getpid(),
                    registration_token="bank_a_active_token_1234",
                    app_env="production",
                    bank_tokens=tokens,
                    allow_replacement=False,
                )
            assert "Cannot overwrite active live worker" in str(exc_no_replace.value)

            # 3. Unauthenticated development_direct attempt to replace verified institutional worker fails closed
            with pytest.raises(TrainingProvenanceSecurityError) as exc_unauth_downgrade:
                WorkerEndpointRegistry.register_worker_endpoint(
                    bank_id="BANK_A",
                    endpoint={"inbox": q_in2, "outbox": q_out2},
                    pid=os.getpid(),
                    app_env="development",
                    allow_replacement=True,
                )
            assert "Cannot overwrite active verified worker" in str(exc_unauth_downgrade.value)

    def test_boundary_proof_c_worker_revocation_and_legitimate_reregistration(self) -> None:
        """Targeted Proof C: Worker revocation and legitimate re-registration lifecycle."""
        WorkerEndpointRegistry.clear_for_testing()
        q_in: mp.Queue[Any] = mp.Queue()
        q_out: mp.Queue[Any] = mp.Queue()
        tokens = {"BANK_A": "token_lifecycle_bank_a_555"}

        with mock.patch("app.application.services.operational_federation_service.get_settings") as mock_set:
            mock_s = mock.MagicMock()
            mock_s.app_env = "production"
            mock_s.training_bank_tokens = tokens
            mock_s.training_worker_token = "generic_shared_training_worker_credential_999"
            mock_set.return_value = mock_s

            WorkerEndpointRegistry.register_worker_endpoint(
                bank_id="BANK_A",
                endpoint={"inbox": q_in, "outbox": q_out},
                pid=os.getpid(),
                registration_token="token_lifecycle_bank_a_555",
                app_env="production",
                bank_tokens=tokens,
            )

            # 1. Unauthenticated revocation attempt is rejected
            with pytest.raises(TrainingProvenanceSecurityError) as exc_unauth:
                WorkerEndpointRegistry.revoke_worker_endpoint("BANK_A", reason="Unauthorized attempt")
            assert (
                "requires authentic institutional token" in str(exc_unauth.value)
                or "without valid credential" in str(exc_unauth.value)
            )

            # 2. Revocation with mismatched token is rejected
            with pytest.raises(TrainingProvenanceSecurityError) as exc_mismatch:
                WorkerEndpointRegistry.revoke_worker_endpoint(
                    "BANK_A",
                    reason="Wrong token attempt",
                    token="wrong_token_777",
                    bank_tokens=tokens,
                )
            assert "invalid institutional token" in str(exc_mismatch.value)

            # 3. Authentic revocation with Bank A token succeeds
            WorkerEndpointRegistry.revoke_worker_endpoint(
                "BANK_A",
                reason="Authorized key rotation",
                token="token_lifecycle_bank_a_555",
                bank_tokens=tokens,
            )
            assert not WorkerEndpointRegistry.is_registered("BANK_A")
            assert WorkerEndpointRegistry.get_worker_endpoint("BANK_A") is None

            # 4. Legitimate re-registration with authentic token and allow_replacement=True succeeds
            WorkerEndpointRegistry.register_worker_endpoint(
                bank_id="BANK_A",
                endpoint={"inbox": q_in, "outbox": q_out},
                pid=os.getpid(),
                registration_token="token_lifecycle_bank_a_555",
                app_env="production",
                bank_tokens=tokens,
                allow_replacement=True,
            )
            assert WorkerEndpointRegistry.is_registered("BANK_A")
            ep_re = WorkerEndpointRegistry.get_trusted_endpoint("BANK_A")
            assert ep_re is not None
            assert ep_re.state == "ACTIVE"

    def test_boundary_proof_d_single_host_worker_provisioner_lifecycle(self, tmp_path: Any) -> None:
        """Targeted Proof D: Authenticated single-host worker provisioning through supported runtime owner."""
        WorkerEndpointRegistry.clear_for_testing()
        db_file = os.path.join(str(tmp_path), "test_provisioner.db")
        vault_root = os.path.join(str(tmp_path), "vaults")
        SQLiteTrainingJobAuthority(db_target=db_file)

        tokens = {
            "BANK_A": "prov_token_a_123",
            "BANK_B": "prov_token_b_456",
        }
        enc_keys = {b: Fernet.generate_key() for b in tokens}
        data_paths = {}

        np.random.seed(42)
        for b_id in tokens:
            X_part = np.random.randn(20, NUM_FEATURES).astype(np.float32)
            y_part = np.random.randint(0, 2, size=20).astype(np.float32)
            data_paths[b_id] = setup_bank_private_storage(
                bank_storage_dir=vault_root,
                bank_id=b_id,
                X_partition=X_part,
                y_partition=y_part,
                bank_token=tokens[b_id],
                bank_encryption_key=enc_keys[b_id],
            )

        provisioner = SingleHostBankWorkerProvisioner(
            bank_tokens=tokens,
            db_url=db_file,
            bank_data_paths=data_paths,
            bank_encryption_keys=enc_keys,
            app_env="development",
        )

        with provisioner:
            endpoints = provisioner.provision_bank_workers(["BANK_A", "BANK_B"])
            assert len(endpoints) == 2
            assert "BANK_A" in endpoints and "BANK_B" in endpoints
            assert WorkerEndpointRegistry.is_registered("BANK_A")
            assert WorkerEndpointRegistry.is_registered("BANK_B")

            status = provisioner.get_worker_status()
            assert "BANK_A" in status and "BANK_B" in status
            assert status["BANK_A"]["is_alive"] is True
            assert status["BANK_B"]["is_alive"] is True
            assert status["BANK_A"]["pid"] > 0
            assert status["BANK_B"]["pid"] > 0

        # After context exit, workers are cleanly terminated and unregistered
        assert not WorkerEndpointRegistry.is_registered("BANK_A")
        assert not WorkerEndpointRegistry.is_registered("BANK_B")

    def test_boundary_proof_e_consortium_specific_operator_authorization(self) -> None:
        """Targeted Proof E: Operator authorization bound to specific target consortium."""
        # 1. Non-member operator attempting to initiate training on cfi-consortium is rejected with 403
        outsider_headers = _get_operator_auth_headers(
            username="external_operator",
            bank_id="BANK_OUTSIDER",
            roles=["admin", "security_admin"],
            clearance_level=3,
        )
        resp_outsider = client.post(
            "/api/v1/training/federation/jobs",
            json={"participating_banks": ["BANK_A", "BANK_B"], "num_rounds": 1},
            headers=outsider_headers,
        )
        assert resp_outsider.status_code == 403
        assert "not an authorized member of target consortium" in resp_outsider.json()["detail"]

        # 2. Member operator with clearance < 3 attempting cross-bank training is rejected with 403
        member_low_clearance_headers = _get_operator_auth_headers(
            username="bank_a_analyst",
            bank_id="BANK_A",
            roles=["admin"],
            clearance_level=2,
        )
        resp_low = client.post(
            "/api/v1/training/federation/jobs",
            json={"participating_banks": ["BANK_A", "BANK_B"], "num_rounds": 1},
            headers=member_low_clearance_headers,
        )
        assert resp_low.status_code == 403
        assert "requires elevated consortium administrator authority" in resp_low.json()["detail"]

    def test_boundary_proof_f_consortium_scope_binding_to_execution_and_candidate(self) -> None:
        """Targeted Proof F: Immutable consortium scope binding to execution and candidate identity."""
        headers = _get_operator_auth_headers(
            username="consortium_admin",
            bank_id="BANK_A",
            roles=["admin"],
            clearance_level=3,
        )

        # 1. Mismatched consortium in execution result rejected with 500
        mismatched_result = {
            "job_id": "job_mismatch_01",
            "consortium_id": "malicious-foreign-consortium",
            "status": "COMPLETED",
            "is_verified": True,
            "metrics": {},
            "artifact_sha256": "d" * 64,
            "model_version": 2,
            "saved_version": {"version": 2, "consortium_id": "cfi-consortium"},
        }
        with mock.patch.object(OperationalFederationService, "execute_operational_federation", return_value=mismatched_result):
            resp = client.post(
                "/api/v1/training/federation/jobs",
                json={"participating_banks": ["BANK_A", "BANK_B"], "num_rounds": 1},
                headers=headers,
            )
            assert resp.status_code == 500
            assert "inconsistent consortium scope binding" in resp.json()["detail"]

        # 2. Candidate model in registry carrying conflicting consortium scope rejected with 500
        conflicting_candidate_result = {
            "job_id": "job_cand_mismatch_01",
            "consortium_id": "cfi-consortium",
            "status": "COMPLETED",
            "is_verified": True,
            "metrics": {},
            "artifact_sha256": "e" * 64,
            "model_version": 2,
            "saved_version": {"version": 2, "consortium_id": "unauthorized-consortium"},
        }
        with mock.patch.object(OperationalFederationService, "execute_operational_federation", return_value=conflicting_candidate_result):
            resp_cand = client.post(
                "/api/v1/training/federation/jobs",
                json={"participating_banks": ["BANK_A", "BANK_B"], "num_rounds": 1},
                headers=headers,
            )
            assert resp_cand.status_code == 500
            assert "violates authoritative consortium scope" in resp_cand.json()["detail"]

    def test_boundary_proof_h_no_receipt_or_candidate_on_authorization_failure(self, tmp_path: Any) -> None:
        """Targeted Proof H: No receipt or production-eligible candidate created when worker resolution fails."""
        WorkerEndpointRegistry.clear_for_testing()
        db_file = os.path.join(str(tmp_path), "test_fail_closed.db")
        registry_dir = os.path.join(str(tmp_path), "registry")
        SQLiteTrainingJobAuthority(db_target=db_file)

        svc = OperationalFederationService(
            db_url=db_file,
            registry_storage_dir=registry_dir,
            app_env="production",
        )

        # 1. Missing coordinator token in production fails closed
        with pytest.raises(TrainingProvenanceConfigurationError) as exc_coord:
            svc.execute_operational_federation(
                job_id="job_fail_closed_999",
                participating_banks=["BANK_A", "BANK_B"],
                num_rounds=1,
            )
        assert "Production training coordinator credential is not configured" in str(exc_coord.value)

        # 2. In production mode, missing worker endpoints raises fail-closed security error
        with pytest.raises(TrainingProvenanceSecurityError) as exc_missing_workers:
            svc.resolve_worker_endpoints(["BANK_A", "BANK_B"], is_prod=True)
        assert "Production bank worker endpoints are not provisioned or registered" in str(exc_missing_workers.value)

        # 3. Development execution without worker endpoints aborts fail-closed
        dev_svc = OperationalFederationService(
            db_url=db_file,
            registry_storage_dir=registry_dir,
            app_env="development",
        )
        with pytest.raises(TrainingProvenanceSecurityError) as exc_dev_abort:
            dev_svc.execute_operational_federation(
                job_id="job_fail_closed_999",
                participating_banks=["BANK_A", "BANK_B"],
                num_rounds=1,
            )
        assert "aborted" in str(exc_dev_abort.value)

        # 4. Verify no candidate model was saved in the ModelRegistry
        reg = ModelRegistry(storage_dir=registry_dir)
        assert reg.resolve_and_verify_champion("job_fail_closed_999") is None
        assert reg._load_manifest("job_fail_closed_999") == []
        assert not os.path.exists(os.path.join(reg.registry_root, "job_fail_closed_999"))


class TestFederationProvenanceFinalizationTargetedCriteria:
    """Rigorous verification of Criteria A through J for Authenticated Federation Provenance."""

    def test_criterion_a_bank_token_verification_against_independent_authoritative_source(self) -> None:
        """Criterion A: Bank token verification checks against independent authoritative configuration source."""
        WorkerEndpointRegistry.clear_for_testing()
        q_in: mp.Queue[Any] = mp.Queue()
        q_out: mp.Queue[Any] = mp.Queue()

        server_tokens = {"BANK_A": "auth_token_bank_a_canonical_123"}

        with mock.patch("app.application.services.operational_federation_service.get_settings") as mock_set:
            mock_s = mock.MagicMock()
            mock_s.app_env = "production"
            mock_s.training_bank_tokens = server_tokens
            mock_s.training_worker_token = "generic_worker_tok_999"
            mock_set.return_value = mock_s

            # 1. Bank A with wrong token fails authentication
            with pytest.raises(TrainingProvenanceSecurityError) as exc_bad_tok:
                WorkerEndpointRegistry.register_worker_endpoint(
                    bank_id="BANK_A",
                    endpoint={"inbox": q_in, "outbox": q_out},
                    pid=os.getpid(),
                    registration_token="wrong_token_456",
                    app_env="production",
                )
            assert "invalid institutional token" in str(exc_bad_tok.value)

            # 2. Bank B not present in server tokens is rejected
            with pytest.raises(TrainingProvenanceSecurityError) as exc_unreg:
                WorkerEndpointRegistry.register_worker_endpoint(
                    bank_id="BANK_B",
                    endpoint={"inbox": q_in, "outbox": q_out},
                    pid=os.getpid(),
                    registration_token="some_token_999",
                    app_env="production",
                )
            assert "no authorized institutional credential configured for this bank" in str(exc_unreg.value)

    def test_criterion_b_caller_replacement_credential_maps_cannot_authenticate_arbitrary_banks(self) -> None:
        """Criterion B: Caller-supplied replacement credential maps cannot authenticate arbitrary banks in production."""
        WorkerEndpointRegistry.clear_for_testing()
        q_in: mp.Queue[Any] = mp.Queue()
        q_out: mp.Queue[Any] = mp.Queue()

        server_tokens = {"BANK_A": "legit_server_token_111"}
        attacker_tokens = {
            "BANK_ATTACKER": "attacker_supplied_token_666",
            "BANK_A": "attacker_override_token_777",
        }

        with mock.patch("app.application.services.operational_federation_service.get_settings") as mock_set:
            mock_s = mock.MagicMock()
            mock_s.app_env = "production"
            mock_s.training_bank_tokens = server_tokens
            mock_s.training_worker_token = "generic_worker_tok_999"
            mock_set.return_value = mock_s

            # Caller passes attacker_tokens mapping to register_worker_endpoint
            with pytest.raises(TrainingProvenanceSecurityError) as exc_arb:
                WorkerEndpointRegistry.register_worker_endpoint(
                    bank_id="BANK_ATTACKER",
                    endpoint={"inbox": q_in, "outbox": q_out},
                    pid=os.getpid(),
                    registration_token="attacker_supplied_token_666",
                    app_env="production",
                    bank_tokens=attacker_tokens,
                )
            assert "no authorized institutional credential configured for this bank" in str(exc_arb.value)

            # Caller tries to override BANK_A token with their attacker map
            with pytest.raises(TrainingProvenanceSecurityError) as exc_override:
                WorkerEndpointRegistry.register_worker_endpoint(
                    bank_id="BANK_A",
                    endpoint={"inbox": q_in, "outbox": q_out},
                    pid=os.getpid(),
                    registration_token="attacker_override_token_777",
                    app_env="production",
                    bank_tokens=attacker_tokens,
                )
            assert "invalid institutional token" in str(exc_override.value)

    def test_criterion_c_batch_provisioner_cannot_bypass_institutional_credential_checks(self) -> None:
        """Criterion C: Batch provisioner cannot bypass single-worker institutional credential verification."""
        WorkerEndpointRegistry.clear_for_testing()
        q_in: mp.Queue[Any] = mp.Queue()
        q_out: mp.Queue[Any] = mp.Queue()

        server_tokens = {"BANK_A": "valid_token_a"}
        endpoints = {
            "BANK_A": {"inbox": q_in, "outbox": q_out},
            "BANK_MALICIOUS": {"inbox": q_in, "outbox": q_out},
        }

        with mock.patch("app.application.services.operational_federation_service.get_settings") as mock_set:
            mock_s = mock.MagicMock()
            mock_s.app_env = "production"
            mock_s.training_bank_tokens = server_tokens
            mock_s.training_worker_token = "generic_worker_tok_999"
            mock_set.return_value = mock_s

            with pytest.raises(TrainingProvenanceSecurityError) as exc_batch:
                WorkerEndpointRegistry.register_provisioned_workers(
                    endpoints=endpoints,
                    bank_tokens={"BANK_MALICIOUS": "self_attested_fake"},
                    app_env="production",
                )
            assert (
                "production requires authentic institutional registration token" in str(exc_batch.value)
                or "no authorized institutional credential configured" in str(exc_batch.value)
            )

    def test_criterion_d_valid_authenticated_institutional_worker_registration_continues_working(self) -> None:
        """Criterion D: Valid authenticated institutional worker registration continues working."""
        WorkerEndpointRegistry.clear_for_testing()
        q_in: mp.Queue[Any] = mp.Queue()
        q_out: mp.Queue[Any] = mp.Queue()

        server_tokens = {"BANK_A": "valid_token_a"}

        with mock.patch("app.application.services.operational_federation_service.get_settings") as mock_set:
            mock_s = mock.MagicMock()
            mock_s.app_env = "production"
            mock_s.training_bank_tokens = server_tokens
            mock_s.training_worker_token = "generic_worker_tok_999"
            mock_set.return_value = mock_s

            WorkerEndpointRegistry.register_worker_endpoint(
                bank_id="BANK_A",
                endpoint={"inbox": q_in, "outbox": q_out},
                pid=os.getpid(),
                registration_token="valid_token_a",
                app_env="production",
            )
            assert WorkerEndpointRegistry.is_registered("BANK_A")
            ep = WorkerEndpointRegistry.get_trusted_endpoint("BANK_A")
            assert ep is not None
            assert ep.bank_id == "BANK_A"
            assert ep.attestation_type == "institutional_token"
            assert ep.state == "ACTIVE"

    def test_criterion_e_signed_development_receipt_contains_exact_intended_consortium(self) -> None:
        """Criterion E: Signed development receipt contains exact intended consortium."""
        issuer = get_default_training_issuer()
        verifier = get_default_training_verifier()
        artifact_bytes = b"sample_model_weights_bytes_criterion_e"
        sha = hashlib.sha256(artifact_bytes).hexdigest()

        evidence = TrainingExecutionEvidence(
            training_run_id="run_crit_e",
            job_id="job_crit_e",
            simulation_id="sim_crit_e",
            completed_at="2026-10-18T10:00:00Z",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=500,
            observed_metrics={"loss": 0.35, "auc_roc": 0.88},
            artifact_sha256=sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
            consortium_id="cfi-consortium",
        )
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=artifact_bytes)
        assert receipt.consortium_id == "cfi-consortium"

        # Verifier with matching consortium succeeds
        ok, reason = verifier.verify_receipt(
            receipt,
            candidate_artifact_bytes=artifact_bytes,
            expected_consortium_id="cfi-consortium",
        )
        assert ok is True
        assert "verified successfully" in reason

        # Verifier with mismatched consortium fails
        mismatch_ok, mismatch_reason = verifier.verify_receipt(
            receipt,
            candidate_artifact_bytes=artifact_bytes,
            expected_consortium_id="other-consortium",
        )
        assert mismatch_ok is False
        assert "Consortium scope mismatch" in mismatch_reason

    def test_criterion_f_independent_signing_authority_receives_consortium_scope_from_authenticated_durable_job_evidence(
        self, tmp_path: Any
    ) -> None:
        """Criterion F: Independent signing authority receives consortium scope from authenticated durable job evidence."""
        db_file = os.path.join(str(tmp_path), "test_crit_f.db")
        coord_tok = "test_coord_secret_f"
        bank_tok = "test_bank_a_secret_f"
        record_source = TrustedTrainingJobRecordSource(
            coordinator_token=coord_tok,
            bank_tokens={"BANK_A": bank_tok},
            db_url=db_file,
            enforce_lifecycle=True,
            app_env="development",
        )

        artifact_bytes = b"model_f_bytes_123"
        sha = hashlib.sha256(artifact_bytes).hexdigest()

        # 1. Schedule with consortium_id
        record_source.schedule_training_job(
            job_id="job_f_1",
            training_run_id="run_f_1",
            simulation_id="sim_f_1",
            coordinator_token=coord_tok,
            permitted_workers=["BANK_A"],
            consortium_id="cfi-consortium",
        )
        record_source.start_training_job(job_id="job_f_1", worker_token=bank_tok)
        record_source.record_round_observation(
            job_id="job_f_1",
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.32,
            worker_token=bank_tok,
            bank_id="BANK_A",
            model_update_digest=sha,
        )
        record_source.record_global_aggregation(
            job_id="job_f_1",
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[sha],
            aggregated_global_model_digest=sha,
            coordinator_token=coord_tok,
        )
        record_source.record_evaluation_observation(
            job_id="job_f_1",
            metric_name="loss",
            metric_value=0.32,
            evaluated_artifact_digest=sha,
            coordinator_token=coord_tok,
        )
        completed_rec = record_source.complete_training_job(
            job_id="job_f_1",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.32},
            artifact_sha256=sha,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=coord_tok,
            enforce_lifecycle=True,
            consortium_id="cfi-consortium",
        )
        assert completed_rec.consortium_id == "cfi-consortium"

        # 2. Test isolated signing worker process receives and signs exact consortium from durable job evidence
        signing_key = ed25519.Ed25519PrivateKey.generate()
        key_raw = signing_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ).hex()

        signing_auth = IsolatedReceiptSigningAuthority(
            signing_key_hex=key_raw,
            db_url=db_file,
            key_id="test_key_f_isolated",
            app_env="development",
        )
        try:
            signed_rcpt = signing_auth.sign_completed_job_receipt(
                job_id="job_f_1",
                artifact_bytes=artifact_bytes,
                caller_identity="COORDINATOR",
            )
            # Verify signed receipt contains exact consortium claim from database record
            assert signed_rcpt.consortium_id == "cfi-consortium"
        finally:
            signing_auth.terminate()

        # 3. Verify coordinator cannot override consortium claim in signed receipt
        issuer = TrainingReceiptIssuer(
            signing_key_hex=key_raw,
            key_id="test_key_f_isolated",
            job_record_source=record_source,
            enforce_job_records=True,
            app_env="development",
        )
        ev_override = TrainingExecutionEvidence(
            training_run_id="run_f_1",
            job_id="job_f_1",
            simulation_id="sim_f_1",
            completed_at=completed_rec.completed_at,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.32},
            artifact_sha256=sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
            consortium_id="malicious_coordinator_override_consortium",
        )
        with pytest.raises(TrainingProvenanceSecurityError) as exc_override:
            issuer.issue_receipt(evidence=ev_override, model_bytes=artifact_bytes)
        assert "Consortium mismatch" in str(exc_override.value)

        # 4. In production mode, job evidence lacking authoritative consortium claim is rejected fail-closed
        record_source.schedule_training_job(
            job_id="job_f_no_consortium",
            training_run_id="run_f_2",
            simulation_id="sim_f_2",
            coordinator_token=coord_tok,
            permitted_workers=["BANK_A"],
            consortium_id=None,
        )
        record_source.start_training_job(job_id="job_f_no_consortium", worker_token=bank_tok)
        record_source.record_round_observation(
            job_id="job_f_no_consortium",
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.32,
            worker_token=bank_tok,
            bank_id="BANK_A",
            model_update_digest=sha,
        )
        record_source.record_global_aggregation(
            job_id="job_f_no_consortium",
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[sha],
            aggregated_global_model_digest=sha,
            coordinator_token=coord_tok,
        )
        record_source.complete_training_job(
            job_id="job_f_no_consortium",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.32},
            artifact_sha256=sha,
            worker_id="BANK_A",
            coordinator_token=coord_tok,
            enforce_lifecycle=True,
            consortium_id=None,
        )

        prod_issuer = TrainingReceiptIssuer(
            signing_key_hex=key_raw,
            key_id="test_key_f_isolated",
            job_record_source=record_source,
            enforce_job_records=True,
            app_env="production",
        )
        ev_no_consortium = TrainingExecutionEvidence(
            training_run_id="run_f_2",
            job_id="job_f_no_consortium",
            simulation_id="sim_f_2",
            completed_at="2026-10-18T10:00:00Z",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.32},
            artifact_sha256=sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="BANK_A",
            consortium_id=None,
        )
        with pytest.raises(TrainingProvenanceSecurityError) as exc_no_cons:
            prod_issuer.issue_receipt(evidence=ev_no_consortium, model_bytes=artifact_bytes)
        assert "lacks authoritative consortium binding" in str(exc_no_cons.value)

    def test_criterion_g_missing_modified_or_mismatched_consortium_claim_cannot_establish_production_candidate_ownership(
        self, tmp_path: Any
    ) -> None:
        """Criterion G: Missing, modified, or mismatched consortium claim cannot establish production candidate ownership."""
        reg_dir = os.path.join(str(tmp_path), "reg_crit_g")
        reg = ModelRegistry(storage_dir=reg_dir)
        issuer = get_default_training_issuer()
        artifact_bytes = b"model_g_artifact_bytes"
        sha = hashlib.sha256(artifact_bytes).hexdigest()

        # 1. Receipt omitting consortium_id rejected for consortium-bound scope
        ev_no_consortium = TrainingExecutionEvidence(
            training_run_id="run_g_1",
            job_id="job_g_1",
            simulation_id="sim_g_1",
            completed_at="2026-10-18T10:00:00Z",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.25},
            artifact_sha256=sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
            consortium_id=None,
        )
        rcpt_no_consortium = issuer.issue_receipt(evidence=ev_no_consortium, model_bytes=artifact_bytes)

        with pytest.raises(ModelAuthorizationError) as exc_omit:
            reg.save_version(
                simulation_id="sim_g_1",
                artifact_bytes=artifact_bytes,
                consortium_id="cfi-consortium",
                training_receipt=rcpt_no_consortium,
            )
        assert "Receipt omits required consortium claim" in str(exc_omit.value)

        # 2. Receipt binding mismatched consortium rejected
        ev_mismatched = TrainingExecutionEvidence(
            training_run_id="run_g_2",
            job_id="job_g_2",
            simulation_id="sim_g_2",
            completed_at="2026-10-18T10:00:00Z",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.25},
            artifact_sha256=sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
            consortium_id="other-consortium",
        )
        rcpt_mismatched = issuer.issue_receipt(evidence=ev_mismatched, model_bytes=artifact_bytes)

        with pytest.raises(ModelAuthorizationError) as exc_diff:
            reg.save_version(
                simulation_id="sim_g_2",
                artifact_bytes=artifact_bytes,
                consortium_id="cfi-consortium",
                training_receipt=rcpt_mismatched,
            )
        assert "does not match authorized scope" in str(exc_diff.value)

        # 3. Receipt with tampered claims/signature rejected
        tampered_dict = rcpt_mismatched.to_dict()
        tampered_dict["consortium_id"] = "cfi-consortium"  # Modified without updating signature
        with pytest.raises(ModelAuthorizationError) as exc_tamper:
            reg.save_version(
                simulation_id="sim_g_3",
                artifact_bytes=artifact_bytes,
                consortium_id="cfi-consortium",
                training_receipt=tampered_dict,
            )
        assert "Receipt verification failed" in str(exc_tamper.value)

    def test_criterion_h_preexisting_model_ownership_does_not_permit_saving_receipt_for_different_consortium(
        self, tmp_path: Any
    ) -> None:
        """Criterion H: Pre-existing model ownership does not permit saving receipt for different consortium."""
        reg_dir = os.path.join(str(tmp_path), "reg_crit_h")
        reg = ModelRegistry(storage_dir=reg_dir)
        issuer = get_default_training_issuer()
        artifact_bytes = b"model_h_bytes"
        sha = hashlib.sha256(artifact_bytes).hexdigest()

        # Step 1: Establish ownership under consortium_alpha
        ev_alpha = TrainingExecutionEvidence(
            training_run_id="run_h_1",
            job_id="job_h_1",
            simulation_id="sim_h_shared",
            completed_at="2026-10-18T10:00:00Z",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.25},
            artifact_sha256=sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
            consortium_id="consortium_alpha",
        )
        rcpt_alpha = issuer.issue_receipt(evidence=ev_alpha, model_bytes=artifact_bytes)
        v1 = reg.save_version(
            simulation_id="sim_h_shared",
            artifact_bytes=artifact_bytes,
            consortium_id="consortium_alpha",
            training_receipt=rcpt_alpha,
        )
        assert v1["version"] == 1
        assert v1["consortium_id"] == "consortium_alpha"

        # Step 2: Attempt to save version 2 using a receipt signed for consortium_beta
        ev_beta = TrainingExecutionEvidence(
            training_run_id="run_h_2",
            job_id="job_h_2",
            simulation_id="sim_h_shared",
            completed_at="2026-10-18T10:00:00Z",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.22},
            artifact_sha256=sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
            consortium_id="consortium_beta",
        )
        rcpt_beta = issuer.issue_receipt(evidence=ev_beta, model_bytes=artifact_bytes)

        with pytest.raises(ModelAuthorizationError) as exc_clash:
            reg.save_version(
                simulation_id="sim_h_shared",
                artifact_bytes=artifact_bytes,
                training_receipt=rcpt_beta,
            )
        assert "does not match authorized scope" in str(exc_clash.value)

    def test_criterion_i_real_operational_federation_service_multiprocess_execution_produces_verified_inactive_candidate_with_consistent_scope(
        self, tmp_path: Any
    ) -> None:
        """Criterion I: Real OperationalFederationService multi-process execution produces verified inactive candidate with consistent scope."""
        WorkerEndpointRegistry.clear_for_testing()
        db_file = os.path.join(str(tmp_path), "test_crit_i.db")
        registry_dir = os.path.join(str(tmp_path), "registry_crit_i")
        vault_root = os.path.join(str(tmp_path), "vaults_crit_i")
        SQLiteTrainingJobAuthority(db_target=db_file)

        banks = ["BANK_A", "BANK_B"]
        tokens = {b: f"token_crit_i_{b}" for b in banks}
        enc_keys = {b: Fernet.generate_key() for b in banks}
        data_paths = {}

        np.random.seed(123)
        for b_id in banks:
            X_part = np.random.randn(25, NUM_FEATURES).astype(np.float32)
            y_part = np.random.randint(0, 2, size=25).astype(np.float32)
            data_paths[b_id] = setup_bank_private_storage(
                bank_storage_dir=vault_root,
                bank_id=b_id,
                X_partition=X_part,
                y_partition=y_part,
                bank_token=tokens[b_id],
                bank_encryption_key=enc_keys[b_id],
            )

        provisioner = SingleHostBankWorkerProvisioner(
            bank_tokens=tokens,
            db_url=db_file,
            bank_data_paths=data_paths,
            bank_encryption_keys=enc_keys,
            app_env="development",
        )

        with provisioner:
            provisioner.provision_bank_workers(banks)

            svc = OperationalFederationService(
                db_url=db_file,
                registry_storage_dir=registry_dir,
                app_env="development",
            )

            result = svc.execute_operational_federation(
                job_id="job_crit_i_multi",
                participating_banks=banks,
                num_rounds=1,
                consortium_id="cfi-consortium",
            )

            assert result["status"] == "COMPLETED"
            assert result["consortium_id"] == "cfi-consortium"
            assert result["is_verified"] is True
            assert result["saved_version"]["consortium_id"] == "cfi-consortium"
            assert result["saved_version"]["status"] == "inactive"
            assert result["is_promoted_champion"] is False
            assert result["saved_version"]["is_production_eligible"] is True
            assert result["saved_version"]["provenance_type"] == "TRAINING_RECORDED"

            # Check model manifest
            reg = ModelRegistry(storage_dir=registry_dir)
            manifest = reg._load_manifest(result["simulation_id"])
            assert len(manifest) == 1
            assert manifest[0]["consortium_id"] == "cfi-consortium"
            assert manifest[0]["is_production_eligible"] is True
            assert manifest[0]["status"] == "inactive"

    def test_criterion_j_failure_to_establish_authoritative_consortium_scope_produces_no_falsely_verified_receipt_or_candidate(
        self, tmp_path: Any
    ) -> None:
        """Criterion J: Failure to establish authoritative consortium scope produces no falsely verified receipt or candidate."""
        registry_dir = os.path.join(str(tmp_path), "reg_crit_j")
        reg = ModelRegistry(storage_dir=registry_dir)
        artifact_bytes = b"model_j_bytes"

        # Attempt to save a candidate with mismatched consortium scope
        with pytest.raises(ModelAuthorizationError):
            reg.save_version(
                simulation_id="sim_crit_j_unauth",
                artifact_bytes=artifact_bytes,
                consortium_id="cfi-consortium",
                training_receipt={
                    "receipt_id": "fake_rcpt",
                    "consortium_id": "unauthorized_consortium",
                    "signature": "bad",
                },
            )

        # Verify no production-eligible candidate was created in registry
        assert reg.resolve_and_verify_champion("sim_crit_j_unauth") is None
        assert reg._load_manifest("sim_crit_j_unauth") == []


class TestImmutablePreTrainingConsortiumBinding:
    """Rigorous verification of Cases A through G for immutable pre-training consortium binding."""

    def test_case_a_scheduled_consortium_missing_completion_supplies_consortium_rejected_in_production(
        self, tmp_path: Any
    ) -> None:
        """Case A: Scheduled consortium missing; completion supplies consortium: rejected under production authority."""
        db_file = os.path.join(str(tmp_path), "case_a.db")
        auth = SQLiteTrainingJobAuthority(db_target=db_file, app_env="production")

        auth.schedule_job(
            job_id="job_case_a",
            training_run_id="run_case_a",
            simulation_id="sim_case_a",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            consortium_id=None,
        )
        auth.start_job(job_id="job_case_a")

        sha = "a" * 64
        obs = RoundObservation(
            event_id="obs_case_a",
            job_id="job_case_a",
            training_run_id="run_case_a",
            simulation_id="sim_case_a",
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=1,
            attempt_number=1,
            actual_sample_count=50,
            actual_executed_epochs=1,
            observed_loss=0.35,
            metrics={"loss": 0.35},
            model_update_digest=sha,
            execution_timestamp="2026-10-18T12:00:00Z",
        )
        auth.record_observation(obs)
        agg = GlobalAggregationEvent(
            event_id="agg_case_a",
            job_id="job_case_a",
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=("BANK_A",),
            accepted_local_update_digests=(sha,),
            aggregation_configuration={"round": 1},
            aggregated_global_model_digest=sha,
            timestamp="2026-10-18T12:00:05Z",
        )
        auth.record_global_aggregation(agg)

        # Attempt to complete supplying a consortium when scheduled lacked one
        with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
            auth.complete_job(
                job_id="job_case_a",
                executed_rounds=1,
                executed_epochs=1,
                sample_count=50,
                observed_metrics={"loss": 0.35},
                artifact_sha256=sha,
                worker_id="FEDERATED_COORDINATOR",
                enforce_lifecycle=True,
                consortium_id="cfi-consortium",
            )
        assert (
            "authoritative scheduled job lacks a valid consortium binding" in str(exc_info.value)
            or "Retroactive consortium binding is rejected" in str(exc_info.value)
        )

    def test_case_b_scheduled_consortium_exists_completion_supplies_different_consortium_rejected(
        self, tmp_path: Any
    ) -> None:
        """Case B: Scheduled consortium exists; completion supplies different consortium: rejected."""
        db_file = os.path.join(str(tmp_path), "case_b.db")
        auth = SQLiteTrainingJobAuthority(db_target=db_file, app_env="development")
        sha = "b" * 64

        auth.schedule_job(
            job_id="job_case_b",
            training_run_id="run_case_b",
            simulation_id="sim_case_b",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            consortium_id="consortium_authorized",
        )
        auth.start_job(job_id="job_case_b")
        obs = RoundObservation(
            event_id="obs_case_b",
            job_id="job_case_b",
            training_run_id="run_case_b",
            simulation_id="sim_case_b",
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=1,
            attempt_number=1,
            actual_sample_count=50,
            actual_executed_epochs=1,
            observed_loss=0.35,
            metrics={"loss": 0.35},
            model_update_digest=sha,
            execution_timestamp="2026-10-18T12:00:00Z",
        )
        auth.record_observation(obs)
        agg = GlobalAggregationEvent(
            event_id="agg_case_b",
            job_id="job_case_b",
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=("BANK_A",),
            accepted_local_update_digests=(sha,),
            aggregation_configuration={"round": 1},
            aggregated_global_model_digest=sha,
            timestamp="2026-10-18T12:00:05Z",
        )
        auth.record_global_aggregation(agg)

        with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
            auth.complete_job(
                job_id="job_case_b",
                executed_rounds=1,
                executed_epochs=1,
                sample_count=50,
                observed_metrics={"loss": 0.35},
                artifact_sha256=sha,
                worker_id="FEDERATED_COORDINATOR",
                consortium_id="consortium_foreign",
            )
        assert "Consortium ID mismatch" in str(exc_info.value)

    def test_case_c_scheduled_consortium_exists_completion_omits_consortium_uses_authorized_scheduled_identity(
        self, tmp_path: Any
    ) -> None:
        """Case C: Scheduled consortium exists; completion omits consortium: uses already-authorized scheduled identity."""
        db_file = os.path.join(str(tmp_path), "case_c.db")
        auth = SQLiteTrainingJobAuthority(db_target=db_file, app_env="development")
        sha = "c" * 64

        auth.schedule_job(
            job_id="job_case_c",
            training_run_id="run_case_c",
            simulation_id="sim_case_c",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            consortium_id="consortium_authorized_pre_training",
        )
        auth.start_job(job_id="job_case_c")
        obs = RoundObservation(
            event_id="obs_case_c",
            job_id="job_case_c",
            training_run_id="run_case_c",
            simulation_id="sim_case_c",
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=1,
            attempt_number=1,
            actual_sample_count=50,
            actual_executed_epochs=1,
            observed_loss=0.35,
            metrics={"loss": 0.35},
            model_update_digest=sha,
            execution_timestamp="2026-10-18T12:00:00Z",
        )
        auth.record_observation(obs)
        agg = GlobalAggregationEvent(
            event_id="agg_case_c",
            job_id="job_case_c",
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=("BANK_A",),
            accepted_local_update_digests=(sha,),
            aggregation_configuration={"round": 1},
            aggregated_global_model_digest=sha,
            timestamp="2026-10-18T12:00:05Z",
        )
        auth.record_global_aggregation(agg)

        # Complete without passing consortium_id (consortium_id=None)
        rec = auth.complete_job(
            job_id="job_case_c",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=50,
            observed_metrics={"loss": 0.35},
            artifact_sha256=sha,
            worker_id="FEDERATED_COORDINATOR",
            consortium_id=None,
        )
        assert rec.consortium_id == "consortium_authorized_pre_training"

    def test_case_d_scheduled_and_supplied_consortium_match_legitimate_completion_succeeds(
        self, tmp_path: Any
    ) -> None:
        """Case D: Scheduled and supplied consortium match: legitimate completion succeeds."""
        db_file = os.path.join(str(tmp_path), "case_d.db")
        auth = SQLiteTrainingJobAuthority(db_target=db_file, app_env="development")
        sha = "d" * 64

        auth.schedule_job(
            job_id="job_case_d",
            training_run_id="run_case_d",
            simulation_id="sim_case_d",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            consortium_id="cfi-consortium",
        )
        auth.start_job(job_id="job_case_d")
        obs = RoundObservation(
            event_id="obs_case_d",
            job_id="job_case_d",
            training_run_id="run_case_d",
            simulation_id="sim_case_d",
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=1,
            attempt_number=1,
            actual_sample_count=50,
            actual_executed_epochs=1,
            observed_loss=0.35,
            metrics={"loss": 0.35},
            model_update_digest=sha,
            execution_timestamp="2026-10-18T12:00:00Z",
        )
        auth.record_observation(obs)
        agg = GlobalAggregationEvent(
            event_id="agg_case_d",
            job_id="job_case_d",
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=("BANK_A",),
            accepted_local_update_digests=(sha,),
            aggregation_configuration={"round": 1},
            aggregated_global_model_digest=sha,
            timestamp="2026-10-18T12:00:05Z",
        )
        auth.record_global_aggregation(agg)

        rec = auth.complete_job(
            job_id="job_case_d",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=50,
            observed_metrics={"loss": 0.35},
            artifact_sha256=sha,
            worker_id="FEDERATED_COORDINATOR",
            consortium_id="cfi-consortium",
        )
        assert rec.consortium_id == "cfi-consortium"
        assert rec.status == "COMPLETED"

    def test_case_e_existing_completed_record_cannot_be_relabeled_under_another_consortium(
        self, tmp_path: Any
    ) -> None:
        """Case E: Existing completed record cannot be relabeled under another consortium."""
        db_file = os.path.join(str(tmp_path), "case_e.db")
        auth = SQLiteTrainingJobAuthority(db_target=db_file, app_env="development")
        sha = "e" * 64

        auth.schedule_job(
            job_id="job_case_e",
            training_run_id="run_case_e",
            simulation_id="sim_case_e",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            consortium_id="consortium_initial",
        )
        auth.start_job(job_id="job_case_e")
        obs = RoundObservation(
            event_id="obs_case_e",
            job_id="job_case_e",
            training_run_id="run_case_e",
            simulation_id="sim_case_e",
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=1,
            attempt_number=1,
            actual_sample_count=50,
            actual_executed_epochs=1,
            observed_loss=0.35,
            metrics={"loss": 0.35},
            model_update_digest=sha,
            execution_timestamp="2026-10-18T12:00:00Z",
        )
        auth.record_observation(obs)
        agg = GlobalAggregationEvent(
            event_id="agg_case_e",
            job_id="job_case_e",
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=("BANK_A",),
            accepted_local_update_digests=(sha,),
            aggregation_configuration={"round": 1},
            aggregated_global_model_digest=sha,
            timestamp="2026-10-18T12:00:05Z",
        )
        auth.record_global_aggregation(agg)

        rec = auth.complete_job(
            job_id="job_case_e",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=50,
            observed_metrics={"loss": 0.35},
            artifact_sha256=sha,
            worker_id="FEDERATED_COORDINATOR",
            consortium_id="consortium_initial",
        )
        assert rec.consortium_id == "consortium_initial"

        # Re-attempting completion with different consortium fails closed
        with pytest.raises(TrainingProvenanceSecurityError):
            auth.complete_job(
                job_id="job_case_e",
                executed_rounds=1,
                executed_epochs=1,
                sample_count=50,
                observed_metrics={"loss": 0.35},
                artifact_sha256=sha,
                worker_id="FEDERATED_COORDINATOR",
                consortium_id="consortium_relabel_attempt",
            )

    def test_case_f_repeated_scheduling_with_different_consortium_remains_rejected(
        self, tmp_path: Any
    ) -> None:
        """Case F: Repeated scheduling with a different consortium remains rejected."""
        db_file = os.path.join(str(tmp_path), "case_f.db")
        auth = SQLiteTrainingJobAuthority(db_target=db_file, app_env="development")

        auth.schedule_job(
            job_id="job_case_f",
            training_run_id="run_case_f",
            simulation_id="sim_case_f",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            consortium_id="consortium_original",
        )

        with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
            auth.schedule_job(
                job_id="job_case_f",
                training_run_id="run_case_f",
                simulation_id="sim_case_f",
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                expected_rounds=1,
                expected_epochs=1,
                input_dim=NUM_FEATURES,
                feature_names=list(FEATURE_NAMES),
                consortium_id="consortium_different",
            )
        assert "Conflicting training job scheduling" in str(exc_info.value)

    def test_case_g_failed_retroactive_binding_produces_no_signed_receipt_or_candidate(
        self, tmp_path: Any
    ) -> None:
        """Case G: Failed retroactive binding cannot produce signed production receipt or production-eligible candidate."""
        db_file = os.path.join(str(tmp_path), "case_g.db")
        registry_dir = os.path.join(str(tmp_path), "registry_case_g")
        model_bytes = b"model_bytes_case_g_payload"
        sha = hashlib.sha256(model_bytes).hexdigest()
        auth = SQLiteTrainingJobAuthority(db_target=db_file, app_env="development")

        # Schedule job WITHOUT consortium in development
        auth.schedule_job(
            job_id="job_case_g",
            training_run_id="run_case_g",
            simulation_id="sim_case_g",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            consortium_id=None,
        )
        auth.start_job(job_id="job_case_g")
        obs = RoundObservation(
            event_id="obs_case_g",
            job_id="job_case_g",
            training_run_id="run_case_g",
            simulation_id="sim_case_g",
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=1,
            attempt_number=1,
            actual_sample_count=50,
            actual_executed_epochs=1,
            observed_loss=0.35,
            metrics={"loss": 0.35},
            model_update_digest=sha,
            execution_timestamp="2026-10-18T12:00:00Z",
        )
        auth.record_observation(obs)
        agg = GlobalAggregationEvent(
            event_id="agg_case_g",
            job_id="job_case_g",
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=("BANK_A",),
            accepted_local_update_digests=(sha,),
            aggregation_configuration={"round": 1},
            aggregated_global_model_digest=sha,
            timestamp="2026-10-18T12:00:05Z",
        )
        auth.record_global_aggregation(agg)

        # 1. Attempting retroactive assignment at completion is rejected
        with pytest.raises(TrainingProvenanceSecurityError) as exc_retro:
            auth.complete_job(
                job_id="job_case_g",
                executed_rounds=1,
                executed_epochs=1,
                sample_count=50,
                observed_metrics={"loss": 0.35},
                artifact_sha256=sha,
                worker_id="FEDERATED_COORDINATOR",
                consortium_id="retroactive_consortium",
            )
        assert "Retroactive consortium binding is rejected" in str(exc_retro.value)

        # 2. Complete with None (unbound legacy completion in development)
        completed_rec = auth.complete_job(
            job_id="job_case_g",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=50,
            observed_metrics={"loss": 0.35},
            artifact_sha256=sha,
            worker_id="FEDERATED_COORDINATOR",
            consortium_id=None,
        )
        assert completed_rec.consortium_id is None

        # 3. Independent production receipt issuer refuses to sign receipt without authoritative consortium
        signing_key = ed25519.Ed25519PrivateKey.generate()
        key_raw = signing_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ).hex()
        record_source = TrustedTrainingJobRecordSource(
            db_url=db_file,
            enforce_lifecycle=True,
            app_env="development",
        )
        issuer = TrainingReceiptIssuer(
            signing_key_hex=key_raw,
            key_id="test_key_case_g",
            job_record_source=record_source,
            enforce_job_records=True,
            app_env="production",
        )
        ev_retro = TrainingExecutionEvidence(
            training_run_id="run_case_g",
            job_id="job_case_g",
            simulation_id="sim_case_g",
            completed_at=completed_rec.completed_at,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=50,
            observed_metrics={"loss": 0.35},
            artifact_sha256=sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
            consortium_id="retroactive_consortium",
        )
        with pytest.raises(TrainingProvenanceSecurityError) as exc_sign:
            issuer.issue_receipt(evidence=ev_retro, model_bytes=model_bytes)
        assert "lacks authoritative consortium binding" in str(exc_sign.value)

        # 4. ModelRegistry rejects any candidate registration attempt
        reg = ModelRegistry(storage_dir=registry_dir)
        with pytest.raises(ModelAuthorizationError):
            reg.save_version(
                simulation_id="sim_case_g",
                artifact_bytes=model_bytes,
                consortium_id="retroactive_consortium",
                training_receipt=None,
            )
        assert reg.resolve_and_verify_champion("sim_case_g") is None
        assert reg._load_manifest("sim_case_g") == []

    def test_continuous_operational_federation_to_champion_serving_pipeline(
        self, clean_test_environment: str, tmp_path: Any
    ) -> None:
        """Section 01-I Final Integration: Continuous E2E execution from genuine operational federation to serving and feedback."""
        import app.presentation.routers.predict as predict_router
        import app.presentation.routers.realtime_inference as realtime_router
        from app.application.services.model_registry import (
            ModelEvaluationEngine,
            generate_signoff_signature,
            validate_production_admission,
        )
        from app.domain.consortium_governance import (
            Consortium,
            ConsortiumMember,
            ConsortiumStatus,
            MemberRole,
        )
        from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore

        registry_dir = str(tmp_path / "model_registry")
        db_file = str(tmp_path / "authority.db")
        vault_root = str(tmp_path / "bank_vaults")

        # Configure environment so registry points to isolated test storage
        old_storage = os.environ.get("CFI_STORAGE_DIR")
        os.environ["CFI_STORAGE_DIR"] = registry_dir

        try:
            # 1. Prepare private bank partitions and spawn real worker subprocesses
            val_X = np.random.randn(20, NUM_FEATURES).astype(np.float32)
            val_y = np.random.randint(0, 2, size=20).astype(np.float32)

            bank_data_paths = {}
            bank_enc_keys = {b: Fernet.generate_key() for b in ("BANK_A", "BANK_B")}
            bank_tokens = {b: f"test_worker_token_{b}" for b in ("BANK_A", "BANK_B")}

            np.random.seed(42)
            for b_id, n_samples in (("BANK_A", 40), ("BANK_B", 40)):
                X_part = np.random.randn(n_samples, NUM_FEATURES).astype(np.float32)
                y_part = np.random.randint(0, 2, size=n_samples).astype(np.float32)
                p = setup_bank_private_storage(
                    bank_storage_dir=vault_root,
                    bank_id=b_id,
                    X_partition=X_part,
                    y_partition=y_part,
                    bank_token=bank_tokens[b_id],
                    bank_encryption_key=bank_enc_keys[b_id],
                )
                bank_data_paths[b_id] = p

            procs, endpoints = spawn_bank_workers(
                participating_banks=["BANK_A", "BANK_B"],
                bank_tokens=bank_tokens,
                db_url=db_file,
                bank_data_paths=bank_data_paths,
                bank_encryption_keys=bank_enc_keys,
                app_env="development",
            )

            try:
                pids = {b: proc.pid or 0 for b, proc in procs.items()}
                WorkerEndpointRegistry.register_provisioned_workers(
                    endpoints=endpoints,
                    pids=pids,
                    app_env="development",
                )

                # Initialize active consortium in DurableConsortiumStore for institutional authorization
                consortium_store = DurableConsortiumStore.get_instance()
                old_consortium = consortium_store.get_consortium("cfi-consortium")
                def _init_consortium(consortia: dict[str, Any], proposals: dict[str, Any]) -> None:
                    consortia["cfi-consortium"] = Consortium(
                        consortium_id="cfi-consortium",
                        name="CFI Operational Consortium",
                        quorum_ratio=0.5,
                        min_members_n=2,
                        max_epsilon=5.0,
                        status=ConsortiumStatus.ACTIVE,
                        members={
                            "bank_a": ConsortiumMember(bank_id="bank_a", role=MemberRole.FOUNDER, voting_power=1.0),
                            "bank_b": ConsortiumMember(bank_id="bank_b", role=MemberRole.FULL_MEMBER, voting_power=1.0),
                            "bank_c": ConsortiumMember(bank_id="bank_c", role=MemberRole.FULL_MEMBER, voting_power=1.0),
                        },
                    )
                consortium_store.atomic_mutate(_init_consortium)

                svc = OperationalFederationService(
                    db_url=db_file,
                    holdout_val_data=(val_X, val_y),
                    registry_storage_dir=registry_dir,
                    app_env="development",
                )

                # 2. Execute operational federation via canonical service adapter
                target_job_id = "job_continuous_e2e_01"
                target_sim_id = "sim_continuous_e2e_01"
                exec_result = svc.execute_operational_federation(
                    job_id=target_job_id,
                    simulation_id=target_sim_id,
                    participating_banks=["BANK_A", "BANK_B"],
                    num_rounds=1,
                    consortium_id="cfi-consortium",
                )

                # 3. Verify persisted completed-job evidence in authority DB and signed receipt
                assert exec_result["status"] == "COMPLETED"
                assert exec_result["is_verified"] is True
                assert exec_result["consortium_id"] == "cfi-consortium"
                artifact_sha = exec_result["artifact_sha256"]
                assert len(artifact_sha) == 64
                model_ver = exec_result["model_version"]
                assert model_ver is not None

                job_authority = SQLiteTrainingJobAuthority(db_file)
                completed_rec = job_authority.get_completed_job_by_id(target_job_id)
                assert completed_rec is not None
                assert completed_rec.status == "COMPLETED"
                assert completed_rec.simulation_id == target_sim_id
                assert completed_rec.artifact_sha256 == artifact_sha
                assert completed_rec.executed_rounds >= 1

                receipt = exec_result.get("receipt")
                assert receipt is not None
                receipt_dict = receipt.to_dict() if hasattr(receipt, "to_dict") else receipt
                assert receipt_dict.get("job_id") == target_job_id
                assert receipt_dict.get("simulation_id") == target_sim_id
                assert receipt_dict.get("model_artifact_sha256") == artifact_sha

                # 4 & 5. Verify exact artifact SHA-256 continuity and inactive status before approval
                reg = ModelRegistry(storage_dir=registry_dir)
                candidate_entry = reg.get_version_metadata(target_sim_id, model_ver)
                assert candidate_entry is not None
                assert candidate_entry["status"] == "inactive"
                assert candidate_entry["provenance_type"] == "TRAINING_RECORDED"
                assert candidate_entry["consortium_id"] == "cfi-consortium"
                assert candidate_entry["sha256"] == artifact_sha
                assert candidate_entry["is_active"] is False
                assert exec_result["is_promoted_champion"] is False

                # 6. Create two distinct institutional approver identities using AuthenticationService
                auth_svc = AuthenticationService.get_instance()

                # ML Engineer approver
                ml_user = auth_svc.get_user("ml_engineer_signoff")
                if not ml_user:
                    ml_user = auth_svc.register_user(
                        username="ml_engineer_signoff",
                        plain_password="SafePassword123!",
                        bank_id="bank_a",
                        roles=["ml_engineer"],
                        clearance_level=2,
                    )
                ml_user.roles = ["ml_engineer"]
                ml_user.bank_id = "bank_a"
                ml_user.clearance_level = 2
                ml_user.is_active = True
                ml_user.status = "active"
                auth_svc.identity_store.save_user(ml_user)

                # Compliance approver
                comp_user = auth_svc.get_user("compliance_signoff")
                if not comp_user:
                    comp_user = auth_svc.register_user(
                        username="compliance_signoff",
                        plain_password="SafePassword123!",
                        bank_id="bank_b",
                        roles=["compliance"],
                        clearance_level=2,
                    )
                comp_user.roles = ["compliance"]
                comp_user.bank_id = "bank_b"
                comp_user.clearance_level = 2
                comp_user.is_active = True
                comp_user.status = "active"
                auth_svc.identity_store.save_user(comp_user)

                # Lead operator for promotion
                lead_user = auth_svc.get_user("lead_operator")
                if not lead_user:
                    lead_user = auth_svc.register_user(
                        username="lead_operator",
                        plain_password="SafePassword123!",
                        bank_id="bank_a",
                        roles=["ml_lead", "admin"],
                        clearance_level=3,
                    )
                lead_user.roles = ["ml_lead", "admin"]
                lead_user.bank_id = "bank_a"
                lead_user.clearance_level = 3
                lead_user.is_active = True
                lead_user.status = "active"
                auth_svc.identity_store.save_user(lead_user)
                lead_token = auth_svc.create_token_bundle(lead_user).access_token

                # 7. Perform genuine Four-Eyes sign-offs through production implementation
                sig_ml = generate_signoff_signature(
                    simulation_id=target_sim_id,
                    version=model_ver,
                    artifact_sha256=artifact_sha,
                    role="ml_engineer",
                    user=ml_user.username,
                )
                reg.sign_off(
                    simulation_id=target_sim_id,
                    version=model_ver,
                    role="ml_engineer",
                    user=ml_user.username,
                    signature=sig_ml,
                )

                sig_comp = generate_signoff_signature(
                    simulation_id=target_sim_id,
                    version=model_ver,
                    artifact_sha256=artifact_sha,
                    role="compliance",
                    user=comp_user.username,
                )
                reg.sign_off(
                    simulation_id=target_sim_id,
                    version=model_ver,
                    role="compliance",
                    user=comp_user.username,
                    signature=sig_comp,
                )

                entry_approved = reg.get_version_metadata(target_sim_id, model_ver)
                assert entry_approved is not None
                assert entry_approved["status"] == "approved"
                assert entry_approved["is_production_eligible"] is True

                # 8. Promote candidate to champion via governance contract
                promoted = reg.promote_version(
                    simulation_id=target_sim_id,
                    version=model_ver,
                    target_status="champion",
                    auth_token=lead_token,
                    actor=lead_user.username,
                )
                assert promoted["status"] == "champion"
                assert promoted["is_active"] is True
                assert promoted["sha256"] == artifact_sha

                # 9. Retrieve promoted artifact through secure champion resolution
                champion_resolved = reg.resolve_and_verify_champion(simulation_id=target_sim_id)
                assert champion_resolved is not None
                champ_path, champ_bytes, champ_meta = champion_resolved
                assert champ_meta["version"] == model_ver
                assert champ_meta["sha256"] == artifact_sha
                assert hashlib.sha256(champ_bytes).hexdigest() == artifact_sha

                # Synchronize serving singletons to isolated test registry
                predict_router._registry = reg
                predict_router._eval_engine = ModelEvaluationEngine(reg)
                predict_router.clear_all_serving_caches()
                realtime_router.reset_model_cache()

                # 10 & 11. Execute authenticated HTTP inference using the actual promoted model
                bank_a_headers = _get_operator_auth_headers(
                    username="bank_a_operator",
                    bank_id="bank_a",
                    roles=["analyst"],
                    clearance_level=2,
                )

                predict_req = {
                    "transaction_id": "txn_continuous_e2e_001",
                    "simulation_id": target_sim_id,
                    "bank_id": "bank_a",
                    "transaction_amount": 150.0,
                    "merchant_category": "electronics",
                    "country_code": "US",
                    "device_type": "mobile_app",
                    "velocity": 2.5,
                    "hour_of_day": 14,
                    "merchant_risk_score": 0.15,
                    "customer_history_score": 0.88,
                    "chargeback_count": 0,
                    "account_age_days": 420,
                }
                resp_predict = client.post("/api/v1/predict", json=predict_req, headers=bank_a_headers)
                assert resp_predict.status_code == 200, resp_predict.text
                pred_json = resp_predict.json()
                assert pred_json["transaction_id"] == "txn_continuous_e2e_001"
                assert 0.0 <= pred_json["fraud_probability"] <= 1.0
                assert 0.0 <= pred_json["risk_score"] <= 1000.0
                assert pred_json["policy_action"] in ("ALLOW", "FLAG", "BLOCK", "REVIEW")

                # Test /api/v1/inference/score endpoint against exact champion
                score_req = {
                    "transaction_id": "txn_realtime_score_002",
                    "simulation_id": target_sim_id,
                    "bank_id": "bank_a",
                    "amount": 250.0,
                    "merchant_category": "grocery",
                    "velocity_1h": 2,
                }
                resp_score = client.post("/api/v1/inference/score", json=score_req, headers=bank_a_headers)
                assert resp_score.status_code == 200, resp_score.text
                score_json = resp_score.json()
                assert score_json["transaction_id"] == "txn_realtime_score_002"
                assert score_json["evaluated_by"] == "ML_MODEL"
                assert 0.0 <= score_json["risk_score"] <= 1.0
                assert "ML Model" in score_json["explanation"]

                # 12 & 13. Prediction evidence contract & authorized ground-truth feedback
                rec_before = predict_router._eval_engine.get_prediction_record(
                    simulation_id=target_sim_id,
                    transaction_id="txn_continuous_e2e_001",
                    bank_id="bank_a",
                )
                assert rec_before is not None
                assert rec_before["transaction_id"] == "txn_continuous_e2e_001"
                assert rec_before["champion_version"] == model_ver
                assert rec_before["bank_id"] == "bank_a"
                assert rec_before["champion_prob"] == pytest.approx(pred_json["fraud_probability"], rel=1e-5)
                assert rec_before.get("actual_label") is None

                feedback_req = {
                    "transaction_id": "txn_continuous_e2e_001",
                    "actual_label": 1,
                    "simulation_id": target_sim_id,
                }
                resp_fb = client.post("/api/v1/predict/feedback", json=feedback_req, headers=bank_a_headers)
                assert resp_fb.status_code == 200, resp_fb.text
                fb_json = resp_fb.json()
                assert fb_json["status"] == "success"
                assert "metrics" in fb_json

                rec_after = predict_router._eval_engine.get_prediction_record(
                    simulation_id=target_sim_id,
                    transaction_id="txn_continuous_e2e_001",
                    bank_id="bank_a",
                )
                assert rec_after is not None
                assert rec_after["actual_label"] == 1

                # Essential Negative Assertion 1: UNVERIFIED simulation artifacts cannot be promoted through production gate
                unverified_candidate = {
                    "version": 99,
                    "status": "inactive",
                    "provenance_type": "UNVERIFIED",
                    "is_production_eligible": False,
                    "sha256": "c" * 64,
                }
                with mock.patch("app.config.get_settings") as mock_set:
                    mock_set.return_value = mock.MagicMock(app_env="production")
                    with pytest.raises(ModelIntegrityError) as exc_unverified:
                        validate_production_admission(unverified_candidate, context="promote_version")
                    assert "ineligible" in str(exc_unverified.value).lower() or "unverified" in str(exc_unverified.value).lower()

                # Essential Negative Assertion 2: Foreign bank cannot execute the private champion unless explicitly authorized
                foreign_headers = _get_operator_auth_headers(
                    username="foreign_bank_analyst",
                    bank_id="bank_foreign_unauthorized",
                    roles=["analyst"],
                    clearance_level=2,
                )
                predict_req_foreign = {**predict_req, "bank_id": "bank_foreign_unauthorized"}
                resp_foreign = client.post("/api/v1/predict", json=predict_req_foreign, headers=foreign_headers)
                assert resp_foreign.status_code == 403
                assert "not an active member" in resp_foreign.json()["detail"].lower()

                # Essential Negative Assertion 3: Unauthorized feedback cannot modify recorded transaction label
                bank_b_headers = _get_operator_auth_headers(
                    username="bank_b_analyst",
                    bank_id="bank_b",
                    roles=["analyst"],
                    clearance_level=2,
                )
                tamper_fb_req = {
                    "transaction_id": "txn_continuous_e2e_001",
                    "actual_label": 0,
                    "simulation_id": target_sim_id,
                }
                resp_tamper = client.post("/api/v1/predict/feedback", json=tamper_fb_req, headers=bank_b_headers)
                assert resp_tamper.status_code == 403
                assert "cross-tenant feedback rejected" in resp_tamper.json()["detail"].lower()

                # Verify label remained 1.0
                rec_final = predict_router._eval_engine.get_prediction_record(
                    simulation_id=target_sim_id,
                    transaction_id="txn_continuous_e2e_001",
                    bank_id="bank_a",
                )
                assert rec_final is not None
                assert rec_final["actual_label"] == 1.0

            finally:
                if old_consortium is not None:
                    def _restore_consortium(consortia: dict[str, Any], proposals: dict[str, Any]) -> None:
                        consortia["cfi-consortium"] = old_consortium
                    consortium_store.atomic_mutate(_restore_consortium)
                for proc in procs.values():
                    try:
                        proc.terminate()
                        proc.join(timeout=2.0)
                    except Exception:
                        pass
        finally:
            if old_storage is not None:
                os.environ["CFI_STORAGE_DIR"] = old_storage
            else:
                os.environ.pop("CFI_STORAGE_DIR", None)





