"""Pydantic v2 schemas for Enterprise Security Suite, Vault KMS, ABAC, and PQC APIs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ── ABAC & Zero-Trust Security Status ──────────────────────────────────


class ABACEvalRequest(BaseModel):
    """Request payload to test dynamic ABAC policy evaluation for user and resource attributes."""

    model_config = ConfigDict(extra="ignore")

    user_username: str = Field(default="analyst_a1", description="Username or analyst ID")
    user_bank_id: str = Field(default="bank_a", description="Bank institution ID of the user")
    user_roles: list[str] = Field(default=["analyst"], description="Assigned roles for user")
    user_clearance: int = Field(default=2, ge=1, le=5, description="Clearance level 1-5")
    user_shift_hours: str = Field(default="08:00-18:00", description="Permitted operating hours")
    user_approval_tier: float = Field(default=50000.0, ge=0.0, description="Financial approval threshold")

    resource_type: str = Field(default="alert", description="Resource type (alert, case, model, transaction)")
    resource_id: str = Field(default="alt_1001", description="Unique resource identifier")
    resource_bank_id: str = Field(default="bank_a", description="Owning institution ID of the resource")
    resource_amount: float = Field(default=12500.0, ge=0.0, description="Financial value or risk amount")
    resource_classification: int = Field(default=1, ge=1, le=5, description="Resource classification level")

    action: str = Field(default="read", description="Requested action (read, write, approve, export)")
    hour_override: int | None = Field(default=None, ge=0, le=23, description="Optional hour override for time-window testing")

    # Contract alias fields
    actor: str | None = None
    actor_role: str | None = None
    bank_id: str | None = None
    resource: str | None = None
    attributes: dict[str, Any] | None = None


class ABACEvalResponse(BaseModel):
    """Result of ABAC policy engine evaluation."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    policy_name: str
    reason: str
    evaluated_at: str


class AuditChainEntryResponse(BaseModel):
    """Single immutable event entry from the SHA-256 cryptographic audit chain."""

    model_config = ConfigDict(extra="forbid")

    index: int
    event_type: str
    actor: str
    target_id: str
    timestamp: str
    details: dict[str, Any]
    prev_hash: str
    curr_hash: str


class AuditChainVerifyResponse(BaseModel):
    """Verification receipt proving cryptographic integrity of the SHA-256 audit chain."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool
    total_records: int
    broken_index: int | None = None
    tamper_reason: str | None = None
    genesis_hash: str
    last_hash: str
    verified_at: str


class SecurityStatusResponse(BaseModel):
    """Telemetry report covering enterprise security layers."""

    model_config = ConfigDict(extra="forbid")

    mtls: dict[str, Any]
    oidc: dict[str, Any]
    abac: dict[str, Any]
    vault: dict[str, Any]
    audit_chain: dict[str, Any]


# ── Zero-Knowledge Proof (zk-SNARK) Schemas ────────────────────────────


class VerifyZKProofRequest(BaseModel):
    """Payload to verify Groth16 zk-SNARK attestation proof for model weights."""

    model_config = ConfigDict(extra="forbid")

    proof_id: str = Field(default="zk_proof_bank_alpha_r1")
    bank_id: str = Field(default="bank_alpha")
    round_id: int = Field(default=1, ge=1)
    pi_a: list[str] = Field(..., min_length=2, max_length=2)
    pi_b: list[list[str]] = Field(..., min_length=2, max_length=2)
    pi_c: list[str] = Field(..., min_length=2, max_length=2)
    public_weight_hash: str = Field(...)
    l2_norm_bound: float = Field(default=10.0, gt=0.0)
    vector_dimension: int = Field(default=128, gt=0)


class VerifyZKProofResponse(BaseModel):
    """Verification receipt for zk-SNARK attestation proof."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool
    status_code: str
    proof_id: str
    bank_id: str
    verification_time_ms: float
    verification_message: str
    pairing_check_passed: bool
    circuit_metadata: dict[str, Any]


