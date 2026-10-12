"""Cryptographic Receipt Issuance and Authority Isolation Suite (CR-01 to CR-10).

Verifies the cryptographic trust boundary separating coordinator, issuer, verifier, and registry:
- CR-01: Coordinator attempts to access the signing private key -> coordinator has no access.
- CR-02: Coordinator tries signing_key_hex in production -> rejected.
- CR-03: Coordinator tries to inject an issuer with a rogue key -> rejected by registry.
- CR-04: Issuer constructed with enforce_job_records=False in production -> rejected.
- CR-05: Arbitrary evidence object submitted without trusted completion -> rejected.
- CR-06: Validly signed receipt from unapproved key submitted to Registry -> rejected.
- CR-07: Signing authority unavailable -> fails closed.
- CR-08: Issuer and Registry trust roots disagree -> rejected.
- CR-09: Public-key-only verifier process attempts to sign -> cannot sign (no private key).
- CR-10: Legitimate completed job obtains receipt from independent issuer, verified by Registry.
"""

from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import Generator

import pytest
import torch

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    IsolatedReceiptSigningAuthority,
    _generate_ed25519_keypair_hex,
    provision_isolated_issuer_vault,
    spawn_isolated_coordinator_process,
)
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
    TrustedTrainingJobRecordSource,
    get_default_training_verifier,
)
from app.config import get_settings

COORD_SECRET = "coord_secret_cr_attacks_production_991"
BANK_A_SECRET = "bank_a_secret_cr_attacks_alpha_112"


@pytest.fixture(scope="module")
def real_pg_instance() -> Generator[str, None, None]:
    """Provide embedded real PostgreSQL 18.1 instance."""
    try:
        import pg0
    except ImportError:
        pytest.skip("pg0 is not installed (PostgreSQL 18.1 embedded authority unavailable)")
    try:
        import psycopg  # noqa: F401
    except ImportError:
        pytest.skip("psycopg is not installed (PostgreSQL driver unavailable)")

    pg = pg0.Pg0()
    pg.start()
    try:
        assert pg.uri is not None
        yield pg.uri
    finally:
        pg.stop()


class TestCryptographicReceiptIsolation:
    """Security verification of independently authorized Ed25519 receipt issuance."""

    def test_cr_01_coordinator_cannot_access_signing_private_key(
        self, real_pg_instance: str
    ) -> None:
        """CR-01: Coordinator manager domain holds no signing private key or private key attributes."""
        with pytest.raises(TrainingProvenanceSecurityError, match="Direct instantiation or execution inside an unrestricted process is prohibited"):
            DistributedFederatedTrainingManager(
                coordinator_token=COORD_SECRET,
                db_url=real_pg_instance,
                app_env="production",
            )
        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="development",
        )
        assert not hasattr(manager, "signing_key_hex")
        assert not hasattr(manager, "_private_key")
        assert not hasattr(manager.authority_source, "_private_key")

    def test_cr_02_coordinator_rejects_signing_key_hex_in_production(
        self, real_pg_instance: str
    ) -> None:
        """CR-02: Supplying signing_key_hex to coordinator execute_distributed_federation in production is rejected."""
        priv_hex, _ = _generate_ed25519_keypair_hex()
        proc, client = spawn_isolated_coordinator_process(app_env="production")
        try:
            with pytest.raises(TrainingProvenanceSecurityError, match=r"Production coordinator cannot receive.*signing private key"):
                client.execute_federation(
                    job_id="job_cr02",
                    training_run_id="run_cr02",
                    simulation_id="sim_cr02",
                    holdout_val_data=(None, None),  # type: ignore[arg-type]
                    participating_banks=["BANK_A"],
                    worker_endpoints={"BANK_A": {"inbox": None, "outbox": None}},
                    num_rounds=1,
                    expected_unique_samples=100,
                    registry_storage_dir=None,
                    coordinator_token=COORD_SECRET,
                    db_url=real_pg_instance,
                    comp_service_endpoint=("127.0.0.1", 1234),
                    signing_authority=None,
                    signing_key_hex=priv_hex,
                )
        finally:
            client.terminate()

    def test_cr_03_rogue_signing_key_rejected_by_registry(
        self, real_pg_instance: str, tmp_path
    ) -> None:
        """CR-03: Receipt signed with an unapproved rogue key is rejected during ModelRegistry candidate admission."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET},
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_cr03_{uuid.uuid4().hex[:8]}"
        run_id = f"run_cr03_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_cr03_{uuid.uuid4().hex[:8]}"

        model_service = ModelService(get_settings())
        model = model_service.create_model(input_dim=NUM_FEATURES)
        state_dict = model.state_dict()
        buf = io.BytesIO()
        torch.save(state_dict, buf)
        model_bytes = buf.getvalue()
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        # Legitimate complete training in authority
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.25,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_cr03",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_cr03"],
            aggregated_global_model_digest=model_sha,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.25,
            evaluated_artifact_digest=model_sha,
            coordinator_token=COORD_SECRET,
        )
        rec = source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.25},
            artifact_sha256=model_sha,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=COORD_SECRET,
            enforce_lifecycle=True,
        )

        # Attacker signs receipt with a newly generated, unauthorized rogue key
        rogue_priv_hex, _ = _generate_ed25519_keypair_hex()
        rogue_issuer = TrainingReceiptIssuer(
            signing_key_hex=rogue_priv_hex,
            key_id="unapproved_rogue_authority_key",
            job_record_source=source,
            enforce_job_records=True,
        )

        evidence = TrainingExecutionEvidence(
            training_run_id=run_id,
            job_id=job_id,
            simulation_id=sim_id,
            completed_at=rec.completed_at,
            status="COMPLETED",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.25},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
        )
        rogue_receipt = rogue_issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes)

        # Production verifier rejects rogue key
        registry = ModelRegistry(storage_dir=str(tmp_path / "reg"))
        candidate = registry.save_version(
            simulation_id=sim_id,
            state_dict=state_dict,
            metrics={"loss": 0.25},
            is_promoted=False,
            status="inactive",
            provenance_type="TRAINING_RECORDED",
            training_run_id=run_id,
            training_receipt=rogue_receipt,
            artifact_bytes=model_bytes,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
        )
        assert candidate["provenance_type"] == "UNVERIFIED"
        assert candidate["is_production_eligible"] is False
        assert "INVALID_RECEIPT" in candidate["attestation_status"]

    def test_cr_04_issuer_enforce_job_records_false_rejected_in_production(
        self, real_pg_instance: str
    ) -> None:
        """CR-04: Constructing an issuer with enforce_job_records=False is strictly forbidden in production."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceConfigurationError, match="require_authenticated_job=False is forbidden in production|enforce_job_records=False is forbidden"):
            TrainingReceiptIssuer(
                job_record_source=source,
                enforce_job_records=False,
                app_env="production",
            )

    def test_cr_05_arbitrary_evidence_submitted_without_completion_rejected(
        self, real_pg_instance: str
    ) -> None:
        """CR-05: Submitting arbitrary evidence object to issuer without trusted completed DB record is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        priv_hex, _ = _generate_ed25519_keypair_hex()
        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            job_record_source=source,
            enforce_job_records=True,
            app_env="production",
        )

        model_bytes_cr05 = b"model_bytes_cr05"
        art_sha = hashlib.sha256(model_bytes_cr05).hexdigest()

        unrecorded_evidence = TrainingExecutionEvidence(
            training_run_id=f"run_cr05_{uuid.uuid4().hex[:8]}",
            job_id=f"job_cr05_{uuid.uuid4().hex[:8]}",
            simulation_id="sim_cr05",
            completed_at="2026-10-11T12:00:00Z",
            status="COMPLETED",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.1},
            artifact_sha256=art_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="has no authenticated execution record"):
            issuer.issue_receipt(evidence=unrecorded_evidence, model_bytes=model_bytes_cr05)

    def test_cr_06_unapproved_key_receipt_rejected_by_verifier(
        self, real_pg_instance: str
    ) -> None:
        """CR-06: Verifier configured with trusted public keys strictly rejects receipts from unknown key_ids."""
        verifier = get_default_training_verifier()
        priv_untrusted, _ = _generate_ed25519_keypair_hex()

        issuer = TrainingReceiptIssuer(
            private_key=priv_untrusted,
            key_id="completely_unknown_key_id",
            require_authenticated_job=False,  # Dev helper
        )
        model_bytes = b"sample_model_weights"
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        evidence = TrainingExecutionEvidence(
            training_run_id="run_cr06",
            job_id="job_cr06",
            simulation_id="sim_cr06",
            completed_at="2026-10-11T12:00:00Z",
            status="COMPLETED",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.1},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
        )
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes)

        is_valid, reason = verifier.verify_receipt(
            receipt=receipt,
            expected_simulation_id="sim_cr06",
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
            actual_artifact_bytes=model_bytes,
        )
        assert not is_valid
        assert "not trusted" in reason.lower() or "not present in trusted" in reason.lower()

    def test_cr_07_signing_authority_fails_closed_when_database_unreachable(
        self, tmp_path
    ) -> None:
        """CR-07: When PostgreSQL authority is unavailable, IsolatedReceiptSigningAuthority fails closed."""
        vault_dir = str(tmp_path / "issuer_vault_cr07")
        key_file, _ = provision_isolated_issuer_vault(vault_dir)
        # Invalid DB port fails closed on initialization
        with pytest.raises(TrainingProvenanceConfigurationError, match="Failed to connect to PostgreSQL authority"):
            IsolatedReceiptSigningAuthority(
                issuer_key_path=key_file,
                coordinator_token=COORD_SECRET,
                db_url="postgresql://user:pass@127.0.0.1:59999/nonexistent_db",
                app_env="production",
            )

    def test_cr_08_issuer_and_registry_trust_roots_disagree_rejected(
        self, real_pg_instance: str, tmp_path
    ) -> None:
        """CR-08: When Registry is configured with Root A, receipts signed under Root B are rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET},
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_cr08_{uuid.uuid4().hex[:8]}"
        run_id = f"run_cr08_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_cr08_{uuid.uuid4().hex[:8]}"

        model_bytes = b"model_bytes_cr08"
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        # Complete job
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.2,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_cr08",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_cr08"],
            aggregated_global_model_digest=model_sha,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.2,
            evaluated_artifact_digest=model_sha,
            coordinator_token=COORD_SECRET,
        )
        rec = source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.2},
            artifact_sha256=model_sha,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=COORD_SECRET,
            enforce_lifecycle=True,
        )

        priv_b, _ = _generate_ed25519_keypair_hex()
        _, pub_a = _generate_ed25519_keypair_hex()

        # Registry trusts Key A only
        custom_trust_store = DurableTrustStore({"key_a": pub_a})
        custom_verifier = TrainingReceiptVerifier(trust_store=custom_trust_store)
        registry = ModelRegistry(storage_dir=str(tmp_path / "reg_08"), verifier=custom_verifier)

        # Issuer signs with Key B
        issuer_b = TrainingReceiptIssuer(
            signing_key_hex=priv_b,
            key_id="key_b",
            job_record_source=source,
            enforce_job_records=True,
        )

        evidence = TrainingExecutionEvidence(
            training_run_id=run_id,
            job_id=job_id,
            simulation_id=sim_id,
            completed_at=rec.completed_at,
            status="COMPLETED",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.2},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
        )
        receipt_b = issuer_b.issue_receipt(evidence=evidence, model_bytes=model_bytes)

        candidate = registry.save_version(
            simulation_id=sim_id,
            state_dict={},
            metrics={"loss": 0.2},
            training_receipt=receipt_b,
            artifact_bytes=model_bytes,
            provenance_type="TRAINING_RECORDED",
            training_run_id=run_id,
        )
        assert candidate["provenance_type"] == "UNVERIFIED"
        assert candidate["is_production_eligible"] is False
        assert "INVALID_RECEIPT" in candidate["attestation_status"]

    def test_cr_09_verifier_cannot_sign_receipts(self) -> None:
        """CR-09: TrainingReceiptVerifier has no private key attributes and cannot sign receipts."""
        verifier = get_default_training_verifier()
        assert not hasattr(verifier, "issue_receipt")
        assert not hasattr(verifier, "_private_key")
        assert not hasattr(verifier, "signing_key")

    def test_cr_10_legitimate_completed_job_signed_by_isolated_authority_and_verified(
        self, real_pg_instance: str, tmp_path
    ) -> None:
        """CR-10: Legitimate completed job obtains receipt from isolated authority and Registry verifies it."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET},
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_cr10_{uuid.uuid4().hex[:8]}"
        run_id = f"run_cr10_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_cr10_{uuid.uuid4().hex[:8]}"

        model_service = ModelService(get_settings())
        model = model_service.create_model(input_dim=NUM_FEATURES)
        buf = io.BytesIO()
        torch.save(model.state_dict(), buf)
        model_bytes = buf.getvalue()
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        # Complete training in PostgreSQL
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.18,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest=model_sha,
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[model_sha],
            aggregated_global_model_digest=model_sha,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.18,
            evaluated_artifact_digest=model_sha,
            coordinator_token=COORD_SECRET,
        )
        source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.18},
            artifact_sha256=model_sha,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=COORD_SECRET,
            enforce_lifecycle=True,
        )

        # Provision independent signing authority
        vault_dir = str(tmp_path / "issuer_vault_cr10")
        key_file, pub_hex = provision_isolated_issuer_vault(vault_dir)
        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_file,
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            key_id="authority_key_10",
            app_env="production",
        )

        # Signing authority verifies DB record and issues receipt
        receipt = signing_auth.sign_completed_job_receipt(
            job_id=job_id,
            artifact_bytes=model_bytes,
            caller_identity="COORDINATOR",
            caller_token=COORD_SECRET,
        )
        assert bool(receipt.signature)
        assert receipt.signature_scheme == "Ed25519"
        assert receipt.model_artifact_sha256 == model_sha

        # Configure Registry with trusted public key
        trust_store = DurableTrustStore({"authority_key_10": pub_hex})
        verifier = TrainingReceiptVerifier(trust_store=trust_store)
        registry = ModelRegistry(storage_dir=str(tmp_path / "reg_10"), verifier=verifier)

        # Candidate is admitted as inactive
        candidate = registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            metrics={"loss": 0.18},
            is_promoted=False,
            status="inactive",
            provenance_type="TRAINING_RECORDED",
            training_run_id=run_id,
            training_receipt=receipt,
            artifact_bytes=model_bytes,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
        )

        assert candidate["status"] == "inactive"
        assert candidate["provenance_type"] == "TRAINING_RECORDED"
        assert registry.get_active_champion_identity(simulation_id=sim_id) is None
        signing_auth.terminate()

    def test_cr_11_coordinator_cannot_read_isolated_issuer_vault_key(self, tmp_path, real_pg_instance: str) -> None:
        """CR-11: Issuer private key is provisioned in isolated vault; coordinator has no access."""
        vault_dir = str(tmp_path / "issuer_vault_cr11")
        key_file, pub_hex = provision_isolated_issuer_vault(vault_dir)
        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_file,
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        assert signing_auth.public_key_hex == pub_hex
        assert not hasattr(signing_auth, "signing_key_hex")
        assert not hasattr(signing_auth, "_private_key")
        signing_auth.terminate()

    def test_cr_12_issuer_rejects_missing_or_invalid_coordinator_token(self, real_pg_instance: str, tmp_path) -> None:
        """CR-12: Production issuer strictly rejects signing request if coordinator token is missing or invalid."""
        vault_dir = str(tmp_path / "issuer_vault_cr12")
        key_file, _ = provision_isolated_issuer_vault(vault_dir)
        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_file,
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized receipt signing request|invalid or missing coordinator token|missing coordinator token|Receipt signing rejected"):
            signing_auth.sign_completed_job_receipt(
                job_id="job_cr12",
                artifact_bytes=b"bytes",
                caller_identity="COORDINATOR",
                caller_token=None,
            )
        with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized receipt signing request|invalid or missing coordinator token|Receipt signing rejected"):
            signing_auth.sign_completed_job_receipt(
                job_id="job_cr12",
                artifact_bytes=b"bytes",
                caller_identity="COORDINATOR",
                caller_token="wrong_token_cr12",
            )
        signing_auth.terminate()

    def test_cr_13_replay_request_for_already_signed_job_rejected(self, real_pg_instance: str, tmp_path) -> None:
        """CR-13: Replaying receipt signing request for an already-signed job is rejected by issuer."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET},
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_cr13_{uuid.uuid4().hex[:8]}"
        run_id = f"run_cr13_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_cr13_{uuid.uuid4().hex[:8]}"
        model_bytes = b"model_bytes_cr13"
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        source.schedule_training_job(job_id=job_id, training_run_id=run_id, simulation_id=sim_id, coordinator_token=COORD_SECRET, permitted_workers=["BANK_A"])
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        source.record_round_observation(job_id=job_id, round_number=1, sample_count=100, actual_executed_epochs=1, observed_loss=0.2, worker_token=BANK_A_SECRET, bank_id="BANK_A", model_update_digest="d13")
        source.record_global_aggregation(job_id=job_id, round_number=1, aggregation_algorithm="fed_avg_weighted", accepted_participant_ids=["BANK_A"], accepted_local_update_digests=["d13"], aggregated_global_model_digest=model_sha, coordinator_token=COORD_SECRET)
        source.record_evaluation_observation(job_id=job_id, metric_name="loss", metric_value=0.2, evaluated_artifact_digest=model_sha, coordinator_token=COORD_SECRET)
        source.complete_training_job(job_id=job_id, executed_rounds=1, executed_epochs=1, sample_count=100, observed_metrics={"loss": 0.2}, artifact_sha256=model_sha, worker_id="FEDERATED_COORDINATOR", coordinator_token=COORD_SECRET, enforce_lifecycle=True)

        vault_dir = str(tmp_path / "issuer_vault_cr13")
        key_file, _ = provision_isolated_issuer_vault(vault_dir)
        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_file,
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        r1 = signing_auth.sign_completed_job_receipt(job_id=job_id, artifact_bytes=model_bytes, caller_identity="COORDINATOR", caller_token=COORD_SECRET)
        assert bool(r1.signature)

        with pytest.raises(TrainingProvenanceSecurityError, match="Duplicate signing request|already-signed|already been signed|Replay detected"):
            signing_auth.sign_completed_job_receipt(job_id=job_id, artifact_bytes=model_bytes, caller_identity="COORDINATOR", caller_token=COORD_SECRET)
        signing_auth.terminate()
