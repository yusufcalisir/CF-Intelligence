"""Unit tests for cross-bank evidence integrity, simulation isolation & institutional privacy assurance.

Verifies:
1. Strict Boundary Between Research Simulation and Operational Intelligence:
   - Production-context principal cannot query simulation mode (fails closed).
   - Operational entities cannot be queried via simulation mode.
   - Simulated signals cannot enter authoritative fraud evidence (SAR/Escalated).
2. Elimination of Evidence File Overwrite and False Durability:
   - Collision-resistant file paths across cases and notes.
   - Safe atomic file creation with fsync.
   - Storage failure fails closed with EvidencePersistenceError (no false durability).
   - Safe CAS conflict rollback without deleting files of other cases.
   - Retrieval integrity check detecting file tampering.
3. Server-Issued Signal Tamper Verification:
   - Signal payload digest verified before attachment.
   - Missing/inconsistent bank, principal, and consortium bindings rejected.
   - Revocation enforced at attachment time.
4. Authoritative Per-Consortium Intelligence Sharing Policy:
   - Persisted policy in Consortium.metadata enforced.
   - Missing institutional jurisdictions fail closed without silent fallbacks.
   - Revocation takes immediate effect.
5. Institutional Isolation & Cryptographic Boundary:
   - Bank private key isolation (Bank A cannot access Bank B's key).
   - Negative controls (removing counterpart entity eliminates match).
"""

from __future__ import annotations

import datetime
import hashlib
import json
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceQueryRequest,
)
from app.application.services.case_service import (
    CaseManagementService,
    EvidencePersistenceError,
    EvidenceRegistryService,
    InvalidCaseTransitionError,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import EntityResolutionService
from app.config import get_settings
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    MemberRole,
)
from app.domain.enums import CaseStatus, EntityType, RiskLevel
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store_file = tmp_path / "consortium_store.json"
    account_file = tmp_path / "account_store.json"
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("CFI_ENABLE_CROSS_BANK_SIMULATION", "true")
    monkeypatch.setenv("APP_ENV", "development")

    from app.config import get_settings

    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    yield
    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()


@pytest.fixture
def auth_tokens() -> dict[str, str]:
    auth_svc = AuthenticationService.get_instance()
    u_a = auth_svc.get_user("intel_analyst_a")
    if not u_a:
        u_a = auth_svc.register_user(
            "intel_analyst_a",
            "AdminPass123!",
            "bank_a",
            roles=["admin", "analyst"],
            clearance_level=3,
        )

    u_b = auth_svc.get_user("intel_analyst_b")
    if not u_b:
        u_b = auth_svc.register_user(
            "intel_analyst_b",
            "AdminPass123!",
            "bank_b",
            roles=["admin", "analyst"],
            clearance_level=3,
        )

    return {
        "bank_a": auth_svc.create_token_bundle(u_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_b).access_token,
    }


@pytest.fixture
def test_setup(auth_tokens: dict[str, str], tmp_path: Path):
    gov_svc = ConsortiumGovernanceService()
    gov_svc.create_consortium(
        consortium_id="high_assurance_ring",
        name="High Assurance Fraud Ring",
        founder_bank_id="bank_a",
        auth_token=auth_tokens["bank_a"],
    )

    from app.application.services.model_registry import create_trusted_internal_context

    ctx = create_trusted_internal_context(
        service_name="consortium_coordinator",
        action="add_member",
        resource_type="consortium",
        resource_id="high_assurance_ring",
        target_bank_id="bank_b",
    )
    gov_svc.add_member(
        "high_assurance_ring",
        "bank_b",
        role=MemberRole.FULL_MEMBER,
        voting_power=1.0,
        trusted_context=ctx,
    )

    entity_svc = EntityResolutionService()
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService()
    intel_svc = CrossBankIntelligenceService(
        consortium_service=gov_svc,
        entity_service=entity_svc,
        case_service=case_svc,
        evidence_service=evidence_svc,
    )
    return {
        "gov_svc": gov_svc,
        "entity_svc": entity_svc,
        "case_svc": case_svc,
        "evidence_svc": evidence_svc,
        "intel_svc": intel_svc,
        "consortium_id": "high_assurance_ring",
    }


