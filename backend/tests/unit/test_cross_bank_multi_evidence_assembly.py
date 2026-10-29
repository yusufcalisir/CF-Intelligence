"""Unit and Adversarial Test Suite for Authenticated Multi-Counterparty Case Evidence Assembly (M6.5 Task 2).

Verifies:
1. Valid multi-counterparty batch evidence assembly (Gate A, B, C).
2. Idempotent replay with zero duplicate evidence or version drift (Gate F).
3. Legitimate partial failure integrity (Gate D: Bank B succeeds, Bank C fails).
4. Durable batch persistence & process restart recovery across independent service instances (Gate A, F).
5. Disclosure preservation in anonymous vs attributed modes & notes validation (Gate E).
6. Adversarial suite: fabricated query_id, cross-tenant reuse, cross-case binding violation,
   idempotency conflicts, terminal case, expired batch, and revoked grant at attachment time.
7. FastAPI endpoint /api/v1/intelligence/cross-bank/attach-multi-to-case error mapping (400, 403, 404, 409).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.services.case_service import (
    CaseManagementService,
    EvidenceRegistryService,
    TerminalCaseImmutableError,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import EntityResolutionService
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    CrossBankIdempotencyConflictError,
)
from app.domain.enums import CaseStatus
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_batch_store import (
    BatchCaseBindingConflictError,
    BatchStorageConflictError,
    DurableCrossBankBatchStore,
)
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.main import app

SHARED_JWT_SECRET = "super-secret-jwt-key-for-test-suites-minimum-32-chars-long"


@pytest.fixture(autouse=True)
def isolate_batch_assembly_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store_file = tmp_path / "consortium_multi_evidence_store.json"
    account_file = tmp_path / "account_multi_evidence_store.json"
    batch_db = tmp_path / "cross_bank_batches_test.sqlite"

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("CFI_BATCH_DB_PATH", str(batch_db))
    monkeypatch.setenv("JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_OIDC_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_IDENTITY_STORE_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_ENABLE_CROSS_BANK_SIMULATION", "true")
    monkeypatch.setattr(
        "app.infrastructure.security.auth_service.hash_password",
        lambda p, rounds=12: "$2b$04$testseedmockhashforevidenceadmissiononly000000000000",
    )

    from app.config import get_settings

    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurableCrossBankBatchStore.reset_instance()
    CrossBankIntelligenceService._instance = None
    yield
    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurableCrossBankBatchStore.reset_instance()
    CrossBankIntelligenceService._instance = None


@pytest.fixture
def multi_assembly_env(tmp_path: Path) -> dict[str, Any]:
    gov_svc = ConsortiumGovernanceService()
    case_svc = CaseManagementService()
    evidence_svc = EvidenceRegistryService(case_service=case_svc)
    entity_svc = EntityResolutionService()
    auth_svc = AuthenticationService.get_instance()
    batch_store = DurableCrossBankBatchStore.get_instance()

    u_admin = auth_svc.get_user("admin")
    u_a = auth_svc.get_user("analyst_a1")
    u_b = auth_svc.get_user("analyst_b1")
    assert u_admin and u_a and u_b

    u_admin_b = auth_svc.get_user("admin_b")
    if not u_admin_b:
        u_admin_b = auth_svc.register_user("admin_b", "Pass123!", "bank_b", roles=["admin"])
    else:
        u_admin_b.roles = ["admin"]
        auth_svc.identity_store.save_user(u_admin_b)

    tokens = {
        "admin": auth_svc.create_token_bundle(u_admin).access_token,
        "bank_a": auth_svc.create_token_bundle(u_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_b).access_token,
        "admin_b": auth_svc.create_token_bundle(u_admin_b).access_token,
    }

    principals = {
        "bank_a": AuthenticatedPrincipal(
            principal_id="analyst_a1",
            tenant_id="bank_a",
            roles=["analyst", "investigator"],
            is_authenticated=True,
        ),
        "bank_b": AuthenticatedPrincipal(
            principal_id="analyst_b1",
            tenant_id="bank_b",
            roles=["analyst", "investigator"],
            is_authenticated=True,
        ),
    }

    gov_svc.create_consortium(
        consortium_id="c_multi_test",
        name="Multi-Counterparty Assembly Consortium",
        founder_bank_id="bank_a",
        auth_token=tokens["admin"],
    )
    gov_svc.add_member(
        consortium_id="c_multi_test",
        bank_id="bank_b",
        auth_token=tokens["admin"],
    )
    gov_svc.add_member(
        consortium_id="c_multi_test",
        bank_id="bank_c",
        auth_token=tokens["admin"],
    )

    cbi_svc = CrossBankIntelligenceService(
        consortium_service=gov_svc,
        case_service=case_svc,
        evidence_service=evidence_svc,
        entity_service=entity_svc,
        batch_store=batch_store,
    )

    return {
        "gov_svc": gov_svc,
        "case_svc": case_svc,
        "evidence_svc": evidence_svc,
        "entity_svc": entity_svc,
        "batch_store": batch_store,
        "cbi_svc": cbi_svc,
        "tokens": tokens,
        "principals": principals,
        "consortium_id": "c_multi_test",
    }


def _seed_operational_signal(
    cbi_svc: CrossBankIntelligenceService,
    consortium_id: str = "c_multi_test",
    requesting_bank_id: str = "bank_a",
    target_bank_id: str = "bank_b",
    query_entity_id: str = "entity_alpha",
    disclosure_mode: str = "ATTRIBUTED_INSTITUTIONS",
    match_confirmed: bool = True,
) -> tuple[str, str]:
    """Helper to seed an authentic admitted operational signal and its receipt."""
    sig_id = f"sig_{uuid.uuid4().hex}"
    rcp_id = f"rcp_{uuid.uuid4().hex}"
    now = datetime.datetime.now(UTC)

    sig_data = {
        "signal_id": sig_id,
        "consortium_id": consortium_id,
        "requesting_bank_id": requesting_bank_id,
        "target_bank_id": target_bank_id if disclosure_mode == "ATTRIBUTED_INSTITUTIONS" else None,
        "query_entity_id": query_entity_id,
        "privacy_hash_truncated": "hash_sample",
        "match_confirmed": match_confirmed,
        "match_category": "EXACT_MATCH" if match_confirmed else "NO_MATCH",
        "match_confidence": 1.0 if match_confirmed else 0.0,
        "contributing_institutions_count": 1 if match_confirmed else 0,
        "contributing_institutions": [target_bank_id] if disclosure_mode == "ATTRIBUTED_INSTITUTIONS" and match_confirmed else [],
        "disclosure_mode": disclosure_mode,
        "sanitized_risk_level": "HIGH" if match_confirmed else "LOW",
        "sanitized_typology_indicators": ["CROSS_BANK_ENTITY_OBSERVED"] if match_confirmed else [],
        "graph_context": {"degree": 1},
        "provenance": {
            "receipt_id": rcp_id,
            "target_bank_id": target_bank_id if disclosure_mode == "ATTRIBUTED_INSTITUTIONS" else None,
            "execution_mode": "VERIFIED_OPERATIONAL",
            "assurance_level": "REVIEWED_ESTABLISHED_PSI_VERIFIED",
        },
        "execution_mode": "VERIFIED_OPERATIONAL",
        "attestation_status": "HARDWARE_ENCLAVE_ATTESTED",
        "is_authoritative": True,
        "is_synthetic": False,
        "assurance_level": "REVIEWED_ESTABLISHED_PSI_VERIFIED",
    }
    content_hash = hashlib.sha256(json.dumps(sig_data, sort_keys=True).encode("utf-8")).hexdigest()

    record = {
        "signal_id": sig_id,
        "signal_data": sig_data,
        "consortium_id": consortium_id,
        "requesting_bank_id": requesting_bank_id,
        "target_bank_id": target_bank_id,
        "query_entity_id": query_entity_id,
        "issued_to_principal": "analyst_a1",
        "receipt_id": rcp_id,
        "content_hash": content_hash,
        "admission_status": "COMMITTED",
        "publication_status": "PUBLISHED",
        "is_authoritative": True,
        "is_synthetic": False,
        "is_attachable": True,
        "execution_mode": "VERIFIED_OPERATIONAL",
        "assurance_level": "REVIEWED_ESTABLISHED_PSI_VERIFIED",
        "disclosure_mode": disclosure_mode,
        "created_at": now.isoformat(),
        "expires_at": (now + datetime.timedelta(hours=24)).isoformat(),
    }
    cbi_svc._signal_store.set(sig_id, record)

    # Register in cryptographic admission store with content hash
    admission_rec = {
        "receipt_id": rcp_id,
        "signal_id": sig_id,
        "requesting_bank_id": requesting_bank_id,
        "target_bank_id": target_bank_id,
        "consortium_id": consortium_id,
        "query_entity_id": query_entity_id,
        "content_hash": content_hash,
        "execution_mode": "VERIFIED_OPERATIONAL",
        "verified_at": now.isoformat(),
    }
    cbi_svc._admitted_receipts_store.set(rcp_id, admission_rec)
    cbi_svc._admitted_receipt_ids.add(rcp_id)

    return sig_id, rcp_id


# ==============================================================================
# Positive Regression Tests (Section 10)
# ==============================================================================


def test_valid_multi_counterparty_case_evidence_assembly(multi_assembly_env: dict[str, Any]):
    """Demonstrate full assembly of eligible signals from Bank B and Bank C into Bank A case."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    # 1. Seed verified signals for Bank B and Bank C
    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b", disclosure_mode="ATTRIBUTED_INSTITUTIONS")
    sig_c, rcp_c = _seed_operational_signal(cbi_svc, target_bank_id="bank_c", disclosure_mode="ANONYMIZED_SUMMARY")

    # 2. Record server-authoritative batch
    query_id = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        execution_outcomes={
            "bank_b": {"status": "SUCCESS"},
            "bank_c": {"status": "SUCCESS"},
        },
        successful_signal_ids={"bank_b": sig_b, "bank_c": sig_c},
        receipt_ids={"bank_b": rcp_b, "bank_c": rcp_c},
        overall_outcome="COMPLETED",
    )

    # 3. Create legitimate Bank A case
    case = case_svc.create_case(title="Multi-Counterparty Fraud Investigation", bank_id="bank_a")

    # 4. Attach multi-counterparty batch to case
    res = cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=query_id,
        case_id=case.id,
        principal=principal_a,
        notes="Authentic cross-bank multi-counterparty investigation evidence",
    )

    # 5. Verify assembly response semantics
    assert res.assembly_status == "COMPLETED"
    assert res.attached_count == 2
    assert res.replayed_count == 0
    assert res.failed_count == 0
    assert len(res.attached_evidence) == 2
    assert len(res.omitted_or_failed) == 0

    item_b = next(i for i in res.attached_evidence if i.signal_id == sig_b)
    item_c = next(i for i in res.attached_evidence if i.signal_id == sig_c)

    # Bank B was attributed: target_bank_id must be exposed
    assert item_b.target_bank_id == "bank_b"
    assert item_b.status == "ATTACHED"
    assert item_b.disclosure_mode == "ATTRIBUTED_INSTITUTIONS"

    # Bank C was anonymous: target_bank_id must NOT be exposed
    assert item_c.target_bank_id is None
    assert item_c.status == "ATTACHED"
    assert item_c.disclosure_mode == "ANONYMIZED_SUMMARY"

    # 6. Verify evidence records on case and file persistence
    evidence_list = cbi_svc.evidence_service.get_case_evidence(case.id)
    assert len(evidence_list) == 2

    for ev in evidence_list:
        ev_file = Path(ev["file_path"])
        assert ev_file.exists()
        persisted = json.loads(ev_file.read_text(encoding="utf-8"))
        assert persisted["is_authoritative"] is True
        assert persisted["batch_query_id"] == query_id

    # 7. Verify ImmutableAuditChain record
    audit_events = ImmutableAuditChain.get_instance().get_snapshot()
    matched = [
        e for e in audit_events
        if e.event_type == "CROSS_BANK_INTELLIGENCE_MULTI_EVIDENCE_ATTACHED"
        and e.details.get("query_id") == query_id
        and e.details.get("case_id") == case.id
    ]
    assert len(matched) >= 1
    assert matched[0].details["attached_count"] == 2
    assert matched[0].details["assembly_status"] == "COMPLETED"


