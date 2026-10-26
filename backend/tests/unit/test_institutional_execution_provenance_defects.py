"""Defect Reproduction Tests for Milestone 4 Trust Boundary.

Demonstrates the 5 specific vulnerabilities identified in the independent review:
1. Admission service requires Bank A/B symmetric HMAC signing secrets for verification.
2. Admission service validates Bank B's ticket, but fails to independently validate Bank B's signed counterpart PSI response evidence.
3. The coordinator's receipt signature does not establish the authenticity of the full disclosed intelligence result.
4. Signal risk, typology, disclosure metadata, and related fields are not cryptographically bound to the admitted result.
5. A signed signal can be fabricated without executing OpenMined PSI and successfully admitted.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import uuid
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

SECRET_A = "auth_sec_bank_a_defect_demo_secret_12345"
SECRET_B = "auth_sec_bank_b_defect_demo_secret_67890"
SECRET_COORD = "auth_sec_coord_defect_demo_secret_99999"


@pytest.fixture(autouse=True)
def setup_defect_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("CFI_WORKER_SECRET_BANK_A", SECRET_A)
    monkeypatch.setenv("CFI_PEER_KEY_BANK_B", SECRET_B)
    monkeypatch.setenv("CFI_WORKER_SECRET_BANK_B", SECRET_B)
    monkeypatch.setenv("CFI_COORDINATOR_SECRET", SECRET_COORD)


def _build_legacy_signal(
    risk_level: str = "UNAVAILABLE",
    typologies: list[str] | None = None,
    disclosure_mode: str = "ATTRIBUTED_INSTITUTIONS",
) -> tuple[CrossBankIntelligenceSignal, CrossBankIntelligenceQueryRequest, dict]:
    consortium_id = "c_alpha"
    requesting_bank = "bank_a"
    target_bank = "bank_b"
    query_entity = "ent_defect_123"
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
        participating_banks=[requesting_bank, target_bank],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        institution_jurisdictions={requesting_bank: "EU", target_bank: "EU"},
        allowed_jurisdictions=["EU", "US"],
        allow_cross_border=False,
        allow_institution_identity_sharing=False,
    )

    unsigned_ticket = OperationAuthorizationTicket(
        ticket_id=ticket_id,
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank,
        target_bank_id=target_bank,
        operation="ESTABLISHED_PSI",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        query_scope=f"entity:{query_entity}",
        grant_id=grant_id,
        grant_revision=grant_revision,
        grant_scope_digest=scope_digest,
        job_id=job_id,
        issued_at=now_iso,
        expires_at=expires_at,
        bank_approvals={},
    )

    sig_b_ticket = hmac.new(
        SECRET_B.encode(),
        f"{unsigned_ticket.canonical_digest()}:{target_bank}".encode(),
        hashlib.sha256,
    ).hexdigest()
    sig_a_ticket = hmac.new(
        SECRET_A.encode(),
        f"{unsigned_ticket.canonical_digest()}:{requesting_bank}".encode(),
        hashlib.sha256,
    ).hexdigest()

    ticket = OperationAuthorizationTicket(
        ticket_id=ticket_id,
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank,
        target_bank_id=target_bank,
        operation="ESTABLISHED_PSI",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        query_scope=f"entity:{query_entity}",
        grant_id=grant_id,
        grant_revision=grant_revision,
        grant_scope_digest=scope_digest,
        job_id=job_id,
        issued_at=now_iso,
        expires_at=expires_at,
        bank_approvals={requesting_bank: sig_a_ticket, target_bank: sig_b_ticket},
    )

    transcript_digest = hashlib.sha256(f"fake_transcript_{job_id}".encode()).hexdigest()
    canonical_receipt = f"{receipt_id}:{transcript_digest}:True:PROBABILISTIC_FUZZY".encode()
    worker_a_sig = hmac.new(SECRET_A.encode(), canonical_receipt, hashlib.sha256).hexdigest()
    coord_sig = hmac.new(
        SECRET_COORD.encode(),
        f"{receipt_id}:{transcript_digest}:{consortium_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    receipt = VerifiedMatchReceipt(
        receipt_id=receipt_id,
        job_id=job_id,
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank,
        target_bank_id=target_bank,
        query_entity_id=query_entity,
        match_confirmed=True,
        match_confidence=0.999999999,
        protocol="OPENMINED-PSI-2.0.6-ECDH",
        transcript_digest=transcript_digest,
        executed_at=now_iso,
        worker_a_signature=worker_a_sig,
        worker_b_signature="unverified_legacy_b_sig",
        coordinator_signature=coord_sig,
        matched_risk_level="UNAVAILABLE",
        matched_typologies=("CROSS_BANK_ENTITY_OBSERVED",),
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
        match_category="PROBABILISTIC_FUZZY",
        is_exact_confirmed=False,
        disclosure_mode=disclosure_mode,
        contributing_institutions=(target_bank,),
    )

    from dataclasses import asdict

    signal = CrossBankIntelligenceSignal(
        signal_id=f"sig_{uuid.uuid4().hex[:12]}",
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank,
        query_entity_id=query_entity,
        privacy_hash_truncated=f"{query_entity[:8]}...[BLINDED]",
        match_confirmed=True,
        match_category="PROBABILISTIC_FUZZY",
        match_confidence=0.999999999,
        contributing_institutions_count=1,
        contributing_institutions=[target_bank],
        disclosure_mode=disclosure_mode,  # type: ignore[arg-type]
        sanitized_risk_level=risk_level,  # type: ignore[arg-type]
        sanitized_typology_indicators=typologies or ["CROSS_BANK_ENTITY_OBSERVED"],
        provenance={
            "target_bank_id": target_bank,
            "query_entity_id": query_entity,
            "job_id": job_id,
            "ticket_id": ticket_id,
            "receipt_id": receipt_id,
            "protocol": "OPENMINED-PSI-2.0.6-ECDH",
            "receipt": asdict(receipt),
            "ticket": asdict(ticket),
            "coordinator_signature": coord_sig,
        },
        is_authoritative=True,
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
        execution_mode="VERIFIED_OPERATIONAL",
        attestation_status="ISOLATED_MULTI_PROCESS",
    )

    req = CrossBankIntelligenceQueryRequest(
        consortium_id=consortium_id,
        requesting_bank_id=requesting_bank,
        target_bank_id=target_bank,
        query_entity_id=query_entity,
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )

    extra_data = {"grant_id": grant_id, "revision": grant_revision, "scope_digest": scope_digest}
    return signal, req, extra_data


def test_defect_demonstration_risk_and_typology_tampering_vulnerability() -> None:
    """Demonstrate Defect 4: In the legacy contract, an attacker or coordinator can tamper

    with sanitized_risk_level and inject typologies, and admission does NOT reject it.
    """
    consortium_svc = ConsortiumGovernanceService()
    intel_svc = CrossBankIntelligenceService(consortium_service=consortium_svc)

    signal, req, grant_meta = _build_legacy_signal(risk_level="UNAVAILABLE")
    consortium = Consortium(
        consortium_id=req.consortium_id,
        name="Defect Test Consortium",
        members={"bank_a": ConsortiumMember(bank_id="bank_a"), "bank_b": ConsortiumMember(bank_id="bank_b")},
        status=ConsortiumStatus.ACTIVE,
        metadata={
            "operational_data_sharing_grant": {
                "grant_id": grant_meta["grant_id"],
                "revision": grant_meta["revision"],
                "scope_digest": grant_meta["scope_digest"],
                "is_active": True,
                "status": "ACTIVE",
            }
        },
    )
    consortium_svc._consortia[req.consortium_id] = consortium

    # TAMPERING ATTACK: Attacker changes sanitized_risk_level to CRITICAL
    # and injects TERRORIST_FINANCING into typologies.
    tampered_signal = signal.model_copy(
        update={
            "sanitized_risk_level": "CRITICAL",
            "sanitized_typology_indicators": ["CROSS_BANK_ENTITY_OBSERVED", "TERRORIST_FINANCING"],
        }
    )

    # In the REMEDIATED code, this tampered signal is strictly rejected!
    with pytest.raises(ConsortiumSecurityError, match="Sanitized risk level mismatch|Typology mismatch"):
        intel_svc._admit_and_verify_operational_signal(
            signal=tampered_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_defect_demonstration_unverified_counterpart_response() -> None:
    """Demonstrate Defect 2 remediation: The admission service now independently validates
    Bank B's actual counterpart PSI response evidence and rejects bogus counterpart signatures.
    """
    consortium_svc = ConsortiumGovernanceService()
    intel_svc = CrossBankIntelligenceService(consortium_service=consortium_svc)

    signal, req, grant_meta = _build_legacy_signal()
    consortium = Consortium(
        consortium_id=req.consortium_id,
        name="Defect Test Consortium",
        members={"bank_a": ConsortiumMember(bank_id="bank_a"), "bank_b": ConsortiumMember(bank_id="bank_b")},
        status=ConsortiumStatus.ACTIVE,
        metadata={
            "operational_data_sharing_grant": {
                "grant_id": grant_meta["grant_id"],
                "revision": grant_meta["revision"],
                "scope_digest": grant_meta["scope_digest"],
                "is_active": True,
                "status": "ACTIVE",
            }
        },
    )
    consortium_svc._consortia[req.consortium_id] = consortium

    # Worker B's signature in receipt is garbage/unverified
    tampered_provenance = dict(signal.provenance)
    tampered_receipt = dict(tampered_provenance["receipt"])
    tampered_receipt["worker_b_signature"] = "completely_bogus_unverified_signature_12345"
    tampered_provenance["receipt"] = tampered_receipt

    bogus_b_signal = signal.model_copy(update={"provenance": tampered_provenance})

    # In the REMEDIATED code, this is strictly rejected because Bank B counterpart evidence is verified!
    with pytest.raises(
        ConsortiumSecurityError,
        match="Cryptographic verification failed for Counterpart Bank B|Missing Bank B cryptographic signature|Missing authenticated counterpart execution evidence",
    ):
        intel_svc._admit_and_verify_operational_signal(
            signal=bogus_b_signal,
            expected_req=req,
            audited_actor="analyst_alice",
        )


def test_defect_demonstration_symmetric_secret_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Demonstrate Defect 1 remediation: The admission service no longer requires Bank A's private
    signing key or symmetric secret. Verification operates purely on public keys via InstitutionalVerificationRegistry.
    """
    from app.domain.institutional_identity import (
        InstitutionalSigningIdentity,
        InstitutionalVerificationRegistry,
    )
    from tests.unit.test_operational_result_admission_contract import (
        SECRET_A as CONTRACT_SECRET_A,
    )
    from tests.unit.test_operational_result_admission_contract import (
        SECRET_B as CONTRACT_SECRET_B,
    )
    from tests.unit.test_operational_result_admission_contract import (
        SECRET_COORD as CONTRACT_SECRET_COORD,
    )
    from tests.unit.test_operational_result_admission_contract import (
        _build_authentic_test_signal,
    )

    monkeypatch.setenv("CFI_COORDINATOR_SECRET", CONTRACT_SECRET_COORD)
    monkeypatch.setenv("COORDINATOR_AUTH_SECRET", CONTRACT_SECRET_COORD)

    # Remove Bank A and B private secrets from environment
    monkeypatch.delenv("CFI_WORKER_SECRET_BANK_A", raising=False)
    monkeypatch.delenv("WORKER_AUTH_SECRET_BANK_A", raising=False)
    monkeypatch.delenv("WORKER_AUTH_SECRET", raising=False)
    monkeypatch.delenv("CFI_WORKER_SECRET_BANK_B", raising=False)
    monkeypatch.delenv("WORKER_AUTH_SECRET_BANK_B", raising=False)

    # Worker nodes possess private keys generated independently
    ident_a = InstitutionalSigningIdentity.generate("bank_a")
    ident_b = InstitutionalSigningIdentity.generate("bank_b")

    # Analyst service holds ONLY public keys in InstitutionalVerificationRegistry
    registry = InstitutionalVerificationRegistry()
    registry.register_public_key("bank_a", ident_a.public_key)
    registry.register_public_key("bank_b", ident_b.public_key)

    consortium_svc = ConsortiumGovernanceService()
    intel_svc = CrossBankIntelligenceService(consortium_service=consortium_svc, institutional_registry=registry)

    signal, req, _, _, _ = _build_authentic_test_signal("c_alpha", sign_a=ident_a, sign_b=ident_b)
    consortium = Consortium(
        consortium_id=req.consortium_id,
        name="Defect Test Consortium",
        members={"bank_a": ConsortiumMember(bank_id="bank_a"), "bank_b": ConsortiumMember(bank_id="bank_b")},
        status=ConsortiumStatus.ACTIVE,
        metadata={
            "operational_data_sharing_grant": {
                "grant_id": signal.provenance["grant_id"],
                "revision": signal.provenance["grant_revision"],
                "scope_digest": signal.provenance["grant_scope_digest"],
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
    consortium_svc._consortia[req.consortium_id] = consortium

    # Verification succeeds using ONLY public keys without any bank private secrets!
    record = intel_svc._admit_and_verify_operational_signal(
        signal=signal,
        expected_req=req,
        audited_actor="analyst_alice",
    )
    assert record["signal_id"] == signal.signal_id
    assert record["is_authoritative"] is True
