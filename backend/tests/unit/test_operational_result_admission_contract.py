"""Adversarial and Positive Unit Tests for the Verifiable Result-Admission Contract.

Mandatory verification tests:
1. A fabricated coordinator response cannot become authoritative (coordinator signature cannot substitute for bank signatures).
2. A valid ticket combined with a forged counterpart PSI response fails.
3. Changing a legitimate signal's risk level to CRITICAL fails.
4. Injecting a typology such as TERRORIST_FINANCING into a signed result fails.
5. Changing disclosure mode or attributed institutions fails unless authenticated disclosure evidence supports it.
6. Modifying the PSI result or match category fails.
7. Changing the session, entity, grant revision, or target bank fails.
8. Substituting a new institutional public key fails.
9. Replaying signed execution evidence fails.
10. The analyst service cannot access either bank's private signing key.
11. A complete genuine two-bank execution continues to succeed.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import uuid
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
)
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    compute_operational_grant_scope_digest,
)
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.isolated_matching_service import (
    OperationAuthorizationTicket,
    VerifiedMatchReceipt,
)
from app.domain.consortium_governance import (
    Consortium,
    ConsortiumMember,
    ConsortiumSecurityError,
    ConsortiumStatus,
)
from app.domain.institutional_identity import (
    InstitutionalSigningIdentity,
    InstitutionalVerificationRegistry,
    canonical_counterpart_evidence_bytes,
    canonical_counterpart_evidence_digest,
    canonical_evaluated_result_bytes,
    canonical_evaluated_result_digest,
)

SECRET_A = "auth_sec_bank_a_81729384719283749182"
SECRET_B = "auth_sec_bank_b_98127394812739481273"
SECRET_COORD = "auth_sec_coord_99912384719283749123"


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CFI_WORKER_SECRET_BANK_A", SECRET_A)
    monkeypatch.setenv("CFI_PEER_KEY_BANK_B", SECRET_B)
    monkeypatch.setenv("CFI_WORKER_SECRET_BANK_B", SECRET_B)
    monkeypatch.setenv("CFI_COORDINATOR_SECRET", SECRET_COORD)
    monkeypatch.setenv("WORKER_AUTH_SECRET", SECRET_A)
    monkeypatch.setenv("COORDINATOR_AUTH_SECRET", SECRET_COORD)
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")


def _build_authentic_test_signal(
    consortium_id: str = "c_alpha",
    requesting_bank_id: str = "bank_a",
    target_bank_id: str = "bank_b",
    query_entity_id: str = "ent_target_123",
    match_confirmed: bool = True,
    match_category: str = "PROBABILISTIC_FUZZY",
    match_confidence: float = 0.999999999,
    disclosure_mode: str = "ATTRIBUTED_INSTITUTIONS",
    sanitized_risk_level: str | None = None,
    sanitized_typologies: tuple[str, ...] | None = None,
    sign_a: InstitutionalSigningIdentity | None = None,
    sign_b: InstitutionalSigningIdentity | None = None,
) -> tuple[
    CrossBankIntelligenceSignal,
    CrossBankIntelligenceQueryRequest,
    OperationAuthorizationTicket,
    VerifiedMatchReceipt,
    dict[str, Any],
]:
    now = datetime.datetime.now(UTC)
    now_iso = now.isoformat()
    expires_at = (now + datetime.timedelta(seconds=300)).isoformat()
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    ticket_id = f"tkt_{uuid.uuid4().hex[:12]}"
    receipt_id = f"rcp_{uuid.uuid4().hex[:12]}"
    grant_id = f"grant_{consortium_id}"
    grant_revision = 1

    ident_a = sign_a or InstitutionalSigningIdentity.from_seed(requesting_bank_id, SECRET_A)
    ident_b = sign_b or InstitutionalSigningIdentity.from_seed(target_bank_id, SECRET_B)

    scope_digest = compute_operational_grant_scope_digest(
        grant_id=grant_id,
        revision=grant_revision,
        participating_banks=[requesting_bank_id, target_bank_id],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={requesting_bank_id: "EU", target_bank_id: "EU"},
        allowed_jurisdictions=["EU", "US"],
        allow_cross_border=False,
        allow_institution_identity_sharing=True,
    )

    unsigned_ticket = OperationAuthorizationTicket(
        ticket_id=ticket_id,
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank_id,
        target_bank_id=target_bank_id,
        operation="ESTABLISHED_PSI",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        query_scope=f"entity:{query_entity_id}",
        grant_id=grant_id,
        grant_revision=grant_revision,
        grant_scope_digest=scope_digest,
        job_id=job_id,
        issued_at=now_iso,
        expires_at=expires_at,
        bank_approvals={},
    )

    ticket_canonical = f"{unsigned_ticket.canonical_digest()}:{target_bank_id}".encode()
    sig_b_ticket = hmac.new(SECRET_B.encode(), ticket_canonical, hashlib.sha256).hexdigest()
    sig_a_ticket = hmac.new(
        SECRET_A.encode(),
        f"{unsigned_ticket.canonical_digest()}:{requesting_bank_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    ticket = OperationAuthorizationTicket(
        ticket_id=ticket_id,
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank_id,
        target_bank_id=target_bank_id,
        operation="ESTABLISHED_PSI",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        query_scope=f"entity:{query_entity_id}",
        grant_id=grant_id,
        grant_revision=grant_revision,
        grant_scope_digest=scope_digest,
        job_id=job_id,
        issued_at=now_iso,
        expires_at=expires_at,
        bank_approvals={requesting_bank_id: sig_a_ticket, target_bank_id: sig_b_ticket},
    )

    # 1. Bank B signs Counterpart Evidence
    b_nonce = uuid.uuid4().hex
    setup_hash = hashlib.sha256(f"setup_{job_id}".encode()).hexdigest()
    resp_hash = hashlib.sha256(f"resp_{job_id}".encode()).hexdigest()
    counterpart_bytes = canonical_counterpart_evidence_bytes(
        version="1.0",
        consortium_id=consortium_id,
        grant_id=grant_id,
        grant_revision=grant_revision,
        grant_scope_digest=scope_digest,
        requesting_bank_id=requesting_bank_id,
        target_bank_id=target_bank_id,
        job_id=job_id,
        ticket_digest=ticket.canonical_digest(),
        psi_server_setup_hash=setup_hash,
        psi_response_hash=resp_hash,
        protocol="OPENMINED-PSI-2.0.6-ECDH",
        nonce=b_nonce,
        timestamp=now_iso,
    )
    counterpart_evidence_digest = canonical_counterpart_evidence_digest(
        version="1.0",
        consortium_id=consortium_id,
        grant_id=grant_id,
        grant_revision=grant_revision,
        grant_scope_digest=scope_digest,
        requesting_bank_id=requesting_bank_id,
        target_bank_id=target_bank_id,
        job_id=job_id,
        ticket_digest=ticket.canonical_digest(),
        psi_server_setup_hash=setup_hash,
        psi_response_hash=resp_hash,
        protocol="OPENMINED-PSI-2.0.6-ECDH",
        nonce=b_nonce,
        timestamp=now_iso,
    )
    worker_b_sig = ident_b.sign(counterpart_bytes)

    counterpart_evidence = {
        "version": "1.0",
        "consortium_id": consortium_id,
        "grant_id": grant_id,
        "grant_revision": grant_revision,
        "grant_scope_digest": scope_digest,
        "requesting_bank_id": requesting_bank_id,
        "target_bank_id": target_bank_id,
        "job_id": job_id,
        "ticket_digest": ticket.canonical_digest(),
        "psi_server_setup_hash": setup_hash,
        "psi_response_hash": resp_hash,
        "protocol": "OPENMINED-PSI-2.0.6-ECDH",
        "nonce": b_nonce,
        "timestamp": now_iso,
        "counterpart_evidence_digest": counterpart_evidence_digest,
        "worker_b_signature": worker_b_sig,
    }

    # 2. Bank A signs Evaluated Outcome
    risk = sanitized_risk_level or ("UNAVAILABLE" if match_confirmed else "UNOBSERVED")
    typs = (
        sanitized_typologies
        if sanitized_typologies is not None
        else (("CROSS_BANK_ENTITY_OBSERVED",) if match_confirmed else ())
    )
    contributing = (target_bank_id,) if (match_confirmed and disclosure_mode == "ATTRIBUTED_INSTITUTIONS") else ()

    a_evaluated_bytes = canonical_evaluated_result_bytes(
        version="1.0",
        receipt_id=receipt_id,
        job_id=job_id,
        consortium_id=consortium_id,
        grant_id=grant_id,
        grant_revision=grant_revision,
        grant_scope_digest=scope_digest,
        requesting_bank_id=requesting_bank_id,
        target_bank_id=target_bank_id,
        query_entity_id=query_entity_id,
        ticket_digest=ticket.canonical_digest(),
        counterpart_evidence_digest=counterpart_evidence_digest,
        match_confirmed=match_confirmed,
        match_category=match_category,
        match_confidence=match_confidence,
        matched_risk_level=risk,
        matched_typologies=typs,
        disclosure_mode=disclosure_mode,
        contributing_institutions=contributing,
        protocol="OPENMINED-PSI-2.0.6-ECDH",
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
        executed_at=now_iso,
    )
    evaluated_result_digest = canonical_evaluated_result_digest(
        version="1.0",
        receipt_id=receipt_id,
        job_id=job_id,
        consortium_id=consortium_id,
        grant_id=grant_id,
        grant_revision=grant_revision,
        grant_scope_digest=scope_digest,
        requesting_bank_id=requesting_bank_id,
        target_bank_id=target_bank_id,
        query_entity_id=query_entity_id,
        ticket_digest=ticket.canonical_digest(),
        counterpart_evidence_digest=counterpart_evidence_digest,
        match_confirmed=match_confirmed,
        match_category=match_category,
        match_confidence=match_confidence,
        matched_risk_level=risk,
        matched_typologies=typs,
        disclosure_mode=disclosure_mode,
        contributing_institutions=contributing,
        protocol="OPENMINED-PSI-2.0.6-ECDH",
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
        executed_at=now_iso,
    )
    worker_a_sig = ident_a.sign(a_evaluated_bytes)

    # 3. Coordinator Routing Attestation
    transcript_digest = hashlib.sha256(f"transcript_{job_id}".encode()).hexdigest()
    coord_sig = hmac.new(
        SECRET_COORD.encode(),
        f"{receipt_id}:{transcript_digest}:{consortium_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    receipt = VerifiedMatchReceipt(
        receipt_id=receipt_id,
        job_id=job_id,
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank_id,
        target_bank_id=target_bank_id,
        query_entity_id=query_entity_id,
        match_confirmed=match_confirmed,
        match_confidence=match_confidence,
        protocol="OPENMINED-PSI-2.0.6-ECDH",
        transcript_digest=transcript_digest,
        executed_at=now_iso,
        worker_a_signature=worker_a_sig,
        worker_b_signature=worker_b_sig,
        coordinator_signature=coord_sig,
        matched_risk_level=risk,
        matched_typologies=typs,
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
        match_category=match_category,
        is_exact_confirmed=False,
        grant_id=grant_id,
        grant_revision=grant_revision,
        grant_scope_digest=scope_digest,
        ticket_digest=ticket.canonical_digest(),
        counterpart_evidence_digest=counterpart_evidence_digest,
        counterpart_evidence=counterpart_evidence,
        disclosure_mode=disclosure_mode,
        contributing_institutions=contributing,
        evaluated_result_digest=evaluated_result_digest,
        version="1.0",
    )

    signal = CrossBankIntelligenceSignal(
        signal_id=f"sig_{uuid.uuid4().hex[:12]}",
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank_id,
        query_entity_id=query_entity_id,
        privacy_hash_truncated=f"{query_entity_id[:8]}...[BLINDED]",
        match_confirmed=match_confirmed,
        match_category=match_category,
        match_confidence=match_confidence,
        contributing_institutions_count=len(contributing),
        contributing_institutions=list(contributing),
        disclosure_mode=disclosure_mode,
        sanitized_risk_level=risk,
        sanitized_typology_indicators=list(typs),
        provenance={
            "job_id": job_id,
            "ticket_id": ticket_id,
            "receipt_id": receipt_id,
            "target_bank_id": target_bank_id,
            "query_entity_id": query_entity_id,
            "protocol": "OPENMINED-PSI-2.0.6-ECDH",
            "grant_id": grant_id,
            "grant_revision": grant_revision,
            "grant_scope_digest": scope_digest,
            "transcript_digest": transcript_digest,
            "worker_a_signature": worker_a_sig,
            "worker_b_signature": worker_b_sig,
            "coordinator_signature": coord_sig,
            "counterpart_evidence": counterpart_evidence,
            "counterpart_evidence_digest": counterpart_evidence_digest,
            "evaluated_result_digest": evaluated_result_digest,
            "receipt": asdict(receipt),
            "ticket": asdict(ticket),
            "assurance_level": "REVIEWED_ESTABLISHED_PSI_VERIFIED",
        },
        execution_mode="VERIFIED_OPERATIONAL",
        attestation_status="ISOLATED_MULTI_PROCESS",
        is_authoritative=True,
        is_synthetic=False,
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
    )

    req = CrossBankIntelligenceQueryRequest(
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank_id,
        target_bank_id=target_bank_id,
        query_entity_id=query_entity_id,
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        use_established_psi=True,
    )

    return signal, req, ticket, receipt, counterpart_evidence


def _create_mock_consortium_service(consortium_id: str = "c_alpha") -> ConsortiumGovernanceService:
    gov = ConsortiumGovernanceService()
    grant_id = f"grant_{consortium_id}"
    digest = compute_operational_grant_scope_digest(
        grant_id=grant_id,
        revision=1,
        participating_banks=["bank_a", "bank_b"],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        allowed_jurisdictions=["EU", "US"],
        allow_cross_border=False,
        allow_institution_identity_sharing=True,
    )
    consortium = Consortium(
        consortium_id=consortium_id,
        name="Alpha Consortium",
        status=ConsortiumStatus.ACTIVE,
        members={
            "bank_a": ConsortiumMember("bank_a"),
            "bank_b": ConsortiumMember("bank_b"),
        },
        metadata={
            "operational_data_sharing_grant": {
                "grant_id": grant_id,
                "revision": 1,
                "scope_digest": digest,
                "is_active": True,
                "status": "APPROVED",
                "participating_banks": ["bank_a", "bank_b"],
                "allowed_purposes": ["AML_COLLABORATIVE_INVESTIGATION"],
                "institution_jurisdictions": {"bank_a": "EU", "bank_b": "EU"},
                "allowed_jurisdictions": ["EU", "US"],
                "allow_cross_border": False,
                "allow_institution_identity_sharing": True,
                "approvals": {"bank_a": "sig_a", "bank_b": "sig_b"},
            }
        },
    )
    gov._consortia[consortium_id] = consortium
    return gov


def _create_cbi_service(consortium_id: str = "c_alpha") -> CrossBankIntelligenceService:
    gov = _create_mock_consortium_service(consortium_id)
    reg = InstitutionalVerificationRegistry()
    reg.register_public_key("bank_a", InstitutionalSigningIdentity.from_seed("bank_a", SECRET_A).public_key)
    reg.register_public_key("bank_b", InstitutionalSigningIdentity.from_seed("bank_b", SECRET_B).public_key)
    return CrossBankIntelligenceService(consortium_service=gov, institutional_registry=reg)


# =========================================================================
# 11 MANDATORY ADVERSARIAL AND VERIFICATION CONTRACT TESTS
# =========================================================================


def test_genuine_operational_signal_admission_succeeds() -> None:
    """Requirement 11: Genuine two-bank signed execution succeeds and admits signal."""
    cbi = _create_cbi_service("c_alpha")
    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha")

    record = cbi._admit_and_verify_operational_signal(
        signal=signal,
        expected_req=req,
        audited_actor="analyst_alice",
    )
    assert record["signal_id"] == signal.signal_id
    assert record["is_authoritative"] is True
    assert record["operational_status"] == "OPERATIONAL"


def test_adversarial_fabricated_coordinator_response_fails() -> None:
    """Requirement 1: A fabricated coordinator response cannot substitute for institutional signatures."""
    cbi = _create_cbi_service("c_alpha")
    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha")

    # Coordinator attempts to substitute its signature while stripping Bank A signature
    tampered_provenance = deepcopy(signal.provenance)
    tampered_provenance["receipt"]["worker_a_signature"] = ""
    tampered_signal = signal.model_copy(update={"provenance": tampered_provenance})

    with pytest.raises(
        ConsortiumSecurityError,
        match="Missing Bank A cryptographic signature on evaluated result",
    ):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )

    # Coordinator forges its own routing signature
    tampered_provenance = deepcopy(signal.provenance)
    tampered_provenance["coordinator_signature"] = "forged_coordinator_hmac_signature"
    tampered_signal = signal.model_copy(update={"provenance": tampered_provenance})

    with pytest.raises(
        ConsortiumSecurityError,
        match="Cryptographic verification failed for Coordinator relay",
    ):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_valid_ticket_forged_counterpart_psi_response_fails() -> None:
    """Requirement 2: Valid ticket combined with forged counterpart PSI response fails."""
    cbi = _create_cbi_service("c_alpha")
    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha")

    # Attacker preserves valid ticket but alters Bank B's counterpart evidence payload
    tampered_provenance = deepcopy(signal.provenance)
    tampered_provenance["counterpart_evidence"]["psi_response_hash"] = (
        hashlib.sha256(b"tampered_psi_response_payload").hexdigest()
    )
    if (
        "receipt" in tampered_provenance
        and isinstance(tampered_provenance["receipt"], dict)
        and "counterpart_evidence" in tampered_provenance["receipt"]
    ):
        tampered_provenance["receipt"]["counterpart_evidence"]["psi_response_hash"] = (
            hashlib.sha256(b"tampered_psi_response_payload").hexdigest()
        )
    tampered_signal = signal.model_copy(update={"provenance": tampered_provenance})

    with pytest.raises(
        ConsortiumSecurityError,
        match="Counterpart evidence digest mismatch|Cryptographic verification failed for Counterpart Bank B",
    ):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_changing_risk_level_to_critical_fails() -> None:
    """Requirement 3: Changing a legitimate signal's risk level to CRITICAL fails."""
    cbi = _create_cbi_service("c_alpha")
    # Legitimate signal with UNAVAILABLE risk
    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha", sanitized_risk_level="UNAVAILABLE")

    # Adversary alters risk level on signal to CRITICAL without valid institutional signature
    tampered_signal = signal.model_copy(update={"sanitized_risk_level": "CRITICAL"})

    with pytest.raises(ConsortiumSecurityError, match="Sanitized risk level mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_injecting_typology_terrorist_financing_fails() -> None:
    """Requirement 4: Injecting typology TERRORIST_FINANCING into signed result fails."""
    cbi = _create_cbi_service("c_alpha")
    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha")

    # Adversary injects TERRORIST_FINANCING
    tampered_typologies = list(signal.sanitized_typology_indicators) + ["TERRORIST_FINANCING"]
    tampered_signal = signal.model_copy(update={"sanitized_typology_indicators": tampered_typologies})

    with pytest.raises(ConsortiumSecurityError, match="Typology mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_changing_disclosure_mode_or_attributed_institutions_fails() -> None:
    """Requirement 5: Changing disclosure mode or attributed institutions fails without authenticated proof."""
    cbi = _create_cbi_service("c_alpha")
    # Build signal in ANONYMIZED_SUMMARY mode
    signal, req, _, _, _ = _build_authentic_test_signal(
        "c_alpha",
        disclosure_mode="ANONYMIZED_SUMMARY",
    )

    # Adversary escalates disclosure mode to ATTRIBUTED_INSTITUTIONS
    tampered_signal = signal.model_copy(
        update={
            "disclosure_mode": "ATTRIBUTED_INSTITUTIONS",
            "contributing_institutions": ["bank_b"],
        }
    )

    with pytest.raises(
        ConsortiumSecurityError,
        match="Disclosure mode mismatch|Contributing institutions mismatch",
    ):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_modifying_psi_result_or_category_fails() -> None:
    """Requirement 6: Modifying PSI result (match_confirmed) or category fails."""
    cbi = _create_cbi_service("c_alpha")
    signal, req, _, _, _ = _build_authentic_test_signal(
        "c_alpha",
        match_confirmed=False,
        match_category="NO_MATCH",
        match_confidence=0.0,
        sanitized_risk_level="UNOBSERVED",
        sanitized_typologies=(),
    )

    # 1. Modify match_confirmed False -> True
    tampered_confirmed = signal.model_copy(update={"match_confirmed": True})
    with pytest.raises(ConsortiumSecurityError, match="Match confirmation status mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_confirmed,
            expected_req=req,
            audited_actor="analyst_alice",
        )

    # 2. Modify match_category NO_MATCH -> EXACT_MATCH
    tampered_category = signal.model_copy(update={"match_category": "EXACT_MATCH"})
    with pytest.raises(ConsortiumSecurityError, match="Match category mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_category,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_changing_session_entity_grant_revision_or_target_bank_fails() -> None:
    """Requirement 7: Changing session, entity, grant revision, or target bank fails."""
    cbi = _create_cbi_service("c_alpha")
    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha")

    # 1. Tamper target bank
    tampered_prov = deepcopy(signal.provenance)
    tampered_prov["target_bank_id"] = "bank_evil"
    with pytest.raises(ConsortiumSecurityError, match="Target counterpart bank mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=signal.model_copy(update={"provenance": tampered_prov}),
            expected_req=req,
            audited_actor="analyst_alice",
        )

    # 2. Tamper query entity
    tampered_prov = deepcopy(signal.provenance)
    tampered_prov["query_entity_id"] = "ent_different_999"
    with pytest.raises(ConsortiumSecurityError, match="Query entity identifier mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=signal.model_copy(update={"provenance": tampered_prov}),
            expected_req=req,
            audited_actor="analyst_alice",
        )

    # 3. Tamper grant revision
    tampered_prov = deepcopy(signal.provenance)
    tampered_prov["ticket"]["grant_revision"] = 99
    with pytest.raises(ConsortiumSecurityError, match="grant revision mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=signal.model_copy(update={"provenance": tampered_prov}),
            expected_req=req,
            audited_actor="analyst_alice",
        )

    # 4. Tamper job session
    tampered_prov = deepcopy(signal.provenance)
    tampered_prov["job_id"] = "job_stolen_session_123"
    with pytest.raises(ConsortiumSecurityError, match="Job ID mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=signal.model_copy(update={"provenance": tampered_prov}),
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_substituting_new_institutional_public_key_fails() -> None:
    """Requirement 8: Substituting a new institutional public key fails without authorized rotation."""
    gov = _create_mock_consortium_service("c_alpha")
    reg = InstitutionalVerificationRegistry()
    reg.register_public_key("bank_a", InstitutionalSigningIdentity.from_seed("bank_a", SECRET_A).public_key)
    reg.register_public_key("bank_b", InstitutionalSigningIdentity.from_seed("bank_b", SECRET_B).public_key)

    # Attacker tries to un-authenticatedly replace bank_b key in registry
    evil_identity = InstitutionalSigningIdentity.generate("bank_b")
    # Verify signature from attacker key fails against enrolled registry
    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha", sign_b=evil_identity)
    cbi = CrossBankIntelligenceService(consortium_service=gov, institutional_registry=reg)

    with pytest.raises(
        ConsortiumSecurityError,
        match="Cryptographic verification failed for Counterpart Bank B execution evidence",
    ):
        cbi._admit_and_verify_operational_signal(
            signal=signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_replaying_signed_execution_evidence_fails() -> None:
    """Requirement 9: Replaying signed execution evidence fails (anti-replay defense)."""
    cbi = _create_cbi_service("c_alpha")
    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha")

    # First admission succeeds
    cbi._admit_and_verify_operational_signal(
        signal=signal,
        expected_req=req,
        audited_actor="analyst_alice",
    )

    # Replay of the exact same receipt fails closed
    with pytest.raises(ConsortiumSecurityError, match="Replay attack detected"):
        cbi._admit_and_verify_operational_signal(
            signal=signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_analyst_service_cannot_access_private_signing_keys() -> None:
    """Requirement 10: Analyst host / service cannot access either bank's private signing keys."""
    reg = InstitutionalVerificationRegistry()
    # 1. Attempting to register private key raises PermissionError
    fake_priv = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(PermissionError, match="Private key material cannot be registered"):
        reg.register_public_key("bank_a", fake_priv)  # type: ignore[arg-type]

    # 2. InstitutionalVerificationRegistry only has public keys and verify_signature method
    reg.register_public_key("bank_a", fake_priv.public_key())
    pub = reg.get_public_key("bank_a")
    assert isinstance(pub, ed25519.Ed25519PublicKey)
    assert not hasattr(pub, "sign")
    assert not hasattr(pub, "private_bytes")


def test_adversarial_missing_execution_evidence_fails() -> None:
    """Negative control: Missing receipt or ticket in provenance fails closed."""
    cbi = _create_cbi_service("c_alpha")
    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha")

    tampered_provenance = deepcopy(signal.provenance)
    del tampered_provenance["receipt"]
    tampered_signal = signal.model_copy(update={"provenance": tampered_provenance})

    with pytest.raises(ConsortiumSecurityError, match="Missing or corrupt authentic worker execution receipt"):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_revoked_consent_rejects_admission() -> None:
    """Negative control: Bank B revokes bilateral consent, subsequent admission fails closed."""
    gov = _create_mock_consortium_service("c_alpha")
    grant = gov._consortia["c_alpha"].metadata["operational_data_sharing_grant"]
    grant["revoked_by_institutions"] = ["bank_b"]

    reg = InstitutionalVerificationRegistry()
    reg.register_public_key("bank_a", InstitutionalSigningIdentity.from_seed("bank_a", SECRET_A).public_key)
    reg.register_public_key("bank_b", InstitutionalSigningIdentity.from_seed("bank_b", SECRET_B).public_key)
    cbi = CrossBankIntelligenceService(consortium_service=gov, institutional_registry=reg)
    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha")

    with pytest.raises(ConsortiumSecurityError, match="Bilateral operational consent for grant .* has been revoked"):
        cbi._admit_and_verify_operational_signal(
            signal=signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )
