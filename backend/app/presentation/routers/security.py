"""Enterprise Security Suite API Endpoints.

Exposes status, ABAC policy testing, HashiCorp Vault secrets metadata,
mTLS certificate status, tamper-proof SHA-256 cryptographic audit chain verification,
zk-SNARK proof verification, Post-Quantum Cryptography (PQC), federated unlearning,
Layer-2 multi-chain settlement bridge, adaptive DP auto-scaler, and KMS key rotation.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, HTTPException, Query, status

from app.application.schemas.security import (
    ABACEvalRequest,
    ABACEvalResponse,
    AuditChainEntryResponse,
    AuditChainVerifyResponse,
    BridgeStatusResponse,
    CalibrateRDPRequest,
    CalibrateRDPResponse,
    CrossChainDisburseRequest,
    CrossChainDisburseResponse,
    CrossChainRouteItem,
    EncapsulatePQCResponse,
    GeneratePQCKeypairRequest,
    GeneratePQCKeypairResponse,
    KMSKeyMetadataResponse,
    KMSKeyRotateRequest,
    KMSKeyRotateResponse,
    PQCStatusResponse,
    RDPStatusResponse,
    SecurityStatusResponse,
    UnlearnBankRequest,
    UnlearnBankResponse,
    UnlearningStatusResponse,
    VaultSealStatusResponse,
    VerifyZKProofRequest,
    VerifyZKProofResponse,
    ZKVerifierStatusResponse,
)
from app.application.services.federated_unlearning_engine import FederatedUnlearningEngine
from app.application.services.privacy_service import PrivacyBudgetExceededError
from app.config import get_settings
from app.domain.value_objects_unlearning import UnlearningMethod
from app.domain.value_objects_zkp import ZKSNARKAttestationProof
from app.infrastructure.security.abac_engine import ABACEngine, ABACResource
from app.infrastructure.security.adaptive_dp_autoscaler import AdaptiveDPAutoScaler
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.layer2_crosschain_bridge import Layer2CrossChainBridgeDriver
from app.infrastructure.security.mtls_manager import MTLSManager
from app.infrastructure.security.oidc_authenticator import OIDCAuthenticator, UserClaims
from app.infrastructure.security.pqc_secagg_driver import PQCSecAggDriver
from app.infrastructure.security.tenant_kms import TenantKMSKeyManager
from app.infrastructure.security.vault_client import VaultClient
from app.infrastructure.security.zk_snark_verifier import ZKSNARKProofVerifier

logger = logging.getLogger(__name__)

# Legacy and Canonical Routers for dual-prefix support (/v1/security and /api/v1/security)
router = APIRouter(prefix="/v1/security", tags=["security"])
api_router = APIRouter(prefix="/api/v1/security", tags=["security"])

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
_zk_verifier = ZKSNARKProofVerifier()
_unlearning_engine = FederatedUnlearningEngine()
_pqc_driver = PQCSecAggDriver()
_bridge_driver = Layer2CrossChainBridgeDriver()
_rdp_autoscaler = AdaptiveDPAutoScaler()


# ── Handler Implementations ───────────────────────────────────


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


async def evaluate_abac_policy(req: ABACEvalRequest) -> ABACEvalResponse:
    """Test dynamic ABAC policy evaluation for arbitrary user and resource attributes."""
    username = req.actor if req.actor else req.user_username
    bank_id = req.bank_id if req.bank_id else req.user_bank_id
    roles = [req.actor_role] if req.actor_role else req.user_roles
    clearance = req.user_clearance
    if req.attributes and "clearance_level" in req.attributes:
        val = str(req.attributes["clearance_level"]).upper()
        if "LEVEL_3" in val or "3" in val:
            clearance = 3
        elif "LEVEL_4" in val or "4" in val:
            clearance = 4
        elif "LEVEL_5" in val or "5" in val:
            clearance = 5

    user = UserClaims(
        sub=f"usr_{username}",
        username=username,
        bank_id=bank_id,
        roles=roles,
        clearance_level=clearance,
        shift_hours=req.user_shift_hours,
        approval_tier=req.user_approval_tier,
    )
    res_type = req.resource if req.resource else req.resource_type
    res_bank = req.bank_id if req.bank_id else req.resource_bank_id

    resource = ABACResource(
        resource_type=res_type,
        resource_id=req.resource_id,
        bank_id=res_bank,
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
        actor=username,
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


async def verify_zk_proof(req: VerifyZKProofRequest) -> VerifyZKProofResponse:
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
    return VerifyZKProofResponse(
        is_valid=res.is_valid,
        status_code=res.status_code,
        proof_id=res.proof_id,
        bank_id=res.bank_id,
        verification_time_ms=res.verification_time_ms,
        verification_message=res.verification_message,
        pairing_check_passed=res.pairing_check_passed,
        circuit_metadata=res.circuit_metadata,
    )


async def get_zkp_status() -> ZKVerifierStatusResponse:
    """Get status telemetry for zk-SNARK model weight attestation circuit and verifier."""
    return ZKVerifierStatusResponse.model_validate(_zk_verifier.get_verifier_status())


async def unlearn_bank_contributions(req: UnlearnBankRequest) -> UnlearnBankResponse:
    """Trigger exact federated model weight unlearning or simulated baseline for an evicted bank."""
    method_enum = (
        UnlearningMethod(req.unlearning_method)
        if req.unlearning_method in UnlearningMethod._value2member_map_
        else UnlearningMethod.EXACT_REAGGREGATION
    )
    res = _unlearning_engine.unlearn_bank_contributions(
        target_bank_id=req.target_bank_id,
        method=method_enum,
        ascent_lr=req.ascent_lr,
        ascent_steps=req.ascent_steps,
        projection_radius=req.projection_radius,
    )
    return UnlearnBankResponse(
        target_bank_id=res.target_bank_id,
        unlearning_method=res.unlearning_method,
        initial_model_l2_norm=res.initial_model_l2_norm,
        unlearned_model_l2_norm=res.unlearned_model_l2_norm,
        parameter_drift_delta=res.parameter_drift_delta,
        hessian_spectral_radius=res.hessian_spectral_radius,
        mia_membership_probability=res.mia_membership_probability,
        execution_time_ms=res.execution_time_ms,
        erasure_verified=res.erasure_verified,
        lineage_hash=res.lineage_hash,
        audit_log=res.audit_log,
        retained_banks=res.retained_banks,
    )


async def get_unlearning_status() -> UnlearningStatusResponse:
    """Get telemetry for federated unlearning engine and MIA risk auditor."""
    return UnlearningStatusResponse(
        engine_status="ACTIVE",
        supported_methods=[m.value for m in UnlearningMethod],
        total_unlearning_runs=_unlearning_engine.unlearning_runs_count,
        target_mia_threshold=0.52,
        unlearning_mechanism="Exact Re-aggregation / Lineage Subtraction / Projected Gradient Ascent",
    )


async def generate_pqc_keypair(req: GeneratePQCKeypairRequest) -> GeneratePQCKeypairResponse:
    """Generate NIST FIPS 203 CRYSTALS-Kyber KEM and FIPS 204 CRYSTALS-Dilithium signature keypairs."""
    kyber_kp = _pqc_driver.generate_kyber_keypair()
    dilithium_kp = _pqc_driver.generate_dilithium_keypair()

    return GeneratePQCKeypairResponse(
        kem_algorithm=kyber_kp.algorithm.value,
        kyber_public_key_hex=kyber_kp.public_key_bytes.hex()[:64] + "...",
        kyber_pk_len_bytes=len(kyber_kp.public_key_bytes),
        kyber_sk_len_bytes=len(kyber_kp.secret_key_bytes),
        signature_algorithm=dilithium_kp.algorithm.value,
        dilithium_public_key_hex=dilithium_kp.public_key_bytes.hex()[:64] + "...",
        dilithium_pk_len_bytes=len(dilithium_kp.public_key_bytes),
        dilithium_sk_len_bytes=len(dilithium_kp.secret_key_bytes),
        quantum_security_level="NIST Security Level 3 (256-bit Lattice Security)",
    )


async def encapsulate_pqc_secret() -> EncapsulatePQCResponse:
    """Execute NIST FIPS 203 Kyber KEM secret encapsulation and hybrid shared secret derivation."""
    kyber_kp = _pqc_driver.generate_kyber_keypair()
    ct, ss = _pqc_driver.encapsulate_secret(kyber_kp.public_key_bytes)

    return EncapsulatePQCResponse(
        kem_algorithm="Kyber768",
        ciphertext_hex=ct.hex()[:64] + "...",
        ciphertext_len_bytes=len(ct),
        shared_secret_hash=hashlib.sha256(ss).hexdigest(),
        shared_secret_len_bytes=len(ss),
        encapsulation_status="COMPLETED",
        lattice_security="M-LWE (Module Learning With Errors)",
    )


async def get_pqc_status() -> PQCStatusResponse:
    """Get status telemetry for Post-Quantum Cryptography suite and NIST standards compliance."""
    state = _pqc_driver.compute_pqc_secagg_round_state(
        round_id=42,
        participating_banks=["bank_alpha", "bank_beta", "bank_gamma", "bank_delta"],
    )
    return PQCStatusResponse(
        status="ACTIVE",
        standard_fips_203="NIST ML-KEM (CRYSTALS-Kyber-768)",
        standard_fips_204="NIST ML-DSA (CRYSTALS-Dilithium-3)",
        quantum_security_level=state.quantum_security_level,
        total_encapsulations=_pqc_driver.encapsulations_count,
        total_signatures_verified=_pqc_driver.signatures_verified_count,
        round_state={
            "round_id": state.round_id,
            "participating_banks": state.participating_banks,
            "hybrid_shared_secrets_derived": state.hybrid_shared_secrets_derived,
            "zero_sum_verified": state.zero_sum_verified,
            "lineage_hash": state.lineage_hash,
            "audit_events": state.audit_events,
        },
    )


async def disburse_crosschain_incentives(req: CrossChainDisburseRequest) -> CrossChainDisburseResponse:
    """Execute multi-ledger cross-chain incentive disbursements based on Shapley allocations."""
    allocs = req.allocations or {
        "bank_alpha": 0.38,
        "bank_beta": 0.29,
        "bank_gamma": 0.21,
        "bank_delta": 0.12,
    }
    result = _bridge_driver.disburse_crosschain_incentives(
        epoch_id=req.epoch_id,
        allocations=allocs,
        pool_amount=req.pool_amount,
        currency=req.currency,
    )
    routes = [
        CrossChainRouteItem(
            bank_id=r.bank_id,
            network=r.network.value,
            protocol=r.protocol.value,
            token_symbol=r.token_symbol,
            amount=r.amount,
            shapley_share_pct=r.shapley_share_pct,
            destination_recipient=r.destination_recipient,
            message_id=r.message_id,
            gas_fee_usd=r.gas_fee_usd,
            status=r.status,
        )
        for r in result.routes
    ]
    return CrossChainDisburseResponse(
        epoch_id=result.epoch_id,
        pool_currency=result.pool_currency,
        total_pool_amount=result.total_pool_amount,
        total_gas_fees_usd=result.total_gas_fees_usd,
        routes=routes,
        execution_time_ms=result.execution_time_ms,
        bridge_audit_hash=result.bridge_audit_hash,
        is_fully_finalized=result.is_fully_finalized,
        audit_events=result.audit_events,
    )


async def get_bridge_status() -> BridgeStatusResponse:
    """Get telemetry for Cross-Chain Inter-Bank Settlement & Layer-2 Liquidity Bridge."""
    return BridgeStatusResponse(
        bridge_status="ACTIVE",
        primary_protocols=[
            "Chainlink CCIP (Cross-Chain Interoperability)",
            "LayerZero V2",
            "Canton Daml Interop",
        ],
        total_disbursements_count=_bridge_driver.total_disbursements_count,
        total_volume_settled_usd=_bridge_driver.total_volume_settled_usd,
        supported_networks=_bridge_driver.get_network_metrics(),
    )


async def calibrate_rdp_noise(req: CalibrateRDPRequest) -> CalibrateRDPResponse:
    """Dynamically calibrate per-round noise multiplier sigma_t using Rényi DP and loss velocity."""
    try:
        cal = _rdp_autoscaler.auto_scale_noise_multiplier(
            round_id=req.round_id,
            current_loss=req.current_loss,
            prev_loss=req.prev_loss,
            batch_size=req.batch_size,
            total_samples=req.total_samples,
            total_rounds=req.total_rounds,
            node_id=req.node_id,
            enforce_budget_limit=req.enforce_budget_limit,
            target_epsilon=req.target_epsilon,
        )
    except PrivacyBudgetExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        ) from e

    state = _rdp_autoscaler.get_accountant_state(
        node_id=req.node_id, target_epsilon=req.target_epsilon
    )
    return CalibrateRDPResponse(
        round_id=cal.round_id,
        node_id=cal.node_id,
        calibrated_sigma=cal.calibrated_sigma,
        gradient_clip_c=cal.gradient_clip_c,
        instantaneous_epsilon=cal.instantaneous_epsilon,
        optimal_alpha=cal.optimal_alpha,
        loss_velocity=cal.loss_velocity,
        sample_ratio_q=cal.sample_ratio_q,
        cumulative_epsilon=state.current_epsilon_at_delta,
        target_epsilon=state.target_epsilon,
        budget_exhaustion_pct=state.budget_exhaustion_pct,
        is_budget_exceeded=state.is_budget_exceeded,
    )


async def get_rdp_status(node_id: str = Query("global", description="Bank node identifier")) -> RDPStatusResponse:
    """Get real-time telemetry and budget projection for the adaptive DP auto-scaler."""
    telemetry = _rdp_autoscaler.get_telemetry(node_id=node_id)
    return RDPStatusResponse(
        node_id=telemetry.node_id,
        active_sigma=telemetry.active_sigma,
        active_clip_norm=telemetry.active_clip_norm,
        cumulative_epsilon=telemetry.cumulative_epsilon,
        target_epsilon=telemetry.target_epsilon,
        remaining_budget_pct=telemetry.remaining_budget_pct,
        projected_final_epsilon=telemetry.projected_final_epsilon,
        snr_signal_to_noise=telemetry.snr_signal_to_noise,
        risk_tier=telemetry.risk_tier,
        audit_events=telemetry.audit_events,
        audit_chain_valid=telemetry.audit_chain_valid,
        nodes_summary=_rdp_autoscaler.get_all_nodes_summary(),
    )


async def rotate_kms_key(req: KMSKeyRotateRequest) -> KMSKeyRotateResponse:
    """Rotate tenant encryption key in HashiCorp Vault Transit engine and local KMS."""
    kms_mgr = TenantKMSKeyManager.get_instance()
    res = kms_mgr.rotate_key(req.bank_id)
    return KMSKeyRotateResponse(
        status=res["status"],
        bank_id=res["bank_id"],
        key_name=res["key_name"],
        previous_version=res["previous_version"],
        new_version=res["new_version"],
        rotated_at=res["rotated_at"],
    )


async def get_kms_key_metadata(bank_id: str) -> KMSKeyMetadataResponse:
    """Retrieve key metadata and active versions for a bank tenant."""
    kms_mgr = TenantKMSKeyManager.get_instance()
    if not kms_mgr.has_tenant_key(bank_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active KMS key found for bank '{bank_id}'",
        )
    res = kms_mgr.get_key_metadata(bank_id)
    return KMSKeyMetadataResponse(
        bank_id=res["bank_id"],
        key_name=res["key_name"],
        algorithm=res["algorithm"],
        latest_version=res["latest_version"],
        created_at=res["created_at"],
        last_rotated_at=res["last_rotated_at"],
        active_versions=res["active_versions"],
        vault_transit_synced=res["vault_transit_synced"],
    )


async def get_vault_seal_status() -> VaultSealStatusResponse:
    """Inspect HashiCorp Vault seal status and high availability readiness."""
    is_healthy = _vault_client.is_healthy()
    return VaultSealStatusResponse(
        initialized=True,
        sealed=not is_healthy if settings.vault_enabled else False,
        standby=False,
        vault_url=settings.vault_url,
        mount_point="secret",
        ha_enabled=True,
    )


# ── Route Binding to Router Variants ──────────────────────────

for prefix_tag, r in [("v1", router), ("api_v1", api_router)]:
    r.add_api_route(
        "/status",
        get_security_status,
        methods=["GET"],
        response_model=SecurityStatusResponse,
        summary="Get Enterprise Security Suite Status",
        operation_id=f"{prefix_tag}_get_security_status",
    )
    r.add_api_route(
        "/abac/evaluate",
        evaluate_abac_policy,
        methods=["POST"],
        response_model=ABACEvalResponse,
        summary="Evaluate Dynamic ABAC Policy",
        operation_id=f"{prefix_tag}_evaluate_abac_policy",
    )
    r.add_api_route(
        "/audit-chain",
        list_audit_chain,
        methods=["GET"],
        response_model=list[AuditChainEntryResponse],
        summary="List Cryptographic Audit Chain Entries",
        operation_id=f"{prefix_tag}_list_audit_chain",
    )
    r.add_api_route(
        "/audit-chain/verify",
        verify_audit_chain,
        methods=["POST"],
        response_model=AuditChainVerifyResponse,
        summary="Verify Cryptographic Audit Chain Integrity",
        operation_id=f"{prefix_tag}_verify_audit_chain",
    )
    r.add_api_route(
        "/zkp/verify",
        verify_zk_proof,
        methods=["POST"],
        response_model=VerifyZKProofResponse,
        summary="Verify Groth16 zk-SNARK Attestation Proof",
        operation_id=f"{prefix_tag}_verify_zk_proof",
    )
    r.add_api_route(
        "/zk/verify",
        verify_zk_proof,
        methods=["POST"],
        response_model=VerifyZKProofResponse,
        summary="Verify Groth16 zk-SNARK Attestation Proof (Alias)",
        operation_id=f"{prefix_tag}_verify_zk_proof_alias",
    )
    r.add_api_route(
        "/zkp/status",
        get_zkp_status,
        methods=["GET"],
        response_model=ZKVerifierStatusResponse,
        summary="Get zk-SNARK Verifier Status Telemetry",
        operation_id=f"{prefix_tag}_get_zkp_status",
    )
    r.add_api_route(
        "/zk/status",
        get_zkp_status,
        methods=["GET"],
        response_model=ZKVerifierStatusResponse,
        summary="Get zk-SNARK Verifier Status Telemetry (Alias)",
        operation_id=f"{prefix_tag}_get_zk_status_alias",
    )
    r.add_api_route(
        "/unlearn",
        unlearn_bank_contributions,
        methods=["POST"],
        response_model=UnlearnBankResponse,
        summary="Trigger Exact Federated Model Weight Unlearning",
        operation_id=f"{prefix_tag}_unlearn_bank_contributions",
    )
    r.add_api_route(
        "/unlearn/status",
        get_unlearning_status,
        methods=["GET"],
        response_model=UnlearningStatusResponse,
        summary="Get Federated Unlearning Engine Telemetry",
        operation_id=f"{prefix_tag}_get_unlearning_status",
    )
    r.add_api_route(
        "/unlearning/status",
        get_unlearning_status,
        methods=["GET"],
        response_model=UnlearningStatusResponse,
        summary="Get Federated Unlearning Engine Telemetry (Alias)",
        operation_id=f"{prefix_tag}_get_unlearning_status_alias",
    )
    r.add_api_route(
        "/pqc/keypair",
        generate_pqc_keypair,
        methods=["POST"],
        response_model=GeneratePQCKeypairResponse,
        summary="Generate Quantum-Safe Kyber/Dilithium Keypair",
        operation_id=f"{prefix_tag}_generate_pqc_keypair",
    )
    r.add_api_route(
        "/pqc/encapsulate",
        encapsulate_pqc_secret,
        methods=["POST"],
        response_model=EncapsulatePQCResponse,
        summary="Encapsulate Secret via NIST FIPS 203 ML-KEM",
        operation_id=f"{prefix_tag}_encapsulate_pqc_secret",
    )
    r.add_api_route(
        "/pqc/status",
        get_pqc_status,
        methods=["GET"],
        response_model=PQCStatusResponse,
        summary="Get Post-Quantum Cryptography Suite Telemetry",
        operation_id=f"{prefix_tag}_get_pqc_status",
    )
    r.add_api_route(
        "/bridge/disburse",
        disburse_crosschain_incentives,
        methods=["POST"],
        response_model=CrossChainDisburseResponse,
        summary="Disburse Multi-Chain Cross-Ledger Incentives",
        operation_id=f"{prefix_tag}_disburse_crosschain_incentives",
    )
    r.add_api_route(
        "/bridge/status",
        get_bridge_status,
        methods=["GET"],
        response_model=BridgeStatusResponse,
        summary="Get Cross-Chain Liquidity Bridge Telemetry",
        operation_id=f"{prefix_tag}_get_bridge_status",
    )
    r.add_api_route(
        "/rdp/calibrate",
        calibrate_rdp_noise,
        methods=["POST"],
        response_model=CalibrateRDPResponse,
        summary="Dynamically Calibrate Rényi Differential Privacy Noise",
        operation_id=f"{prefix_tag}_calibrate_rdp_noise",
    )
    r.add_api_route(
        "/rdp/status",
        get_rdp_status,
        methods=["GET"],
        response_model=RDPStatusResponse,
        summary="Get Adaptive DP Auto-Scaler Telemetry",
        operation_id=f"{prefix_tag}_get_rdp_status",
    )
    r.add_api_route(
        "/kms/rotate",
        rotate_kms_key,
        methods=["POST"],
        response_model=KMSKeyRotateResponse,
        summary="Rotate Tenant Encryption Key in Vault & KMS",
        operation_id=f"{prefix_tag}_rotate_kms_key",
    )
    r.add_api_route(
        "/kms/keys/{bank_id}",
        get_kms_key_metadata,
        methods=["GET"],
        response_model=KMSKeyMetadataResponse,
        summary="Get Tenant KMS Encryption Key Metadata",
        operation_id=f"{prefix_tag}_get_kms_key_metadata",
    )
    r.add_api_route(
        "/vault/seal-status",
        get_vault_seal_status,
        methods=["GET"],
        response_model=VaultSealStatusResponse,
        summary="Get HashiCorp Vault Seal and HA Status",
        operation_id=f"{prefix_tag}_get_vault_seal_status",
    )
