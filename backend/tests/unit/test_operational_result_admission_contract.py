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
from dataclasses import asdict, replace
from datetime import UTC
from pathlib import Path
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
    EstablishedPSIQueryMessage,
    IsolatedBankWorkerNode,
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

TEST_SIGNING_IDENTITY_A = InstitutionalSigningIdentity.generate("bank_a")
TEST_SIGNING_IDENTITY_B = InstitutionalSigningIdentity.generate("bank_b")


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CFI_INSTITUTION_PUBLIC_KEY_BANK_A", TEST_SIGNING_IDENTITY_A.public_key_hex)
    monkeypatch.setenv("CFI_INSTITUTION_PUBLIC_KEY_BANK_B", TEST_SIGNING_IDENTITY_B.public_key_hex)
    monkeypatch.setenv("CFI_PEER_KEY_BANK_B", TEST_SIGNING_IDENTITY_B.public_key_hex)
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

    ident_a = sign_a or TEST_SIGNING_IDENTITY_A
    ident_b = sign_b or TEST_SIGNING_IDENTITY_B

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
                "authority": "BILATERAL_INSTITUTIONAL_CONSENT",
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
                "approvals": {
                    "bank_a": {"signature": "sig_a", "scope_digest": digest, "revision": 1},
                    "bank_b": {"signature": "sig_b", "scope_digest": digest, "revision": 1},
                },
            }
        },
    )
    gov._consortia[consortium_id] = consortium
    return gov


