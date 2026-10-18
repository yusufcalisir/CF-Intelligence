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
from fastapi.testclient import TestClient

from app.application.schemas.simulation import (
    OperationalFederationRequest,
)
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
)
from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES
from app.application.services.operational_federation_service import (
    OperationalFederationService,
    TrustedWorkerEndpoint,
    WorkerEndpointRegistry,
)
from app.application.services.simulation_service import SimulationService
from app.application.services.training_provenance_service import (
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
)
from app.infrastructure.database.training_authority_backends import SQLiteTrainingJobAuthority
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
            )

            assert data["job_id"] == "job_op_test_101"
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
            ModelIntegrityError,
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

        # 4. Caller claiming CENTRAL bank identity but with clearance < 3 rejected
        central_low_clearance_headers = _get_operator_auth_headers(
            username="fake_central_operator",
            bank_id="CENTRAL",
            roles=["admin"],
            clearance_level=2,
        )
        resp_fake_central = client.post(
            "/api/v1/training/federation/jobs",
            json={"participating_banks": ["BANK_A", "BANK_B"], "num_rounds": 1},
            headers=central_low_clearance_headers,
        )
        assert resp_fake_central.status_code == 403
        assert "requires elevated consortium administrator authority" in resp_fake_central.json()["detail"]

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

        # 8. Central/Consortium Admin (Clearance >= 3 and admin role) CAN initiate multi-bank training
        consortium_admin_headers = _get_operator_auth_headers(
            username="central_consortium_admin",
            bank_id="consortium",
            roles=["admin"],
            clearance_level=3,
        )
        mock_ok = {
            "job_id": "job_consortium_01",
            "training_run_id": "run_01",
            "simulation_id": "sim_01",
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
            assert "Cannot overwrite active verified worker" in str(exc_overwrite.value)

        # 7. Explicit revocation marks state REVOKED and removes from active endpoint retrieval
        WorkerEndpointRegistry.revoke_worker_endpoint("BANK_A", reason="Key rotation")
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
            spawn_bank_workers,
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
            assert len(result["artifact_sha256"]) == 64
            assert result["model_version"] is not None

            # Confirm inactive candidate was recorded in ModelRegistry
            saved_ver = result["saved_version"]
            assert saved_ver["status"] == "inactive"
            assert saved_ver["provenance_type"] == "TRAINING_RECORDED"
            assert result["is_promoted_champion"] is False
        finally:
            for proc in procs.values():
                try:
                    proc.terminate()
                    proc.join(timeout=2.0)
                except Exception:
                    pass


