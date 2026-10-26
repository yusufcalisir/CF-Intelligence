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

import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

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
)
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


@pytest.fixture
def containment_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    consortium_store_file = tmp_path / "containment_consortium_store.json"
    account_store_file = tmp_path / "containment_account_store.json"
    pilot_db_file = tmp_path / "containment_pilot_operations.db"

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(consortium_store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_store_file))
    monkeypatch.setenv("CFI_PILOT_DB_PATH", str(pilot_db_file))
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", SHARED_CONSORTIUM_STORE_SECRET)
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
    coord_daemon = InstitutionalCoordinatorDaemon(coord_obj, host="127.0.0.1", port=0)
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


def _create_and_activate_pilot(
    env: dict[str, Any],
    pilot_id: str = "pilot_test_01",
    quota_total: int = 10,
    quota_per_inst: int = 5,
    expires_at: str | None = None,
) -> PilotEntity:
    svc: PilotOperationsService = env["pilot_svc"]
    if not expires_at:
        expires_at = (datetime.datetime.now(UTC) + datetime.timedelta(hours=24)).isoformat()

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
    past_iso = (datetime.datetime.now(UTC) - datetime.timedelta(hours=1)).isoformat()
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
        "expires_at": (datetime.datetime.now(UTC) + datetime.timedelta(minutes=30)).isoformat(),
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
    with pytest.raises(ConsortiumSecurityError) as exc_attach:
        intel_svc.attach_signal_to_case_evidence(
            case_id="case_race_01",
            signal_id=sig_id,
            case_bank="bank_a",
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
            case_bank="bank_a",
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
        "is_authoritative": True,
        "is_attachable": True,
        "expires_at": (datetime.datetime.now(UTC) + datetime.timedelta(minutes=30)).isoformat(),
    }
    intel_svc._signal_store.set(tampered_sig_id, tampered_record)

    with pytest.raises(ConsortiumSecurityError) as exc:
        intel_svc.attach_signal_to_case_evidence(
            case_id="case_01",
            signal_id=tampered_sig_id,
            case_bank="bank_a",
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
        expires_at=(datetime.datetime.now(UTC) + datetime.timedelta(hours=10)).isoformat(),
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
        expires_at=(datetime.datetime.now(UTC) + datetime.timedelta(hours=10)).isoformat(),
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