def _create_cbi_service(consortium_id: str = "c_alpha") -> CrossBankIntelligenceService:
    gov = _create_mock_consortium_service(consortium_id)
    reg = InstitutionalVerificationRegistry()
    reg.register_public_key("bank_a", TEST_SIGNING_IDENTITY_A.public_key)
    reg.register_public_key("bank_b", TEST_SIGNING_IDENTITY_B.public_key)
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
    reg.register_public_key("bank_a", TEST_SIGNING_IDENTITY_A.public_key)
    reg.register_public_key("bank_b", TEST_SIGNING_IDENTITY_B.public_key)

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
    reg.register_public_key("bank_a", TEST_SIGNING_IDENTITY_A.public_key)
    reg.register_public_key("bank_b", TEST_SIGNING_IDENTITY_B.public_key)
    cbi = CrossBankIntelligenceService(consortium_service=gov, institutional_registry=reg)
    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha")

    with pytest.raises(ConsortiumSecurityError, match="Bilateral operational consent for grant .* has been revoked"):
        cbi._admit_and_verify_operational_signal(
            signal=signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


# =========================================================================
# MILESTONE 4 FINAL BOUNDED SECURITY REMEDIATION ADVERSARIAL TESTS
# =========================================================================


def test_adversarial_peer_cannot_reconstruct_counterpart_signing_key_or_forge_signature() -> None:
    """P0 Requirement: Bank A possesses all legitimate matching and peer credentials,
    but cannot reconstruct Bank B's signing key or produce a Bank B signature accepted by registry.
    Reciprocal test: Bank B possesses Bank A's credentials, cannot forge Bank A's signature.
    """
    bank_a_signing = InstitutionalSigningIdentity.generate("bank_a")
    bank_b_signing = InstitutionalSigningIdentity.generate("bank_b")

    registry = InstitutionalVerificationRegistry()
    registry.register_public_key("bank_a", bank_a_signing.public_key)
    registry.register_public_key("bank_b", bank_b_signing.public_key)

    # Bank A possesses legitimate matching and counterpart context
    canonical_evidence = canonical_counterpart_evidence_bytes(
        version="1.0",
        consortium_id="c_alpha",
        grant_id="grant_c_alpha",
        grant_revision=1,
        grant_scope_digest="scope_123",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        job_id="job_777",
        ticket_digest="ticket_777",
        psi_server_setup_hash="setup_777",
        psi_response_hash="resp_777",
        protocol="OPENMINED-PSI-2.0.6-ECDH",
        nonce="nonce_777",
        timestamp="2026-10-26T00:00:00Z",
    )

    # Bank A attempts to forge Bank B's signature using HMAC credentials
    forged_hmac_sig = hmac.new(b"auth_sec_bank_b", canonical_evidence, hashlib.sha256).hexdigest()
    assert not registry.verify_signature("bank_b", forged_hmac_sig, canonical_evidence), (
        "Registry accepted an HMAC credential signature as an authoritative Ed25519 signature!"
    )

    # Bank A attempts to forge Bank B's signature using its own Ed25519 key
    forged_bank_a_sig = bank_a_signing.sign(canonical_evidence)
    assert not registry.verify_signature("bank_b", forged_bank_a_sig, canonical_evidence), (
        "Registry accepted Bank A's signature as Bank B's signature!"
    )

    # Seed derivation from shared secrets is strictly forbidden
    with pytest.raises(PermissionError, match="Deriving institutional signing keys from shared secrets .* is strictly prohibited"):
        InstitutionalSigningIdentity.from_seed("bank_b", "auth_sec_bank_b")

    # Reciprocal attack: Bank B tries to forge Bank A evaluated result signature
    canonical_evaluated = canonical_evaluated_result_bytes(
        version="1.0",
        receipt_id="rcp_888",
        job_id="job_888",
        consortium_id="c_alpha",
        grant_id="grant_c_alpha",
        grant_revision=1,
        grant_scope_digest="scope_123",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="ent_888",
        ticket_digest="ticket_888",
        counterpart_evidence_digest="cp_888",
        match_confirmed=True,
        match_category="PROBABILISTIC_FUZZY",
        match_confidence=0.999999999,
        matched_risk_level="HIGH",
        matched_typologies=("MONEY_LAUNDERING",),
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        contributing_institutions=("bank_b",),
        protocol="OPENMINED-PSI-2.0.6-ECDH",
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
        executed_at="2026-10-26T00:00:00Z",
    )
    forged_bank_b_sig = bank_b_signing.sign(canonical_evaluated)
    assert not registry.verify_signature("bank_a", forged_bank_b_sig, canonical_evaluated), (
        "Registry accepted Bank B's signature as Bank A's signature!"
    )


def test_adversarial_coordinator_cannot_forge_bank_signatures_using_matching_tokens() -> None:
    """P0 Requirement: The coordinator cannot forge either bank's execution signature
    using its matching tokens or routing secrets.
    """
    bank_a_signing = InstitutionalSigningIdentity.generate("bank_a")
    registry = InstitutionalVerificationRegistry()
    registry.register_public_key("bank_a", bank_a_signing.public_key)

    canonical_msg = b"EVALUATED_OUTCOME_DATA"
    coord_matching_token = "match_token_999888777"
    forged_sig = hmac.new(coord_matching_token.encode(), canonical_msg, hashlib.sha256).hexdigest()

    assert not registry.verify_signature("bank_a", forged_sig, canonical_msg), (
        "Registry accepted coordinator HMAC token as Bank A execution signature!"
    )


def test_adversarial_registry_rejects_arbitrary_string_credentials_and_seeds() -> None:
    """P0 Requirement: Registry rejects arbitrary strings, passwords, HMAC credentials,
    and seed-like credentials as public verification keys.
    """
    reg = InstitutionalVerificationRegistry()

    # Passwords or arbitrary text
    with pytest.raises(ValueError, match="String credentials, passwords, HMAC secrets, and seeds are strictly rejected"):
        reg.register_public_key("bank_a", "password123")

    with pytest.raises(ValueError, match="String credentials, passwords, HMAC secrets, and seeds are strictly rejected"):
        reg.register_public_key("bank_b", "auth_secret_shared_with_peers")

    # Private key material strictly prohibited
    priv = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(PermissionError, match="Security boundary violation: Private key material cannot be registered"):
        reg.register_public_key("bank_c", priv)  # type: ignore[arg-type]


def test_adversarial_registry_prevents_unauthorized_key_replacement() -> None:
    """P0 Requirement: Registry prevents replacement of an existing institutional
    public key through ordinary registration. Authorized rotation must succeed.
    """
    bank_signing = InstitutionalSigningIdentity.generate("bank_b")
    reg = InstitutionalVerificationRegistry()
    reg.register_public_key("bank_b", bank_signing.public_key)

    # Attack: Attacker generates a new key and tries to overwrite Bank B's key via normal registration
    evil_signing = InstitutionalSigningIdentity.generate("bank_b")
    with pytest.raises(ConsortiumSecurityError, match="Unauthorized key replacement via ordinary registration is prohibited"):
        reg.register_public_key("bank_b", evil_signing.public_key)

    # Legitimate path: Authorized rotation signed by current key
    new_signing = InstitutionalSigningIdentity.generate("bank_b")
    current_version = reg._key_versions["bank_b"]
    challenge = f"ROTATE:bank_b:{new_signing.public_key_hex}:{current_version + 1}".encode()
    valid_rotation_sig = bank_signing.sign(challenge)

    reg.rotate_public_key("bank_b", new_signing.public_key, valid_rotation_sig)
    assert reg.get_public_key_hex("bank_b") == new_signing.public_key_hex
    assert reg._key_versions["bank_b"] == current_version + 1


def test_adversarial_signal_integrity_attacks_fail_admission() -> None:
    """P1 Requirements: 5 Required Attacks using genuine signed PSI response:
    1. Change contributing-institution count.
    2. Forge graph correlation indicator or inflated degree band.
    3. Elevate attestation status or operational assurance level.
    4. Inject attributed counterpart when disclosure is anonymous.
    5. Modify additional material evidence field (risk level).
    """
    cbi = _create_cbi_service("c_alpha")

    # Attack 1: Change contributing-institutions count (e.g. claim 5 banks observed)
    sig_1, req_1, _, _, _ = _build_authentic_test_signal("c_alpha")
    sig_1.contributing_institutions_count = 5
    with pytest.raises(ConsortiumSecurityError, match="Contributing institutions count mismatch"):
        cbi._admit_and_verify_operational_signal(signal=sig_1, expected_req=req_1, audited_actor="analyst")

    # Attack 2a: Forge graph correlation indicator (claim cross-bank correlation when no match)
    sig_2a, req_2a, _, _, _ = _build_authentic_test_signal("c_alpha", match_confirmed=False, match_category="NO_MATCH", match_confidence=0.0)
    sig_2a.graph_context["has_incident_cross_bank_correlations"] = True
    with pytest.raises(ConsortiumSecurityError, match="Graph context correlation forgery detected"):
        cbi._admit_and_verify_operational_signal(signal=sig_2a, expected_req=req_2a, audited_actor="analyst")

    # Attack 2b: Inflate relationship degree band to CONSORTIUM_WIDE
    sig_2b, req_2b, _, _, _ = _build_authentic_test_signal("c_alpha")
    sig_2b.graph_context["sanitized_degree_band"] = "CONSORTIUM_WIDE"
    with pytest.raises(ConsortiumSecurityError, match="Inflated graph relationship degree"):
        cbi._admit_and_verify_operational_signal(signal=sig_2b, expected_req=req_2b, audited_actor="analyst")

    # Attack 3a: Elevate attestation status to HARDWARE_ENCLAVE_VERIFIED without hardware enclave
    sig_3a, req_3a, _, _, _ = _build_authentic_test_signal("c_alpha")
    sig_3a.attestation_status = "HARDWARE_ENCLAVE_VERIFIED"
    with pytest.raises(ConsortiumSecurityError, match="Elevated or unverified attestation status"):
        cbi._admit_and_verify_operational_signal(signal=sig_3a, expected_req=req_3a, audited_actor="analyst")

    # Attack 3b: Elevate assurance level to HARDWARE_CONFIDENTIAL_COMPUTE
    sig_3b, req_3b, _, _, _ = _build_authentic_test_signal("c_alpha")
    sig_3b.assurance_level = "HARDWARE_CONFIDENTIAL_COMPUTE"  # type: ignore[assignment]
    with pytest.raises(ConsortiumSecurityError, match="Unverified assurance level"):
        cbi._admit_and_verify_operational_signal(signal=sig_3b, expected_req=req_3b, audited_actor="analyst")

    # Attack 4: Inject attributed counterpart when disclosure is anonymous
    sig_4, req_4, _, _, _ = _build_authentic_test_signal("c_alpha", disclosure_mode="ANONYMIZED_SUMMARY")
    sig_4.contributing_institutions = ["bank_b"]  # attacker tries to unmask counterpart
    with pytest.raises(ConsortiumSecurityError, match="Attributed institutions injected into signal under anonymous disclosure mode"):
        cbi._admit_and_verify_operational_signal(signal=sig_4, expected_req=req_4, audited_actor="analyst")

    # Attack 5: Tamper risk level without valid signature
    sig_5, req_5, _, _, _ = _build_authentic_test_signal("c_alpha", sanitized_risk_level="LOW")
    sig_5.sanitized_risk_level = "CRITICAL"
    with pytest.raises(ConsortiumSecurityError, match="Sanitized risk level mismatch"):
        cbi._admit_and_verify_operational_signal(signal=sig_5, expected_req=req_5, audited_actor="analyst")


def test_missing_signing_key_fails_closed_in_worker_node(tmp_path: Path) -> None:
    """P0 Requirement: Missing institutional signing key provisioning causes worker to fail closed,
    rather than silently fabricating a new key.
    """
    from app.application.services.isolated_matching_service import IsolatedBankWorkerNode

    worker = IsolatedBankWorkerNode(
        bank_id="bank_unprovisioned",
        storage_root=str(tmp_path),
        signing_key=None,
    )

    with pytest.raises(ConsortiumSecurityError, match="Worker node 'bank_unprovisioned' has no provisioned signing key"):
        _ = worker.public_key_hex


def _setup_adversarial_test_fixture(
    tmp_path: Path,
) -> tuple[
    IsolatedBankWorkerNode,
    IsolatedBankWorkerNode,
    InstitutionalSigningIdentity,
    InstitutionalSigningIdentity,
    OperationAuthorizationTicket,
    EstablishedPSIQueryMessage,
]:
    ident_a = InstitutionalSigningIdentity.generate("bank_a")
    ident_b = InstitutionalSigningIdentity.generate("bank_b")

    worker_a = IsolatedBankWorkerNode(
        bank_id="bank_a",
        storage_root=str(tmp_path),
        signing_key=ident_a,
        trusted_peer_keys={"bank_b": ident_b.public_key_hex},
    )
    worker_b = IsolatedBankWorkerNode(
        bank_id="bank_b",
        storage_root=str(tmp_path),
        signing_key=ident_b,
        trusted_peer_keys={"bank_a": ident_a.public_key_hex},
    )

    grant_id = f"grant_{uuid.uuid4().hex[:8]}"
    scope_digest = "b" * 64
    worker_a.register_authorized_grant(
        consortium_id="consortium_test",
        grant_id=grant_id,
        revision=1,
        scope_digest=scope_digest,
        permitted_counterparts=["bank_b"],
        permitted_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
    )
    worker_b.register_authorized_grant(
        consortium_id="consortium_test",
        grant_id=grant_id,
        revision=1,
        scope_digest=scope_digest,
        permitted_counterparts=["bank_a"],
        permitted_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
    )

    now = datetime.datetime.now(UTC)
    expires = (now + datetime.timedelta(seconds=300)).isoformat()
    raw_ticket = OperationAuthorizationTicket(
        ticket_id=f"tkt_{uuid.uuid4().hex[:12]}",
        consortium_id="consortium_test",
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        operation="ESTABLISHED_PSI",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        query_scope="entity:ent_test",
        grant_id=grant_id,
        grant_revision=1,
        grant_scope_digest=scope_digest,
        job_id=f"job_{uuid.uuid4().hex[:8]}",
        issued_at=now.isoformat(),
        expires_at=expires,
        bank_approvals={},
    )
    sig_a = worker_a.sign_operation_authorization_ticket(raw_ticket)
    sig_b = worker_b.sign_operation_authorization_ticket(raw_ticket)
    ticket = replace(raw_ticket, bank_approvals={"bank_a": sig_a, "bank_b": sig_b})

    canonical_query = (
        f"{ticket.job_id}:{ticket.consortium_id}:bank_a:bank_b:"
        f"dummy_b64:nonce_123:{now.isoformat()}:{ticket.purpose}:{ticket.canonical_digest()}".encode()
    )
    query_sig = ident_a.sign(canonical_query)
    query = EstablishedPSIQueryMessage(
        job_id=ticket.job_id,
        consortium_id=ticket.consortium_id,
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        psi_request_b64="dummy_b64",
        nonce="nonce_123",
        timestamp=now.isoformat(),
        purpose=ticket.purpose,
        signature=query_sig,
        ticket=ticket,
    )

    return worker_a, worker_b, ident_a, ident_b, ticket, query


def test_adversarial_hmac_with_public_key_rejected_in_psi_query(tmp_path: Path) -> None:
    """1. Mandatory negative control: Forged Bank A PSI query signed using HMAC with Bank A's public key."""
    _, worker_b, ident_a, _, _, query = _setup_adversarial_test_fixture(tmp_path)

    canonical_query = (
        f"{query.job_id}:{query.consortium_id}:{query.requesting_bank_id}:"
        f"{query.target_bank_id}:{query.psi_request_b64}:{query.nonce}:"
        f"{query.timestamp}:{query.purpose}:{query.ticket.canonical_digest()}".encode()
    )
    # Attacker knows Bank A's public key and computes HMAC with it
    forged_hmac_sig = hmac.new(
        ident_a.public_key_hex.encode("utf-8"), canonical_query, hashlib.sha256
    ).hexdigest()
    attacker_query = replace(query, signature=forged_hmac_sig)

    # Bank B must reject before private matching
    with pytest.raises(
        ConsortiumSecurityError,
        match="Peer authentication failed: Invalid signature from requesting bank 'bank_a'",
    ):
        worker_b.process_established_psi_counterpart(attacker_query)


def test_adversarial_hmac_with_public_key_rejected_in_ticket_approval(tmp_path: Path) -> None:
    """2. Mandatory negative control: Forged Bank A operation-ticket approval signed with HMAC using Bank A's public key."""
    _, worker_b, ident_a, _, ticket, query = _setup_adversarial_test_fixture(tmp_path)

    req_canonical = f"{ticket.canonical_digest()}:bank_a".encode()
    forged_ticket_hmac = hmac.new(
        ident_a.public_key_hex.encode("utf-8"), req_canonical, hashlib.sha256
    ).hexdigest()
    forged_ticket = replace(
        ticket,
        bank_approvals={"bank_a": forged_ticket_hmac, "bank_b": ticket.bank_approvals["bank_b"]},
    )
    canonical_env = (
        f"{query.job_id}:{query.consortium_id}:{query.requesting_bank_id}:"
        f"{query.target_bank_id}:{query.psi_request_b64}:{query.nonce}:"
        f"{query.timestamp}:{query.purpose}:{forged_ticket.canonical_digest()}".encode()
    )
    attacker_query = replace(query, ticket=forged_ticket, signature=ident_a.sign(canonical_env))

    with pytest.raises(
        ConsortiumSecurityError,
        match="Requesting institution 'bank_a' authorization verification failed",
    ):
        worker_b.process_established_psi_counterpart(attacker_query)


def test_adversarial_completely_missing_requesting_bank_ticket_signature(tmp_path: Path) -> None:
    """3. Mandatory negative control: Completely missing requesting-bank ticket signature."""
    _, worker_b, ident_a, _, ticket, query = _setup_adversarial_test_fixture(tmp_path)

    ticket_missing_a = replace(
        ticket,
        bank_approvals={"bank_b": ticket.bank_approvals["bank_b"]},
        requesting_bank_signature="",
    )
    canonical_env = (
        f"{query.job_id}:{query.consortium_id}:{query.requesting_bank_id}:"
        f"{query.target_bank_id}:{query.psi_request_b64}:{query.nonce}:"
        f"{query.timestamp}:{query.purpose}:{ticket_missing_a.canonical_digest()}".encode()
    )
    attacker_query = replace(query, ticket=ticket_missing_a, signature=ident_a.sign(canonical_env))

    with pytest.raises(
        ConsortiumSecurityError, match="missing approval signature in ticket"
    ):
        worker_b.process_established_psi_counterpart(attacker_query)


def test_adversarial_invalid_ed25519_signature_with_valid_consortium_grant(tmp_path: Path) -> None:
    """4. Mandatory negative control: Invalid Ed25519 signature with otherwise valid consortium grant."""
    _, worker_b, ident_a, _, ticket, query = _setup_adversarial_test_fixture(tmp_path)

    corrupt_sig = "ee" * 64
    ticket_corrupt_sig = replace(
        ticket,
        bank_approvals={"bank_a": corrupt_sig, "bank_b": ticket.bank_approvals["bank_b"]},
    )
    canonical_env = (
        f"{query.job_id}:{query.consortium_id}:{query.requesting_bank_id}:"
        f"{query.target_bank_id}:{query.psi_request_b64}:{query.nonce}:"
        f"{query.timestamp}:{query.purpose}:{ticket_corrupt_sig.canonical_digest()}".encode()
    )
    attacker_query = replace(query, ticket=ticket_corrupt_sig, signature=ident_a.sign(canonical_env))

    with pytest.raises(
        ConsortiumSecurityError,
        match="Requesting institution 'bank_a' authorization verification failed",
    ):
        worker_b.process_established_psi_counterpart(attacker_query)


def test_adversarial_coordinator_cannot_impersonate_institutions_with_matching_tokens(tmp_path: Path) -> None:
    """5. Mandatory negative control: Coordinator possessing matching tokens but no bank signing keys."""
    from app.application.services.isolated_matching_service import (
        CrossBankMatchingCoordinator,
    )
    from app.infrastructure.security.auth_service import AuthenticationService

    gov = _create_mock_consortium_service("c_alpha")
    coord = CrossBankMatchingCoordinator(
        consortium_service=gov,
        storage_root=str(tmp_path),
    )

    class FakeWorkerWithoutSigningIdentity:
        def __init__(self, bank_id: str):
            self.bank_id = bank_id
            self._auth_secret = "matching_token_coord"
            self._post = True

    coord.register_worker("bank_a", FakeWorkerWithoutSigningIdentity("bank_a"), matching_token="coord_tok_a")
    coord.register_worker("bank_b", FakeWorkerWithoutSigningIdentity("bank_b"), matching_token="coord_tok_b")

    auth_svc = AuthenticationService.get_instance()
    user = auth_svc.register_user(
        f"analyst_{uuid.uuid4().hex[:6]}", "Password123!", "bank_a", roles=["analyst"]
    )
    auth_token = auth_svc.create_token_bundle(user).access_token

    req = CrossBankIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        consortium_id="c_alpha",
        query_entity_id="e_test",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        use_established_psi=True,
    )
    with pytest.raises(
        ConsortiumSecurityError,
        match="Coordinator cannot obtain authorization signature from requesting bank 'bank_a'",
    ):
        coord.execute_two_bank_private_matching(req=req, auth_token=auth_token)