def test_idempotent_batch_assembly_replay(multi_assembly_env: dict[str, Any]):
    """Demonstrate that replaying the identical batch assembly returns existing references with zero duplication."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")
    query_id = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b},
        receipt_ids={"bank_b": rcp_b},
        overall_outcome="COMPLETED",
    )

    case = case_svc.create_case(title="Idempotency Test Case", bank_id="bank_a")

    # First assembly
    res1 = cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=query_id,
        case_id=case.id,
        principal=principal_a,
        idempotency_key="idem_batch_key_001",
        notes="First attempt",
    )
    assert res1.assembly_status == "COMPLETED"
    assert res1.attached_count == 1
    assert res1.replayed_count == 0
    assert res1.is_duplicate_submission is False
    v1 = res1.case_version

    # Replay identical assembly
    res2 = cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=query_id,
        case_id=case.id,
        principal=principal_a,
        idempotency_key="idem_batch_key_001",
        notes="First attempt",
    )
    assert res2.assembly_status == "COMPLETED"
    assert res2.attached_count == 0
    assert res2.replayed_count == 1
    assert res2.is_duplicate_submission is True
    assert res2.case_version == v1
    assert res2.attached_evidence[0].status == "ALREADY_ATTACHED"
    assert res2.attached_evidence[0].evidence_id == res1.attached_evidence[0].evidence_id

    # Verify case evidence count remains strictly 1
    evidence_list = cbi_svc.evidence_service.get_case_evidence(case.id)
    assert len(evidence_list) == 1


def test_legitimate_partial_failure_assembly(multi_assembly_env: dict[str, Any]):
    """Demonstrate that when Bank B succeeds and Bank C fails, Bank B attaches and Bank C contributes NO fake evidence."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    gov_svc: ConsortiumGovernanceService = multi_assembly_env["gov_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    # Register pairwise grant allowing attributed sharing between bank_a and bank_c
    gov_svc.register_operational_data_sharing_grant(
        consortium_id="c_multi_test",
        grant_id="grant_ac_partial_test",
        authority="FFIEC",
        participating_banks=["bank_a", "bank_c"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        institution_jurisdictions={"bank_a": "EU", "bank_c": "EU"},
        allow_institution_identity_sharing=True,
        revision=1,
        approving_bank_id="bank_a",
        auth_token=multi_assembly_env["tokens"]["admin"],
    )

    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")
    query_id = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        execution_outcomes={
            "bank_b": {"status": "SUCCESS"},
            "bank_c": {
                "status": "FAILED",
                "failure": {
                    "error_category": "WORKER_TIMEOUT",
                    "error_message": "Worker node at bank_c timed out after 10.0s.",
                },
            },
        },
        successful_signal_ids={"bank_b": sig_b},
        receipt_ids={"bank_b": rcp_b},
        overall_outcome="PARTIAL_FAILURE",
    )

    case = case_svc.create_case(title="Partial Failure Test Case", bank_id="bank_a")

    res = cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=query_id,
        case_id=case.id,
        principal=principal_a,
    )

    assert res.assembly_status == "PARTIAL"
    assert res.attached_count == 1
    assert res.failed_count == 1
    assert len(res.attached_evidence) == 1
    assert res.attached_evidence[0].signal_id == sig_b

    assert len(res.omitted_or_failed) == 1
    omitted = res.omitted_or_failed[0]
    assert omitted.target_bank_id == "bank_c"
    assert omitted.category in ("WORKER_TIMEOUT", "COUNTERPART_EXECUTION_FAILURE")
    assert "timed out" in omitted.reason

    # Verify only Bank B evidence was created
    ev_list = cbi_svc.evidence_service.get_case_evidence(case.id)
    assert len(ev_list) == 1
    assert ev_list[0]["metadata"]["signal_id"] == sig_b