class ZKVerifierStatusResponse(BaseModel):
    """Status and circuit telemetry for the Groth16 proof verifier."""

    model_config = ConfigDict(extra="ignore")

    proving_scheme: str = "Groth16"
    curve: str = "BN254"
    hash_algorithm: str = "Poseidon-BN254"
    verified_proofs_count: int = 0
    rejected_proofs_count: int = 0
    verification_complexity: str = "O(1) Constant Time"
    typical_verification_sla_ms: float = 2.5
    circuit_type: str | None = "Groth16BN254"
    constraints_count: int | None = 65536
    total_proofs_verified: int | None = 0
    failed_verifications: int | None = 0
    average_verification_time_ms: float | None = 2.5
    max_l2_norm_bound: float | None = 10.0
    supported_vector_dimension: int | None = 128
    enclave_hardware_acceleration: bool | None = True
    last_verified_proof: dict[str, Any] | None = None



# ── Confidential Federated Unlearning Schemas ──────────────────────────


class UnlearnBankRequest(BaseModel):
    """Request payload to trigger federated model weight unlearning for an evicted bank."""

    model_config = ConfigDict(extra="forbid")

    target_bank_id: str = Field(default="bank_gamma", description="Evicted bank ID to unlearn")
    unlearning_method: str = Field(default="exact_reaggregation", description="Unlearning method")
    start_round: int = Field(default=1, ge=1)
    end_round: int = Field(default=42, ge=1)
    ascent_lr: float = Field(default=0.01, gt=0.0)
    ascent_steps: int = Field(default=3, ge=1)
    projection_radius: float = Field(default=0.15, gt=0.0)


class UnlearnBankResponse(BaseModel):
    """Execution receipt and post-unlearning validation metrics."""

    model_config = ConfigDict(extra="ignore")

    target_bank_id: str
    unlearning_method: str
    initial_model_l2_norm: float
    unlearned_model_l2_norm: float
    parameter_drift_delta: float
    hessian_spectral_radius: float
    mia_membership_probability: float | None = None
    execution_time_ms: float
    erasure_verified: bool
    lineage_hash: str
    audit_log: list[dict[str, Any]] | list[str]
    retained_banks: list[str]


class UnlearningStatusResponse(BaseModel):
    """Telemetry report for federated unlearning engine."""

    model_config = ConfigDict(extra="ignore")

    engine_status: str
    supported_methods: list[str]
    total_unlearning_runs: int
    target_mia_threshold: float
    unlearning_mechanism: str


# ── Post-Quantum Cryptography (PQC SecAgg) Schemas ─────────────────────


class GeneratePQCKeypairRequest(BaseModel):
    """Request parameters for generating quantum-resistant KEM and signature keypairs."""

    model_config = ConfigDict(extra="ignore")

    kem_algorithm: str = Field(default="kyber_768", description="NIST FIPS 203 ML-KEM algorithm")
    signature_algorithm: str = Field(default="dilithium_3", description="NIST FIPS 204 ML-DSA algorithm")


class GeneratePQCKeypairResponse(BaseModel):
    """Generated NIST FIPS 203 and 204 public keys and keypair metadata."""

    model_config = ConfigDict(extra="ignore")

    kem_algorithm: str
    kyber_public_key_hex: str
    kyber_pk_len_bytes: int
    kyber_sk_len_bytes: int
    signature_algorithm: str
    dilithium_public_key_hex: str
    dilithium_pk_len_bytes: int
    dilithium_sk_len_bytes: int
    quantum_security_level: str


class EncapsulatePQCResponse(BaseModel):
    """NIST ML-KEM secret encapsulation receipt and derived shared secret hash."""

    model_config = ConfigDict(extra="ignore")

    kem_algorithm: str
    ciphertext_hex: str
    ciphertext_len_bytes: int
    shared_secret_hash: str
    shared_secret_len_bytes: int
    encapsulation_status: str
    lattice_security: str


class PQCStatusResponse(BaseModel):
    """Status report for the Post-Quantum Cryptography suite."""

    model_config = ConfigDict(extra="ignore")

    status: str
    standard_fips_203: str
    standard_fips_204: str
    quantum_security_level: str
    total_encapsulations: int
    total_signatures_verified: int
    round_state: dict[str, Any]


# ── Cross-Chain Settlement & Layer-2 Liquidity Bridge Schemas ───────────


class CrossChainDisburseRequest(BaseModel):
    """Payload to trigger multi-ledger incentive disbursements."""

    model_config = ConfigDict(extra="ignore")

    epoch_id: int = Field(default=42, ge=1)
    pool_amount: float = Field(default=100000.0, gt=0.0)
    currency: str = Field(default="wCBDC")
    allocations: dict[str, float] | None = Field(default=None)