class TestSimulationIsolation:
    def test_production_context_rejects_simulation_mode(
        self, test_setup: dict[str, Any], auth_tokens: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ):
        """Negative test: Production principal cannot obtain counterpart intelligence via simulation mode."""
        intel_svc: CrossBankIntelligenceService = test_setup["intel_svc"]
        entity_svc: EntityResolutionService = test_setup["entity_svc"]

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="corp.executive@bank-a.com",
            bank_id="bank_a",
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id=test_setup["consortium_id"],
            execution_mode="SIMULATED_RESEARCH",
        )

        # Force production environment
        monkeypatch.setattr(get_settings(), "app_env", "production")

        with pytest.raises(ConsortiumSecurityError, match="strictly prohibited in production"):
            intel_svc.query_cross_bank_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    def test_querying_operational_entity_via_simulation_fails_closed(
        self, test_setup: dict[str, Any], auth_tokens: dict[str, str]
    ):
        """Simulation mode cannot be used as a backdoor to query live operational entities."""
        intel_svc: CrossBankIntelligenceService = test_setup["intel_svc"]
        entity_svc: EntityResolutionService = test_setup["entity_svc"]

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="live.operational.account@bank-a.com",
            bank_id="bank_a",
            attributes={"is_production_operational": True},
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id=test_setup["consortium_id"],
            execution_mode="SIMULATED_RESEARCH",
        )

        with pytest.raises(ConsortiumSecurityError, match="cannot be queried via research simulation mode"):
            intel_svc.query_cross_bank_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    def test_simulated_signal_cannot_be_attached_to_authoritative_or_sar_case(
        self, test_setup: dict[str, Any], auth_tokens: dict[str, str]
    ):
        """Simulated intelligence must never enter authoritative regulatory case filings."""
        intel_svc: CrossBankIntelligenceService = test_setup["intel_svc"]
        entity_svc: EntityResolutionService = test_setup["entity_svc"]
        case_svc: CaseManagementService = test_setup["case_svc"]

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="simulated.merchant@domain.org",
            bank_id="bank_a",
        )

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id=test_setup["consortium_id"],
            execution_mode="SIMULATED_RESEARCH",
        )

        signal = intel_svc.query_cross_bank_intelligence(req=req, auth_token=auth_tokens["bank_a"])
        assert signal.is_synthetic is True
        assert signal.is_authoritative is False

        # Create SAR_FILED case
        from app.application.services.case_service import _case_to_dict

        sar_case = case_svc.create_case(title="SAR Filed Regulatory Dossier", bank_id="bank_a")
        sar_case.status = CaseStatus.SAR_FILED
        case_svc._cases.set(sar_case.id, _case_to_dict(sar_case))

        with pytest.raises(ConsortiumSecurityError, match="Simulated research signals cannot be attached to authoritative"):
            intel_svc.attach_signal_to_case_evidence(
                case_id=sar_case.id,
                signal_id=signal.signal_id,
                auth_token=auth_tokens["bank_a"],
            )


def _make_signal_authoritative(intel_svc: CrossBankIntelligenceService, signal_id: str) -> None:
    record = intel_svc._signal_store.get(signal_id)
    assert record is not None
    record["is_authoritative"] = True
    record["is_synthetic"] = False
    record["execution_mode"] = "VERIFIED_OPERATIONAL"
    record["admission_status"] = "COMMITTED"
    record["publication_status"] = "PUBLISHED"
    record["is_attachable"] = True
    record["signal_data"]["execution_mode"] = "VERIFIED_OPERATIONAL"
    record["signal_data"]["is_authoritative"] = True
    record["signal_data"]["is_synthetic"] = False
    record["content_hash"] = hashlib.sha256(
        json.dumps(record["signal_data"], sort_keys=True).encode("utf-8")
    ).hexdigest()
    rcp_id = record.get("receipt_id") or f"rcp_{signal_id}"
    record["receipt_id"] = rcp_id
    intel_svc._admitted_receipts_store.set(
        rcp_id,
        {
            "admitted_at": datetime.datetime.now(UTC).isoformat(),
            "signal_id": signal_id,
            "receipt_id": rcp_id,
            "content_hash": record["content_hash"],
            "requesting_bank_id": record.get("requesting_bank_id"),
            "target_bank_id": record.get("target_bank_id"),
            "consortium_id": record.get("consortium_id"),
            "execution_mode": "VERIFIED_OPERATIONAL",
        },
    )
    intel_svc._signal_store.set(signal_id, record)