def test_process_restart_batch_recovery(multi_assembly_env: dict[str, Any]):
    """Demonstrate durable SQLite batch index survives service instance restart."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")
    query_id = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b},
        receipt_ids={"bank_b": rcp_b},
        overall_outcome="COMPLETED",
    )

    case = case_svc.create_case(title="Restart Recovery Case", bank_id="bank_a")

    # Simulate process restart
    DurableCrossBankBatchStore.reset_instance()
    new_batch_store = DurableCrossBankBatchStore.get_instance(db_path=batch_store.db_path)
    new_cbi_svc = CrossBankIntelligenceService(
        consortium_service=multi_assembly_env["gov_svc"],
        case_service=case_svc,
        evidence_service=multi_assembly_env["evidence_svc"],
        entity_service=multi_assembly_env["entity_svc"],
        batch_store=new_batch_store,
    )

    res = new_cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=query_id,
        case_id=case.id,
        principal=principal_a,
    )
    assert res.assembly_status == "COMPLETED"
    assert res.attached_count == 1
    assert res.attached_evidence[0].signal_id == sig_b


def test_disclosure_preservation_notes_policy(multi_assembly_env: dict[str, Any]):
    """Demonstrate anonymous notes policy blocks leaking counterpart identities in multi-batch assembly."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    # Bank C is in anonymous mode
    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b", disclosure_mode="ATTRIBUTED_INSTITUTIONS")
    sig_c, rcp_c = _seed_operational_signal(cbi_svc, target_bank_id="bank_c", disclosure_mode="ANONYMIZED_SUMMARY")

    query_id = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}, "bank_c": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b, "bank_c": sig_c},
        receipt_ids={"bank_b": rcp_b, "bank_c": rcp_c},
        overall_outcome="COMPLETED",
    )

    case = case_svc.create_case(title="Notes Policy Case", bank_id="bank_a")

    # Analyst writes notes identifying bank_c (which is anonymous)
    with pytest.raises(ConsortiumSecurityError) as exc:
        cbi_svc.attach_multi_counterparty_to_case_evidence(
            query_id=query_id,
            case_id=case.id,
            principal=principal_a,
            notes="Observed counterpart bank_c participating in high-value transfer",
        )
    assert "violates anonymity policy" in str(exc.value) or "violate anonymity policy" in str(exc.value)

    # Valid notes without leaking anonymous counterpart should succeed
    res = cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=query_id,
        case_id=case.id,
        principal=principal_a,
        notes="Standard multi-counterparty AML investigation review",
    )
    assert res.assembly_status == "COMPLETED"
    assert res.attached_count == 2