class CrossChainRouteItem(BaseModel):
    """Individual bridge settlement route transaction."""

    model_config = ConfigDict(extra="ignore")

    bank_id: str
    network: str
    protocol: str
    token_symbol: str
    amount: float
    shapley_share_pct: float
    destination_recipient: str
    message_id: str
    gas_fee_usd: float
    status: str


class CrossChainDisburseResponse(BaseModel):
    """Settlement receipt proving atomic cross-chain disbursement across participating bank nodes."""

    model_config = ConfigDict(extra="ignore")

    epoch_id: int
    pool_currency: str
    total_pool_amount: float
    total_gas_fees_usd: float
    routes: list[CrossChainRouteItem]
    execution_time_ms: float
    bridge_audit_hash: str
    is_fully_finalized: bool
    audit_events: list[dict[str, Any]] | list[str]


class BridgeStatusResponse(BaseModel):
    """Telemetry report for the cross-chain liquidity bridge."""

    model_config = ConfigDict(extra="ignore")

    bridge_status: str
    primary_protocols: list[str]
    total_disbursements_count: int
    total_volume_settled_usd: float
    supported_networks: list[dict[str, Any]] | dict[str, Any]


# ── Adaptive Differential Privacy Auto-Scaler Schemas ──────────────────


class CalibrateRDPRequest(BaseModel):
    """Payload to calibrate dynamic noise scale sigma via loss velocity and Rényi DP."""

    model_config = ConfigDict(extra="ignore")

    round_id: int = Field(default=1, ge=1)
    current_loss: float = Field(default=0.42, ge=0.0)
    prev_loss: float = Field(default=0.55, ge=0.0)
    batch_size: int = Field(default=256, ge=1)
    total_samples: int = Field(default=10000, ge=1)
    target_epsilon: float = Field(default=4.0, gt=0.0)
    total_rounds: int = Field(default=50, ge=1)
    node_id: str = Field(default="global")
    enforce_budget_limit: bool = Field(default=False)


class CalibrateRDPResponse(BaseModel):
    """Dynamic calibration result for Gaussian noise multiplier and privacy budget expenditure."""

    model_config = ConfigDict(extra="ignore")

    round_id: int
    node_id: str
    calibrated_sigma: float
    gradient_clip_c: float
    instantaneous_epsilon: float
    optimal_alpha: float
    loss_velocity: float
    sample_ratio_q: float
    cumulative_epsilon: float
    target_epsilon: float
    budget_exhaustion_pct: float
    is_budget_exceeded: bool


class RDPStatusResponse(BaseModel):
    """Telemetry report for adaptive DP budget auto-scaler."""

    model_config = ConfigDict(extra="ignore")

    node_id: str
    active_sigma: float
    active_clip_norm: float
    cumulative_epsilon: float
    target_epsilon: float
    remaining_budget_pct: float
    projected_final_epsilon: float
    snr_signal_to_noise: float
    risk_tier: str
    audit_events: list[dict[str, Any]] | list[str]
    audit_chain_valid: bool
    nodes_summary: list[dict[str, Any]] | dict[str, Any]


# ── HashiCorp Vault & Tenant KMS Key Management Schemas ─────────────────


class KMSKeyRotateRequest(BaseModel):
    """Payload to rotate tenant encryption key in Vault Transit engine and local KMS."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(..., min_length=2, max_length=64, description="Target bank institution ID")


class KMSKeyRotateResponse(BaseModel):
    """Confirmation receipt for rotated KMS tenant encryption key."""

    model_config = ConfigDict(extra="forbid")

    status: str
    bank_id: str
    key_name: str
    previous_version: int
    new_version: int
    rotated_at: str


class KMSKeyMetadataResponse(BaseModel):
    """Metadata inspection for a tenant encryption key in KMS."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str
    key_name: str
    algorithm: str
    latest_version: int
    created_at: str
    last_rotated_at: str
    active_versions: list[int]
    vault_transit_synced: bool


class VaultSealStatusResponse(BaseModel):
    """HashiCorp Vault seal and health status."""

    model_config = ConfigDict(extra="forbid")

    initialized: bool
    sealed: bool
    standby: bool
    vault_url: str
    mount_point: str
    ha_enabled: bool