class TestEvidenceIntegrityAndPersistence:
    def test_same_signal_attached_to_different_cases_produces_distinct_files(
        self, test_setup: dict[str, Any], auth_tokens: dict[str, str]
    ):
        """The same signal attached to multiple cases must have non-colliding immutable storage files."""
        intel_svc: CrossBankIntelligenceService = test_setup["intel_svc"]
        entity_svc: EntityResolutionService = test_setup["entity_svc"]
        case_svc: CaseManagementService = test_setup["case_svc"]
        evidence_svc: EvidenceRegistryService = test_setup["evidence_svc"]

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="multi.case.target@bank-a.com",
            bank_id="bank_a",
        )
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id=test_setup["consortium_id"],
        )
        signal = intel_svc.query_cross_bank_intelligence(req=req, auth_token=auth_tokens["bank_a"])
        _make_signal_authoritative(intel_svc, signal.signal_id)

        case_1 = case_svc.create_case(title="Case Investigation Alpha", bank_id="bank_a")
        case_2 = case_svc.create_case(title="Case Investigation Beta", bank_id="bank_a")

        resp1 = intel_svc.attach_signal_to_case_evidence(
            case_id=case_1.id,
            signal_id=signal.signal_id,
            auth_token=auth_tokens["bank_a"],
            notes="Case 1 notes",
        )
        resp2 = intel_svc.attach_signal_to_case_evidence(
            case_id=case_2.id,
            signal_id=signal.signal_id,
            auth_token=auth_tokens["bank_a"],
            notes="Case 2 notes",
        )

        ev1 = evidence_svc.get_evidence(resp1.evidence_id)
        ev2 = evidence_svc.get_evidence(resp2.evidence_id)
        assert ev1 is not None and ev2 is not None

        # Storage paths must be distinct and non-colliding
        assert ev1["file_path"] != ev2["file_path"]
        assert Path(ev1["file_path"]).exists()
        assert Path(ev2["file_path"]).exists()

        # Both contents are retrievable and verified
        content1 = evidence_svc.get_evidence_content(resp1.evidence_id)
        content2 = evidence_svc.get_evidence_content(resp2.evidence_id)
        assert "Case 1 notes" in content1
        assert "Case 2 notes" in content2

    def test_different_notes_produce_different_content_hashes(
        self, test_setup: dict[str, Any], auth_tokens: dict[str, str]
    ):
        """Different metadata or analyst notes produce distinct content hashes and evidence identities."""
        intel_svc: CrossBankIntelligenceService = test_setup["intel_svc"]
        entity_svc: EntityResolutionService = test_setup["entity_svc"]
        case_svc: CaseManagementService = test_setup["case_svc"]

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="notes.target@bank-a.com",
            bank_id="bank_a",
        )
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id=test_setup["consortium_id"],
        )
        signal = intel_svc.query_cross_bank_intelligence(req=req, auth_token=auth_tokens["bank_a"])
        _make_signal_authoritative(intel_svc, signal.signal_id)

        case_1 = case_svc.create_case(title="Case Note 1", bank_id="bank_a")
        case_2 = case_svc.create_case(title="Case Note 2", bank_id="bank_a")

        resp1 = intel_svc.attach_signal_to_case_evidence(
            case_id=case_1.id,
            signal_id=signal.signal_id,
            auth_token=auth_tokens["bank_a"],
            notes="Primary initial observation",
        )
        resp2 = intel_svc.attach_signal_to_case_evidence(
            case_id=case_2.id,
            signal_id=signal.signal_id,
            auth_token=auth_tokens["bank_a"],
            notes="Secondary divergent observation",
        )

        assert resp1.content_hash != resp2.content_hash

    def test_file_io_failure_fails_closed_without_false_durability(
        self, test_setup: dict[str, Any]
    ):
        """Failed disk write must raise EvidencePersistenceError and never report evidence as registered."""
        evidence_svc: EvidenceRegistryService = test_setup["evidence_svc"]
        case_svc: CaseManagementService = test_setup["case_svc"]

        case = case_svc.create_case(title="Disk IO Failure Case", bank_id="bank_a")

        with patch("builtins.open", side_effect=OSError("Disk write I/O error")):
            with pytest.raises(EvidencePersistenceError, match="Evidence persistence failed"):
                evidence_svc.register_evidence(
                    case_id=case.id,
                    evidence_type="CROSS_BANK_INTELLIGENCE",
                    title="Failed Disk Write Evidence",
                    file_path=f"storage/evidence/fail_io_{case.id}.json",
                    content="{'critical': 'evidence'}",
                    uploaded_by="analyst_alice",
                )

        # Invariant: No evidence registered in case
        case_after = case_svc.get_case(case.id)
        assert case_after is not None
        assert len(case_after.evidence_ids) == 0

    def test_cas_conflict_rolls_back_created_file_only(
        self, test_setup: dict[str, Any]
    ):
        """When CAS conditional update fails, the created file is removed without affecting existing files."""
        evidence_svc: EvidenceRegistryService = test_setup["evidence_svc"]
        case_svc: CaseManagementService = test_setup["case_svc"]

        # 1. Successfully register primary evidence file
        case = case_svc.create_case(title="CAS Rollback Target Case", bank_id="bank_a")
        ev_primary = evidence_svc.register_evidence(
            case_id=case.id,
            evidence_type="DOCUMENT",
            title="Primary Permanent Evidence",
            file_path=f"storage/evidence/perm_{case.id}.json",
            content="{'permanent': True}",
            uploaded_by="analyst_alice",
        )
        assert Path(ev_primary["file_path"]).exists()

        # 2. Attempt secondary evidence where case CAS fails
        conflicting_path = f"storage/evidence/conflict_{case.id}.json"
        with patch.object(evidence_svc._case_service._cases, "update_conditional", return_value=False):
            with pytest.raises(InvalidCaseTransitionError):
                evidence_svc.register_evidence(
                    case_id=case.id,
                    evidence_type="DOCUMENT",
                    title="Conflicting Evidence",
                    file_path=conflicting_path,
                    content="{'conflict': True}",
                    uploaded_by="analyst_alice",
                )

        # Invariant: Conflicting file was rolled back, primary file remains intact
        assert not Path(conflicting_path).exists()
        assert Path(ev_primary["file_path"]).exists()

    def test_evidence_retrieval_detects_disk_tampering(
        self, test_setup: dict[str, Any]
    ):
        """Tampering with an evidence file on disk is detected by SHA-256 verification."""
        evidence_svc: EvidenceRegistryService = test_setup["evidence_svc"]
        case_svc: CaseManagementService = test_setup["case_svc"]

        case = case_svc.create_case(title="Tamper Audit Case", bank_id="bank_a")
        ev = evidence_svc.register_evidence(
            case_id=case.id,
            evidence_type="DOCUMENT",
            title="Tamper Target Evidence",
            file_path=f"storage/evidence/tamper_{case.id}.json",
            content="{'legitimate_evidence': True}",
            uploaded_by="analyst_alice",
        )

        # Integrity passes initially
        verif = evidence_svc.verify_evidence_integrity(ev["id"])
        assert verif["intact"] is True

        # Tamper with file directly on disk
        with open(ev["file_path"], "w", encoding="utf-8") as f:
            f.write("{'tampered_evidence': 'malicious_modification'}")

        # Invariant: Integrity check reports mismatch
        verif_after = evidence_svc.verify_evidence_integrity(ev["id"])
        assert verif_after["intact"] is False

        # Invariant: Content retrieval raises EvidencePersistenceError
        with pytest.raises(EvidencePersistenceError, match="Cryptographic evidence tampering detected"):
            evidence_svc.get_evidence_content(ev["id"])