# ==============================================================================
# Adversarial Regression Tests (Section 11)
# ==============================================================================


def test_adversarial_fabricated_query_id_rejected(multi_assembly_env: dict[str, Any]):
    """Fabricated query_id not in server batch index must fail closed."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    case = case_svc.create_case(title="Fabricated Query Case", bank_id="bank_a")

    with pytest.raises(ValueError) as exc:
        cbi_svc.attach_multi_counterparty_to_case_evidence(
            query_id=f"fake_query_{uuid.uuid4().hex}",
            case_id=case.id,
            principal=principal_a,
        )
    assert "not found, has expired, or was never issued" in str(exc.value)


def test_adversarial_cross_tenant_query_reuse_rejected(multi_assembly_env: dict[str, Any]):
    """Bank B investigator attempting to attach Bank A's query batch must be rejected."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_b = multi_assembly_env["principals"]["bank_b"]

    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")
    query_id = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",  # Owned by Bank A
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b},
        receipt_ids={"bank_b": rcp_b},
        overall_outcome="COMPLETED",
    )

    # Bank B owns this case
    case_b = case_svc.create_case(title="Bank B Case", bank_id="bank_b")

    with pytest.raises(PermissionError) as exc:
        cbi_svc.attach_multi_counterparty_to_case_evidence(
            query_id=query_id,
            case_id=case_b.id,
            principal=principal_b,
        )
    assert "Cross-tenant batch violation" in str(exc.value)


