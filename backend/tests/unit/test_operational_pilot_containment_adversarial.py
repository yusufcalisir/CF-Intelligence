"""Comprehensive Operational Pilot Containment & Adversarial Acceptance Test Suite.

Milestone 5 Operational Acceptance Verification:
1. P0 — Paused-Pilot Omission Bypass Elimination:
   - Durable execution-scope binding ensures non-active pilots remain discoverable.
   - Omitting pilot_id rejects operations when governing pilot is PAUSED, ABORTED, COMPLETED, expired, or quota-exhausted.
   - Non-pilot M4 operations for uncontained scopes remain authorized under their own rules.
2. P0 — Signal-Persistence / Reservation-Commit Race Elimination:
   - Reliable result admission state transition (PENDING_COMMIT -> COMMITTED / REJECTED).
   - Signal cannot become authoritative or attachable before execution reservation commit.
   - Mid-flight pause or database failure terminates reservation as ABORTED and marks signal REJECTED.
   - Evidence attachment verifies reservation is COMMITTED with matching signal_id and active governing pilot.
   - Resuming pilot does not revive earlier rejected signals.
3. P1 — Real, Authenticated Infrastructure Preflight:
   - Mandatory worker and coordinator topology for real execution.
   - Missing topology, wrong bank ID, non-coordinator role, SSRF metadata targets, and stopped workers fail closed.
   - Preflight revalidation enforced before RESUME.
4. P1 — Exact Approved Grant Revision Binding:
   - Rejection when grant revision, scope digest, allowed institutions, purpose, or jurisdiction changes without fresh approval.
   - Rejection when grant is revoked during execution.
"""