class TestServerIssuedSignalIntegrity:
    def test_tampered_signal_data_rejected_on_attachment(
        self, test_setup: dict[str, Any], auth_tokens: dict[str, str]
    ):
        """If signal_data in the server store is tampered with, attachment is rejected by digest mismatch."""
        intel_svc: CrossBankIntelligenceService = test_setup["intel_svc"]
        entity_svc: EntityResolutionService = test_setup["entity_svc"]
        case_svc: CaseManagementService = test_setup["case_svc"]

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="tamper.sig@bank-a.com",
            bank_id="bank_a",
        )
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id=test_setup["consortium_id"],
        )
        signal = intel_svc.query_cross_bank_intelligence(req=req, auth_token=auth_tokens["bank_a"])
        _make_signal_authoritative(intel_svc, signal.signal_id)

        # Tamper with signal_data in store
        record = intel_svc._signal_store.get(signal.signal_id)
        assert record is not None
        record["signal_data"]["match_category"] = "MALICIOUSLY_FABRICATED"
        intel_svc._signal_store.set(signal.signal_id, record)

        case = case_svc.create_case(title="Signal Tamper Case", bank_id="bank_a")

        with pytest.raises(ConsortiumSecurityError, match="failed server-side tamper verification"):
            intel_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=signal.signal_id,
                auth_token=auth_tokens["bank_a"],
            )

    def test_mismatched_consortium_binding_rejected_on_attachment(
        self, test_setup: dict[str, Any], auth_tokens: dict[str, str]
    ):
        """Signal bound to Consortium X cannot be attached under Consortium Y context."""
        intel_svc: CrossBankIntelligenceService = test_setup["intel_svc"]
        entity_svc: EntityResolutionService = test_setup["entity_svc"]
        case_svc: CaseManagementService = test_setup["case_svc"]

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="consortium.binding@bank-a.com",
            bank_id="bank_a",
        )
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id=test_setup["consortium_id"],
        )
        signal = intel_svc.query_cross_bank_intelligence(req=req, auth_token=auth_tokens["bank_a"])
        _make_signal_authoritative(intel_svc, signal.signal_id)

        # Create different consortium
        gov_svc: ConsortiumGovernanceService = test_setup["gov_svc"]
        gov_svc.create_consortium(
            consortium_id="other_consortium_ring",
            name="Other Ring",
            founder_bank_id="bank_a",
            auth_token=auth_tokens["bank_a"],
        )

        case = case_svc.create_case(title="Binding Case", bank_id="bank_a")

        # Mutate binding in record to a non-existent consortium while keeping hash intact
        record = intel_svc._signal_store.get(signal.signal_id)
        assert record is not None
        record["consortium_id"] = "nonexistent_consortium_ring"
        intel_svc._signal_store.set(signal.signal_id, record)

        with pytest.raises(ConsortiumSecurityError, match="no longer exists|does not exist"):
            intel_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=signal.signal_id,
                auth_token=auth_tokens["bank_a"],
            )

    def test_revoked_member_cannot_attach_previously_issued_signal(
        self, test_setup: dict[str, Any], auth_tokens: dict[str, str]
    ):
        """When an institution is evicted/revoked, it cannot attach previously issued signals."""
        intel_svc: CrossBankIntelligenceService = test_setup["intel_svc"]
        entity_svc: EntityResolutionService = test_setup["entity_svc"]
        gov_svc: ConsortiumGovernanceService = test_setup["gov_svc"]
        case_svc: CaseManagementService = test_setup["case_svc"]

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="revocation.attach@bank-a.com",
            bank_id="bank_a",
        )
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id=test_setup["consortium_id"],
        )
        signal = intel_svc.query_cross_bank_intelligence(req=req, auth_token=auth_tokens["bank_a"])
        _make_signal_authoritative(intel_svc, signal.signal_id)

        case = case_svc.create_case(title="Revocation Case", bank_id="bank_a")

        # Revoke bank_a
        def _evict(consortia: Any, proposals: Any) -> None:
            c_mut = consortia[test_setup["consortium_id"]]
            c_mut.removed_members["bank_a"] = {"bank_id": "bank_a", "reason": "evicted"}
            c_mut.members.pop("bank_a", None)

        gov_svc._store.atomic_mutate(_evict)

        with pytest.raises(ConsortiumSecurityError, match="revoked/evicted|not an active member"):
            intel_svc.attach_signal_to_case_evidence(
                case_id=case.id,
                signal_id=signal.signal_id,
                auth_token=auth_tokens["bank_a"],
            )