def test_adversarial_cross_case_binding_violation_rejected(multi_assembly_env: dict[str, Any]):
    """Batch bound to case_1 cannot be attached to case_2."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    case_1 = case_svc.create_case(title="Bound Case 1", bank_id="bank_a")
    case_2 = case_svc.create_case(title="Unbound Case 2", bank_id="bank_a")

    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")
    query_id = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        case_id=case_1.id,  # Bound strictly to case_1
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b},
        receipt_ids={"bank_b": rcp_b},
        overall_outcome="COMPLETED",
    )

    with pytest.raises(ConsortiumSecurityError) as exc:
        cbi_svc.attach_multi_counterparty_to_case_evidence(
            query_id=query_id,
            case_id=case_2.id,  # Attempting to attach to case_2
            principal=principal_a,
        )
    assert "Cross-case binding violation" in str(exc.value)


def test_adversarial_batch_with_zero_eligible_signals(multi_assembly_env: dict[str, Any]):
    """Batch with all failed counterparts returns assembly_status='FAILED' and zero attached evidence."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    query_id = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        execution_outcomes={
            "bank_b": {"status": "FAILED", "failure": {"error_category": "TRANSPORT_ERROR", "error_message": "Connection refused"}},
            "bank_c": {"status": "FAILED", "failure": {"error_category": "WORKER_UNAVAILABLE", "error_message": "Node offline"}},
        },
        successful_signal_ids={},  # Zero successful signals
        receipt_ids={},
        overall_outcome="FAILED",
    )

    case = case_svc.create_case(title="Zero Signals Case", bank_id="bank_a")

    res = cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=query_id,
        case_id=case.id,
        principal=principal_a,
    )
    assert res.assembly_status == "FAILED"
    assert res.attached_count == 0
    assert res.failed_count == 2
    assert len(res.attached_evidence) == 0
    assert len(res.omitted_or_failed) == 2


def test_adversarial_caller_bank_not_case_owner_rejected(multi_assembly_env: dict[str, Any]):
    """Strict case tenant ownership: Bank B principal cannot modify Bank A case."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    principal_b = multi_assembly_env["principals"]["bank_b"]

    case_a = case_svc.create_case(title="Bank A Case", bank_id="bank_a")

    with pytest.raises(PermissionError) as exc:
        cbi_svc.attach_multi_counterparty_to_case_evidence(
            query_id=f"query_{uuid.uuid4().hex}",
            case_id=case_a.id,
            principal=principal_b,
        )
    assert "Tenant isolation violation" in str(exc.value)


def test_adversarial_terminal_case_rejected(multi_assembly_env: dict[str, Any]):
    """Finalized terminal case rejects multi-counterparty evidence assembly."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")
    query_id = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b},
        receipt_ids={"bank_b": rcp_b},
        overall_outcome="COMPLETED",
    )

    case = case_svc.create_case(title="Terminal Case", bank_id="bank_a")
    from app.application.services.case_service import _case_to_dict
    case.status = CaseStatus.CLOSED_CONFIRMED
    case_svc._cases.set(case.id, _case_to_dict(case))

    with pytest.raises(TerminalCaseImmutableError) as exc:
        cbi_svc.attach_multi_counterparty_to_case_evidence(
            query_id=query_id,
            case_id=case.id,
            principal=principal_a,
        )
    assert "terminal state" in str(exc.value)


