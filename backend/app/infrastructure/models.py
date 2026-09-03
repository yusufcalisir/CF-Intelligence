"""SQLAlchemy ORM models.

These map domain concepts to PostgreSQL tables. They're separate from
domain entities — the repository layer handles conversion between
ORM models and domain objects.

Uses SQLAlchemy 2.0 Mapped/mapped_column syntax.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class SimulationRunModel(Base):
    """Persistent record of a simulation run."""

    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    total_rounds: Mapped[int] = mapped_column(Integer, default=10)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Denormalized results stored as JSON for simplicity.
    # In a production system, these would be normalized into separate tables.
    banks_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rounds_data: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class BankConfigModel(Base):
    """Bank configuration for a simulation."""

    __tablename__ = "bank_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    simulation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    fraud_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    num_transactions: Mapped[int] = mapped_column(Integer, nullable=False)
    data_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    local_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    federated_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class TrainingRoundModel(Base):
    """Record of a single federated training round."""

    __tablename__ = "training_rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    simulation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    global_loss: Mapped[float] = mapped_column(Float, default=0.0)
    participating_bank_ids: Mapped[list] = mapped_column(JSON, default=list)
    dropped_bank_ids: Mapped[list] = mapped_column(JSON, default=list)
    per_bank_loss: Mapped[dict] = mapped_column(JSON, default=dict)
    per_bank_samples: Mapped[dict] = mapped_column(JSON, default=dict)
    aggregation_time_ms: Mapped[float] = mapped_column(Float, default=0.0)
    round_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Phase 2: AML Intelligence Platform ────────


class AlertModel(Base):
    """Persistent record of a fraud alert."""

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bank_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    transaction_id: Mapped[str] = mapped_column(String(36), nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    involved_entity_ids: Mapped[list] = mapped_column(JSON, default=list)
    top_features: Mapped[list] = mapped_column(JSON, default=list)
    risk_factors: Mapped[list] = mapped_column(JSON, default=list)
    model_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    historical_evidence: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CaseModel(Base):
    """Persistent record of an investigation case."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="p3_medium")
    assigned_to: Mapped[str | None] = mapped_column(String(100), nullable=True)
    alert_ids: Mapped[list] = mapped_column(JSON, default=list)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    timeline: Mapped[list] = mapped_column(JSON, default=list)
    total_risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EntityModel(Base):
    """Persistent record of a privacy-preserving entity."""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    privacy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    bank_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    display_label: Mapped[str] = mapped_column(String(50), nullable=False)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_level: Mapped[str] = mapped_column(String(20), default="minimal")
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )


class RelationshipModel(Base):
    """Persistent record of an entity relationship (graph edge)."""

    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    target_entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )


class SharedIntelligenceModel(Base):
    """Persistent record of shared cross-institution intelligence."""

    __tablename__ = "shared_intelligence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_bank_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    intelligence_type: Mapped[str] = mapped_column(String(30), nullable=False)
    privacy_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    risk_indicator: Mapped[float] = mapped_column(Float, default=0.0)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    related_alert_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceModel(Base):
    """Persistent record of case evidence."""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(100), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )


class InvestigatorAuditLogModel(Base):
    """Persistent record of investigator audit logs."""

    __tablename__ = "investigator_audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    investigator: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    session_duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class BusinessRuleModel(Base):
    """Persistent record of a dynamic AML/Fraud policy rule."""

    __tablename__ = "business_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    condition: Mapped[dict] = mapped_column(JSON, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Integer, default=True
    )  # Stored as SQLite/Postgres int boolean
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Phase 35: Production Domain Persistence ────────────────────────────────


class FederatedRoundModel(Base):
    """Persistent record of a federated training round (production, not simulation)."""

    __tablename__ = "federated_rounds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    consortium_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="collecting_gradients")
    # JSON list of bank_ids that submitted gradients so far
    submitted_bank_ids: Mapped[list] = mapped_column(JSON, default=list)
    quorum_required: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    global_model_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    aggregation_strategy: Mapped[str] = mapped_column(String(30), default="fedavg")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GlobalModelModel(Base):
    """Persistent record of an aggregated global model version."""

    __tablename__ = "global_models"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    consortium_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    round_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    # CANDIDATE | SHADOW | CHAMPION | ROLLED_BACK | DEPRECATED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="candidate")
    auc: Mapped[float | None] = mapped_column(Float, nullable=True)
    f1_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Path in model registry vault / S3 / local storage
    weights_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Architecture metadata serialized as JSON
    architecture_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GradientSubmissionModel(Base):
    """Immutable record of a single bank's gradient submission within a federated round."""

    __tablename__ = "gradient_submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    round_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    bank_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # SHA-256 hex digest of the compressed masked gradient bytes
    gradient_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dp_epsilon_used: Mapped[float] = mapped_column(Float, nullable=False)
    participant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # ACCEPTED | REJECTED_BYZANTINE | REJECTED_EPSILON | REJECTED_SIGNATURE
    validation_status: Mapped[str] = mapped_column(String(30), default="accepted")
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )


class ConsortiumMemberModel(Base):
    """Persistent record of a bank's membership within a federated consortium."""

    __tablename__ = "consortium_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    consortium_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    bank_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # PENDING | ACTIVE | SUSPENDED | EVICTED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    role: Mapped[str] = mapped_column(String(20), default="member")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantConfigModel(Base):
    """Persistent record of per-bank tenant configuration and provisioning state."""

    __tablename__ = "tenant_configs"

    bank_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(10), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    data_residency_region: Mapped[str] = mapped_column(String(30), nullable=False)
    # PENDING_VERIFICATION | ACTIVE | SUSPENDED | OFFBOARDED
    status: Mapped[str] = mapped_column(String(25), nullable=False, default="pending_verification")
    # SHA-256 fingerprint of the current mTLS certificate
    cert_fingerprint: Mapped[str | None] = mapped_column(String(96), nullable=True)
    cert_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Vault transit key path for this tenant's encryption
    vault_key_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # True once PostgreSQL schema tenant_{bank_id} has been created
    schema_provisioned: Mapped[bool] = mapped_column(Integer, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEventModel(Base):
    """Immutable append-only audit chain for all security-critical platform events.

    Each row's ``chain_hash`` is SHA-256(previous_chain_hash + event payload),
    forming a tamper-evident linked chain.
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # GRADIENT_RECEIVED | MODEL_DEPLOYED | GDPR_ERASURE | CERT_ROTATED | BANK_REGISTERED | etc.
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    actor_bank_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Serialized event payload (no raw PII — only hashed identifiers)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # SHA-256(prev_chain_hash + json.dumps(event_type + actor + timestamp + payload))
    chain_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
    )