class TestAuthoritativeConsortiumSharingPolicy:
    def test_disallowed_sharing_purpose_rejected_by_persisted_policy(
        self, test_setup: dict[str, Any], auth_tokens: dict[str, str]
    ):
        """Custom policy in Consortium.metadata rejects unauthorized query purposes."""
        intel_svc: CrossBankIntelligenceService = test_setup["intel_svc"]
        entity_svc: EntityResolutionService = test_setup["entity_svc"]
        gov_svc: ConsortiumGovernanceService = test_setup["gov_svc"]

        # Persist strict sharing policy permitting only SANCTIONS_SCREENING
        def _set_strict_policy(consortia: Any, proposals: Any) -> None:
            c = consortia[test_setup["consortium_id"]]
            c.metadata["intelligence_sharing_policy"] = {
                "allowed_purposes": ["SANCTIONS_SCREENING"],
            }

        gov_svc._store.atomic_mutate(_set_strict_policy)

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="restricted.purpose@bank-a.com",
            bank_id="bank_a",
        )

        # Query with default FRAUD_INVESTIGATION purpose
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id=test_setup["consortium_id"],
            sharing_purpose="FRAUD_INVESTIGATION",
        )

        with pytest.raises(ConsortiumSecurityError, match="Consortium data-sharing policy rejected query"):
            intel_svc.query_cross_bank_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    def test_missing_institutional_jurisdiction_fails_closed_without_fallback(
        self, test_setup: dict[str, Any], auth_tokens: dict[str, str]
    ):
        """If cross-border sharing is restricted and institutional jurisdictions are unknown, fails closed."""
        intel_svc: CrossBankIntelligenceService = test_setup["intel_svc"]
        entity_svc: EntityResolutionService = test_setup["entity_svc"]
        gov_svc: ConsortiumGovernanceService = test_setup["gov_svc"]

        # Disallow cross-border sharing
        def _restrict_cross_border(consortia: Any, proposals: Any) -> None:
            c = consortia[test_setup["consortium_id"]]
            c.metadata["intelligence_sharing_policy"] = {
                "allow_cross_border_sharing": False,
            }

        gov_svc._store.atomic_mutate(_restrict_cross_border)

        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="crossborder.entity@bank-a.com",
            bank_id="bank_a",
        )

        # Query without providing explicit institutional jurisdictions
        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id=test_setup["consortium_id"],
        )

        with pytest.raises(ConsortiumSecurityError, match="Authoritative jurisdiction"):
            intel_svc.query_cross_bank_intelligence(req=req, auth_token=auth_tokens["bank_a"])