def test_adversarial_idempotency_conflicts(multi_assembly_env: dict[str, Any]):
    """Reusing same idempotency key with conflicting query_id or notes must raise conflict."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    sig_b1, rcp_b1 = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")
    sig_b2, rcp_b2 = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")

    q1 = f"batch_{uuid.uuid4().hex}"
    q2 = f"batch_{uuid.uuid4().hex}"

    batch_store.record_batch(
        query_id=q1,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b1},
        receipt_ids={"bank_b": rcp_b1},
        overall_outcome="COMPLETED",
    )
    batch_store.record_batch(
        query_id=q2,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b2},
        receipt_ids={"bank_b": rcp_b2},
        overall_outcome="COMPLETED",
    )

    case = case_svc.create_case(title="Idempotency Conflict Case", bank_id="bank_a")

    # Initial assembly with key
    cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=q1,
        case_id=case.id,
        principal=principal_a,
        idempotency_key="reused_idem_key",
        notes="Notes version 1",
    )

    # Conflict 1: same key, different query_id
    with pytest.raises(CrossBankIdempotencyConflictError) as exc1:
        cbi_svc.attach_multi_counterparty_to_case_evidence(
            query_id=q2,
            case_id=case.id,
            principal=principal_a,
            idempotency_key="reused_idem_key",
            notes="Notes version 1",
        )
    assert "differing query" in str(exc1.value)

    # Conflict 2: same key, same query_id, different notes
    with pytest.raises(CrossBankIdempotencyConflictError) as exc2:
        cbi_svc.attach_multi_counterparty_to_case_evidence(
            query_id=q1,
            case_id=case.id,
            principal=principal_a,
            idempotency_key="reused_idem_key",
            notes="Altered notes version 2",
        )
    assert "differing material batch payload or analyst notes" in str(exc2.value)


def test_adversarial_revoked_grant_at_attachment_time(multi_assembly_env: dict[str, Any]):
    """Demonstrate fail closed when a grant is revoked after query execution but before evidence assembly."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    gov_svc: ConsortiumGovernanceService = multi_assembly_env["gov_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    # Register operational grant for bank_a <-> bank_b
    gov_svc.register_operational_data_sharing_grant(
        consortium_id="c_multi_test",
        grant_id="grant_rev_test",
        authority="FFIEC",
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        revision=1,
        approving_bank_id="bank_a",
        auth_token=multi_assembly_env["tokens"]["admin"],
    )

    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")
    # Bind grant_id into signal
    sig_rec = cbi_svc._signal_store.get(sig_b)
    sig_rec["grant_id"] = "grant_rev_test"
    sig_rec["grant_revision"] = 1
    cbi_svc._signal_store.set(sig_b, sig_rec)

    query_id = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b},
        receipt_ids={"bank_b": rcp_b},
        overall_outcome="COMPLETED",
    )

    # Revoke consent prior to evidence attachment
    gov_svc.revoke_operational_data_sharing_grant(
        consortium_id="c_multi_test",
        grant_id="grant_rev_test",
        revoking_bank_id="bank_b",
        reason="Security breach investigation",
        auth_token=multi_assembly_env["tokens"]["admin_b"],
    )

    case = case_svc.create_case(title="Revoked Grant Case", bank_id="bank_a")

    res = cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=query_id,
        case_id=case.id,
        principal=principal_a,
    )

    # Attachment must fail closed and report omission
    assert res.assembly_status == "FAILED"
    assert res.attached_count == 0
    assert res.failed_count == 1
    assert len(res.omitted_or_failed) == 1
    assert res.omitted_or_failed[0].category == "GOVERNANCE_OR_ADMISSION_REJECTED"
    assert "revoked" in res.omitted_or_failed[0].reason.lower()


# ==============================================================================
# FastAPI Endpoint Integration Tests (Section 9)
# ==============================================================================


