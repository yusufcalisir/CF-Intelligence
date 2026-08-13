"""Enterprise Security Suite API Endpoints.

Exposes status, ABAC policy testing, HashiCorp Vault secrets metadata,
mTLS certificate status, and tamper-proof SHA-256 cryptographic audit chain verification.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.application.services.federated_unlearning_engine import FederatedUnlearningEngine
from app.config import get_settings
from app.domain.value_objects_pqc import PQCKemAlgorithm, PQCSignatureAlgorithm
from app.domain.value_objects_unlearning import UnlearningMethod
from app.domain.value_objects_zkp import ZKSNARKAttestationProof
from app.infrastructure.security.abac_engine import ABACEngine, ABACResource
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.mtls_manager import MTLSManager
from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator, UserClaims
from app.infrastructure.security.pqc_secagg_driver import PQCSecAggDriver
from app.infrastructure.security.vault_client import VaultClient
from app.infrastructure.security.zk_snark_verifier import ZKSNARKProofVerifier

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/security", tags=["security"])

settings = get_settings()
_mtls_mgr = MTLSManager(ca_cn=settings.mtls_ca_cn)
_oidc_auth = OIDCAuthenticator(
    issuer=settings.oidc_issuer_url,
    audience=settings.oidc_client_id,
    signing_secret=settings.oidc_jwt_signing_secret,
)
_abac_engine = ABACEngine()
_vault_client = VaultClient(
    vault_url=settings.vault_url,
    vault_token=settings.vault_token,
    enabled=settings.vault_enabled,
)
_audit_chain = ImmutableAuditChain.get_instance()


# ── Schemas ───────────────────────────────────────────────────


class ABACEvalRequest(BaseModel):
    user_username: str = "analyst_a1"
    user_bank_id: str = "bank_a"
    user_roles: list[str] = ["analyst"]
    user_clearance: int = 2
    user_shift_hours: str = "08:00-18:00"
    user_approval_tier: float = 50000.0

    resource_type: str = "alert"
    resource_id: str = "alt_1001"
    resource_bank_id: str = "bank_a"
    resource_amount: float = 12500.0
    resource_classification: int = 1

    action: str = "read"
    hour_override: int | None = None


class ABACEvalResponse(BaseModel):
    allowed: bool
    policy_name: str
    reason: str
    evaluated_at: str


class AuditChainEntryResponse(BaseModel):
    index: int
    event_type: str
    actor: str
    target_id: str
    timestamp: str
    details: dict[str, Any]
    prev_hash: str
    curr_hash: str


class AuditChainVerifyResponse(BaseModel):
    is_valid: bool
    total_records: int
    broken_index: int | None = None
    tamper_reason: str | None = None
    genesis_hash: str
    last_hash: str
    verified_at: str


class SecurityStatusResponse(BaseModel):
    mtls: dict[str, Any]
    oidc: dict[str, Any]
    abac: dict[str, Any]
    vault: dict[str, Any]
    audit_chain: dict[str, Any]


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/status", response_model=SecurityStatusResponse)
async def get_security_status() -> SecurityStatusResponse:
    """Get Enterprise Security Suite status across mTLS, OIDC, ABAC, Vault, and Audit Chain."""
    cert = _mtls_mgr.generate_cert_info("gateway.internal")
    chain_rpt = _audit_chain.verify_chain_integrity()
    vault_meta = _vault_client.get_secret_metadata("database/credentials")

    return SecurityStatusResponse(
        mtls={
            "enabled": settings.mtls_enabled,
            "ca_cn": settings.mtls_ca_cn,
            "tls_version": "TLS 1.3",
            "peer_verification": "CERT_REQUIRED",
            "sample_cert": {
                "cn": cert.subject_cn,
                "sans": cert.sans,
                "valid_until": cert.valid_until,
            },
        },
        oidc={
            "enabled": settings.oidc_enabled,
            "issuer": settings.oidc_issuer_url,
            "client_id": settings.oidc_client_id,
            "supported_algorithms": ["RS256", "HS256"],
            "claims_extracted": [
                "sub",
                "bank_id",
                "roles",
                "clearance_level",
                "shift_hours",
                "approval_tier",
            ],
        },
        abac={
            "enabled": settings.abac_enabled,
            "active_rules_count": 5,
            "enforced_policies": [
                "RULE-TENANT-ISOLATION",
                "RULE-SHIFT-HOURS-RESTRICTION",
                "RULE-APPROVAL-TIER-EXCEEDED",
                "RULE-CLEARANCE-LEVEL-INSUFFICIENT",
                "RULE-SUPERADMIN-OVERRIDE",
            ],
        },
        vault={
            "enabled": settings.vault_enabled,
            "vault_url": settings.vault_url,
            "mount_point": "secret",
            "sample_secret_source": vault_meta.source,
        },
        audit_chain={
            "enabled": settings.immutable_audit_chain_enabled,
            "total_events": len(_audit_chain.chain),
            "chain_valid": chain_rpt.is_valid,
            "last_hash": chain_rpt.last_hash,
            "hashing_algorithm": "SHA-256 Chain (H_i = SHA256(L_i || H_{i-1}))",
        },
    )


@router.post("/abac/evaluate", response_model=ABACEvalResponse)
async def evaluate_abac_policy(req: ABACEvalRequest) -> ABACEvalResponse:
    """Test dynamic ABAC policy evaluation for arbitrary user and resource attributes."""
    user = UserClaims(
        sub=f"usr_{req.user_username}",
        username=req.user_username,
        bank_id=req.user_bank_id,
        roles=req.user_roles,
        clearance_level=req.user_clearance,
        shift_hours=req.user_shift_hours,
        approval_tier=req.user_approval_tier,
    )
    resource = ABACResource(
        resource_type=req.resource_type,
        resource_id=req.resource_id,
        bank_id=req.resource_bank_id,
        amount=req.resource_amount,
        classification_level=req.resource_classification,
    )

    res = _abac_engine.evaluate_access(
        user=user,
        resource=resource,
        action=req.action,
        current_hour_override=req.hour_override,
    )

    # Log evaluation in cryptographic audit chain
    _audit_chain.append_event(
        event_type="ABAC_EVALUATION",
        actor=req.user_username,
        target_id=f"{req.resource_type}:{req.resource_id}",
        details={
            "action": req.action,
            "allowed": res.allowed,
            "policy": res.policy_name,
        },
    )

    return ABACEvalResponse(
        allowed=res.allowed,
        policy_name=res.policy_name,
        reason=res.reason,
        evaluated_at=res.evaluated_at,
    )


@router.get("/audit-chain", response_model=list[AuditChainEntryResponse])
async def list_audit_chain(limit: int = Query(50, ge=1, le=200)) -> list[AuditChainEntryResponse]:
    """Get entries from the cryptographic SHA-256 audit chain ledger."""
    entries = _audit_chain.chain[-limit:]
    return [
        AuditChainEntryResponse(
            index=e.index,
            event_type=e.event_type,
            actor=e.actor,
            target_id=e.target_id,
            timestamp=e.timestamp,
            details=e.details,
            prev_hash=e.prev_hash,
            curr_hash=e.curr_hash,
        )
        for e in entries
    ]


@router.post("/audit-chain/verify", response_model=AuditChainVerifyResponse)
async def verify_audit_chain() -> AuditChainVerifyResponse:
    """Execute 1-click retrospective SHA-256 chain verification to detect tampering."""
    rpt = _audit_chain.verify_chain_integrity()
    return AuditChainVerifyResponse(
        is_valid=rpt.is_valid,
        total_records=rpt.total_records,
        broken_index=rpt.broken_index,
        tamper_reason=rpt.tamper_reason,
        genesis_hash=rpt.genesis_hash,
        last_hash=rpt.last_hash,
        verified_at=rpt.verified_at,
    )


# ── Zero-Knowledge Proof (zk-SNARK) Endpoints ─────────────────────────

_zk_verifier = ZKSNARKProofVerifier()


class VerifyZKProofRequest(BaseModel):
    proof_id: str = "zk_proof_bank_alpha_r1"
    bank_id: str = "bank_alpha"
    round_id: int = 1
    pi_a: list[str] = ["0x1234", "0x5678"]
    pi_b: list[list[str]] = [["0x1", "0x2"], ["0x3", "0x4"]]
    pi_c: list[str] = ["0x9abc", "0xdef0"]
    public_weight_hash: str = "0x0000000000000000000000000000000000000000000000000000000000000001"
    l2_norm_bound: float = 10.0
    vector_dimension: int = 128


@router.post("/zkp/verify")
async def verify_zk_proof(req: VerifyZKProofRequest) -> dict[str, Any]:
    """Verify Groth16 zk-SNARK model weight attestation proof in O(1) time."""
    proof = ZKSNARKAttestationProof(
        proof_id=req.proof_id,
        bank_id=req.bank_id,
        round_id=req.round_id,
        pi_a=req.pi_a,
        pi_b=req.pi_b,
        pi_c=req.pi_c,
        public_weight_hash=req.public_weight_hash,
        l2_norm_bound=req.l2_norm_bound,
        vector_dimension=req.vector_dimension,
        created_at_timestamp=0.0,
    )
    res = _zk_verifier.verify_attestation_proof(proof)
    return {
        "is_valid": res.is_valid,
        "status_code": res.status_code,
        "proof_id": res.proof_id,
        "bank_id": res.bank_id,
        "verification_time_ms": res.verification_time_ms,
        "verification_message": res.verification_message,
        "pairing_check_passed": res.pairing_check_passed,
        "circuit_metadata": res.circuit_metadata,
    }


@router.get("/zkp/status")
async def get_zkp_status() -> dict[str, Any]:
    """Get status telemetry for zk-SNARK model weight attestation circuit and verifier."""
    return _zk_verifier.get_verifier_status()


# ── Confidential Federated Unlearning Endpoints ───────────────────────

_unlearning_engine = FederatedUnlearningEngine()


class UnlearnBankRequest(BaseModel):
    target_bank_id: str = "bank_gamma"
    unlearning_method: str = UnlearningMethod.FIRST_ORDER_HESSIAN_INVERSION.value
    start_round: int = 1
    end_round: int = 42


@router.post("/unlearn")
async def unlearn_bank_contributions(req: UnlearnBankRequest) -> dict[str, Any]:
    """Trigger exact or approximate federated model weight unlearning for an evicted bank."""
    method_enum = UnlearningMethod(req.unlearning_method) if req.unlearning_method in UnlearningMethod._value2member_map_ else UnlearningMethod.FIRST_ORDER_HESSIAN_INVERSION
    res = _unlearning_engine.unlearn_bank_contributions(
        target_bank_id=req.target_bank_id,
        method=method_enum,
    )
    return {
        "target_bank_id": res.target_bank_id,
        "unlearning_method": res.unlearning_method,
        "initial_model_l2_norm": res.initial_model_l2_norm,
        "unlearned_model_l2_norm": res.unlearned_model_l2_norm,
        "parameter_drift_delta": res.parameter_drift_delta,
        "hessian_spectral_radius": res.hessian_spectral_radius,
        "mia_membership_probability": res.mia_membership_probability,
        "execution_time_ms": res.execution_time_ms,
        "erasure_verified": res.erasure_verified,
        "lineage_hash": res.lineage_hash,
        "audit_log": res.audit_log,
    }


@router.get("/unlearn/status")
async def get_unlearning_status() -> dict[str, Any]:
    """Get telemetry for federated unlearning engine and MIA risk auditor."""
    return {
        "engine_status": "ACTIVE",
        "supported_methods": [m.value for m in UnlearningMethod],
        "total_unlearning_runs": _unlearning_engine.unlearning_runs_count,
        "target_mia_threshold": 0.52,
        "hessian_inversion_solver": "Conjugate Gradient (H^-1 v)",
    }


# ── Post-Quantum Cryptography (PQC SecAgg & Kyber/Dilithium) Endpoints ───

_pqc_driver = PQCSecAggDriver()


class GeneratePQCKeypairRequest(BaseModel):
    kem_algorithm: str = PQCKemAlgorithm.KYBER_768.value
    signature_algorithm: str = PQCSignatureAlgorithm.DILITHIUM_3.value


@router.post("/pqc/keypair")
async def generate_pqc_keypair(req: GeneratePQCKeypairRequest) -> dict[str, Any]:
    """Generate NIST FIPS 203 CRYSTALS-Kyber KEM and FIPS 204 CRYSTALS-Dilithium signature keypairs."""
    kyber_kp = _pqc_driver.generate_kyber_keypair()
    dilithium_kp = _pqc_driver.generate_dilithium_keypair()

    return {
        "kem_algorithm": kyber_kp.algorithm.value,
        "kyber_public_key_hex": kyber_kp.public_key_bytes.hex()[:64] + "...",
        "kyber_pk_len_bytes": len(kyber_kp.public_key_bytes),
        "kyber_sk_len_bytes": len(kyber_kp.secret_key_bytes),
        "signature_algorithm": dilithium_kp.algorithm.value,
        "dilithium_public_key_hex": dilithium_kp.public_key_bytes.hex()[:64] + "...",
        "dilithium_pk_len_bytes": len(dilithium_kp.public_key_bytes),
        "dilithium_sk_len_bytes": len(dilithium_kp.secret_key_bytes),
        "quantum_security_level": "NIST Security Level 3 (256-bit Lattice Security)",
    }


@router.post("/pqc/encapsulate")
async def encapsulate_pqc_secret() -> dict[str, Any]:
    """Execute NIST FIPS 203 Kyber KEM secret encapsulation and hybrid shared secret derivation."""
    kyber_kp = _pqc_driver.generate_kyber_keypair()
    ct, ss = _pqc_driver.encapsulate_secret(kyber_kp.public_key_bytes)

    return {
        "kem_algorithm": "Kyber768",
        "ciphertext_hex": ct.hex()[:64] + "...",
        "ciphertext_len_bytes": len(ct),
        "shared_secret_hash": hashlib.sha256(ss).hexdigest(),
        "shared_secret_len_bytes": len(ss),
        "encapsulation_status": "COMPLETED",
        "lattice_security": "M-LWE (Module Learning With Errors)",
    }


@router.get("/pqc/status")
async def get_pqc_status() -> dict[str, Any]:
    """Get status telemetry for Post-Quantum Cryptography suite and NIST standards compliance."""
    state = _pqc_driver.compute_pqc_secagg_round_state(
        round_id=42,
        participating_banks=["bank_alpha", "bank_beta", "bank_gamma", "bank_delta"],
    )
    return {
        "status": "ACTIVE",
        "standard_fips_203": "NIST ML-KEM (CRYSTALS-Kyber-768)",
        "standard_fips_204": "NIST ML-DSA (CRYSTALS-Dilithium-3)",
        "quantum_security_level": state.quantum_security_level,
        "total_encapsulations": _pqc_driver.encapsulations_count,
        "total_signatures_verified": _pqc_driver.signatures_verified_count,
        "round_state": {
            "round_id": state.round_id,
            "participating_banks": state.participating_banks,
            "hybrid_shared_secrets_derived": state.hybrid_shared_secrets_derived,
            "zero_sum_verified": state.zero_sum_verified,
            "lineage_hash": state.lineage_hash,
            "audit_events": state.audit_events,
        },
    }