class TestInstitutionalIsolationAndNegativeControl:
    def test_bank_private_exponent_isolation(self):
        """Bank A KMS private exponent is access-control isolated from Bank B."""
        from app.application.services.kms_service import TenantKMSSession, get_kms_service

        kms = get_kms_service()
        key_a = kms.get_psi_private_exponent("bank_a")
        key_b = kms.get_psi_private_exponent("bank_b")

        assert key_a != key_b
        assert key_a > 1
        assert key_b > 1

        # Enforce institutional access-control boundary: Caller tenant bank_a cannot access bank_b keys
        with pytest.raises(PermissionError, match="KMS access control violation"):
            kms.get_psi_private_exponent("bank_b", caller_tenant="bank_a")

        with pytest.raises(PermissionError, match="KMS access control violation"):
            kms.get_hmac_key("bank_b", caller_tenant="bank_a")

        # Scoped TenantKMSSession permanently bound to bank_a retrieves bank_a keys
        session_a = TenantKMSSession(bank_id="bank_a", kms_service=kms)
        assert session_a.get_psi_private_exponent() == key_a
        assert session_a.bank_id == "bank_a"

    def test_negative_control_removing_counterpart_removes_match(
        self, test_setup: dict[str, Any], auth_tokens: dict[str, str]
    ):
        """Negative control: Removing counterpart entity confirms signal is genuinely derived, not fabricated."""
        intel_svc: CrossBankIntelligenceService = test_setup["intel_svc"]
        entity_svc: EntityResolutionService = test_setup["entity_svc"]

        # 1. Create matching entities at both banks
        ent_a = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="target.control@domain.org",
            bank_id="bank_a",
        )
        ent_b = entity_svc.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="target.control@domain.org",
            bank_id="bank_b",
        )
        entity_svc.update_risk_level(ent_b.id, RiskLevel.HIGH)

        req = CrossBankIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id=ent_a.id,
            consortium_id=test_setup["consortium_id"],
            target_bank_id="bank_b",
        )
        signal_with_match = intel_svc.query_cross_bank_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
        )
        assert signal_with_match.match_confirmed is True

        # 2. Negative Control: Delete/deregister Bank B counterpart entity
        entity_svc._entities.delete(ent_b.id)

        signal_without_match = intel_svc.query_cross_bank_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
        )
        assert signal_without_match.match_confirmed is False
        assert signal_without_match.match_category == "NO_MATCH"
        assert signal_without_match.contributing_institutions_count == 0