def test_fastapi_attach_multi_to_case_routes(multi_assembly_env: dict[str, Any]):
    """Verify HTTP POST /api/v1/intelligence/cross-bank/attach-multi-to-case endpoint."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    token_a = multi_assembly_env["tokens"]["bank_a"]
    token_b = multi_assembly_env["tokens"]["bank_b"]

    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")
    query_id = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=query_id,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b},
        receipt_ids={"bank_b": rcp_b},
        overall_outcome="COMPLETED",
    )

    case = case_svc.create_case(title="FastAPI Route Case", bank_id="bank_a")
    client = TestClient(app)

    # 1. Successful attachment via HTTP
    resp = client.post(
        "/api/v1/intelligence/cross-bank/attach-multi-to-case",
        json={
            "query_id": query_id,
            "case_id": case.id,
            "notes": "HTTP attached multi-evidence",
            "idempotency_key": "http_multi_key_1",
        },
        headers={"Authorization": f"Bearer {token_a}", "X-Tenant-ID": "bank_a"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["assembly_status"] == "COMPLETED"
    assert data["attached_count"] == 1
    assert data["is_duplicate_submission"] is False

    # 2. Replay with identical key returns 200 with is_duplicate_submission=True
    resp_replay = client.post(
        "/api/v1/intelligence/cross-bank/attach-multi-to-case",
        json={
            "query_id": query_id,
            "case_id": case.id,
            "notes": "HTTP attached multi-evidence",
            "idempotency_key": "http_multi_key_1",
        },
        headers={"Authorization": f"Bearer {token_a}", "X-Tenant-ID": "bank_a"},
    )
    assert resp_replay.status_code == 200
    assert resp_replay.json()["is_duplicate_submission"] is True

    # 3. Conflict with differing notes returns 409
    resp_conflict = client.post(
        "/api/v1/intelligence/cross-bank/attach-multi-to-case",
        json={
            "query_id": query_id,
            "case_id": case.id,
            "notes": "DIFFERING NOTES PAYLOAD",
            "idempotency_key": "http_multi_key_1",
        },
        headers={"Authorization": f"Bearer {token_a}", "X-Tenant-ID": "bank_a"},
    )
    assert resp_conflict.status_code == 409

    # 4. Bank B token attempting to attach Bank A case returns 403
    resp_tenant = client.post(
        "/api/v1/intelligence/cross-bank/attach-multi-to-case",
        json={
            "query_id": query_id,
            "case_id": case.id,
        },
        headers={"Authorization": f"Bearer {token_b}", "X-Tenant-ID": "bank_b"},
    )
    assert resp_tenant.status_code == 403

    # 5. Non-existent case returns 404
    resp_404 = client.post(
        "/api/v1/intelligence/cross-bank/attach-multi-to-case",
        json={
            "query_id": query_id,
            "case_id": "nonexistent_case_9999",
        },
        headers={"Authorization": f"Bearer {token_a}", "X-Tenant-ID": "bank_a"},
    )
    assert resp_404.status_code == 404


# ==============================================================================
# Targeted Correction Regression Tests (Corrections A, B, C)
# ==============================================================================


def test_batch_store_immutable_insert_and_conflict_on_mutation(multi_assembly_env: dict[str, Any]):
    """Correction A: DurableCrossBankBatchStore enforces insert-only immutable semantics; conflicting mutations raise BatchStorageConflictError."""
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    qid = f"batch_{uuid.uuid4().hex}"

    # Initial authoritative recording
    batch_store.record_batch(
        query_id=qid,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": "sig_orig"},
        receipt_ids={"bank_b": "rcp_orig"},
        overall_outcome="COMPLETED",
    )

    # Mutation attempt 1: differing signal_ids
    with pytest.raises(BatchStorageConflictError) as exc1:
        batch_store.record_batch(
            query_id=qid,
            requesting_bank_id="bank_a",
            consortium_id="c_multi_test",
            query_entity_id="entity_alpha",
            principal_id="analyst_a1",
            principal_tenant_id="bank_a",
            target_bank_ids=["bank_b"],
            execution_outcomes={"bank_b": {"status": "SUCCESS"}},
            successful_signal_ids={"bank_b": "sig_MUTATED"},
            receipt_ids={"bank_b": "rcp_orig"},
            overall_outcome="COMPLETED",
        )
    assert "Immutable batch record conflict" in str(exc1.value)

    # Mutation attempt 2: differing receipt_ids
    with pytest.raises(BatchStorageConflictError) as exc2:
        batch_store.record_batch(
            query_id=qid,
            requesting_bank_id="bank_a",
            consortium_id="c_multi_test",
            query_entity_id="entity_alpha",
            principal_id="analyst_a1",
            principal_tenant_id="bank_a",
            target_bank_ids=["bank_b"],
            execution_outcomes={"bank_b": {"status": "SUCCESS"}},
            successful_signal_ids={"bank_b": "sig_orig"},
            receipt_ids={"bank_b": "rcp_MUTATED"},
            overall_outcome="COMPLETED",
        )
    assert "receipt_ids" in str(exc2.value)

    # Mutation attempt 3: differing requesting_bank_id
    with pytest.raises(BatchStorageConflictError) as exc3:
        batch_store.record_batch(
            query_id=qid,
            requesting_bank_id="bank_b",  # Differing tenant
            consortium_id="c_multi_test",
            query_entity_id="entity_alpha",
            principal_id="analyst_a1",
            principal_tenant_id="bank_a",
            target_bank_ids=["bank_b"],
            execution_outcomes={"bank_b": {"status": "SUCCESS"}},
            successful_signal_ids={"bank_b": "sig_orig"},
            receipt_ids={"bank_b": "rcp_orig"},
            overall_outcome="COMPLETED",
        )
    assert "requesting_bank_id" in str(exc3.value)


def test_batch_store_idempotent_replay_preserves_immutable_record(multi_assembly_env: dict[str, Any]):
    """Correction A: Identical batch recording replay succeeds idempotently and preserves existing record."""
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    qid = f"batch_{uuid.uuid4().hex}"

    batch_store.record_batch(
        query_id=qid,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": "sig_orig"},
        receipt_ids={"bank_b": "rcp_orig"},
        overall_outcome="COMPLETED",
    )

    rec1 = batch_store.get_batch(qid)
    assert rec1 is not None

    # Identical replay
    batch_store.record_batch(
        query_id=qid,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": "sig_orig"},
        receipt_ids={"bank_b": "rcp_orig"},
        overall_outcome="COMPLETED",
    )

    rec2 = batch_store.get_batch(qid)
    assert rec2 is not None
    assert rec1["created_at"] == rec2["created_at"]
    assert rec1["query_id"] == rec2["query_id"]


def test_batch_store_single_case_binding_and_conflict(multi_assembly_env: dict[str, Any]):
    """Correction B: bind_case() establishes atomic single-case binding; conflicting bindings raise BatchCaseBindingConflictError."""
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    qid = f"batch_{uuid.uuid4().hex}"

    # Record un-bound batch
    batch_store.record_batch(
        query_id=qid,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": "sig_b"},
        receipt_ids={"bank_b": "rcp_b"},
        overall_outcome="COMPLETED",
    )

    # First binding to case_1
    bound = batch_store.bind_case(query_id=qid, case_id="case_1")
    assert bound == "case_1"

    # Replay binding to case_1 succeeds idempotently
    bound_replay = batch_store.bind_case(query_id=qid, case_id="case_1")
    assert bound_replay == "case_1"

    # Competing binding to case_2 must fail closed
    with pytest.raises(BatchCaseBindingConflictError) as exc:
        batch_store.bind_case(query_id=qid, case_id="case_2")
    assert "Cross-case binding violation" in str(exc.value)


def test_batch_assembly_exact_receipt_binding_mismatch_fails_closed(multi_assembly_env: dict[str, Any]):
    """Correction A: Assembly checks exact receipt binding against _admitted_receipts_store; mismatch is rejected without attaching fake evidence."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")
    qid = f"batch_{uuid.uuid4().hex}"

    # Batch claims receipt is 'rcp_mismatched_unadmitted'
    batch_store.record_batch(
        query_id=qid,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b},
        receipt_ids={"bank_b": "rcp_mismatched_unadmitted"},
        overall_outcome="COMPLETED",
    )

    case = case_svc.create_case(title="Receipt Mismatch Test Case", bank_id="bank_a")

    res = cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=qid,
        case_id=case.id,
        principal=principal_a,
    )

    assert res.assembly_status == "FAILED"
    assert res.attached_count == 0
    assert res.failed_count == 1
    assert len(res.attached_evidence) == 0
    assert len(res.omitted_or_failed) == 1
    assert res.omitted_or_failed[0].category == "GOVERNANCE_OR_ADMISSION_REJECTED"
    assert "cryptographic" in res.omitted_or_failed[0].reason.lower() or "admission" in res.omitted_or_failed[0].reason.lower()

    # Zero evidence persisted on case
    ev_list = cbi_svc.evidence_service.get_case_evidence(case.id)
    assert len(ev_list) == 0