import hashlib
import hmac
import json
import threading
import urllib.parse
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.application.services.case_service import _case_to_dict
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
)
from app.application.services.cross_bank_intelligence import (
    CrossBankIntelligenceService,
)
from app.application.services.isolated_matching_service import (
    CrossBankMatchingCoordinator,
    InstitutionalCoordinatorDaemon,
    InstitutionalWorkerDaemon,
    IsolatedBankWorkerNode,
)
from app.application.services.pilot_operations_service import (
    PilotExpiredError,
    PilotOperationsService,
    PilotQuotaExceededError,
)
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    MemberRole,
)
from app.domain.institutional_identity import (
    InstitutionalSigningIdentity,
    canonical_coordinator_readiness_bytes,
)
from app.domain.investigation_entities import Case
from app.domain.pilot_operations import (
    PilotEntity,
    PilotGovernanceError,
    PilotStatus,
    canonical_pilot_approval_bytes,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.infrastructure.storage.durable_pilot_store import DurablePilotStore

SHARED_JWT_SECRET = "jwt_secret_adversarial_containment_91823749812734"
SHARED_CONSORTIUM_STORE_SECRET = "consortium_store_hmac_adversarial_98712398471"
SHARED_COORDINATOR_SECRET = "coordinator_secret_adversarial_containment_99887766"


@pytest.fixture
def containment_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, Any], None, None]:
    consortium_store_file = tmp_path / "containment_consortium_store.json"
    account_store_file = tmp_path / "containment_account_store.json"
    pilot_db_file = tmp_path / "containment_pilot_operations.db"

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(consortium_store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_store_file))
    monkeypatch.setenv("CFI_PILOT_DB_PATH", str(pilot_db_file))
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", SHARED_CONSORTIUM_STORE_SECRET)
    monkeypatch.setenv("CFI_COORDINATOR_SECRET", SHARED_COORDINATOR_SECRET)
    monkeypatch.setenv("JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_OIDC_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_IDENTITY_STORE_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", SHARED_JWT_SECRET)

    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurablePilotStore._instance = None
    PilotOperationsService._instance = None
    CrossBankIntelligenceService._instance = None

    auth_svc = AuthenticationService.get_instance()
    u_op = auth_svc.register_user(
        username="op_user",
        plain_password="Pass123!",
        bank_id="consortium",
        roles=["admin", "security_officer", "operator"],
        clearance_level=3,
    )
    u_a = auth_svc.register_user(
        username="bank_a_user",
        plain_password="Pass123!",
        bank_id="bank_a",
        roles=["admin", "compliance_officer"],
        clearance_level=3,
    )
    u_b = auth_svc.register_user(
        username="bank_b_user",
        plain_password="Pass123!",
        bank_id="bank_b",
        roles=["admin", "compliance_officer"],
        clearance_level=3,
    )

    token_op = auth_svc.create_token_bundle(u_op).access_token
    token_a = auth_svc.create_token_bundle(u_a).access_token
    token_b = auth_svc.create_token_bundle(u_b).access_token

    principal_op = AuthenticatedPrincipal(
        principal_id="op_user",
        tenant_id="consortium",
        roles=["admin", "security_officer", "operator"],
        is_authenticated=True,
        auth_method="jwt",
    )
    principal_a = AuthenticatedPrincipal(
        principal_id="bank_a_user",
        tenant_id="bank_a",
        roles=["admin", "compliance_officer"],
        is_authenticated=True,
        auth_method="jwt",
    )
    principal_b = AuthenticatedPrincipal(
        principal_id="bank_b_user",
        tenant_id="bank_b",
        roles=["admin", "compliance_officer"],
        is_authenticated=True,
        auth_method="jwt",
    )

    gov_svc = ConsortiumGovernanceService()
    gov_svc.create_consortium(
        consortium_id="c_containment",
        name="Containment Verification Consortium",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.add_member(
        consortium_id="c_containment",
        bank_id="bank_b",
        role=MemberRole.FULL_MEMBER,
        auth_token=token_a,
    )
    gov_svc.register_operational_data_sharing_grant(
        consortium_id="c_containment",
        grant_id="grant_c_containment",
        authority="FFIEC",
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        revision=1,
        approving_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.approve_operational_data_sharing_grant(
        consortium_id="c_containment",
        grant_id="grant_c_containment",
        approving_bank_id="bank_b",
        auth_token=token_b,
    )

    pilot_store = DurablePilotStore(str(pilot_db_file))
    pilot_svc = PilotOperationsService(pilot_store=pilot_store, consortium_service=gov_svc)
    id_a = InstitutionalSigningIdentity.generate("bank_a")
    id_b = InstitutionalSigningIdentity.generate("bank_b")
    pilot_svc.institutional_registry.register_public_key("bank_a", id_a.public_key)
    pilot_svc.institutional_registry.register_public_key("bank_b", id_b.public_key)
    PilotOperationsService._instance = pilot_svc

    # Start live test daemons on ephemeral ports for authentic preflight verification
    node_a = IsolatedBankWorkerNode("bank_a", storage_root=str(tmp_path / "bank_a_vault"), signing_key=id_a)
    node_b = IsolatedBankWorkerNode("bank_b", storage_root=str(tmp_path / "bank_b_vault"), signing_key=id_b)
    daemon_a = InstitutionalWorkerDaemon(node_a, host="127.0.0.1", port=0, run_as_process=False)
    daemon_b = InstitutionalWorkerDaemon(node_b, host="127.0.0.1", port=0, run_as_process=False)
    url_a = daemon_a.start()
    url_b = daemon_b.start()
    coord_obj = CrossBankMatchingCoordinator()
    coord_daemon = InstitutionalCoordinatorDaemon(
        coord_obj, host="127.0.0.1", port=0, coordinator_secret=SHARED_COORDINATOR_SECRET
    )
    url_coord = coord_daemon.start()

    test_topology = {
        "bank_a_worker_url": url_a,
        "bank_b_worker_url": url_b,
        "coordinator_url": url_coord,
    }

    try:
        yield {
            "pilot_svc": pilot_svc,
            "gov_svc": gov_svc,
            "pilot_db_file": pilot_db_file,
            "id_a": id_a,
            "id_b": id_b,
            "principal_op": principal_op,
            "principal_a": principal_a,
            "principal_b": principal_b,
            "token_op": token_op,
            "token_a": token_a,
            "token_b": token_b,
            "test_topology": test_topology,
        }
    finally:
        daemon_a.stop()
        daemon_b.stop()
        coord_daemon.stop()


def _register_test_case(case_svc: Any, case_id: str = "case_01", bank_id: str = "bank_a") -> None:
    c = Case(id=case_id, title=f"Case {case_id}", bank_id=bank_id)
    case_svc._cases.set(case_id, _case_to_dict(c))


def _create_and_activate_pilot(
    env: dict[str, Any],
    pilot_id: str = "pilot_test_01",
    quota_total: int = 10,
    quota_per_inst: int = 5,
    expires_at: str | None = None,
) -> PilotEntity:
    svc: PilotOperationsService = env["pilot_svc"]
    if not expires_at:
        expires_at = (datetime.now(UTC) + timedelta(hours=24)).isoformat()

    pilot = svc.provision_pilot(
        pilot_id=pilot_id,
        consortium_id="c_containment",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_datasets={"fraud_signals": "sha256_mock_hash"},
        query_quota_total=quota_total,
        query_quota_per_institution=quota_per_inst,
        expires_at=expires_at,
        created_by="op_user",
        principal=env["principal_op"],
        topology=dict(env.get("test_topology") or {}),
    )

    # Run preflight
    svc.run_preflight(pilot_id)

    # 3-party approvals
    sig_a = env["id_a"].sign(canonical_pilot_approval_bytes(
        pilot_id=pilot_id,
        revision=pilot.config.revision,
        scope_digest=pilot.scope_digest,
        approver_institution="bank_a",
        approver_id="bank_a_user",
    ))
    svc.approve_pilot(
        pilot_id=pilot_id,
        approving_institution="bank_a",
        principal=env["principal_a"],
        signature=sig_a,
    )

    sig_b = env["id_b"].sign(canonical_pilot_approval_bytes(
        pilot_id=pilot_id,
        revision=pilot.config.revision,
        scope_digest=pilot.scope_digest,
        approver_institution="bank_b",
        approver_id="bank_b_user",
    ))
    svc.approve_pilot(
        pilot_id=pilot_id,
        approving_institution="bank_b",
        principal=env["principal_b"],
        signature=sig_b,
    )

    svc.approve_pilot(
        pilot_id=pilot_id,
        approving_institution="consortium",
        principal=env["principal_op"],
    )

    return svc.activate_pilot(pilot_id=pilot_id)


# ── Defect 1: P0 — Paused-Pilot Omission Bypass ────────────────────────────────


def test_omission_bypass_rejected_when_pilot_paused(containment_env: dict[str, Any]) -> None:
    """When a governing pilot is PAUSED, omitting pilot_id must NOT escape containment."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    pilot = _create_and_activate_pilot(containment_env, pilot_id="pilot_pause_bypass")
    assert pilot.status == PilotStatus.ACTIVE

    # 1. Authoritative pilot query with or without pilot_id succeeds when ACTIVE
    p_found = svc.find_active_or_scoped_pilot(
        requesting_bank="bank_a",
        target_bank="bank_b",
        purpose="AML_INVESTIGATION",
        consortium_id="c_containment",
    )
    assert p_found is not None
    assert p_found.config.pilot_id == "pilot_pause_bypass"

    # 2. Kill switch triggered: PAUSE pilot
    svc.pause_pilot(
        pilot_id="pilot_pause_bypass",
        principal=containment_env["principal_op"],
        reason="Investigating anomaly",
    )

    # 3. Attacker resubmits query WITHOUT pilot_id, attempting to bypass kill switch
    # find_active_or_scoped_pilot MUST still return the governing pilot
    scoped_pilot = svc.find_active_or_scoped_pilot(
        requesting_bank="bank_a",
        target_bank="bank_b",
        purpose="AML_INVESTIGATION",
        consortium_id="c_containment",
    )
    assert scoped_pilot is not None
    assert scoped_pilot.config.pilot_id == "pilot_pause_bypass"
    assert scoped_pilot.status == PilotStatus.PAUSED

    # 4. validate_pilot_query MUST fail closed with PilotGovernanceError even with pilot_id=None
    with pytest.raises(PilotGovernanceError) as exc:
        svc.validate_pilot_query(
            pilot_id=None,
            requesting_bank="bank_a",
            target_bank="bank_b",
            purpose="AML_INVESTIGATION",
            consortium_id="c_containment",
        )
    assert "kill switch activated" in str(exc.value).lower() or "paused" in str(exc.value).lower()


@pytest.mark.parametrize("terminal_status", [PilotStatus.ABORTED, PilotStatus.COMPLETED])
def test_omission_bypass_rejected_in_terminal_states(
    containment_env: dict[str, Any], terminal_status: PilotStatus
) -> None:
    """When a governing pilot is in terminal state, omitting pilot_id must NOT escape containment."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    pid = f"pilot_term_{terminal_status.value}"
    _create_and_activate_pilot(containment_env, pilot_id=pid)

    if terminal_status == PilotStatus.ABORTED:
        svc.abort_pilot(pilot_id=pid, principal=containment_env["principal_op"], reason="Security breach")
    else:
        # Mark completed in store
        p = svc.get_pilot(pid)
        assert p is not None
        p.status = PilotStatus.COMPLETED
        svc.store.save_pilot(p)

    with pytest.raises(PilotGovernanceError) as exc:
        svc.validate_pilot_query(
            pilot_id=None,
            requesting_bank="bank_a",
            target_bank="bank_b",
            purpose="AML_INVESTIGATION",
            consortium_id="c_containment",
        )
    assert terminal_status.value.lower() in str(exc.value).lower()


def test_omission_bypass_rejected_when_pilot_expired(containment_env: dict[str, Any]) -> None:
    """When governing pilot has expired, omitting pilot_id must fail closed with PilotExpiredError."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    past_iso = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    _create_and_activate_pilot(containment_env, pilot_id="pilot_expired_bypass", expires_at=past_iso)

    with pytest.raises(PilotExpiredError) as exc:
        svc.validate_pilot_query(
            pilot_id=None,
            requesting_bank="bank_a",
            target_bank="bank_b",
            purpose="AML_INVESTIGATION",
            consortium_id="c_containment",
        )
    assert "expired" in str(exc.value).lower()


def test_omission_bypass_rejected_when_quota_exhausted(containment_env: dict[str, Any]) -> None:
    """When governing pilot query quota is exhausted, omitting pilot_id fails closed."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    _create_and_activate_pilot(containment_env, pilot_id="pilot_quota_bypass", quota_total=1)

    # Reserve the 1 slot
    _, res_id = svc.reserve_query_slot("pilot_quota_bypass", "bank_a", "bank_b", "AML_INVESTIGATION")
    svc.commit_query_reservation(res_id, "pilot_quota_bypass", "rcp_1", 10.0, True)

    with pytest.raises(PilotQuotaExceededError) as exc:
        svc.validate_pilot_query(
            pilot_id=None,
            requesting_bank="bank_a",
            target_bank="bank_b",
            purpose="AML_INVESTIGATION",
            consortium_id="c_containment",
        )
    assert "quota exhausted" in str(exc.value).lower()


def test_separate_authorized_non_pilot_operations_preserved(containment_env: dict[str, Any]) -> None:
    """A truly separate query for an un-contained institution pair returns None and is not falsely contained."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    _create_and_activate_pilot(containment_env, pilot_id="pilot_contained_pair")

    # bank_x and bank_y have no governing pilot
    uncontained = svc.find_active_or_scoped_pilot(
        requesting_bank="bank_x",
        target_bank="bank_y",
        purpose="AML_INVESTIGATION",
        consortium_id="c_other",
    )
    assert uncontained is None


# ── Defect 2: P0 — Signal-Persistence / Reservation-Commit Race ───────────────


def test_admission_race_midflight_pause_fails_closed(containment_env: dict[str, Any]) -> None:
    """When kill switch triggers between PSI evaluation and reservation commit:

    - Commit is rejected (PilotGovernanceError).
    - Reservation status transitions to ABORTED.
    - Signal cannot become authoritative or attachable.
    - Attaching signal to case evidence is rejected.
    - Resuming pilot does NOT make earlier rejected signal attachable.
    - A fresh query after resume succeeds.
    """
    svc: PilotOperationsService = containment_env["pilot_svc"]
    _create_and_activate_pilot(containment_env, pilot_id="pilot_race_test")
    intel_svc = CrossBankIntelligenceService.get_instance()

    # 1. Start execution: reserve execution slot
    _, res_id = svc.reserve_query_slot(
        pilot_id="pilot_race_test",
        requesting_bank="bank_a",
        target_bank="bank_b",
        purpose="AML_INVESTIGATION",
    )

    # 2. PSI evaluates result, creates provisional signal record
    sig_id = f"sig_race_{uuid.uuid4().hex[:12]}"
    provisional_record = {
        "signal_id": sig_id,
        "consortium_id": "c_containment",
        "requesting_bank_id": "bank_a",
        "target_bank_id": "bank_b",
        "purpose": "AML_INVESTIGATION",
        "query_entity_id": "ent_race_123",
        "issued_to_principal": "analyst_a1",
        "pilot_id": "pilot_race_test",
        "reservation_id": res_id,
        "admission_status": "PENDING_COMMIT",
        "is_authoritative": False,
        "is_attachable": False,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
    }
    intel_svc._signal_store.set(sig_id, provisional_record)

    # 3. Kill switch fires right before commit!
    svc.pause_pilot(
        pilot_id="pilot_race_test",
        principal=containment_env["principal_op"],
        reason="Emergency containment mid-flight",
    )

    # 4. Durable commit must fail closed
    with pytest.raises(PilotGovernanceError) as exc_commit:
        svc.commit_query_reservation(
            reservation_id=res_id,
            pilot_id="pilot_race_test",
            receipt_id="rec_race_01",
            latency_ms=15.0,
            match_confirmed=True,
            signal_id=sig_id,
        )
    assert "kill switch activated" in str(exc_commit.value).lower()

    # 5. Check reservation in store: ABORTED
    durable_res = svc.store.get_query_reservation(res_id)
    assert durable_res is not None
    assert durable_res["status"] == "ABORTED"

    # Mark signal REJECTED on commit failure
    provisional_record["admission_status"] = "REJECTED"
    provisional_record["is_authoritative"] = False
    provisional_record["is_attachable"] = False
    intel_svc._signal_store.set(sig_id, provisional_record)

    # 6. Evidence attachment must reject rejected/uncommitted signal
    _register_test_case(intel_svc.case_service, "case_race_01", "bank_a")
    with pytest.raises(ConsortiumSecurityError) as exc_attach:
        intel_svc.attach_signal_to_case_evidence(
            case_id="case_race_01",
            signal_id=sig_id,
            principal=containment_env["principal_a"],
        )
    assert "rejected" in str(exc_attach.value).lower() or "not committed" in str(exc_attach.value).lower()

    # 7. Resume the pilot: previous rejected signal must NOT become attachable
    svc.resume_pilot(pilot_id="pilot_race_test", principal=containment_env["principal_op"])
    resumed_pilot = svc.get_pilot("pilot_race_test")
    assert resumed_pilot is not None
    assert resumed_pilot.status == PilotStatus.ACTIVE

    with pytest.raises(ConsortiumSecurityError):
        intel_svc.attach_signal_to_case_evidence(
            case_id="case_race_01",
            signal_id=sig_id,
            principal=containment_env["principal_a"],
        )

    # 8. Fresh operational query can now succeed cleanly
    _, fresh_res_id = svc.reserve_query_slot(
        pilot_id="pilot_race_test",
        requesting_bank="bank_a",
        target_bank="bank_b",
        purpose="AML_INVESTIGATION",
    )
    fresh_sig_id = f"sig_fresh_{uuid.uuid4().hex[:12]}"
    svc.commit_query_reservation(
        reservation_id=fresh_res_id,
        pilot_id="pilot_race_test",
        receipt_id="rec_fresh_01",
        latency_ms=20.0,
        match_confirmed=True,
        signal_id=fresh_sig_id,
    )
    fresh_res = svc.store.get_query_reservation(fresh_res_id)
    assert fresh_res is not None
    assert fresh_res["status"] == "COMMITTED"
    assert fresh_res["signal_id"] == fresh_sig_id


def test_attachment_verifies_durable_reservation_and_signal_binding(containment_env: dict[str, Any]) -> None:
    """Evidence attachment fails closed if signal lacks reservation or binding is tampered."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    _create_and_activate_pilot(containment_env, pilot_id="pilot_binding_test")
    intel_svc = CrossBankIntelligenceService.get_instance()

    # Mismatched signal_id in reservation
    _, res_id = svc.reserve_query_slot("pilot_binding_test", "bank_a", "bank_b", "AML_INVESTIGATION")
    svc.commit_query_reservation(res_id, "pilot_binding_test", "rcp_ok", 10.0, True, signal_id="legitimate_sig")

    tampered_sig_id = "attacker_forged_sig"
    tampered_record = {
        "signal_id": tampered_sig_id,
        "consortium_id": "c_containment",
        "requesting_bank_id": "bank_a",
        "target_bank_id": "bank_b",
        "purpose": "AML_INVESTIGATION",
        "query_entity_id": "ent_123",
        "issued_to_principal": "analyst_a1",
        "pilot_id": "pilot_binding_test",
        "reservation_id": res_id,
        "admission_status": "COMMITTED",
        "publication_status": "PUBLISHED",
        "is_authoritative": True,
        "is_attachable": True,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
    }
    intel_svc._signal_store.set(tampered_sig_id, tampered_record)

    # 1. While reservation status is COMMITTED (not yet PUBLISHED): rejected
    _register_test_case(intel_svc.case_service, "case_01", "bank_a")
    with pytest.raises(ConsortiumSecurityError) as exc_unpub:
        intel_svc.attach_signal_to_case_evidence(
            case_id="case_01",
            signal_id=tampered_sig_id,
            principal=containment_env["principal_a"],
        )
    assert "not published" in str(exc_unpub.value).lower()

    # 2. Authorize publication for legitimate_sig
    svc.authorize_and_record_signal_publication(res_id, "pilot_binding_test", "legitimate_sig", "rcp_ok")

    # 3. Now reservation is PUBLISHED, but bound to legitimate_sig, so tampered_sig_id is rejected for mismatch
    with pytest.raises(ConsortiumSecurityError) as exc:
        intel_svc.attach_signal_to_case_evidence(
            case_id="case_01",
            signal_id=tampered_sig_id,
            principal=containment_env["principal_a"],
        )
    assert "does not match" in str(exc.value).lower()


# ── Defect 3: P1 — Real Authenticated Infrastructure Preflight ────────────────


def test_preflight_requires_mandatory_topology(containment_env: dict[str, Any]) -> None:
    """Real bank execution pilot fails preflight if worker or coordinator topology is missing."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    p = svc.provision_pilot(
        pilot_id="pilot_preflight_topo",
        consortium_id="c_containment",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_datasets={"data": "h1"},
        query_quota_total=5,
        query_quota_per_institution=3,
        expires_at=(datetime.now(UTC) + timedelta(hours=10)).isoformat(),
        created_by="op_user",
        principal=containment_env["principal_op"],
        worker_endpoints={"bank_a": "http://127.0.0.1:8001"},  # bank_b missing!
        coordinator_endpoint="http://127.0.0.1:8000",
    )

    with pytest.raises(PilotGovernanceError) as exc:
        svc.run_preflight(p.config.pilot_id, principal=containment_env["principal_op"])
    assert "topology" in str(exc.value).lower() or "missing" in str(exc.value).lower()


def test_preflight_rejects_ssrf_and_cloud_metadata(containment_env: dict[str, Any]) -> None:
    """Preflight fails closed and rejects SSRF destinations (e.g. AWS/GCP metadata service)."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    p = svc.provision_pilot(
        pilot_id="pilot_ssrf_test",
        consortium_id="c_containment",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_datasets={"data": "h1"},
        query_quota_total=5,
        query_quota_per_institution=3,
        expires_at=(datetime.now(UTC) + timedelta(hours=10)).isoformat(),
        created_by="op_user",
        principal=containment_env["principal_op"],
        worker_endpoints={
            "bank_a": "http://169.254.169.254/latest/meta-data",
            "bank_b": "http://127.0.0.1:8002",
        },
        coordinator_endpoint="http://127.0.0.1:8000",
    )

    with pytest.raises(PilotGovernanceError) as exc:
        svc.run_preflight(p.config.pilot_id, principal=containment_env["principal_op"])
    assert "ssrf" in str(exc.value).lower() or "prohibited" in str(exc.value).lower() or "preflight failed" in str(exc.value).lower()


def test_preflight_revalidated_before_resume(containment_env: dict[str, Any]) -> None:
    """When resuming a paused pilot, preflight must revalidate all topology and fail closed if corrupted."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    p = _create_and_activate_pilot(containment_env, pilot_id="pilot_resume_preflight")
    svc.pause_pilot(p.config.pilot_id, principal=containment_env["principal_op"])

    # Corrupt pilot topology before resume
    p_paused = svc.get_pilot(p.config.pilot_id)
    assert p_paused is not None
    p_paused.config.worker_endpoints = {"bank_a": "http://169.254.169.254/exploit"}
    svc.store.save_pilot(p_paused)

    with pytest.raises(PilotGovernanceError) as exc:
        svc.resume_pilot(p.config.pilot_id, principal=containment_env["principal_op"])
    assert "preflight" in str(exc.value).lower() or "ssrf" in str(exc.value).lower()


# ── Defect 5: P1 — Exact Approved Grant Revision Binding ──────────────────────


def test_grant_revision_change_invalidates_active_pilot(containment_env: dict[str, Any]) -> None:
    """When grant revision increments without new pilot approval, queries fail closed."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    gov: ConsortiumGovernanceService = containment_env["gov_svc"]
    pilot = _create_and_activate_pilot(containment_env, pilot_id="pilot_grant_rev_test")
    assert pilot.grant_revision == 1

    # Mutate grant revision in consortium governance
    c = gov.get_consortium("c_containment")
    assert c is not None
    grant = c.metadata["operational_data_sharing_grant"]
    grant["revision"] = 2
    gov.store.save_consortium(c)

    # validate_pilot_query must detect revision mismatch and reject
    with pytest.raises(PilotGovernanceError) as exc:
        svc.validate_pilot_query(
            pilot_id="pilot_grant_rev_test",
            requesting_bank="bank_a",
            target_bank="bank_b",
            purpose="AML_INVESTIGATION",
            consortium_id="c_containment",
        )
    assert "revision mismatch" in str(exc.value).lower() or "grant revision" in str(exc.value).lower()


def test_grant_institution_removal_rejects_query(containment_env: dict[str, Any]) -> None:
    """When a participating bank is removed from the grant, active pilot query is rejected."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    gov: ConsortiumGovernanceService = containment_env["gov_svc"]
    _create_and_activate_pilot(containment_env, pilot_id="pilot_grant_inst_test")

    # Remove bank_b from participating banks in grant
    c = gov.get_consortium("c_containment")
    assert c is not None
    grant = c.metadata["operational_data_sharing_grant"]
    grant["participating_banks"] = ["bank_a"]  # bank_b removed!
    gov.store.save_consortium(c)

    with pytest.raises(PilotGovernanceError) as exc:
        svc.validate_pilot_query(
            pilot_id="pilot_grant_inst_test",
            requesting_bank="bank_a",
            target_bank="bank_b",
            purpose="AML_INVESTIGATION",
            consortium_id="c_containment",
        )
    assert "grant" in str(exc.value).lower()


def test_grant_revocation_during_execution_fails_closed(containment_env: dict[str, Any]) -> None:
    """When grant is revoked, active pilot query validation fails closed immediately."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    gov: ConsortiumGovernanceService = containment_env["gov_svc"]
    _create_and_activate_pilot(containment_env, pilot_id="pilot_grant_revoked_test")

    # Revoke grant
    gov.revoke_operational_data_sharing_grant(
        consortium_id="c_containment",
        grant_id="grant_c_containment",
        revoking_bank_id="bank_b",
        reason="Compliance audit failure",
        auth_token=containment_env["token_b"],
    )

    with pytest.raises(PilotGovernanceError) as exc:
        svc.validate_pilot_query(
            pilot_id="pilot_grant_revoked_test",
            requesting_bank="bank_a",
            target_bank="bank_b",
            purpose="AML_INVESTIGATION",
            consortium_id="c_containment",
        )
    assert "inactive or revoked" in str(exc.value).lower() or "revoked" in str(exc.value).lower()


def test_mandatory_negative_activation_prevented_without_topology(containment_env: dict[str, Any]) -> None:
    """Mandatory negative test: Provision pilot without worker/coordinator endpoints.
    Collect valid institutional approvals and attempt activation.
    The pilot must NOT become ACTIVE.
    """
    svc: PilotOperationsService = containment_env["pilot_svc"]
    pilot = svc.provision_pilot(
        pilot_id="pilot_no_topo_neg_test",
        consortium_id="c_containment",
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        approved_datasets={"fraud": "sha256_hash"},
        created_by="op_user",
        principal=containment_env["principal_op"],
        topology={},  # NO TOPOLOGY!
    )
    sig_a = containment_env["id_a"].sign(canonical_pilot_approval_bytes(
        pilot_id="pilot_no_topo_neg_test",
        revision=pilot.config.revision,
        scope_digest=pilot.scope_digest,
        approver_institution="bank_a",
        approver_id="bank_a_user",
    ))
    sig_b = containment_env["id_b"].sign(canonical_pilot_approval_bytes(
        pilot_id="pilot_no_topo_neg_test",
        revision=pilot.config.revision,
        scope_digest=pilot.scope_digest,
        approver_institution="bank_b",
        approver_id="bank_b_user",
    ))
    svc.approve_pilot("pilot_no_topo_neg_test", "bank_a", principal=containment_env["principal_a"], signature=sig_a)
    svc.approve_pilot("pilot_no_topo_neg_test", "bank_b", principal=containment_env["principal_b"], signature=sig_b)
    svc.approve_pilot("pilot_no_topo_neg_test", "operator", principal=containment_env["principal_op"])

    # Attempt activation without topology
    with pytest.raises(PilotGovernanceError) as exc_act:
        svc.activate_pilot("pilot_no_topo_neg_test", principal=containment_env["principal_op"])
    assert "mandatory topology" in str(exc_act.value).lower() or "not been verified" in str(exc_act.value).lower()

    # The pilot must NOT become ACTIVE
    p_final = svc.get_pilot("pilot_no_topo_neg_test")
    assert p_final is not None
    assert p_final.status != PilotStatus.ACTIVE


def test_mandatory_attack_fake_worker_rejected_by_preflight_and_resumption(containment_env: dict[str, Any]) -> None:
    """Mandatory attack test: Replace Bank B's expected endpoint with a fake server
    returning {"status":"HEALTHY","bank_id":"bank_b"} without valid institutional signature.
    Preflight and resumption must reject it.
    """
    svc: PilotOperationsService = containment_env["pilot_svc"]

    class RogueHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"HEALTHY","bank_id":"bank_b","role":"worker"}')

    rogue_server = ThreadingHTTPServer(("127.0.0.1", 0), RogueHandler)
    rogue_port = rogue_server.server_port
    rogue_thread = threading.Thread(target=rogue_server.serve_forever, daemon=True)
    rogue_thread.start()

    try:
        rogue_url = f"http://127.0.0.1:{rogue_port}"
        # 1. Preflight must reject fake Bank B worker
        _ = svc.provision_pilot(
            pilot_id="pilot_fake_worker_attack",
            consortium_id="c_containment",
            participating_institutions=["bank_a", "bank_b"],
            allowed_purposes=["AML_INVESTIGATION"],
            allowed_jurisdictions=["EU"],
            approved_datasets={"data": "h1"},
            principal=containment_env["principal_op"],
            worker_endpoints={
                "bank_a": containment_env["test_topology"]["bank_a_worker_url"],
                "bank_b": rogue_url,  # ATTACK: fake server!
            },
            coordinator_endpoint=containment_env["test_topology"]["coordinator_url"],
        )

        with pytest.raises(PilotGovernanceError) as exc_pref:
            svc.run_preflight("pilot_fake_worker_attack", principal=containment_env["principal_op"])
        assert "authentication_failed" in str(exc_pref.value).lower() or "preflight verification failed" in str(exc_pref.value).lower()

        p_check = svc.get_pilot("pilot_fake_worker_attack")
        assert p_check is not None
        assert p_check.status == PilotStatus.FAILED

        # 2. Resumption must also reject fake Bank B worker
        _ = _create_and_activate_pilot(containment_env, pilot_id="pilot_resume_fake_test")
        svc.pause_pilot("pilot_resume_fake_test", principal=containment_env["principal_op"])

        # Tamper topology to point Bank B to rogue server
        active_p_obj = svc.get_pilot("pilot_resume_fake_test")
        assert active_p_obj is not None
        active_p_obj.config.worker_endpoints["bank_b"] = rogue_url
        active_p_obj.config.topology["bank_b_worker_url"] = rogue_url
        svc.store.save_pilot(active_p_obj)

        with pytest.raises(PilotGovernanceError) as exc_res:
            svc.resume_pilot("pilot_resume_fake_test", principal=containment_env["principal_op"])
        assert "authentication_failed" in str(exc_res.value).lower() or "preflight probe failed" in str(exc_res.value).lower()
    finally:
        rogue_server.shutdown()
        rogue_server.server_close()