def test_adversarial_peer_key_replacement_via_allow_rotation_bypassed_rejected(tmp_path: Path) -> None:
    """6. Mandatory negative control: Attempted peer public-key replacement using allow_rotation=True without valid rotation signature."""
    _, worker_b, ident_a, _, _, _ = _setup_adversarial_test_fixture(tmp_path)

    evil_ident = InstitutionalSigningIdentity.generate("bank_a")
    with pytest.raises(
        ConsortiumSecurityError,
        match="Ordinary registration must never overwrite an existing institutional key",
    ):
        worker_b.enroll_trusted_peer("bank_a", evil_ident.public_key_hex, allow_rotation=True)

    # Confirm original key was not modified
    assert worker_b.get_trusted_peer_key("bank_a") == ident_a.public_key_hex


def test_authorized_key_rotation_signed_by_old_active_key(tmp_path: Path) -> None:
    """7. Mandatory positive control: Authorized rotation signed by the old active institutional key."""
    _, worker_b, ident_a, _, _, _ = _setup_adversarial_test_fixture(tmp_path)

    ident_a_v2 = InstitutionalSigningIdentity.generate("bank_a")
    challenge = f"ROTATE:bank_a:{ident_a_v2.public_key_hex}:2".encode()
    rotation_sig = ident_a.sign(challenge)

    worker_b.rotate_trusted_peer("bank_a", ident_a_v2.public_key_hex, rotation_sig)
    assert worker_b.get_trusted_peer_key("bank_a") == ident_a_v2.public_key_hex


def test_adversarial_stale_key_verification_fails_after_rotation(tmp_path: Path) -> None:
    """8. Mandatory negative control: Stale-key verification after rotation."""
    _, worker_b, ident_a, _, ticket, _ = _setup_adversarial_test_fixture(tmp_path)

    ident_a_v2 = InstitutionalSigningIdentity.generate("bank_a")
    challenge = f"ROTATE:bank_a:{ident_a_v2.public_key_hex}:2".encode()
    rotation_sig = ident_a.sign(challenge)
    worker_b.rotate_trusted_peer("bank_a", ident_a_v2.public_key_hex, rotation_sig)

    # 1. Attacker attempts to use old key ident_a after rotation
    now = datetime.datetime.now(UTC)
    stale_canonical = (
        f"{ticket.job_id}:{ticket.consortium_id}:bank_a:bank_b:"
        f"dummy_b64:nonce_stale:{now.isoformat()}:{ticket.purpose}:{ticket.canonical_digest()}".encode()
    )
    stale_query_sig = ident_a.sign(stale_canonical)
    stale_query = EstablishedPSIQueryMessage(
        job_id=ticket.job_id,
        consortium_id=ticket.consortium_id,
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        psi_request_b64="dummy_b64",
        nonce="nonce_stale",
        timestamp=now.isoformat(),
        purpose=ticket.purpose,
        signature=stale_query_sig,
        ticket=ticket,
    )
    with pytest.raises(
        ConsortiumSecurityError,
        match="Peer authentication failed: Invalid signature from requesting bank 'bank_a'",
    ):
        worker_b.process_established_psi_counterpart(stale_query)

    # 2. Genuine new key ident_a_v2 signs successfully
    fresh_ticket_sig_a = ident_a_v2.sign(f"{ticket.canonical_digest()}:bank_a".encode())
    fresh_ticket = replace(
        ticket,
        bank_approvals={"bank_a": fresh_ticket_sig_a, "bank_b": ticket.bank_approvals["bank_b"]},
    )
    fresh_canonical = (
        f"{ticket.job_id}:{ticket.consortium_id}:bank_a:bank_b:"
        f"dummy_b64:nonce_fresh:{now.isoformat()}:{ticket.purpose}:{fresh_ticket.canonical_digest()}".encode()
    )
    fresh_query_sig = ident_a_v2.sign(fresh_canonical)
    fresh_query = EstablishedPSIQueryMessage(
        job_id=ticket.job_id,
        consortium_id=ticket.consortium_id,
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        psi_request_b64="dummy_b64",
        nonce="nonce_fresh",
        timestamp=now.isoformat(),
        purpose=ticket.purpose,
        signature=fresh_query_sig,
        ticket=fresh_ticket,
    )
    # Verification of query and ticket succeeds against rotated key
    worker_b.verify_operation_authorization(
        fresh_query.ticket,
        expected_job_id=fresh_query.job_id,
        expected_requesting_bank=fresh_query.requesting_bank_id,
        expected_purpose=fresh_query.purpose,
    )


def test_adversarial_replayed_authorization_ticket_fails_before_matching(tmp_path: Path) -> None:
    """9. Mandatory negative control: Replayed authorization ticket."""
    _, worker_b, _, _, ticket, query = _setup_adversarial_test_fixture(tmp_path)

    # Consume ticket in worker_b
    worker_b._persist_consumed_nonce(f"ticket:{ticket.ticket_id}")

    # Replayed query with same ticket but fresh nonce
    replayed_query = replace(query, nonce=f"nonce_replay_{uuid.uuid4().hex[:8]}")
    with pytest.raises(
        ConsortiumSecurityError,
        match="Replay attack detected: Operation authorization ticket .* has already been processed",
    ):
        worker_b.verify_operation_authorization(
            replayed_query.ticket,
            expected_job_id=replayed_query.job_id,
            expected_requesting_bank=replayed_query.requesting_bank_id,
            expected_purpose=replayed_query.purpose,
        )