def test_batch_assembly_unbound_batch_first_case_binding_and_subsequent_conflict(multi_assembly_env: dict[str, Any]):
    """Correction B: Unbound batch binds atomically to first case; attaching same batch to second case raises 409 / ConsortiumSecurityError."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    sig_b, rcp_b = _seed_operational_signal(cbi_svc, target_bank_id="bank_b")
    qid = f"batch_{uuid.uuid4().hex}"

    batch_store.record_batch(
        query_id=qid,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_b"],
        execution_outcomes={"bank_b": {"status": "SUCCESS"}},
        successful_signal_ids={"bank_b": sig_b},
        receipt_ids={"bank_b": rcp_b},
        overall_outcome="COMPLETED",
    )

    case_1 = case_svc.create_case(title="Destination Case 1", bank_id="bank_a")
    case_2 = case_svc.create_case(title="Destination Case 2", bank_id="bank_a")

    # Attaching to Case 1 succeeds
    res1 = cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=qid,
        case_id=case_1.id,
        principal=principal_a,
    )
    assert res1.assembly_status == "COMPLETED"
    assert res1.attached_count == 1

    # Attaching same batch to Case 2 raises ConsortiumSecurityError (which maps to 409 HTTP Conflict)
    with pytest.raises(ConsortiumSecurityError) as exc:
        cbi_svc.attach_multi_counterparty_to_case_evidence(
            query_id=qid,
            case_id=case_2.id,
            principal=principal_a,
        )
    assert "Cross-case binding violation" in str(exc.value)

    # Case 2 remains unmodified
    ev_case_2 = cbi_svc.evidence_service.get_case_evidence(case_2.id)
    assert len(ev_case_2) == 0


def test_batch_assembly_disclosure_safe_failed_outcomes_anonymity_preservation(multi_assembly_env: dict[str, Any]):
    """Correction C: Anonymous failed counterpart returns target_bank_id=None, signal_id=None, and sanitized domain reason without leaking counterpart name or internal error text."""
    cbi_svc: CrossBankIntelligenceService = multi_assembly_env["cbi_svc"]
    case_svc: CaseManagementService = multi_assembly_env["case_svc"]
    batch_store: DurableCrossBankBatchStore = multi_assembly_env["batch_store"]
    principal_a = multi_assembly_env["principals"]["bank_a"]

    qid = f"batch_{uuid.uuid4().hex}"
    batch_store.record_batch(
        query_id=qid,
        requesting_bank_id="bank_a",
        consortium_id="c_multi_test",
        query_entity_id="entity_alpha",
        principal_id="analyst_a1",
        principal_tenant_id="bank_a",
        target_bank_ids=["bank_c"],  # Bank C has NO attributed grant (strictly anonymous)
        execution_outcomes={
            "bank_c": {
                "status": "FAILED",
                "failure": {
                    "error_category": "WORKER_TIMEOUT",
                    "error_message": "Worker node secret_worker_42 at bank_c timed out on private PSI socket.",
                },
            },
        },
        successful_signal_ids={},
        receipt_ids={},
        overall_outcome="FAILED",
    )

    case = case_svc.create_case(title="Anonymous Failure Test Case", bank_id="bank_a")

    res = cbi_svc.attach_multi_counterparty_to_case_evidence(
        query_id=qid,
        case_id=case.id,
        principal=principal_a,
    )

    assert res.assembly_status == "FAILED"
    assert len(res.omitted_or_failed) == 1
    fail = res.omitted_or_failed[0]

    # Confidentiality guarantees: target_bank_id and signal_id are strictly None
    assert fail.target_bank_id is None
    assert fail.signal_id is None

    # Categorization and reason sanitization
    assert fail.category in ("WORKER_TIMEOUT", "COUNTERPART_TIMEOUT")
    assert fail.reason == "Counterpart execution timed out."
    assert "bank_c" not in fail.reason
    assert "secret_worker_42" not in fail.reason
    assert "PSI" not in fail.reason