def test_coordinator_authentication_mandatory_negative_controls(
    containment_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mandatory negative controls for Coordinator Authentication (P0):
    1. CFI_COORDINATOR_SECRET completely unset -> preflight and resume fail closed.
    2. CFI_COORDINATOR_SECRET set to empty string -> preflight and resume fail closed.
    3. Fabricated healthy coordinator endpoint with no valid signature -> preflight and resume fail closed.
    4. Wrong coordinator HMAC -> preflight and resume fail closed.
    5. Replayed readiness response (stale timestamp >300s) -> preflight and resume fail closed.
    6. Mismatched nonce -> preflight and resume fail closed.
    7. Substituted role / wrong identity -> preflight and resume fail closed.
    8. Successful independently authenticated coordinator -> preflight and resume succeed.
    """
    svc: PilotOperationsService = containment_env["pilot_svc"]

    adversarial_state = {
        "mode": "unsigned",
    }

    class DynamicAdversarialCoordinatorHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            probe_nonce = query.get("nonce", [""])[0]
            now_iso = datetime.now(UTC).isoformat()
            mode = adversarial_state["mode"]

            if mode == "unsigned":
                body = {
                    "status": "HEALTHY",
                    "role": "coordinator",
                    "coordinator_id": "coordinator_default",
                    "protocol": "OPENMINED-PSI-2.0.6-ECDH",
                    "nonce": probe_nonce,
                    "timestamp": now_iso,
                }
            elif mode == "wrong_hmac":
                can_bytes = canonical_coordinator_readiness_bytes(
                    role="coordinator",
                    coordinator_id="coordinator_default",
                    nonce=probe_nonce,
                    timestamp=now_iso,
                    protocol="OPENMINED-PSI-2.0.6-ECDH",
                )
                wrong_sig = hmac.new(b"wrong_secret_adversarial_123", can_bytes, hashlib.sha256).hexdigest()
                body = {
                    "status": "HEALTHY",
                    "role": "coordinator",
                    "coordinator_id": "coordinator_default",
                    "protocol": "OPENMINED-PSI-2.0.6-ECDH",
                    "nonce": probe_nonce,
                    "timestamp": now_iso,
                    "coordinator_signature": wrong_sig,
                }
            elif mode == "stale_timestamp":
                stale_ts = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
                can_bytes = canonical_coordinator_readiness_bytes(
                    role="coordinator",
                    coordinator_id="coordinator_default",
                    nonce=probe_nonce,
                    timestamp=stale_ts,
                    protocol="OPENMINED-PSI-2.0.6-ECDH",
                )
                sig = hmac.new(SHARED_COORDINATOR_SECRET.encode("utf-8"), can_bytes, hashlib.sha256).hexdigest()
                body = {
                    "status": "HEALTHY",
                    "role": "coordinator",
                    "coordinator_id": "coordinator_default",
                    "protocol": "OPENMINED-PSI-2.0.6-ECDH",
                    "nonce": probe_nonce,
                    "timestamp": stale_ts,
                    "coordinator_signature": sig,
                }
            elif mode == "mismatched_nonce":
                bad_nonce = "adversarial_nonce_mismatch_888"
                can_bytes = canonical_coordinator_readiness_bytes(
                    role="coordinator",
                    coordinator_id="coordinator_default",
                    nonce=bad_nonce,
                    timestamp=now_iso,
                    protocol="OPENMINED-PSI-2.0.6-ECDH",
                )
                sig = hmac.new(SHARED_COORDINATOR_SECRET.encode("utf-8"), can_bytes, hashlib.sha256).hexdigest()
                body = {
                    "status": "HEALTHY",
                    "role": "coordinator",
                    "coordinator_id": "coordinator_default",
                    "protocol": "OPENMINED-PSI-2.0.6-ECDH",
                    "nonce": bad_nonce,
                    "timestamp": now_iso,
                    "coordinator_signature": sig,
                }
            elif mode == "substituted_role":
                can_bytes = canonical_coordinator_readiness_bytes(
                    role="worker",
                    coordinator_id="coordinator_default",
                    nonce=probe_nonce,
                    timestamp=now_iso,
                    protocol="OPENMINED-PSI-2.0.6-ECDH",
                )
                sig = hmac.new(SHARED_COORDINATOR_SECRET.encode("utf-8"), can_bytes, hashlib.sha256).hexdigest()
                body = {
                    "status": "HEALTHY",
                    "role": "worker",
                    "coordinator_id": "coordinator_default",
                    "protocol": "OPENMINED-PSI-2.0.6-ECDH",
                    "nonce": probe_nonce,
                    "timestamp": now_iso,
                    "coordinator_signature": sig,
                }
            else:
                body = {"status": "UNKNOWN"}

            resp_bytes = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)

    adv_server = ThreadingHTTPServer(("127.0.0.1", 0), DynamicAdversarialCoordinatorHandler)
    adv_port = adv_server.server_address[1]
    adv_thread = threading.Thread(target=adv_server.serve_forever, daemon=True)
    adv_thread.start()

    try:
        adv_url = f"http://127.0.0.1:{adv_port}"
        legit_coord_url = containment_env["test_topology"]["coordinator_url"]

        def assert_preflight_rejected(pilot_id: str, coord_endpoint: str) -> None:
            _ = svc.provision_pilot(
                pilot_id=pilot_id,
                consortium_id="c_containment",
                participating_institutions=["bank_a", "bank_b"],
                allowed_purposes=["AML_INVESTIGATION"],
                allowed_jurisdictions=["EU"],
                approved_datasets={"data": "h1"},
                principal=containment_env["principal_op"],
                worker_endpoints={
                    "bank_a": containment_env["test_topology"]["bank_a_worker_url"],
                    "bank_b": containment_env["test_topology"]["bank_b_worker_url"],
                },
                coordinator_endpoint=coord_endpoint,
            )
            with pytest.raises(PilotGovernanceError) as exc_info:
                svc.run_preflight(pilot_id, principal=containment_env["principal_op"])
            err_msg = str(exc_info.value).lower()
            assert (
                "authentication_failed" in err_msg
                or "preflight verification failed" in err_msg
                or "coordinator_reachable" in err_msg
            )
            p = svc.get_pilot(pilot_id)
            assert p is not None and p.status == PilotStatus.FAILED

        def assert_resume_rejected(active_pilot_id: str, coord_endpoint: str) -> None:
            p = svc.get_pilot(active_pilot_id)
            assert p is not None
            p.config.coordinator_endpoint = coord_endpoint
            p.config.topology["coordinator_url"] = coord_endpoint
            svc.store.save_pilot(p)
            with pytest.raises(PilotGovernanceError) as exc_info:
                svc.resume_pilot(active_pilot_id, principal=containment_env["principal_op"])
            err_msg = str(exc_info.value).lower()
            assert (
                "authentication_failed" in err_msg
                or "preflight probe failed" in err_msg
                or "coordinator_reachable" in err_msg
            )
            p_after = svc.get_pilot(active_pilot_id)
            assert p_after is not None and p_after.status == PilotStatus.PAUSED

        # 1. Negative Scenario: CFI_COORDINATOR_SECRET completely unset
        monkeypatch.delenv("CFI_COORDINATOR_SECRET", raising=False)
        monkeypatch.delenv("COORDINATOR_SECRET", raising=False)
        monkeypatch.delenv("COORDINATOR_AUTH_SECRET", raising=False)
        assert_preflight_rejected("pilot_neg_coord_unset", legit_coord_url)

        monkeypatch.setenv("CFI_COORDINATOR_SECRET", SHARED_COORDINATOR_SECRET)
        _create_and_activate_pilot(containment_env, pilot_id="pilot_resume_unset")
        svc.pause_pilot("pilot_resume_unset", principal=containment_env["principal_op"])
        monkeypatch.delenv("CFI_COORDINATOR_SECRET", raising=False)
        monkeypatch.delenv("COORDINATOR_SECRET", raising=False)
        monkeypatch.delenv("COORDINATOR_AUTH_SECRET", raising=False)
        assert_resume_rejected("pilot_resume_unset", legit_coord_url)

        # 2. Negative Scenario: CFI_COORDINATOR_SECRET set to empty string
        monkeypatch.setenv("CFI_COORDINATOR_SECRET", "   ")
        assert_preflight_rejected("pilot_neg_coord_empty", legit_coord_url)

        monkeypatch.setenv("CFI_COORDINATOR_SECRET", SHARED_COORDINATOR_SECRET)
        _create_and_activate_pilot(containment_env, pilot_id="pilot_resume_empty")
        svc.pause_pilot("pilot_resume_empty", principal=containment_env["principal_op"])
        monkeypatch.setenv("CFI_COORDINATOR_SECRET", "   ")
        assert_resume_rejected("pilot_resume_empty", legit_coord_url)

        # Restore valid secret for server-side adversary tests
        monkeypatch.setenv("CFI_COORDINATOR_SECRET", SHARED_COORDINATOR_SECRET)

        # 3. Negative Scenario: Fabricated healthy coordinator endpoint with no valid signature
        adversarial_state["mode"] = "unsigned"
        assert_preflight_rejected("pilot_neg_coord_unsigned", adv_url)
        _create_and_activate_pilot(containment_env, pilot_id="pilot_resume_unsigned")
        svc.pause_pilot("pilot_resume_unsigned", principal=containment_env["principal_op"])
        assert_resume_rejected("pilot_resume_unsigned", adv_url)

        # 4. Negative Scenario: Wrong coordinator HMAC signature
        adversarial_state["mode"] = "wrong_hmac"
        assert_preflight_rejected("pilot_neg_coord_wrong_hmac", adv_url)
        _create_and_activate_pilot(containment_env, pilot_id="pilot_resume_wrong_hmac")
        svc.pause_pilot("pilot_resume_wrong_hmac", principal=containment_env["principal_op"])
        assert_resume_rejected("pilot_resume_wrong_hmac", adv_url)

        # 5. Negative Scenario: Replayed readiness response (stale timestamp >300s)
        adversarial_state["mode"] = "stale_timestamp"
        assert_preflight_rejected("pilot_neg_coord_stale_ts", adv_url)
        _create_and_activate_pilot(containment_env, pilot_id="pilot_resume_stale_ts")
        svc.pause_pilot("pilot_resume_stale_ts", principal=containment_env["principal_op"])
        assert_resume_rejected("pilot_resume_stale_ts", adv_url)

        # 6. Negative Scenario: Mismatched nonce
        adversarial_state["mode"] = "mismatched_nonce"
        assert_preflight_rejected("pilot_neg_coord_bad_nonce", adv_url)
        _create_and_activate_pilot(containment_env, pilot_id="pilot_resume_bad_nonce")
        svc.pause_pilot("pilot_resume_bad_nonce", principal=containment_env["principal_op"])
        assert_resume_rejected("pilot_resume_bad_nonce", adv_url)

        # 7. Negative Scenario: Substituted role / wrong identity
        adversarial_state["mode"] = "substituted_role"
        assert_preflight_rejected("pilot_neg_coord_sub_role", adv_url)
        _create_and_activate_pilot(containment_env, pilot_id="pilot_resume_sub_role")
        svc.pause_pilot("pilot_resume_sub_role", principal=containment_env["principal_op"])
        assert_resume_rejected("pilot_resume_sub_role", adv_url)

        # 8. Positive Scenario: Successful independently authenticated coordinator with valid configured identity
        p_valid = _create_and_activate_pilot(containment_env, pilot_id="pilot_coord_valid_auth")
        assert p_valid.status == PilotStatus.ACTIVE
        svc.pause_pilot("pilot_coord_valid_auth", principal=containment_env["principal_op"])
        p_paused = svc.get_pilot("pilot_coord_valid_auth")
        assert p_paused is not None and p_paused.status == PilotStatus.PAUSED
        svc.resume_pilot("pilot_coord_valid_auth", principal=containment_env["principal_op"])
        p_resumed = svc.get_pilot("pilot_coord_valid_auth")
        assert p_resumed is not None and p_resumed.status == PilotStatus.ACTIVE
    finally:
        adv_server.shutdown()
        adv_server.server_close()


# ==============================================================================
# P0 — Final Durable Publication Linearization & Crash Consistency Closure
# ==============================================================================


def test_process_crash_after_preparation_before_redis_completion(containment_env: dict[str, Any]) -> None:
    """Process failure after publication preparation leaves reservation in PUBLICATION_PREPARED.

    Evidence attachment must fail closed because signal is not published in Redis
    and reservation is not PUBLISHED in SQLite.
    """
    svc: PilotOperationsService = containment_env["pilot_svc"]
    pilot = _create_and_activate_pilot(containment_env, pilot_id="pilot_crash_prep_01")
    p_id = pilot.config.pilot_id

    # Reserve and commit query reservation
    res_id = "res_crash_01"
    svc.reserve_query_slot(p_id, "bank_a", "bank_b", "AML_INVESTIGATION", res_id)
    sig_id = f"sig_{uuid.uuid4().hex}"
    rcp_id = f"rcp_{uuid.uuid4().hex}"
    svc.commit_query_reservation(res_id, p_id, rcp_id, latency_ms=10.0, match_confirmed=True, signal_id=sig_id, case_id="case_01")

    # Step 1: Prepare publication (sets PUBLICATION_PREPARED with fence)
    fence = svc.prepare_query_publication(res_id, p_id, sig_id, rcp_id)
    assert fence.startswith("fence_")

    # Verify SQLite state is PUBLICATION_PREPARED
    res_row = svc.store.get_query_reservation(res_id)
    assert res_row is not None
    assert res_row["status"] == "PUBLICATION_PREPARED"
    assert res_row["publication_fence"] == fence
    assert res_row["signal_id"] == sig_id
    assert res_row["receipt_id"] == rcp_id

    # Step 2: Simulate process crash before publication finalization and Redis write
    cbi_svc = CrossBankIntelligenceService(consortium_service=containment_env["gov_svc"])
    _register_test_case(cbi_svc.case_service, "case_01", "bank_a")
    # Case evidence attachment must fail closed because signal is not in Redis
    with pytest.raises(ValueError, match="Authoritative signal .* not found"):
        cbi_svc.attach_signal_to_case_evidence(
            case_id="case_01",
            signal_id=sig_id,
            principal=containment_env["principal_a"],
        )

    # Even if an unfinalized record exists in Redis with publication_status=PUBLICATION_PREPARED
    cbi_svc._signal_store.set(sig_id, {
        "signal_id": sig_id,
        "pilot_id": p_id,
        "reservation_id": res_id,
        "receipt_id": rcp_id,
        "requesting_bank_id": "bank_a",
        "target_bank_id": "bank_b",
        "purpose": "AML_INVESTIGATION",
        "admission_status": "COMMITTED",
        "publication_status": "PUBLICATION_PREPARED",
        "is_authoritative": False,
        "is_attachable": False,
        "consortium_id": "c_containment",
        "query_entity_id": "ent_01",
        "issued_to_principal": "analyst",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    })
    with pytest.raises(ConsortiumSecurityError, match="publication status is 'PUBLICATION_PREPARED', not PUBLISHED"):
        cbi_svc.attach_signal_to_case_evidence(
            case_id="case_01",
            signal_id=sig_id,
            principal=containment_env["principal_a"],
        )

    # If attacker attempts to claim authoritative in Redis, SQLite durable check fails closed
    cbi_svc._signal_store.set(sig_id, {
        "signal_id": sig_id,
        "pilot_id": p_id,
        "reservation_id": res_id,
        "receipt_id": rcp_id,
        "requesting_bank_id": "bank_a",
        "target_bank_id": "bank_b",
        "purpose": "AML_INVESTIGATION",
        "admission_status": "COMMITTED",
        "publication_status": "PUBLISHED",
        "is_authoritative": True,
        "is_attachable": True,
        "consortium_id": "c_containment",
        "query_entity_id": "ent_01",
        "issued_to_principal": "analyst",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    })
    with pytest.raises(ConsortiumSecurityError, match="publication status is 'PUBLICATION_PREPARED', not PUBLISHED"):
        cbi_svc.attach_signal_to_case_evidence(
            case_id="case_01",
            signal_id=sig_id,
            principal=containment_env["principal_a"],
        )


def test_redis_publication_failure_records_failed(containment_env: dict[str, Any]) -> None:
    """Simulated physical publication failure records PUBLICATION_FAILED and prevents phantom published state."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    pilot = _create_and_activate_pilot(containment_env, pilot_id="pilot_redis_fail_01")
    p_id = pilot.config.pilot_id

    res_id = "res_redis_fail_01"
    svc.reserve_query_slot(p_id, "bank_a", "bank_b", "AML_INVESTIGATION", res_id)
    sig_id = f"sig_{uuid.uuid4().hex}"
    rcp_id = f"rcp_{uuid.uuid4().hex}"
    svc.commit_query_reservation(res_id, p_id, rcp_id, latency_ms=10.0, match_confirmed=True, signal_id=sig_id, case_id="case_01")

    # Prepare and finalize publication
    fence = svc.prepare_query_publication(res_id, p_id, sig_id, rcp_id)
    svc.finalize_query_publication(res_id, p_id, sig_id, rcp_id, publication_fence=fence)

    # Physical Redis write fails: record_publication_failure transitions reservation to PUBLICATION_FAILED
    svc.record_publication_failure(res_id, p_id, "Connection to Redis refused (ECONNREFUSED)")

    res_row = svc.store.get_query_reservation(res_id)
    assert res_row is not None
    assert res_row["status"] == "PUBLICATION_FAILED"
    assert "Physical publication failure" in str(res_row["rejection_reason"])

    # Evidence attachment fails closed
    cbi_svc = CrossBankIntelligenceService(consortium_service=containment_env["gov_svc"])
    _register_test_case(cbi_svc.case_service, "case_01", "bank_a")
    with pytest.raises(ValueError):
        cbi_svc.attach_signal_to_case_evidence(
            case_id="case_01",
            signal_id=sig_id,
            principal=containment_env["principal_a"],
        )


def test_two_concurrent_application_processes_attempting_same_publication(containment_env: dict[str, Any]) -> None:
    """Two concurrent processes attempting identical publication succeed idempotently."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    pilot = _create_and_activate_pilot(containment_env, pilot_id="pilot_concurrent_pub_01")
    p_id = pilot.config.pilot_id

    res_id = "res_concurrent_01"
    svc.reserve_query_slot(p_id, "bank_a", "bank_b", "AML_INVESTIGATION", res_id)
    sig_id = f"sig_{uuid.uuid4().hex}"
    rcp_id = f"rcp_{uuid.uuid4().hex}"
    svc.commit_query_reservation(res_id, p_id, rcp_id, latency_ms=10.0, match_confirmed=True, signal_id=sig_id, case_id="case_01")

    fence = svc.prepare_query_publication(res_id, p_id, sig_id, rcp_id)

    # Process A finalizes
    svc.finalize_query_publication(res_id, p_id, sig_id, rcp_id, publication_fence=fence)

    # Process B attempts to finalize identical publication: succeeds idempotently
    svc.finalize_query_publication(res_id, p_id, sig_id, rcp_id, publication_fence=fence)

    res_row = svc.store.get_query_reservation(res_id)
    assert res_row is not None
    assert res_row["status"] == "PUBLISHED"


def test_rebinding_published_reservation_to_different_signal_or_receipt(containment_env: dict[str, Any]) -> None:
    """Rebinding an already finalized PUBLISHED reservation to a different signal or receipt must fail closed."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    pilot = _create_and_activate_pilot(containment_env, pilot_id="pilot_rebind_01")
    p_id = pilot.config.pilot_id

    res_id = "res_rebind_01"
    svc.reserve_query_slot(p_id, "bank_a", "bank_b", "AML_INVESTIGATION", res_id)
    sig_id = f"sig_{uuid.uuid4().hex}"
    rcp_id = f"rcp_{uuid.uuid4().hex}"
    svc.commit_query_reservation(res_id, p_id, rcp_id, latency_ms=10.0, match_confirmed=True, signal_id=sig_id, case_id="case_01")

    fence = svc.prepare_query_publication(res_id, p_id, sig_id, rcp_id)
    svc.finalize_query_publication(res_id, p_id, sig_id, rcp_id, publication_fence=fence)

    # Attempt to rebind already PUBLISHED reservation to different signal
    diff_sig = f"sig_fraudulent_{uuid.uuid4().hex}"
    with pytest.raises(PilotGovernanceError, match="Immutable binding violation"):
        svc.finalize_query_publication(res_id, p_id, diff_sig, rcp_id)

    # Attempt to rebind already PUBLISHED reservation to different receipt
    diff_rcp = f"rcp_forged_{uuid.uuid4().hex}"
    with pytest.raises(PilotGovernanceError, match="Immutable binding violation"):
        svc.finalize_query_publication(res_id, p_id, sig_id, diff_rcp)

    # Attempt to prepare already PUBLISHED reservation with different signal
    with pytest.raises(PilotGovernanceError, match="Immutable binding violation: Reservation '.*' already PUBLISHED"):
        svc.prepare_query_publication(res_id, p_id, diff_sig, rcp_id)

    # Attempt to mutate status via record_publication_status bypass
    with pytest.raises(PilotGovernanceError, match="Cannot mutate finalized reservation status 'PUBLISHED'"):
        svc.record_publication_status(res_id, p_id, "COMMITTED")


def test_repeated_identical_publication_attempts_idempotent(containment_env: dict[str, Any]) -> None:
    """Repeated identical publication attempts are idempotent and do not corrupt state."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    pilot = _create_and_activate_pilot(containment_env, pilot_id="pilot_idempotent_01")
    p_id = pilot.config.pilot_id

    res_id = "res_idem_01"
    svc.reserve_query_slot(p_id, "bank_a", "bank_b", "AML_INVESTIGATION", res_id)
    sig_id = f"sig_{uuid.uuid4().hex}"
    rcp_id = f"rcp_{uuid.uuid4().hex}"
    svc.commit_query_reservation(res_id, p_id, rcp_id, latency_ms=10.0, match_confirmed=True, signal_id=sig_id, case_id="case_01")

    fence = svc.prepare_query_publication(res_id, p_id, sig_id, rcp_id)
    fence2 = svc.prepare_query_publication(res_id, p_id, sig_id, rcp_id)
    assert fence == fence2

    # Finalize publication
    svc.finalize_query_publication(res_id, p_id, sig_id, rcp_id, publication_fence=fence)

    # Repeated identical finalization calls succeed idempotently
    svc.finalize_query_publication(res_id, p_id, sig_id, rcp_id, publication_fence=fence)
    svc.authorize_and_record_signal_publication(res_id, p_id, sig_id, rcp_id)

    res_row = svc.store.get_query_reservation(res_id)
    assert res_row is not None
    assert res_row["status"] == "PUBLISHED"


def test_pause_and_publication_competing_at_same_durable_boundary(containment_env: dict[str, Any]) -> None:
    """PAUSE and publication completion competing at the same durable boundary serialize correctly."""
    svc: PilotOperationsService = containment_env["pilot_svc"]
    pilot = _create_and_activate_pilot(containment_env, pilot_id="pilot_compete_01")
    p_id = pilot.config.pilot_id

    res_id = "res_compete_01"
    svc.reserve_query_slot(p_id, "bank_a", "bank_b", "AML_INVESTIGATION", res_id)
    sig_id = f"sig_{uuid.uuid4().hex}"
    rcp_id = f"rcp_{uuid.uuid4().hex}"
    svc.commit_query_reservation(res_id, p_id, rcp_id, latency_ms=10.0, match_confirmed=True, signal_id=sig_id, case_id="case_01")

    fence = svc.prepare_query_publication(res_id, p_id, sig_id, rcp_id)

    # Pause pilot while reservation is in PUBLICATION_PREPARED
    svc.pause_pilot(p_id, principal=containment_env["principal_op"], reason="Compete boundary test")

    # In-flight reservation must have been transitioned to PUBLICATION_REJECTED by pause transaction
    res_row = svc.store.get_query_reservation(res_id)
    assert res_row is not None
    assert res_row["status"] == "PUBLICATION_REJECTED"

    # Any subsequent attempt to finalize publication must fail closed
    with pytest.raises(PilotGovernanceError, match="Publication rejected: Reservation '.*' was rejected"):
        svc.finalize_query_publication(res_id, p_id, sig_id, rcp_id, publication_fence=fence)

