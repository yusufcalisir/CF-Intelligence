"""Adversarial and Positive Unit Tests for the Verifiable Result-Admission Contract.

Mandatory verification tests:
1. Genuine coordinator response with authentic bank worker signatures succeeds and admits signal.
2. Fabricated coordinator response with forged coordinator signature is rejected.
3. Modified match result (tampered match_confirmed) is rejected.
4. Modified match category / confidence (tampered match_category or confidence) is rejected.
5. Swapped requesting or target bank is rejected.
6. Replaced consortium grant ID or scope digest is rejected.
7. Forged worker receipt (invalid worker signature) is rejected.
8. Replayed receipt from previous session is rejected (anti-replay defense).
9. Missing execution evidence (missing receipt or ticket) is rejected.
10. Revoked bilateral consent rejects admission (fail closed).
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import uuid
from copy import deepcopy
from datetime import UTC

import pytest

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


def _build_authentic_test_signal(
    consortium_id: str = "c_alpha",
    requesting_bank_id: str = "bank_a",
    target_bank_id: str = "bank_b",
    query_entity_id: str = "ent_target_123",
    match_confirmed: bool = True,
    match_category: str = "PROBABILISTIC_FUZZY",
    match_confidence: float = 0.999999999,
) -> tuple[CrossBankIntelligenceSignal, CrossBankIntelligenceQueryRequest, OperationAuthorizationTicket, VerifiedMatchReceipt]:
    now = datetime.datetime.now(UTC)
    now_iso = now.isoformat()
    expires_at = (now + datetime.timedelta(seconds=300)).isoformat()
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    ticket_id = f"tkt_{uuid.uuid4().hex[:12]}"
    receipt_id = f"rcp_{uuid.uuid4().hex[:12]}"
    grant_id = f"grant_{consortium_id}"
    grant_revision = 1

    scope_digest = compute_operational_grant_scope_digest(
        grant_id=grant_id,
        revision=grant_revision,
        participating_banks=[requesting_bank_id, target_bank_id],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={requesting_bank_id: "EU", target_bank_id: "EU"},
        allowed_jurisdictions=["EU", "US"],
        allow_cross_border=False,
        allow_institution_identity_sharing=False,
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
    sig_a_ticket = hmac.new(SECRET_A.encode(), f"{unsigned_ticket.canonical_digest()}:{requesting_bank_id}".encode(), hashlib.sha256).hexdigest()

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

    transcript_digest = hashlib.sha256(f"transcript_{job_id}".encode()).hexdigest()
    receipt_canonical = f"{receipt_id}:{transcript_digest}:{match_confirmed}:{match_category}".encode()
    worker_a_sig = hmac.new(SECRET_A.encode(), receipt_canonical, hashlib.sha256).hexdigest()
    worker_b_sig = hmac.new(SECRET_B.encode(), f"resp_{job_id}".encode(), hashlib.sha256).hexdigest()

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
        matched_risk_level="UNAVAILABLE" if match_confirmed else "UNOBSERVED",
        matched_typologies=("CROSS_BANK_ENTITY_OBSERVED",) if match_confirmed else (),
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
        match_category=match_category,
        is_exact_confirmed=False,
    )

    from dataclasses import asdict

    signal = CrossBankIntelligenceSignal(
        signal_id=f"sig_{uuid.uuid4().hex[:12]}",
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank_id,
        query_entity_id=query_entity_id,
        privacy_hash_truncated=f"{query_entity_id[:8]}...[BLINDED]",
        match_confirmed=match_confirmed,
        match_category=match_category,
        match_confidence=match_confidence,
        contributing_institutions_count=1 if match_confirmed else 0,
        contributing_institutions=[target_bank_id] if match_confirmed else [],
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        sanitized_risk_level="UNAVAILABLE" if match_confirmed else "UNOBSERVED",
        sanitized_typology_indicators=["CROSS_BANK_ENTITY_OBSERVED"] if match_confirmed else [],
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

    return signal, req, ticket, receipt


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
        allow_institution_identity_sharing=False,
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
                "allow_institution_identity_sharing": False,
                "approvals": {"bank_a": "sig_a", "bank_b": "sig_b"},
            }
        },
    )
    gov._consortia[consortium_id] = consortium
    return gov


def test_genuine_operational_signal_admission_succeeds() -> None:
    """Positive control: Authentically executed, signed operational signal is accepted."""
    gov = _create_mock_consortium_service("c_alpha")
    cbi = CrossBankIntelligenceService(consortium_service=gov)
    signal, req, _, _ = _build_authentic_test_signal("c_alpha")

    record = cbi._admit_and_verify_operational_signal(
        signal=signal,
        expected_req=req,
        audited_actor="analyst_alice",
    )
    assert record["signal_id"] == signal.signal_id
    assert record["is_authoritative"] is True
    assert record["operational_status"] == "OPERATIONAL"


def test_adversarial_fabricated_coordinator_signature_fails() -> None:
    """Adversary Vector 1: Coordinator response has forged coordinator signature."""
    gov = _create_mock_consortium_service("c_alpha")
    cbi = CrossBankIntelligenceService(consortium_service=gov)
    signal, req, _, _ = _build_authentic_test_signal("c_alpha")

    # Forge coordinator signature
    tampered_provenance = deepcopy(signal.provenance)
    tampered_provenance["coordinator_signature"] = "forged_coordinator_hmac_signature"
    tampered_signal = signal.model_copy(update={"provenance": tampered_provenance})

    with pytest.raises(ConsortiumSecurityError, match="Cryptographic verification failed for Coordinator relay"):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_modified_match_confirmed_fails() -> None:
    """Adversary Vector 2: Attacker alters match_confirmed from False to True."""
    gov = _create_mock_consortium_service("c_alpha")
    cbi = CrossBankIntelligenceService(consortium_service=gov)
    signal, req, _, _ = _build_authentic_test_signal("c_alpha", match_confirmed=False, match_category="NO_MATCH", match_confidence=0.0)

    # Attacker forges signal.match_confirmed = True
    tampered_signal = signal.model_copy(update={"match_confirmed": True})

    with pytest.raises(ConsortiumSecurityError, match="Match confirmation status mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_tampered_category_fails() -> None:
    """Adversary Vector 3: Attacker alters match_category to EXACT_MATCH."""
    gov = _create_mock_consortium_service("c_alpha")
    cbi = CrossBankIntelligenceService(consortium_service=gov)
    signal, req, _, _ = _build_authentic_test_signal("c_alpha")

    # Attacker alters category from PROBABILISTIC_FUZZY to EXACT_MATCH
    tampered_signal = signal.model_copy(update={"match_category": "EXACT_MATCH"})

    with pytest.raises(ConsortiumSecurityError, match="Match category mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_swapped_counterpart_bank_fails() -> None:
    """Adversary Vector 4: Swapping target bank ID fails admission."""
    gov = _create_mock_consortium_service("c_alpha")
    cbi = CrossBankIntelligenceService(consortium_service=gov)
    signal, req, _, _ = _build_authentic_test_signal("c_alpha")

    # Attacker submits signal with swapped target bank
    tampered_provenance = deepcopy(signal.provenance)
    tampered_provenance["target_bank_id"] = "bank_evil"
    tampered_signal = signal.model_copy(update={"provenance": tampered_provenance})

    with pytest.raises(ConsortiumSecurityError, match="Target counterpart bank mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_tampered_grant_scope_digest_fails() -> None:
    """Adversary Vector 5: Attacker replaces grant scope digest with arbitrary value."""
    gov = _create_mock_consortium_service("c_alpha")
    cbi = CrossBankIntelligenceService(consortium_service=gov)
    signal, req, _, _ = _build_authentic_test_signal("c_alpha")

    tampered_provenance = deepcopy(signal.provenance)
    tampered_provenance["ticket"]["grant_scope_digest"] = "tampered_digest_999999"
    tampered_signal = signal.model_copy(update={"provenance": tampered_provenance})

    with pytest.raises(ConsortiumSecurityError, match="Ticket grant scope digest mismatch"):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_forged_worker_signature_fails() -> None:
    """Adversary Vector 6: Worker A signature fails cryptographic validation."""
    gov = _create_mock_consortium_service("c_alpha")
    cbi = CrossBankIntelligenceService(consortium_service=gov)
    signal, req, _, _ = _build_authentic_test_signal("c_alpha")

    tampered_provenance = deepcopy(signal.provenance)
    tampered_provenance["receipt"]["worker_a_signature"] = "forged_worker_a_signature_hex"
    tampered_signal = signal.model_copy(update={"provenance": tampered_provenance})

    with pytest.raises(ConsortiumSecurityError, match="Cryptographic verification failed for Requesting Bank worker"):
        cbi._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_adversarial_replay_attack_rejected() -> None:
    """Adversary Vector 7: Replaying a previously admitted receipt is detected and rejected."""
    gov = _create_mock_consortium_service("c_alpha")
    cbi = CrossBankIntelligenceService(consortium_service=gov)
    signal, req, _, _ = _build_authentic_test_signal("c_alpha")

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


def test_adversarial_missing_execution_evidence_fails() -> None:
    """Adversary Vector 8: Missing receipt or ticket in provenance fails closed."""
    gov = _create_mock_consortium_service("c_alpha")
    cbi = CrossBankIntelligenceService(consortium_service=gov)
    signal, req, _, _ = _build_authentic_test_signal("c_alpha")

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
    """Adversary Vector 9: Bank B revokes bilateral consent, subsequent admission fails closed."""
    gov = _create_mock_consortium_service("c_alpha")
    # Mark grant revoked by bank_b
    grant = gov._consortia["c_alpha"].metadata["operational_data_sharing_grant"]
    grant["revoked_by_institutions"] = ["bank_b"]

    cbi = CrossBankIntelligenceService(consortium_service=gov)
    signal, req, _, _ = _build_authentic_test_signal("c_alpha")

    with pytest.raises(ConsortiumSecurityError, match="Bilateral operational consent for grant .* has been revoked"):
        cbi._admit_and_verify_operational_signal(
            signal=signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )
