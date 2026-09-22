"""Domain enumerations.

Defines the bounded vocabulary for simulation states, bank tiers,
and aggregation strategies used throughout the system.
"""

from enum import StrEnum


class SimulationStatus(StrEnum):
    """Lifecycle states of a simulation run."""

    PENDING = "pending"
    INITIALIZING_CLIENTS = "initializing_clients"
    GENERATING_DATA = "generating_data"
    TRAINING_LOCAL = "training_local"
    TRAINING_FEDERATED = "training_federated"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class BankTier(StrEnum):
    """Bank size classification affecting data volume and distribution."""

    LARGE = "large"
    MEDIUM = "medium"
    SMALL = "small"


class ModelType(StrEnum):
    """Whether a model was trained locally or via federation."""

    LOCAL = "local"
    FEDERATED = "federated"


class AggregationMethod(StrEnum):
    """Supported federated aggregation strategies."""

    FED_AVG = "fed_avg"
    FED_AVG_WEIGHTED = "fed_avg_weighted"
    FED_PROX = "fed_prox"
    KRUM = "krum"
    COORDINATE_WISE_MEDIAN = "coordinate_wise_median"
    FED_ADAM = "fed_adam"
    FED_ADAGRAD = "fed_adagrad"
    FED_YOGI = "fed_yogi"
    TRIMMED_MEAN = "trimmed_mean"
    BULYAN = "bulyan"
    SCAFFOLD = "scaffold"


class ClientStatus(StrEnum):
    """Status of a bank client during a training round."""

    ACTIVE = "active"
    DROPPED = "dropped"
    RECONNECTED = "reconnected"
    OFFLINE = "offline"


class PrivacyMechanism(StrEnum):
    """Available privacy-enhancing mechanisms."""

    NONE = "none"
    DIFFERENTIAL_PRIVACY = "differential_privacy"
    SECURE_AGGREGATION = "secure_aggregation"
    BOTH = "both"


class AdversarialAttackType(StrEnum):
    """Supported adversarial evasion attack algorithms for robust training."""

    NONE = "none"
    FGSM = "fgsm"
    PGD = "pgd"


class DPMode(StrEnum):
    """Differential privacy implementation mode.

    POST_HOC: Simplified approach — clip weight delta and add noise after training.
    OPACUS: Industry-standard — per-sample gradient clipping and noise during training
            using Meta AI's Opacus library.
    """

    POST_HOC = "post_hoc"
    OPACUS = "opacus"


class FLEngineType(StrEnum):
    """Federated learning engine implementation.

    CUSTOM: Built-in simulation engine with full control over failure
            injection, Byzantine robustness, and real-time observability.
    FLOWER: Industry-standard Flower (flwr.dev) framework adapter using
            Ray-based simulation for standards-compliant FL execution.
    """

    CUSTOM = "custom"
    FLOWER = "flower"


# ── Phase 2: AML Intelligence Platform ────────


class AlertSeverity(StrEnum):
    """Severity classification for fraud alerts."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AlertStatus(StrEnum):
    """Lifecycle states of a fraud alert."""

    NEW = "new"
    INVESTIGATING = "investigating"
    CONFIRMED_FRAUD = "confirmed_fraud"
    FALSE_POSITIVE = "false_positive"
    ESCALATED = "escalated"
    CLOSED = "closed"


class CaseStatus(StrEnum):
    """Lifecycle states of an investigation case."""

    OPEN = "open"
    ASSIGNED = "assigned"
    INVESTIGATING = "investigating"
    PENDING_REVIEW = "pending_review"
    ESCALATED = "escalated"
    CLOSED_CONFIRMED = "closed_confirmed"
    CLOSED_FALSE_POSITIVE = "closed_false_positive"
    SAR_FILED = "sar_filed"


class CasePriority(StrEnum):
    """Priority classification for investigation cases."""

    P1_CRITICAL = "p1_critical"
    P2_HIGH = "p2_high"
    P3_MEDIUM = "p3_medium"
    P4_LOW = "p4_low"


class TriagePriority(StrEnum):
    """Priority classification for automated alert triage."""

    P1_CRITICAL = "p1_critical"
    P2_HIGH = "p2_high"
    P3_MEDIUM = "p3_medium"
    P4_LOW = "p4_low"


class TriageAction(StrEnum):
    """Recommended operational workflow action for triaged alerts."""

    ESCALATE_IMMEDIATE = "escalate_immediate"
    INVESTIGATE_CASE = "investigate_case"
    QUEUE_STANDARD = "queue_standard"
    AUTO_MONITOR = "auto_monitor"


class EntityType(StrEnum):
    """Types of entities in the financial crime graph."""

    CUSTOMER = "customer"
    MERCHANT = "merchant"
    DEVICE = "device"
    CARD = "card"
    EMAIL = "email"
    PHONE = "phone"
    IP_ADDRESS = "ip_address"


class RelationshipType(StrEnum):
    """Types of edges in the entity relationship graph."""

    OWNS = "owns"
    USES = "uses"
    TRANSACTS_WITH = "transacts_with"
    SHARES_DEVICE = "shares_device"
    SHARES_IP = "shares_ip"
    LINKED_ALERT = "linked_alert"
    SAME_ENTITY = "same_entity"


class RiskLevel(StrEnum):
    """Risk classification for entities and transactions."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    MINIMAL = "minimal"


class IntelligenceType(StrEnum):
    """Types of shared intelligence between institutions."""

    FRAUD_ALERT = "fraud_alert"
    PATTERN_MATCH = "pattern_match"
    VELOCITY_ANOMALY = "velocity_anomaly"
    ENTITY_LINK = "entity_link"
    RISK_SCORE_CHANGE = "risk_score_change"


class ScenarioType(StrEnum):
    """Pre-built fraud scenario types for simulation."""

    FRAUD_RING = "fraud_ring"
    ACCOUNT_TAKEOVER = "account_takeover"
    MONEY_LAUNDERING = "money_laundering"
    CARD_TESTING = "card_testing"


# ── Phase 5: Federated Graph Embedding ────────


class GNNArchitecture(StrEnum):
    """Graph Neural Network architecture for federated graph embedding."""

    GRAPHSAGE = "graphsage"


# ── Phase 36: Bank Onboarding ─────────────────


class BankStatus(StrEnum):
    """Enterprise status of a participating bank node."""

    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFBOARDED = "offboarded"


# ── Phase 106: Inter-Bank FININT Case Messaging ────────────────────────────────


class FinintTicketType(StrEnum):
    """Structured cross-institution FININT request type.

    Each type maps to a distinct compliance workflow and escalation path
    per European Collaborative FININT interchange standards.
    """

    URGENT_FREEZE_REQUEST = "URGENT_FREEZE_REQUEST"
    MULE_ACCOUNT_ALERT = "MULE_ACCOUNT_ALERT"
    INFORMATION_REQUEST = "INFORMATION_REQUEST"
    TRANSACTION_DISPUTE_TRACE = "TRANSACTION_DISPUTE_TRACE"


class FinintTicketStatus(StrEnum):
    """Immutable lifecycle states of an inter-bank FININT ticket.

    Transitions are strictly ordered and cannot regress:
    OPEN → ACKNOWLEDGED → FUNDS_FROZEN / INFORMATION_ATTACHED / DECLINED → CLOSED
    """

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FUNDS_FROZEN = "FUNDS_FROZEN"
    INFORMATION_ATTACHED = "INFORMATION_ATTACHED"
    DECLINED = "DECLINED"
    CLOSED = "CLOSED"
